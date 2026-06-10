import json
import sqlite3
from pathlib import Path

import pytest
import torch

from lineage import init_db, save_image, unlink_parent
from api import make_app


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "image_manager.db"
    init_db(db_path)
    return {"root": tmp_path, "db": db_path}


def _img():
    return torch.zeros(1, 8, 8, 3)


def _save_root(workspace, prefix="root"):
    return save_image(_img(), prefix, {}, workspace["root"], workspace["db"])


def _save_child(workspace, parent_filename, prefix="child"):
    prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": parent_filename}}}
    return save_image(_img(), prefix, prompt, workspace["root"], workspace["db"])


def _sidecar(result):
    return json.loads(Path(result["abs_path"]).with_suffix(".json").read_text())


def _db_row(workspace, img_uuid):
    con = sqlite3.connect(workspace["db"])
    row = con.execute(
        "SELECT uuid, parent_uuid, root_uuid, root_name FROM images WHERE uuid = ?",
        (img_uuid,)
    ).fetchone()
    con.close()
    return row


# --- Cycle 1: unknown UUID ---

def test_unlink_parent_raises_for_unknown_uuid(workspace):
    with pytest.raises(ValueError, match="not found"):
        unlink_parent("no-such-uuid", workspace["db"], workspace["root"])


# --- Cycle 2: already a root ---

def test_unlink_parent_raises_for_root_image(workspace):
    root = _save_root(workspace)
    with pytest.raises(ValueError, match="no parent"):
        unlink_parent(root["uuid"], workspace["db"], workspace["root"])


# --- Cycle 3: parent_uuid cleared ---

def test_unlink_parent_clears_parent_uuid(workspace):
    root = _save_root(workspace)
    child = _save_child(workspace, root["filename"])

    unlink_parent(child["uuid"], workspace["db"], workspace["root"])

    db_row = _db_row(workspace, child["uuid"])
    assert db_row[1] is None  # parent_uuid

    sidecar = _sidecar(child)
    assert sidecar["parent_uuid"] is None


# --- Cycle 4: child becomes its own root ---

def test_unlink_parent_child_becomes_new_root(workspace):
    root = _save_root(workspace, "portraits")
    child = _save_child(workspace, root["filename"])

    result = unlink_parent(child["uuid"], workspace["db"], workspace["root"])

    # root_uuid = child's own uuid
    assert result["new_root_uuid"] == child["uuid"]
    db_row = _db_row(workspace, child["uuid"])
    assert db_row[2] == child["uuid"]  # root_uuid in DB

    # root_name = first path segment of child's filename
    expected_root_name = child["filename"].split("/")[0]
    assert result["new_root_name"] == expected_root_name
    assert db_row[3] == expected_root_name

    sidecar = _sidecar(child)
    assert sidecar["root_uuid"] == child["uuid"]
    assert sidecar["root_name"] == expected_root_name


# --- Cycle 5: descendants cascade ---

def test_unlink_parent_cascades_to_descendants(workspace):
    root = _save_root(workspace, "portraits")
    child = _save_child(workspace, root["filename"])
    grandchild = _save_child(workspace, child["filename"], "grandchild")

    unlink_parent(child["uuid"], workspace["db"], workspace["root"])

    new_root_uuid = child["uuid"]
    expected_root_name = child["filename"].split("/")[0]

    gc_row = _db_row(workspace, grandchild["uuid"])
    assert gc_row[2] == new_root_uuid   # root_uuid cascaded
    assert gc_row[3] == expected_root_name

    gc_sidecar = _sidecar(grandchild)
    assert gc_sidecar["root_uuid"] == new_root_uuid
    assert gc_sidecar["root_name"] == expected_root_name
    # grandchild's parent link is unchanged
    assert gc_sidecar["parent_uuid"] == child["uuid"]


# --- Cycle 6: API route 400 on ValueError ---

@pytest.mark.asyncio
async def test_unlink_parent_route_returns_400_for_root(workspace, aiohttp_client):
    root = _save_root(workspace)
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.post(
        "/image-manager/api/unlink-parent",
        json={"child_uuid": root["uuid"]}
    )
    assert resp.status == 400


# --- Cycle 7: route returns 200 with correct payload ---

@pytest.mark.asyncio
async def test_unlink_parent_route_returns_200_with_payload(workspace, aiohttp_client):
    root = _save_root(workspace, "portraits")
    child = _save_child(workspace, root["filename"])
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.post(
        "/image-manager/api/unlink-parent",
        json={"child_uuid": child["uuid"]}
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["child_uuid"] == child["uuid"]
    assert data["new_root_uuid"] == child["uuid"]
    assert data["new_root_name"] == child["filename"].split("/")[0]
