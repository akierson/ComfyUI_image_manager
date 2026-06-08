import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import torch

from lineage import save_image, load_image, init_db, import_image, scan_and_import, _png_created_at, rename_folder, delete_folder


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "image_manager.db"
    init_db(db_path)
    return {"root": tmp_path, "db": db_path}


def make_image_tensor(h=8, w=8):
    return torch.zeros(1, h, w, 3)


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
