import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import torch

from lineage import save_image, load_image, init_db, import_image, import_from_input, scan_and_import, _png_created_at, rename_folder, delete_folder, set_parent


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "image_manager.db"
    init_db(db_path)
    return {"root": tmp_path, "db": db_path}


def make_image_tensor(h=8, w=8):
    return torch.zeros(1, h, w, 3)


# --- flat filesystem layout (no date prefix) ---

def test_save_image_path_has_no_date_prefix(workspace):
    img = make_image_tensor()
    result = save_image(img, "portraits", {}, workspace["root"], workspace["db"])

    abs_path = Path(result["abs_path"])
    rel = abs_path.relative_to(workspace["root"])
    parts = rel.parts
    import re
    assert not re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]), \
        f"Expected flat path but got date prefix: {rel}"
    assert parts[0] == "portraits"


def test_import_image_path_has_no_date_prefix(workspace):
    from PIL import Image as PilImage
    pil_img = PilImage.new("RGB", (4, 4))
    result = import_image(pil_img, "photo.png", workspace["root"], workspace["db"])

    abs_path = Path(result["abs_path"])
    rel = abs_path.relative_to(workspace["root"])
    parts = rel.parts
    import re
    assert not re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]), \
        f"Expected flat path but got date prefix: {rel}"
    assert parts[0] == "photo"


def test_import_from_input_path_has_no_date_prefix(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir, "base.jpg")
    result = import_from_input(src, workspace["root"], workspace["db"])

    abs_path = Path(result["abs_path"])
    rel = abs_path.relative_to(workspace["root"])
    parts = rel.parts
    import re
    assert not re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]), \
        f"Expected flat path but got date prefix: {rel}"
    assert parts[0] == "base"


# --- root save ---

def test_root_save_writes_sidecar_with_null_parent(workspace):
    img = make_image_tensor()
    prompt = {}  # no ManagedLoadImage nodes

    result = save_image(img, "test", prompt, workspace["root"], workspace["db"])

    sidecar_path = Path(result["abs_path"]).with_suffix(".json")
    meta = json.loads(sidecar_path.read_text())
    assert meta["parent_uuid"] is None
    assert meta["uuid"] == result["uuid"]


# --- child save ---

def test_child_save_inherits_parent_uuid(workspace):
    root_img = make_image_tensor()
    root_result = save_image(root_img, "root", {}, workspace["root"], workspace["db"])

    child_prompt = {
        "1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_result["filename"]}}
    }
    child_img = make_image_tensor()
    child_result = save_image(child_img, "child", child_prompt, workspace["root"], workspace["db"])

    assert child_result["parent_uuid"] == root_result["uuid"]
    assert child_result["root_uuid"] == root_result["uuid"]


# --- PNG metadata chunks ---

def test_saved_png_contains_lineage_chunks(workspace):
    img = make_image_tensor()
    result = save_image(img, "chunky", {}, workspace["root"], workspace["db"])

    from PIL import Image as PilImage
    pil = PilImage.open(result["abs_path"])
    assert pil.text.get("lineage_id") == result["uuid"]
    assert pil.text.get("parent_id") == ""


def test_child_png_contains_parent_id_chunk(workspace):
    img = make_image_tensor()
    root = save_image(img, "root", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])

    from PIL import Image as PilImage
    pil = PilImage.open(child["abs_path"])
    assert pil.text.get("parent_id") == root["uuid"]


# --- parent detection edge cases ---

def test_unknown_managed_load_filename_saves_as_root(workspace):
    img = make_image_tensor()
    prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": "nonexistent/file.png"}}}
    result = save_image(img, "fallback", prompt, workspace["root"], workspace["db"])
    assert result["parent_uuid"] is None
    assert result["root_uuid"] == result["uuid"]


def test_multiple_managed_load_nodes_saves_as_root(workspace):
    img = make_image_tensor()
    root_a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    root_b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    prompt = {
        "1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}},
        "2": {"class_type": "ManagedLoadImage", "inputs": {"image": root_b["filename"]}},
    }
    result = save_image(img, "multi", prompt, workspace["root"], workspace["db"])
    assert result["parent_uuid"] is None


# --- batch siblings ---

def test_batch_siblings_share_parent_uuid(workspace):
    root_img = make_image_tensor()
    root = save_image(root_img, "root", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}

    batch = torch.zeros(3, 8, 8, 3)
    results = [save_image(batch[i:i+1], "sibling", child_prompt, workspace["root"], workspace["db"]) for i in range(3)]

    parent_uuids = {r["parent_uuid"] for r in results}
    assert parent_uuids == {root["uuid"]}
    uuids = [r["uuid"] for r in results]
    assert len(set(uuids)) == 3  # all distinct


# --- sidecar completeness ---

def test_sidecar_has_all_required_fields(workspace):
    img = make_image_tensor()
    result = save_image(img, "complete", {}, workspace["root"], workspace["db"])

    import json
    meta = json.loads(Path(result["abs_path"]).with_suffix(".json").read_text())
    for field in ("uuid", "parent_uuid", "root_uuid", "root_name", "created_at", "filename"):
        assert field in meta, f"sidecar missing field: {field}"
    assert meta["root_uuid"] == meta["uuid"]  # root points to itself
    assert meta["root_name"] == "complete"
    assert meta["filename"] == result["filename"]


# --- extra_pnginfo / workflow chunk ---

def test_extra_pnginfo_written_as_png_text_chunks(workspace):
    img = make_image_tensor()
    workflow = {"nodes": [{"id": 1, "type": "KSampler"}]}
    result = save_image(img, "wf", {}, workspace["root"], workspace["db"],
                        extra_pnginfo={"workflow": workflow})

    from PIL import Image as PilImage
    pil = PilImage.open(result["abs_path"])
    assert "workflow" in pil.text
    assert json.loads(pil.text["workflow"]) == workflow


def test_extra_pnginfo_none_leaves_behavior_unchanged(workspace):
    img = make_image_tensor()
    result = save_image(img, "no_wf", {}, workspace["root"], workspace["db"],
                        extra_pnginfo=None)

    from PIL import Image as PilImage
    pil = PilImage.open(result["abs_path"])
    assert "workflow" not in pil.text
    assert "lineage_id" in pil.text


# --- load ---

def test_load_returns_image_and_mask(workspace):
    img = make_image_tensor()
    result = save_image(img, "load_test", {}, workspace["root"], workspace["db"])

    image_tensor, mask_tensor = load_image(result["filename"], workspace["root"])

    assert image_tensor.shape[-1] == 3
    assert len(mask_tensor.shape) == 3


# --- _png_created_at ---

def test_png_created_at_returns_file_mtime_as_iso_utc(tmp_path):
    f = tmp_path / "test.png"
    f.write_bytes(b"fake")
    known_mtime = 1_700_000_000.0  # 2023-11-14T22:13:20+00:00
    os.utime(f, (known_mtime, known_mtime))

    result = _png_created_at(f)

    expected = datetime.fromtimestamp(known_mtime, tz=timezone.utc).isoformat()
    assert result == expected


# --- import_image mtime ---

def test_import_image_created_at_matches_file_mtime(workspace, tmp_path):
    from PIL import Image as PilImage
    from unittest.mock import patch

    pil_img = PilImage.new("RGB", (4, 4))
    known_mtime_str = "2023-01-15T10:30:00+00:00"

    with patch("lineage._png_created_at", return_value=known_mtime_str):
        result = import_image(pil_img, "photo.png", workspace["root"], workspace["db"])

    sidecar = json.loads(Path(result["abs_path"]).with_suffix(".json").read_text())
    assert sidecar["created_at"] == known_mtime_str

    import sqlite3
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT created_at FROM images WHERE uuid = ?", (result["uuid"],)).fetchone()
    con.close()
    assert row[0] == known_mtime_str


# --- import_folder flat layout ---

def test_import_folder_flat_path_no_date_prefix(workspace, tmp_path):
    from lineage import import_folder
    from PIL import Image as PilImage
    import re

    src = tmp_path / "source"
    src.mkdir()
    subdir = src / "portraits"
    subdir.mkdir()
    img_path = subdir / "photo.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    list(import_folder(src, workspace["root"], workspace["db"]))

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT abs_path, root_name FROM images LIMIT 1").fetchone()
    con.close()

    abs_path = Path(row[0])
    rel = abs_path.relative_to(workspace["root"])
    assert not re.match(r'\d{4}-\d{2}-\d{2}$', rel.parts[0]), \
        f"Expected flat path but got date prefix: {rel}"
    assert row[1] == "portraits"


def test_import_folder_old_layout_source_strips_date(workspace, tmp_path):
    """Importing a source folder that has an old YYYY-MM-DD prefix strips the date."""
    from lineage import import_folder
    from PIL import Image as PilImage
    import re

    src = tmp_path / "source"
    (src / "2022-03-15" / "mychain").mkdir(parents=True)
    img_path = src / "2022-03-15" / "mychain" / "photo.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    list(import_folder(src, workspace["root"], workspace["db"]))

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT abs_path, root_name FROM images LIMIT 1").fetchone()
    con.close()

    abs_path = Path(row[0])
    rel = abs_path.relative_to(workspace["root"])
    assert not re.match(r'\d{4}-\d{2}-\d{2}$', rel.parts[0]), \
        f"Expected flat path but got date prefix: {rel}"
    assert row[1] == "mychain"


# --- import_folder mtime ---

def test_import_folder_created_at_matches_file_mtime(workspace, tmp_path):
    from lineage import import_folder
    from unittest.mock import patch
    from PIL import Image as PilImage

    src = tmp_path / "source"
    src.mkdir()
    img_path = src / "photo.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    known_mtime_str = "2022-06-01T08:00:00+00:00"

    with patch("lineage._png_created_at", return_value=known_mtime_str):
        list(import_folder(src, workspace["root"], workspace["db"]))

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT created_at FROM images LIMIT 1").fetchone()
    con.close()
    assert row[0] == known_mtime_str


# --- _create_missing_sidecars mtime ---

def test_create_missing_sidecars_old_layout_derives_root_name(workspace):
    """Old-layout path YYYY-MM-DD/root_name/file.png → root_name correctly derived."""
    from lineage import _create_missing_sidecars
    from PIL import Image as PilImage

    folder = workspace["root"] / "2023-01-01" / "mychain"
    folder.mkdir(parents=True)
    img_path = folder / "img_00001_.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    _create_missing_sidecars(workspace["root"])

    sidecar = json.loads(img_path.with_suffix(".json").read_text())
    assert sidecar["root_name"] == "mychain"


def test_create_missing_sidecars_new_layout_multilevel_root_name(workspace):
    """New-layout path portraits/session_a/file.png → root_name = portraits/session_a."""
    from lineage import _create_missing_sidecars
    from PIL import Image as PilImage

    folder = workspace["root"] / "portraits" / "session_a"
    folder.mkdir(parents=True)
    img_path = folder / "img_00001_.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    _create_missing_sidecars(workspace["root"])

    sidecar = json.loads(img_path.with_suffix(".json").read_text())
    assert sidecar["root_name"] == "portraits/session_a"


def test_create_missing_sidecars_uses_file_mtime(workspace):
    from lineage import _create_missing_sidecars
    from unittest.mock import patch
    from PIL import Image as PilImage

    folder = workspace["root"] / "2023-01-01" / "mychain"
    folder.mkdir(parents=True)
    img_path = folder / "img_00001_.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    known_mtime_str = "2020-03-10T12:00:00+00:00"

    with patch("lineage._png_created_at", return_value=known_mtime_str):
        count = _create_missing_sidecars(workspace["root"])

    assert count == 1
    sidecar = json.loads(img_path.with_suffix(".json").read_text())
    assert sidecar["created_at"] == known_mtime_str


def test_create_missing_sidecars_falls_back_to_now_on_stat_failure(workspace):
    from lineage import _create_missing_sidecars
    from unittest.mock import patch
    from PIL import Image as PilImage

    folder = workspace["root"] / "2023-01-01" / "mychain"
    folder.mkdir(parents=True)
    img_path = folder / "img_00001_.png"
    PilImage.new("RGB", (4, 4)).save(img_path)

    with patch("lineage._png_created_at", side_effect=OSError("no stat")):
        count = _create_missing_sidecars(workspace["root"])

    assert count == 1
    sidecar = json.loads(img_path.with_suffix(".json").read_text())
    # created_at should be set to something (fallback datetime.now())
    assert sidecar["created_at"]
    dt = datetime.fromisoformat(sidecar["created_at"])
    assert dt.tzinfo is not None


# --- save_image unchanged ---

def test_save_image_created_at_is_not_file_mtime(workspace):
    """save_image uses datetime.now(), not file mtime — generated images stay unchanged."""
    from unittest.mock import patch

    img = torch.zeros(1, 8, 8, 3)
    sentinel = "1999-01-01T00:00:00+00:00"

    # Even if _png_created_at would return a sentinel, save_image must NOT call it
    with patch("lineage._png_created_at", return_value=sentinel) as mock_mtime:
        result = save_image(img, "test", {}, workspace["root"], workspace["db"])
        mock_mtime.assert_not_called()

    sidecar = json.loads(Path(result["abs_path"]).with_suffix(".json").read_text())
    assert sidecar["created_at"] != sentinel


# --- scan_and_import mtime backfill ---

def _make_sidecar_png(managed_root, uuid_str, root_name, created_at_str):
    """Write a minimal PNG + sidecar pair into managed_root for testing."""
    from PIL import Image as PilImage
    folder = managed_root / "2020-01-01" / root_name
    folder.mkdir(parents=True, exist_ok=True)
    png_path = folder / "img_00001_.png"
    PilImage.new("RGB", (2, 2)).save(png_path)
    rel = str(png_path.relative_to(managed_root))
    sidecar = {
        "uuid": uuid_str,
        "parent_uuid": None,
        "root_uuid": uuid_str,
        "root_name": root_name,
        "created_at": created_at_str,
        "filename": rel,
    }
    png_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))
    return png_path


def test_scan_and_import_backfills_stale_created_at(workspace):
    """Sidecar with import-time timestamp is corrected to file mtime during scan."""
    import uuid as uuidlib
    from unittest.mock import patch

    img_uuid = str(uuidlib.uuid4())
    stale_ts = "2023-01-01T00:00:00+00:00"
    corrected_ts = "2019-06-15T08:00:00+00:00"

    png_path = _make_sidecar_png(workspace["root"], img_uuid, "mychain", stale_ts)

    with patch("lineage._png_created_at", return_value=corrected_ts):
        scan_and_import(workspace["root"], workspace["db"])

    # DB should have corrected timestamp
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT created_at FROM images WHERE uuid = ?", (img_uuid,)).fetchone()
    con.close()
    assert row[0] == corrected_ts

    # Sidecar should be rewritten
    sidecar = json.loads(png_path.with_suffix(".json").read_text())
    assert sidecar["created_at"] == corrected_ts


def test_scan_and_import_does_not_rewrite_near_match(workspace):
    """Images where sidecar created_at and mtime differ by < 1s are not rewritten."""
    import uuid as uuidlib
    from unittest.mock import patch

    img_uuid = str(uuidlib.uuid4())
    ts = "2024-03-01T12:00:00+00:00"
    # mtime is 0.5s later — within tolerance
    close_ts = "2024-03-01T12:00:00.500000+00:00"

    png_path = _make_sidecar_png(workspace["root"], img_uuid, "close", ts)
    original_sidecar_content = png_path.with_suffix(".json").read_text()

    with patch("lineage._png_created_at", return_value=close_ts):
        scan_and_import(workspace["root"], workspace["db"])

    # Sidecar should NOT be rewritten
    assert png_path.with_suffix(".json").read_text() == original_sidecar_content

    # DB records the sidecar value (not the mtime value)
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT created_at FROM images WHERE uuid = ?", (img_uuid,)).fetchone()
    con.close()
    assert row[0] == ts


def test_scan_and_import_does_not_rewrite_freshly_saved_images(workspace):
    """Images saved via save_image have created_at ≈ mtime — scan must not rewrite them."""
    img = torch.zeros(1, 8, 8, 3)
    result = save_image(img, "fresh", {}, workspace["root"], workspace["db"])

    png_path = Path(result["abs_path"])
    original_sidecar = png_path.with_suffix(".json").read_text()
    original_created_at = json.loads(original_sidecar)["created_at"]

    # scan_and_import using real mtime (not mocked) — mtime ≈ datetime.now()
    scan_and_import(workspace["root"], workspace["db"])

    # Sidecar should be unchanged
    assert png_path.with_suffix(".json").read_text() == original_sidecar

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT created_at FROM images WHERE uuid = ?", (result["uuid"],)).fetchone()
    con.close()
    assert row[0] == original_created_at


# --- rename_folder ---

def test_rename_folder_updates_root_name_in_db(workspace):
    img = make_image_tensor()
    result = save_image(img, "oldname", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": result["filename"]}}}
    save_image(img, "child", child_prompt, workspace["root"], workspace["db"])

    rename_folder("oldname", "newname", workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    rows = con.execute("SELECT root_name FROM images WHERE root_uuid = ?", (result["uuid"],)).fetchall()
    con.close()
    assert all(r[0] == "newname" for r in rows)
    assert len(rows) == 2


def test_rename_folder_rewrites_sidecars(workspace):
    img = make_image_tensor()
    result = save_image(img, "oldname", {}, workspace["root"], workspace["db"])
    sidecar_path = Path(result["abs_path"]).with_suffix(".json")

    rename_folder("oldname", "newname", workspace["db"], workspace["root"])

    meta = json.loads(sidecar_path.read_text())
    assert meta["root_name"] == "newname"


def test_rename_folder_rejects_empty_name(workspace):
    img = make_image_tensor()
    save_image(img, "myfolder", {}, workspace["root"], workspace["db"])

    with pytest.raises(ValueError, match="empty"):
        rename_folder("myfolder", "  ", workspace["db"], workspace["root"])


def test_rename_folder_rejects_duplicate_name(workspace):
    img = make_image_tensor()
    save_image(img, "folder_a", {}, workspace["root"], workspace["db"])
    save_image(img, "folder_b", {}, workspace["root"], workspace["db"])

    with pytest.raises(ValueError, match="already exists"):
        rename_folder("folder_a", "folder_b", workspace["db"], workspace["root"])


def test_rename_folder_rolls_back_db_on_sidecar_error(workspace):
    from unittest.mock import patch

    img = make_image_tensor()
    result = save_image(img, "myname", {}, workspace["root"], workspace["db"])

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        with pytest.raises(ValueError, match="sidecar write failed"):
            rename_folder("myname", "othername", workspace["db"], workspace["root"])

    # DB must be unchanged
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT root_name FROM images WHERE uuid = ?", (result["uuid"],)).fetchone()
    con.close()
    assert row[0] == "myname"


# --- delete_folder ---

def test_delete_folder_dry_run_returns_count_without_deleting(workspace):
    img = make_image_tensor()
    result1 = save_image(img, "target", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": result1["filename"]}}}
    save_image(img, "child", child_prompt, workspace["root"], workspace["db"])

    response = delete_folder("target", workspace["db"], dry_run=True)

    assert response["count"] == 2
    assert response["dry_run"] is True
    # Nothing deleted
    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE root_name = 'target'").fetchone()[0]
    con.close()
    assert count == 2


def test_delete_folder_removes_files_sidecars_and_db_records(workspace):
    img = make_image_tensor()
    result = save_image(img, "delfolder", {}, workspace["root"], workspace["db"])
    abs_path = Path(result["abs_path"])
    sidecar = abs_path.with_suffix(".json")

    delete_folder("delfolder", workspace["db"])

    assert not abs_path.exists()
    assert not sidecar.exists()
    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE root_name = 'delfolder'").fetchone()[0]
    con.close()
    assert count == 0


def test_delete_folder_raises_for_nonexistent_folder(workspace):
    with pytest.raises(ValueError, match="not found"):
        delete_folder("no_such_folder", workspace["db"])


# --- linking guards ---

def test_scan_and_import_restores_db_from_sidecars_after_partial_write(workspace):
    """After crash between sidecar write and DB commit, scan_and_import makes DB match sidecars."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    # Simulate set_parent writing B's sidecar to point at A before crashing — DB still shows B as root.
    b_sidecar_path = Path(b["abs_path"]).with_suffix(".json")
    meta = json.loads(b_sidecar_path.read_text())
    meta["parent_uuid"] = a["uuid"]
    meta["root_uuid"] = a["uuid"]
    meta["root_name"] = "a"
    b_sidecar_path.write_text(json.dumps(meta, indent=2))
    # DB still says B has no parent
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (b["uuid"],)).fetchone()
    con.close()
    assert row[0] is None
    # scan_and_import rebuilds DB from sidecars
    scan_and_import(workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (b["uuid"],)).fetchone()
    con.close()
    assert row[0] == a["uuid"]


def test_set_parent_writes_sidecar_before_db_commit(workspace):
    """Sidecar reflects new parent even if the DB commit crashes — write order: sidecar first."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    b_sidecar = Path(b["abs_path"]).with_suffix(".json")

    _real_connect = sqlite3.connect

    class _CommitFailing:
        """Delegates everything to the real connection but raises on commit."""
        def __init__(self, path):
            self._c = _real_connect(path)
        def __getattr__(self, name):
            return getattr(self._c, name)
        def commit(self):
            raise RuntimeError("simulated crash before commit")
        def close(self):
            self._c.close()

    with pytest.raises(RuntimeError):
        with patch("lineage.sqlite3.connect", side_effect=_CommitFailing):
            set_parent(b["uuid"], a["uuid"], workspace["db"], workspace["root"])

    meta = json.loads(b_sidecar.read_text())
    assert meta["parent_uuid"] == a["uuid"]


def test_promote_child_writes_sidecar_before_db_commit(workspace):
    """promote_child writes sidecars before committing."""
    from lineage import promote_child
    img = make_image_tensor()
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": parent["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])
    parent_sidecar = Path(parent["abs_path"]).with_suffix(".json")
    child_sidecar = Path(child["abs_path"]).with_suffix(".json")

    _real_connect = sqlite3.connect

    class _CommitFailing:
        def __init__(self, path):
            self._c = _real_connect(path)
        def __getattr__(self, name):
            return getattr(self._c, name)
        def commit(self):
            raise RuntimeError("simulated crash before commit")
        def close(self):
            self._c.close()

    with pytest.raises(RuntimeError):
        with patch("lineage.sqlite3.connect", side_effect=_CommitFailing):
            promote_child(parent["uuid"], child["uuid"], workspace["db"], workspace["root"])

    # Child sidecar should reflect new parent relationship (child promoted above parent)
    child_meta = json.loads(child_sidecar.read_text())
    assert child_meta["parent_uuid"] != parent["uuid"]  # child no longer under parent


def test_fork_chain_writes_sidecar_before_db_commit(workspace):
    """fork_chain writes sidecars before committing."""
    from lineage import fork_chain
    img = make_image_tensor()
    root = save_image(img, "original", {}, workspace["root"], workspace["db"])
    root_sidecar = Path(root["abs_path"]).with_suffix(".json")

    _real_connect = sqlite3.connect

    class _CommitFailing:
        def __init__(self, path):
            self._c = _real_connect(path)
        def __getattr__(self, name):
            return getattr(self._c, name)
        def commit(self):
            raise RuntimeError("simulated crash before commit")
        def close(self):
            self._c.close()

    with pytest.raises(RuntimeError):
        with patch("lineage.sqlite3.connect", side_effect=_CommitFailing):
            fork_chain(root["uuid"], "forked", workspace["db"], workspace["root"])

    meta = json.loads(root_sidecar.read_text())
    assert meta["root_name"] == "forked"


def test_swap_adjacent_single_commit_sidecars_first(workspace):
    """swap_adjacent issues exactly one DB commit; sidecar writes precede it."""
    from lineage import swap_adjacent
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": a["filename"]}}}
    b = save_image(img, "b", child_prompt, workspace["root"], workspace["db"])
    a_sidecar = Path(a["abs_path"]).with_suffix(".json")
    b_sidecar = Path(b["abs_path"]).with_suffix(".json")

    _real_connect = sqlite3.connect
    commit_count = [0]

    class _CountingCommit:
        def __init__(self, path):
            self._c = _real_connect(path)
        def __getattr__(self, name):
            return getattr(self._c, name)
        def commit(self):
            commit_count[0] += 1
            self._c.commit()
        def close(self):
            self._c.close()

    with patch("lineage.sqlite3.connect", side_effect=_CountingCommit):
        swap_adjacent(a["uuid"], b["uuid"], workspace["db"], workspace["root"])

    assert commit_count[0] == 1, f"expected 1 commit, got {commit_count[0]}"
    # After swap: B should now be the root (B.parent = None), A under B
    b_meta = json.loads(b_sidecar.read_text())
    a_meta = json.loads(a_sidecar.read_text())
    assert b_meta["parent_uuid"] is None
    assert a_meta["parent_uuid"] == b["uuid"]


def test_scan_and_import_repairs_sidecar_cycle(workspace, capsys):
    """scan_and_import detects a cycle in sidecars, nulls the closing link, and emits a warning."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    # Inject A→B→A cycle directly into sidecars (simulating corrupted state)
    a_sidecar = Path(a["abs_path"]).with_suffix(".json")
    b_sidecar = Path(b["abs_path"]).with_suffix(".json")
    a_meta = json.loads(a_sidecar.read_text())
    b_meta = json.loads(b_sidecar.read_text())
    a_meta["parent_uuid"] = b["uuid"]
    b_meta["parent_uuid"] = a["uuid"]
    a_sidecar.write_text(json.dumps(a_meta, indent=2))
    b_sidecar.write_text(json.dumps(b_meta, indent=2))

    scan_and_import(workspace["root"], workspace["db"])

    # DB must be cycle-free: exactly one image has parent_uuid nulled
    con = sqlite3.connect(workspace["db"])
    rows = con.execute("SELECT uuid, parent_uuid FROM images").fetchall()
    con.close()
    parent_map = {r[0]: r[1] for r in rows}
    # Walk both: neither chain should loop
    for start_uuid in [a["uuid"], b["uuid"]]:
        seen = set()
        cur = start_uuid
        while cur:
            assert cur not in seen, f"cycle still present starting from {start_uuid}"
            seen.add(cur)
            cur = parent_map.get(cur)

    # Warning was printed
    captured = capsys.readouterr()
    assert "cycle detected" in captured.out.lower() or "cycle" in captured.out.lower()


def test_scan_and_import_repairs_sidecar_cycle_rewrites_sidecar(workspace):
    """The cycle-closing image's sidecar is rewritten to null parent_uuid."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    a_sidecar = Path(a["abs_path"]).with_suffix(".json")
    b_sidecar = Path(b["abs_path"]).with_suffix(".json")
    a_meta = json.loads(a_sidecar.read_text())
    b_meta = json.loads(b_sidecar.read_text())
    a_meta["parent_uuid"] = b["uuid"]
    b_meta["parent_uuid"] = a["uuid"]
    a_sidecar.write_text(json.dumps(a_meta, indent=2))
    b_sidecar.write_text(json.dumps(b_meta, indent=2))

    scan_and_import(workspace["root"], workspace["db"])

    # The cycle-closing image must have null parent_uuid in its sidecar
    a_result = json.loads(a_sidecar.read_text())
    b_result = json.loads(b_sidecar.read_text())
    null_count = sum(1 for m in [a_result, b_result] if m["parent_uuid"] is None)
    assert null_count >= 1, "cycle-closing sidecar was not rewritten"


def test_set_parent_raises_on_existing_db_cycle(workspace):
    """A synthetic DB cycle terminates with ValueError rather than hanging."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    c = save_image(img, "c", {}, workspace["root"], workspace["db"])
    # Inject A→B→A cycle directly into DB (bypassing normal guards)
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET parent_uuid = ? WHERE uuid = ?", (b["uuid"], a["uuid"]))
    con.execute("UPDATE images SET parent_uuid = ? WHERE uuid = ?", (a["uuid"], b["uuid"]))
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="existing cycle"):
        set_parent(c["uuid"], a["uuid"], workspace["db"], workspace["root"])


# --- linking operation logging ---

def test_set_parent_logs_entry_and_completion(workspace, capsys):
    """set_parent prints an entry line and a completion line with descendant count and elapsed."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])

    set_parent(b["uuid"], a["uuid"], workspace["db"], workspace["root"])

    out = capsys.readouterr().out
    assert f"child={b['uuid']}" in out
    assert f"parent={a['uuid']}" in out
    assert "descendants=" in out
    assert "elapsed=" in out


def test_set_parent_logs_error_on_failure(workspace, capsys):
    """set_parent prints an entry line and an error line when it raises."""
    img = make_image_tensor()
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    # Inject cycle so set_parent raises
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET parent_uuid = ? WHERE uuid = ?", (b["uuid"], a["uuid"]))
    con.execute("UPDATE images SET parent_uuid = ? WHERE uuid = ?", (a["uuid"], b["uuid"]))
    con.commit()
    con.close()

    with pytest.raises(ValueError):
        set_parent(b["uuid"], a["uuid"], workspace["db"], workspace["root"])

    out = capsys.readouterr().out
    assert "[set_parent]" in out


# --- search / positive_prompt ---

def test_init_db_creates_positive_prompt_column(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(images)")}
    con.close()
    assert "positive_prompt" in cols


def test_scan_and_import_stores_positive_prompt_from_workflow_chunk(workspace):
    """scan_and_import reads the workflow PNG text chunk and stores positive_prompt in SQLite."""
    from PIL import Image as PilImage
    from PIL.PngImagePlugin import PngInfo

    prompt = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red dragon"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry"}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "positive": [1, 0],
                "negative": [2, 0],
                "steps": 20,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "seed": 42,
                "denoise": 1.0,
            },
        },
    }

    # Write a PNG with a workflow (prompt) text chunk and a matching sidecar
    img_uuid = "aaaaaaaa-0000-0000-0000-000000000001"
    date_dir = workspace["root"] / "2024-01-01" / "dragon"
    date_dir.mkdir(parents=True)
    png_path = date_dir / "dragon_00001_.png"
    info = PngInfo()
    info.add_text("lineage_id", img_uuid)
    info.add_text("parent_id", "")
    info.add_text("prompt", json.dumps(prompt))
    PilImage.new("RGB", (8, 8)).save(png_path, pnginfo=info)

    import json as _json
    rel = str(Path("2024-01-01") / "dragon" / "dragon_00001_.png")
    sidecar = {
        "uuid": img_uuid,
        "parent_uuid": None,
        "root_uuid": img_uuid,
        "root_name": "dragon",
        "created_at": "2024-01-01T00:00:00+00:00",
        "filename": rel,
    }
    png_path.with_suffix(".json").write_text(_json.dumps(sidecar))

    scan_and_import(workspace["root"], workspace["db"])

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT positive_prompt FROM images WHERE uuid = ?", (img_uuid,)).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "a red dragon"


# --- ManagedLoadImageFromInput / import_from_input ---

def test_init_db_creates_original_filename_column(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(images)")}
    con.close()
    assert "original_filename" in cols


def _make_input_file(input_dir: Path, name: str = "portrait.jpg") -> Path:
    from PIL import Image as PilImage
    p = input_dir / name
    PilImage.new("RGB", (4, 4)).save(p)
    return p


def test_import_from_input_copies_file_writes_sidecar_and_db(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir)

    result = import_from_input(src, workspace["root"], workspace["db"])

    # File was copied into managed folder
    assert Path(result["abs_path"]).exists()
    # Sidecar has original_filename
    sidecar = json.loads(Path(result["abs_path"]).with_suffix(".json").read_text())
    assert sidecar["original_filename"] == "portrait.jpg"
    # DB row exists with original_filename
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT original_filename FROM images WHERE uuid = ?", (result["uuid"],)).fetchone()
    con.close()
    assert row[0] == "portrait.jpg"


def test_import_from_input_dedup_returns_existing_record(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir)

    first = import_from_input(src, workspace["root"], workspace["db"])
    second = import_from_input(src, workspace["root"], workspace["db"])

    assert second["uuid"] == first["uuid"]
    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE original_filename = 'portrait.jpg'").fetchone()[0]
    con.close()
    assert count == 1


def test_detect_parent_resolves_managed_load_image_from_input(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir, "base.jpg")
    imported = import_from_input(src, workspace["root"], workspace["db"])

    prompt = {
        "1": {
            "class_type": "ManagedLoadImageFromInput",
            "inputs": {"image": "base.jpg"},
        }
    }
    img = make_image_tensor()
    result = save_image(img, "out", prompt, workspace["root"], workspace["db"])

    assert result["parent_uuid"] == imported["uuid"]
    assert result["root_uuid"] == imported["uuid"]


def test_import_from_input_original_file_unmodified(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir)
    original_bytes = src.read_bytes()

    import_from_input(src, workspace["root"], workspace["db"])

    assert src.read_bytes() == original_bytes


def test_scan_and_import_restores_original_filename_and_dedup_still_works(workspace, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    src = _make_input_file(input_dir, "scene.jpg")
    first = import_from_input(src, workspace["root"], workspace["db"])

    # Wipe the DB to simulate a restart, then rebuild from sidecars
    con = sqlite3.connect(workspace["db"])
    con.execute("DELETE FROM images")
    con.commit()
    con.close()

    scan_and_import(workspace["root"], workspace["db"])

    # original_filename restored in DB
    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT original_filename FROM images WHERE uuid = ?", (first["uuid"],)).fetchone()
    con.close()
    assert row[0] == "scene.jpg"

    # Dedup still works: second import after rebuild returns same uuid
    second = import_from_input(src, workspace["root"], workspace["db"])
    assert second["uuid"] == first["uuid"]
    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE original_filename = 'scene.jpg'").fetchone()[0]
    con.close()
    assert count == 1


def test_init_db_uses_wal_journal_mode(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    con = sqlite3.connect(db_path)
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    assert mode == "wal"


def test_init_db_executes_synchronous_normal_pragma(tmp_path):
    db_path = tmp_path / "test.db"
    executed = []
    _real_connect = sqlite3.connect

    class _SpyConn(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            executed.append(sql.strip())
            return super().execute(sql, *args, **kwargs)

    def spy_connect(path, *args, **kwargs):
        return _SpyConn(path, *args, **kwargs)

    with patch("lineage.sqlite3.connect", side_effect=spy_connect):
        init_db(db_path)

    assert any("synchronous" in s.lower() and "normal" in s.lower() for s in executed)


def test_init_db_adds_original_filename_to_existing_db(tmp_path):
    db_path = tmp_path / "test.db"
    # Create DB without the column
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE images (
        uuid TEXT PRIMARY KEY,
        parent_uuid TEXT,
        root_uuid TEXT NOT NULL,
        root_name TEXT NOT NULL,
        filename TEXT NOT NULL,
        abs_path TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.commit()
    con.close()
    # init_db should migrate it
    init_db(db_path)
    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(images)")}
    con.close()
    assert "original_filename" in cols
