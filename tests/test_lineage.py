import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
import torch

from lineage import save_image, load_image, init_db


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
