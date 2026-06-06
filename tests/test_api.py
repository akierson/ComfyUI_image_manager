import io
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
import torch
from PIL import Image as PilImage

from lineage import save_image, init_db, scan_and_import
from api import make_app


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "image_manager.db"
    init_db(db_path)
    return {"root": tmp_path, "db": db_path}


@pytest.fixture
def client(workspace, aiohttp_client, event_loop):
    app = make_app(workspace["root"], workspace["db"])
    return event_loop.run_until_complete(aiohttp_client(app))


@pytest.mark.asyncio
async def test_roots_empty(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_roots_returns_root_images(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert len(data) == 1
    assert data[0]["root_name"] == "portrait"
    assert data[0]["parent_uuid"] is None


# --- descendant count ---

@pytest.mark.asyncio
async def test_roots_descendant_count(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "root", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    save_image(img, "child", child_prompt, workspace["root"], workspace["db"])
    save_image(img, "child2", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert len(data) == 1
    assert data[0]["descendant_count"] == 2


# --- latest_uuid / chain tip ---

@pytest.mark.asyncio
async def test_roots_latest_uuid_equals_uuid_when_no_descendants(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "solo", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert data[0]["latest_uuid"] == root["uuid"]
    assert data[0]["latest_created_at"] == data[0]["created_at"]


@pytest.mark.asyncio
async def test_roots_latest_uuid_points_to_child(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "refined", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert data[0]["latest_uuid"] == child["uuid"]


@pytest.mark.asyncio
async def test_roots_latest_uuid_points_to_grandchild(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "grandchild", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert data[0]["latest_uuid"] == grandchild["uuid"]


@pytest.mark.asyncio
async def test_roots_latest_uuid_isolated_per_root(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    root_b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}}}
    child_a = save_image(img, "a_child", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    by_name = {r["root_name"]: r for r in data}
    assert by_name["a"]["latest_uuid"] == child_a["uuid"]
    assert by_name["b"]["latest_uuid"] == root_b["uuid"]


# --- tree ---

@pytest.mark.asyncio
async def test_tree_root_with_no_children(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    assert resp.status == 200
    data = await resp.json()

    assert data["uuid"] == root["uuid"]
    assert data["children"] == []


@pytest.mark.asyncio
async def test_tree_includes_children(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])

    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "refined", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    data = await resp.json()

    assert len(data["children"]) == 1
    assert data["children"][0]["uuid"] == child["uuid"]


# --- workflow ---

@pytest.mark.asyncio
async def test_workflow_endpoint_injects_managed_load_node(workspace, aiohttp_client):
    import json
    from PIL import Image as PilImage
    from PIL.PngImagePlugin import PngInfo

    # Save a root PNG with embedded workflow metadata
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "wf_test", {}, workspace["root"], workspace["db"])

    # Embed a fake workflow into the PNG
    original_workflow = {
        "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
    }
    pil = PilImage.open(root["abs_path"])
    info = PngInfo()
    info.add_text("workflow", json.dumps(original_workflow))
    pil.save(root["abs_path"], pnginfo=info)

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/workflow/{root['uuid']}")
    assert resp.status == 200
    workflow = await resp.json()

    # At least one node must be ManagedLoadImage
    node_types = [n["class_type"] for n in workflow.values()]
    assert "ManagedLoadImage" in node_types


# --- import ---

@pytest.mark.asyncio
async def test_import_creates_root_record(workspace, aiohttp_client):
    buf = io.BytesIO()
    PilImage.new("RGB", (8, 8)).save(buf, format="PNG")
    buf.seek(0)

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    form = {"file": buf, "filename": "external.png"}
    resp = await client.post("/image-manager/api/import", data=form)
    assert resp.status == 200
    data = await resp.json()
    assert "uuid" in data

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (data["uuid"],)).fetchone()
    con.close()
    assert row is not None
    assert row[0] is None  # root: no parent


# --- rebuild ---

@pytest.mark.asyncio
async def test_rebuild_repopulates_from_sidecars(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    result = save_image(img, "before_wipe", {}, workspace["root"], workspace["db"])

    # Wipe the DB
    con = sqlite3.connect(workspace["db"])
    con.execute("DELETE FROM images")
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post("/image-manager/api/rebuild")
    assert resp.status == 200

    con = sqlite3.connect(workspace["db"])
    row = con.execute("SELECT uuid FROM images WHERE uuid = ?", (result["uuid"],)).fetchone()
    con.close()
    assert row is not None


# --- chains ---

@pytest.mark.asyncio
async def test_chains_empty(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_chains_single_root_no_children(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "solo", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    data = await resp.json()

    assert len(data) == 1
    chain = data[0]
    assert chain["root_uuid"] == root["uuid"]
    assert chain["root_name"] == "solo"
    assert len(chain["images"]) == 1
    assert chain["images"][0]["uuid"] == root["uuid"]
    assert "filename" in chain["images"][0]
    assert "created_at" in chain["images"][0]


@pytest.mark.asyncio
async def test_chains_images_ordered_newest_first(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "refined", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    data = await resp.json()

    assert len(data) == 1
    images = data[0]["images"]
    assert len(images) == 2
    assert images[0]["uuid"] == child["uuid"]   # newest first
    assert images[1]["uuid"] == root["uuid"]


@pytest.mark.asyncio
async def test_chains_grandchild_ordering(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "grandchild", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    data = await resp.json()

    uuids = [i["uuid"] for i in data[0]["images"]]
    assert uuids == [grandchild["uuid"], child["uuid"], root["uuid"]]


@pytest.mark.asyncio
async def test_chains_two_independent_roots(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    root_b = save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    data = await resp.json()

    assert len(data) == 2
    names = {c["root_name"] for c in data}
    assert names == {"alpha", "beta"}
    for chain in data:
        assert len(chain["images"]) == 1


# --- cluster ---

@pytest.mark.asyncio
async def test_cluster_returns_sse_content_type(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post("/image-manager/api/cluster")
    assert resp.status == 200
    assert "text/event-stream" in resp.content_type


@pytest.mark.asyncio
async def test_cluster_empty_db_streams_done_event(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post("/image-manager/api/cluster")
    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next((e for e in events if e.get("status") == "done"), None)
    assert done is not None
    assert done["clusters"] == {}


@pytest.mark.asyncio
async def test_cluster_with_images_streams_backend_and_done(workspace, aiohttp_client):
    import numpy as np
    from PIL import Image as PilImage

    # Save two real PNGs so the route can embed them
    img = torch.zeros(1, 8, 8, 3)
    r1 = save_image(img, "a", {}, workspace["root"], workspace["db"])
    r2 = save_image(img, "b", {}, workspace["root"], workspace["db"])

    # Stub clustering so the test doesn't depend on sklearn
    fake_embeddings = np.zeros((2, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 1]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    statuses = [e["status"] for e in events]

    assert "backend" in statuses
    done = next(e for e in events if e["status"] == "done")
    all_uuids = [u for uuids in done["clusters"].values() for u in uuids]
    assert set(all_uuids) == {r1["uuid"], r2["uuid"]}
