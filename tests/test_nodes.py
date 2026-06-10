import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from PIL import Image as PilImage

from lineage import init_db, import_from_input, save_image

# Load local nodes.py explicitly to avoid collision with ComfyUI's top-level nodes.py
_nodes_path = Path(__file__).parent.parent / "nodes.py"
_spec = importlib.util.spec_from_file_location("image_manager_nodes", _nodes_path)
_nodes_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nodes_mod)
ManagedLoadImage = _nodes_mod.ManagedLoadImage
ManagedLoadImageFromInput = _nodes_mod.ManagedLoadImageFromInput
ManagedSaveImage = _nodes_mod.ManagedSaveImage


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "image_manager.db"
    init_db(db_path)
    return {"root": tmp_path, "db": db_path, "input": tmp_path / "input"}


def _make_input_file(input_dir: Path, name: str = "portrait.jpg") -> Path:
    input_dir.mkdir(parents=True, exist_ok=True)
    p = input_dir / name
    PilImage.new("RGB", (8, 8)).save(p)
    return p


# --- metadata ---

def test_managed_load_image_from_input_category():
    assert ManagedLoadImageFromInput.CATEGORY == "image_manager"


def test_managed_load_image_from_input_return_types():
    assert ManagedLoadImageFromInput.RETURN_TYPES == ("IMAGE", "MASK", "STRING")


def test_managed_load_image_from_input_function_name():
    assert ManagedLoadImageFromInput.FUNCTION == "load"


def test_load_auto_imports_and_returns_tensor_mask(workspace):
    src = _make_input_file(workspace["input"])
    node = ManagedLoadImageFromInput()

    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch.object(_nodes_mod, "DB_PATH", workspace["db"]), \
         patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        ret = node.load("portrait.jpg")

    image, mask, managed_name = ret["result"]
    assert image.shape[-1] == 3
    assert len(mask.shape) == 3
    # File was imported into DB
    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE original_filename = 'portrait.jpg'").fetchone()[0]
    con.close()
    assert count == 1


def test_is_changed_returns_different_hash_when_file_changes(workspace):
    src = _make_input_file(workspace["input"])

    with patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        h1 = ManagedLoadImageFromInput.IS_CHANGED("portrait.jpg")
        # Write different content
        PilImage.new("RGB", (16, 16), color=(255, 0, 0)).save(src)
        h2 = ManagedLoadImageFromInput.IS_CHANGED("portrait.jpg")

    assert h1 != h2
    assert isinstance(h1, str)


def test_is_changed_returns_nan_for_missing_file(workspace):
    import math
    with patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        result = ManagedLoadImageFromInput.IS_CHANGED("nonexistent.jpg")
    assert math.isnan(result)


def test_load_dedup_returns_same_uuid_on_second_call(workspace):
    src = _make_input_file(workspace["input"])
    node = ManagedLoadImageFromInput()

    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch.object(_nodes_mod, "DB_PATH", workspace["db"]), \
         patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        node.load("portrait.jpg")  # noqa: discard 3-tuple
        node.load("portrait.jpg")

    con = sqlite3.connect(workspace["db"])
    count = con.execute("SELECT COUNT(*) FROM images WHERE original_filename = 'portrait.jpg'").fetchone()[0]
    con.close()
    assert count == 1


def test_save_after_load_has_parent_uuid_of_imported_input(workspace):
    """Integration: ManagedLoadImageFromInput → ManagedSaveImage saves with correct parent_uuid."""
    src = _make_input_file(workspace["input"], "base.jpg")

    imported = import_from_input(src, workspace["root"], workspace["db"])

    prompt = {
        "1": {
            "class_type": "ManagedLoadImageFromInput",
            "inputs": {"image": "base.jpg"},
        }
    }
    img = torch.zeros(1, 8, 8, 3)
    result = save_image(img, "out", prompt, workspace["root"], workspace["db"])

    assert result["parent_uuid"] == imported["uuid"]


# --- ManagedLoadImage return types ---

def test_managed_load_image_from_input_load_returns_managed_filename_as_third(workspace):
    src = _make_input_file(workspace["input"])
    node = ManagedLoadImageFromInput()

    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch.object(_nodes_mod, "DB_PATH", workspace["db"]), \
         patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        ret = node.load("portrait.jpg")

    image, mask, managed_name = ret["result"]
    assert isinstance(managed_name, str)
    assert "portrait" in managed_name
    assert managed_name.endswith(".png")


# --- ManagedLoadImage return types ---

# --- ManagedSaveImage parent_name input ---

def test_managed_save_image_has_optional_parent_name_input():
    input_types = ManagedSaveImage.INPUT_TYPES()
    assert "parent_name" in input_types.get("optional", {})
    assert input_types["optional"]["parent_name"][0] == "STRING"


def test_save_with_parent_name_sets_parent_uuid(workspace):
    """When parent_name is supplied and found in DB, save_image uses it as parent."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "root", {}, workspace["root"], workspace["db"])

    child = save_image(img, "child", {}, workspace["root"], workspace["db"],
                       parent_name=root["filename"])

    assert child["parent_uuid"] == root["uuid"]
    assert child["root_uuid"] == root["uuid"]


def test_save_with_parent_name_missing_saves_as_root_with_warning(workspace, capsys):
    """When parent_name is not in DB, image saves as root and a warning is printed."""
    img = torch.zeros(1, 8, 8, 3)

    result = save_image(img, "orphan", {}, workspace["root"], workspace["db"],
                        parent_name="nonexistent/missing.png")

    assert result["parent_uuid"] is None
    assert result["root_uuid"] == result["uuid"]
    out = capsys.readouterr().out
    assert "WARNING" in out or "warning" in out.lower()


def test_save_with_parent_name_none_uses_detect_parent(workspace):
    """When parent_name is None, _detect_parent runs as before."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "root", {}, workspace["root"], workspace["db"])

    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"],
                       parent_name=None)

    assert child["parent_uuid"] == root["uuid"]


# --- Phase 2: get_directory_by_type patch ---

# --- Phase 2: node preview return format ---

def test_managed_load_image_load_returns_preview_dict(workspace):
    """ManagedLoadImage.load() returns dict with ui.images and result tuple for previews."""
    img = torch.zeros(1, 8, 8, 3)
    from lineage import save_image as _save
    result = _save(img, "portraits", {}, workspace["root"], workspace["db"])

    node = ManagedLoadImage()
    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch("folder_paths.get_filename_list", return_value=[result["filename"]]):
        ret = node.load(result["filename"])

    assert isinstance(ret, dict)
    assert "ui" in ret
    assert "result" in ret
    images = ret["ui"]["images"]
    assert len(images) == 1
    assert images[0]["type"] == "managed"
    assert images[0]["filename"] == Path(result["filename"]).name
    assert images[0]["subfolder"] == str(Path(result["filename"]).parent)
    # result tuple still has 3 elements
    assert len(ret["result"]) == 3


def test_managed_load_image_from_input_load_returns_preview_dict(workspace):
    """ManagedLoadImageFromInput.load() returns dict with ui.images and result tuple."""
    src = _make_input_file(workspace["input"])
    node = ManagedLoadImageFromInput()

    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch.object(_nodes_mod, "DB_PATH", workspace["db"]), \
         patch("folder_paths.get_input_directory", return_value=str(workspace["input"])):
        ret = node.load("portrait.jpg")

    assert isinstance(ret, dict)
    assert "ui" in ret
    assert "result" in ret
    images = ret["ui"]["images"]
    assert len(images) == 1
    assert images[0]["type"] == "managed"
    assert images[0]["filename"].endswith(".png")


def test_managed_save_image_ui_has_managed_type_and_split_path(workspace):
    """ManagedSaveImage.save() UI images include type=managed, subfolder, and basename filename."""
    img = torch.zeros(1, 8, 8, 3)
    node = ManagedSaveImage()

    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch.object(_nodes_mod, "DB_PATH", workspace["db"]):
        ret = node.save(img, "out")

    images = ret["ui"]["images"]
    assert len(images) == 1
    assert images[0]["type"] == "managed"
    # filename should be just the basename, not the full relative path
    assert "/" not in images[0]["filename"] and "\\" not in images[0]["filename"]
    # subfolder contains the directory part
    assert "subfolder" in images[0]


# --- patch tests ---

def test_patched_get_directory_by_type_returns_managed_root(tmp_path):
    """After the patch, get_directory_by_type('managed') returns MANAGED_ROOT."""
    import folder_paths as fp

    original = fp.get_directory_by_type
    managed_root = str(tmp_path)

    def patched(type_name):
        if type_name == "managed":
            return managed_root
        return original(type_name)

    fp.get_directory_by_type = patched
    try:
        assert fp.get_directory_by_type("managed") == managed_root
    finally:
        fp.get_directory_by_type = original


def test_patched_get_directory_by_type_passes_through_other_types(tmp_path):
    """Patching for 'managed' does not affect output/temp/input types."""
    import folder_paths as fp

    original = fp.get_directory_by_type
    managed_root = str(tmp_path)

    def patched(type_name):
        if type_name == "managed":
            return managed_root
        return original(type_name)

    fp.get_directory_by_type = patched
    try:
        assert fp.get_directory_by_type("output") == fp.get_output_directory()
        assert fp.get_directory_by_type("input") == fp.get_input_directory()
        assert fp.get_directory_by_type("temp") == fp.get_temp_directory()
        assert fp.get_directory_by_type("unknown") is None
    finally:
        fp.get_directory_by_type = original


# --- ManagedLoadImage return types ---

def test_managed_load_image_return_types_includes_string():
    assert ManagedLoadImage.RETURN_TYPES == ("IMAGE", "MASK", "STRING")


def test_managed_load_image_return_names():
    assert ManagedLoadImage.RETURN_NAMES == ("image", "mask", "managed_name")


def test_managed_load_image_load_returns_filename_as_third(workspace):
    img = torch.zeros(1, 8, 8, 3)
    from lineage import save_image as _save
    result = _save(img, "portraits", {}, workspace["root"], workspace["db"])

    node = ManagedLoadImage()
    with patch.object(_nodes_mod, "MANAGED_ROOT", workspace["root"]), \
         patch("folder_paths.get_filename_list", return_value=[result["filename"]]):
        ret = node.load(result["filename"])

    image_out, mask_out, managed_name = ret["result"]
    assert managed_name == result["filename"]
    assert image_out.shape[-1] == 3
