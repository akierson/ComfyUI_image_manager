import io
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
import torch
from PIL import Image as PilImage

from lineage import save_image, init_db, scan_and_import, fork_chain, promote_child, swap_adjacent
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


# --- folders ---

@pytest.mark.asyncio
async def test_folders_empty(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/folders")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_folders_one_root(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/folders")
    data = await resp.json()

    assert len(data) == 1
    assert data[0]["root_name"] == "portrait"
    assert data[0]["count"] == 1
    assert "latest_tip_at" in data[0]
    assert "date" not in data[0]
    assert "root_names" not in data[0]


@pytest.mark.asyncio
async def test_folders_multiple_root_names(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/folders")
    data = await resp.json()

    assert len(data) == 2
    names = {d["root_name"] for d in data}
    assert names == {"alpha", "beta"}
    for d in data:
        assert d["count"] == 1


@pytest.mark.asyncio
async def test_folders_count_includes_descendants(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/folders")
    data = await resp.json()

    assert len(data) == 1
    assert data[0]["count"] == 2


@pytest.mark.asyncio
async def test_folders_sorted_by_latest_tip_at(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    root_b = save_image(img, "beta", {}, workspace["root"], workspace["db"])

    # Give beta's root an older timestamp, then add a child to alpha that is newest
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root_b["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-02 00:00:00' WHERE uuid = ?", (root_a["uuid"],))
    con.commit()
    con.close()

    # Add a child to beta with the most recent timestamp
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_b["filename"]}}}
    child = save_image(img, "beta", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-03 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/folders")
    data = await resp.json()

    # beta has a child dated 2020-01-03, alpha's tip is 2020-01-02 — beta should appear first
    assert data[0]["root_name"] == "beta"
    assert data[1]["root_name"] == "alpha"


@pytest.mark.asyncio
async def test_roots_filter_by_date(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    folders = await (await client.get("/image-manager/api/folders")).json()
    today = folders[0]["latest_tip_at"][:10]

    resp = await client.get(f"/image-manager/api/roots?date={today}")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "portrait"

    resp_miss = await client.get("/image-manager/api/roots?date=1999-01-01")
    data_miss = await resp_miss.json()
    assert data_miss == []


@pytest.mark.asyncio
async def test_roots_filter_by_root_name(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/roots?root_name=alpha")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "alpha"

    resp_miss = await client.get("/image-manager/api/roots?root_name=nope")
    assert await resp_miss.json() == []


@pytest.mark.asyncio
async def test_roots_filter_by_date_and_root_name(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    folders = await (await client.get("/image-manager/api/folders")).json()
    today = folders[0]["latest_tip_at"][:10]

    resp = await client.get(f"/image-manager/api/roots?date={today}&root_name=beta")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "beta"


@pytest.mark.asyncio
async def test_roots_no_filter_returns_all(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_roots_sorted_by_tip_date(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    root_b = save_image(img, "beta", {}, workspace["root"], workspace["db"])

    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root_a["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-02 00:00:00' WHERE uuid = ?", (root_b["uuid"],))
    con.commit()
    con.close()

    # alpha gets a child with the newest timestamp
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}}}
    child = save_image(img, "alpha", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-03 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    # alpha tip is 2020-01-03, beta tip is 2020-01-02 — alpha first
    assert data[0]["root_name"] == "alpha"
    assert data[1]["root_name"] == "beta"


@pytest.mark.asyncio
async def test_roots_date_filter_uses_tip_date(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.commit()
    con.close()

    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    # tip date is 2020-01-10 — matches
    resp = await client.get("/image-manager/api/roots?date=2020-01-10")
    assert len(await resp.json()) == 1

    # root date 2020-01-01 is no longer the tip — should not match
    resp_miss = await client.get("/image-manager/api/roots?date=2020-01-01")
    assert await resp_miss.json() == []


# --- leaf-strips ---

@pytest.mark.asyncio
async def test_leaf_strips_empty(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/leaf-strips")
    assert resp.status == 200
    assert await resp.json() == []


@pytest.mark.asyncio
async def test_leaf_strips_orphan_is_gen0_single_image_row(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    orphan = save_image(img, "solo", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/leaf-strips")
    data = await resp.json()

    assert len(data) == 1
    strip = data[0]
    assert strip["leaf_uuid"] == orphan["uuid"]
    assert strip["root_name"] == "solo"
    assert strip["generation"] == 0
    assert len(strip["images"]) == 1
    assert strip["images"][0]["uuid"] == orphan["uuid"]


@pytest.mark.asyncio
async def test_leaf_strips_cross_chain_child_does_not_disqualify_parent_as_leaf(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "chain_a", {}, workspace["root"], workspace["db"])
    # Save child in chain_a, then fork it to chain_b — cross-chain parent_uuid preserved
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}}}
    child = save_image(img, "chain_a", child_prompt, workspace["root"], workspace["db"])
    from lineage import fork_chain
    fork_chain(child["uuid"], "chain_b", workspace["db"], workspace["root"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/leaf-strips")
    data = await resp.json()

    leaf_uuids = {s["leaf_uuid"] for s in data}
    # root_a has only a cross-chain child now → it IS a same-chain leaf
    assert root_a["uuid"] in leaf_uuids
    assert child["uuid"] in leaf_uuids


@pytest.mark.asyncio
async def test_chains_endpoint_is_removed(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/chains")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_leaf_strips_root_name_filter_scopes_to_folder(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/leaf-strips?root_name=alpha")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "alpha"

    resp_all = await client.get("/image-manager/api/leaf-strips")
    assert len(await resp_all.json()) == 2


@pytest.mark.asyncio
async def test_leaf_strips_date_filter_uses_leaf_date_not_root_date(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.commit()
    con.close()
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-06-15 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    # leaf date is 2020-06-15 → matches
    resp = await client.get("/image-manager/api/leaf-strips?date=2020-06-15")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["leaf_uuid"] == child["uuid"]

    # root date (2020-01-01) should NOT match since child is the leaf
    resp_miss = await client.get("/image-manager/api/leaf-strips?date=2020-01-01")
    assert await resp_miss.json() == []


@pytest.mark.asyncio
async def test_leaf_strips_branched_chain_produces_two_rows_with_shared_ancestor(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_a_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child_a = save_image(img, "base", child_a_prompt, workspace["root"], workspace["db"])
    child_b = save_image(img, "base", child_a_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/leaf-strips")
    data = await resp.json()

    assert len(data) == 2
    leaf_uuids = {s["leaf_uuid"] for s in data}
    assert leaf_uuids == {child_a["uuid"], child_b["uuid"]}
    for strip in data:
        assert len(strip["images"]) == 2
        assert strip["images"][-1]["uuid"] == root["uuid"]
        assert strip["generation"] == 1


@pytest.mark.asyncio
async def test_leaf_strips_linear_chain_all_images_newest_first(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "base", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/leaf-strips")
    data = await resp.json()

    assert len(data) == 1
    strip = data[0]
    assert strip["leaf_uuid"] == grandchild["uuid"]
    assert strip["generation"] == 2
    assert len(strip["images"]) == 3
    assert [i["uuid"] for i in strip["images"]] == [grandchild["uuid"], child["uuid"], root["uuid"]]


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
    all_uuids = {item["uuid"] for cluster in done["clusters"].values() for item in cluster}
    assert all_uuids == {r1["uuid"], r2["uuid"]}


# --- cluster filter ---

@pytest.mark.asyncio
async def test_cluster_filter_by_root_name_excludes_others(workspace, aiohttp_client):
    import numpy as np

    img = torch.zeros(1, 8, 8, 3)
    r_alpha = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    r_beta = save_image(img, "beta", {}, workspace["root"], workspace["db"])

    fake_embeddings = np.zeros((1, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster?root_name=alpha")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    all_uuids = [item["uuid"] for cluster in done["clusters"].values() for item in cluster]
    assert all_uuids == [r_alpha["uuid"]]
    assert r_beta["uuid"] not in all_uuids


@pytest.mark.asyncio
async def test_cluster_filter_by_date_excludes_others(workspace, aiohttp_client):
    import numpy as np

    img = torch.zeros(1, 8, 8, 3)
    r1 = save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    folders = await (await client.get("/image-manager/api/folders")).json()
    today = folders[0]["latest_tip_at"][:10]

    fake_embeddings = np.zeros((1, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0]):
        resp = await client.post(f"/image-manager/api/cluster?date={today}")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    all_uuids = [item["uuid"] for cluster in done["clusters"].values() for item in cluster]
    assert all_uuids == [r1["uuid"]]

    # Miss: different date returns empty clusters
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0]):
        resp_miss = await client.post("/image-manager/api/cluster?date=1999-01-01")
    body_miss = await resp_miss.text()
    events_miss = [json.loads(line[6:]) for line in body_miss.splitlines() if line.startswith("data:")]
    done_miss = next(e for e in events_miss if e["status"] == "done")
    assert done_miss["clusters"] == {}


@pytest.mark.asyncio
async def test_cluster_date_filter_uses_tip_date(workspace, aiohttp_client):
    import numpy as np

    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.commit()
    con.close()

    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    # Date filter returns all images in matching chains (root + child), so 2 embeddings
    fake_embeddings = np.zeros((2, 192))
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 0]):
        resp = await client.post("/image-manager/api/cluster?date=2020-01-10")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    all_uuids = {item["uuid"] for cluster in done["clusters"].values() for item in cluster}
    assert root["uuid"] in all_uuids

    # Filtering by root's creation date should no longer match (tip is 2020-01-10)
    fake_embeddings_1 = np.zeros((1, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings_1), \
         patch("clustering.auto_cluster", return_value=[0]):
        resp_miss = await client.post("/image-manager/api/cluster?date=2020-01-01")
    body_miss = await resp_miss.text()
    events_miss = [json.loads(line[6:]) for line in body_miss.splitlines() if line.startswith("data:")]
    done_miss = next(e for e in events_miss if e["status"] == "done")
    assert done_miss["clusters"] == {}


# --- cluster enriched response ---

@pytest.mark.asyncio
async def test_cluster_items_are_enriched_dicts(workspace, aiohttp_client):
    """Each cluster item is a dict with uuid, root_uuid, generation, parent_uuid, filename, orphan."""
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "alpha", {}, workspace["root"], workspace["db"])

    fake_embeddings = np.zeros((1, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    items = [item for cluster in done["clusters"].values() for item in cluster]
    assert len(items) == 1
    item = items[0]
    assert item["uuid"] == root["uuid"]
    assert item["root_uuid"] == root["root_uuid"]
    assert item["generation"] == 0
    assert item["parent_uuid"] is None
    assert "filename" in item
    assert item["orphan"] is True


@pytest.mark.asyncio
async def test_cluster_root_name_filter_includes_children(workspace, aiohttp_client):
    """root_name filter must return roots AND children, not just root images."""
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "alpha", child_prompt, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    fake_embeddings = np.zeros((2, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 0]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster?root_name=alpha")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    all_uuids = {item["uuid"] for cluster in done["clusters"].values() for item in cluster}
    assert root["uuid"] in all_uuids
    assert child["uuid"] in all_uuids


@pytest.mark.asyncio
async def test_cluster_generation_depth(workspace, aiohttp_client):
    """generation=0 for root, 1 for child, 2 for grandchild."""
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    grand_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "chain", grand_prompt, workspace["root"], workspace["db"])

    fake_embeddings = np.zeros((3, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 0, 0]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    items = {item["uuid"]: item for cluster in done["clusters"].values() for item in cluster}
    assert items[root["uuid"]]["generation"] == 0
    assert items[child["uuid"]]["generation"] == 1
    assert items[grandchild["uuid"]]["generation"] == 2


@pytest.mark.asyncio
async def test_cluster_orphan_classification(workspace, aiohttp_client):
    """orphan=True when no other image shares root_uuid; orphan=False for chain members."""
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    orphan = save_image(img, "solo", {}, workspace["root"], workspace["db"])

    fake_embeddings = np.zeros((3, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 0, 1]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    items = {item["uuid"]: item for cluster in done["clusters"].values() for item in cluster}
    assert items[root["uuid"]]["orphan"] is False
    assert items[child["uuid"]]["orphan"] is False
    assert items[orphan["uuid"]]["orphan"] is True


@pytest.mark.asyncio
async def test_cluster_items_sorted_by_creation_date(workspace, aiohttp_client):
    """Images within a cluster are ordered by created_at ascending (oldest first)."""
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    first = save_image(img, "a", {}, workspace["root"], workspace["db"])
    second = save_image(img, "b", {}, workspace["root"], workspace["db"])
    third = save_image(img, "c", {}, workspace["root"], workspace["db"])

    # Assign timestamps out of insertion order so we can verify sort
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2024-01-03 00:00:00' WHERE uuid = ?", (first["uuid"],))
    con.execute("UPDATE images SET created_at = '2024-01-01 00:00:00' WHERE uuid = ?", (second["uuid"],))
    con.execute("UPDATE images SET created_at = '2024-01-02 00:00:00' WHERE uuid = ?", (third["uuid"],))
    con.commit()
    con.close()

    fake_embeddings = np.zeros((3, 192))
    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", return_value=[0, 0, 0]):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")

    body = await resp.text()
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data:")]
    done = next(e for e in events if e["status"] == "done")
    uuids = [item["uuid"] for cluster in done["clusters"].values() for item in cluster]
    assert uuids == [second["uuid"], third["uuid"], first["uuid"]]


# --- all-images filter ---

@pytest.mark.asyncio
async def test_all_images_no_filter_returns_all(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_all_images_filter_by_date_returns_matching(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    folders = await (await client.get("/image-manager/api/folders")).json()
    today = folders[0]["latest_tip_at"][:10]

    resp = await client.get(f"/image-manager/api/all-images?date={today}")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "portrait"

    resp_miss = await client.get("/image-manager/api/all-images?date=1999-01-01")
    assert await resp_miss.json() == []


@pytest.mark.asyncio
async def test_all_images_date_filter_uses_tip_date(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.commit()
    con.close()

    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images?date=2020-01-10")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "portrait"

    resp_miss = await client.get("/image-manager/api/all-images?date=2020-01-01")
    assert await resp_miss.json() == []


@pytest.mark.asyncio
async def test_all_images_filter_by_root_name(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images?root_name=alpha")
    data = await resp.json()
    assert len(data) == 1
    assert data[0]["root_name"] == "alpha"


@pytest.mark.asyncio
async def test_all_images_root_name_filter_excludes_other_folders(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "portraits", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}}}
    child_a = save_image(img, "portraits", child_prompt, workspace["root"], workspace["db"])
    root_b = save_image(img, "landscapes", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images?root_name=portraits")
    data = await resp.json()
    uuids = {d["uuid"] for d in data}

    assert root_a["uuid"] in uuids
    assert child_a["uuid"] in uuids
    assert root_b["uuid"] not in uuids
    assert len(data) == 2


@pytest.mark.asyncio
async def test_all_images_root_name_filter_ordered_newest_first(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])

    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-06-01 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images?root_name=portrait")
    data = await resp.json()

    assert data[0]["uuid"] == child["uuid"]
    assert data[1]["uuid"] == root["uuid"]


@pytest.mark.asyncio
async def test_all_images_root_name_filter_includes_children(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    save_image(img, "other", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images?root_name=portrait")
    data = await resp.json()
    uuids = {d["uuid"] for d in data}

    assert root["uuid"] in uuids
    assert child["uuid"] in uuids
    assert len(data) == 2


@pytest.mark.asyncio
async def test_all_images_child_image_includes_root_uuid_of_its_root(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()
    by_uuid = {d["uuid"]: d for d in data}

    assert by_uuid[child["uuid"]]["root_uuid"] == root["uuid"]
    assert by_uuid[child["uuid"]]["root_uuid"] != child["uuid"]


@pytest.mark.asyncio
async def test_all_images_root_image_includes_root_uuid_equal_to_uuid(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    assert len(data) == 1
    assert "root_uuid" in data[0]
    assert data[0]["root_uuid"] == saved["uuid"]


# --- metadata ---

@pytest.mark.asyncio
async def test_metadata_unknown_uuid_returns_404(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/metadata/does-not-exist")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_metadata_root_image_returns_lineage_fields(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/metadata/{saved['uuid']}")
    assert resp.status == 200
    data = await resp.json()

    assert data["uuid"] == saved["uuid"]
    assert data["root_name"] == "portrait"
    assert data["root_uuid"] == saved["uuid"]
    assert data["parent_uuid"] is None
    assert "filename" in data
    assert "created_at" in data
    assert data["generation"] == 0
    assert data["file_size"] > 0


@pytest.mark.asyncio
async def test_metadata_generation_depth(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "base", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp_root = await client.get(f"/image-manager/api/metadata/{root['uuid']}")
    resp_child = await client.get(f"/image-manager/api/metadata/{child['uuid']}")
    resp_grand = await client.get(f"/image-manager/api/metadata/{grandchild['uuid']}")

    assert (await resp_root.json())["generation"] == 0
    assert (await resp_child.json())["generation"] == 1
    assert (await resp_grand.json())["generation"] == 2


@pytest.mark.asyncio
async def test_metadata_no_workflow_returns_has_workflow_false(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/metadata/{saved['uuid']}")
    data = await resp.json()

    assert data["has_workflow"] is False
    assert "positive_prompt" not in data
    assert "checkpoint" not in data


STANDARD_KSAMPLER_PROMPT = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a beautiful portrait", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality", "clip": ["4", 1]}},
    "3": {"class_type": "KSampler", "inputs": {
        "seed": 12345, "steps": 20, "cfg": 7.5,
        "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0,
        "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
        "latent_image": ["5", 0],
    }},
}


@pytest.mark.asyncio
async def test_metadata_standard_workflow_extracts_all_fields(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", STANDARD_KSAMPLER_PROMPT, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/metadata/{saved['uuid']}")
    data = await resp.json()

    assert data["has_workflow"] is True
    assert data["checkpoint"] == "v1-5.safetensors"
    assert data["positive_prompt"] == "a beautiful portrait"
    assert data["negative_prompt"] == "blurry, low quality"
    assert data["steps"] == 20
    assert data["cfg"] == 7.5
    assert data["sampler"] == "euler"
    assert data["scheduler"] == "karras"
    assert data["seed"] == 12345
    assert data["denoise"] == 1.0
    assert data["width"] == 8
    assert data["height"] == 8
    assert "loras" not in data


PROMPT_WITH_LORA = {
    **STANDARD_KSAMPLER_PROMPT,
    "10": {"class_type": "LoraLoader", "inputs": {
        "lora_name": "detail.safetensors", "strength_model": 0.75, "strength_clip": 0.75,
        "model": ["4", 0], "clip": ["4", 1],
    }},
}


@pytest.mark.asyncio
async def test_metadata_workflow_with_lora_includes_loras(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", PROMPT_WITH_LORA, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/metadata/{saved['uuid']}")
    data = await resp.json()

    assert data["has_workflow"] is True
    assert len(data["loras"]) == 1
    assert data["loras"][0]["name"] == "detail.safetensors"
    assert data["loras"][0]["strength"] == 0.75


# --- tree generation numbers ---

@pytest.mark.asyncio
async def test_tree_root_node_has_generation_zero(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    data = await resp.json()

    assert data["generation"] == 0


@pytest.mark.asyncio
async def test_tree_child_and_grandchild_generation_numbers(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    save_image(img, "base", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    tree = await resp.json()

    assert tree["generation"] == 0
    assert tree["children"][0]["generation"] == 1
    assert tree["children"][0]["children"][0]["generation"] == 2


@pytest.mark.asyncio
async def test_tree_branching_independent_generation_counts(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child_a = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    child_b = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    # Grandchild off child_a only
    gc_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child_a["filename"]}}}
    save_image(img, "base", gc_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    tree = await resp.json()

    by_uuid = {tree["uuid"]: tree}
    for c in tree["children"]:
        by_uuid[c["uuid"]] = c
        for gc in c.get("children", []):
            by_uuid[gc["uuid"]] = gc

    assert by_uuid[child_a["uuid"]]["generation"] == 1
    assert by_uuid[child_b["uuid"]]["generation"] == 1
    # Grandchild off child_a is generation 2
    grandchild = by_uuid[child_a["uuid"]]["children"][0]
    assert grandchild["generation"] == 2


# --- roots latest_filename ---

@pytest.mark.asyncio
async def test_roots_latest_filename_is_stem_of_tip_image(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    assert len(data) == 1
    # latest_uuid should be the child (newer)
    assert data[0]["latest_uuid"] == child["uuid"]
    # latest_filename should be the stem (no extension, no directory) of the tip's filename
    tip_stem = child["filename"].split("/")[-1].rsplit(".", 1)[0]
    assert data[0]["latest_filename"] == tip_stem


@pytest.mark.asyncio
async def test_roots_latest_filename_equals_root_stem_when_no_descendants(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/roots")
    data = await resp.json()

    root_stem = root["filename"].split("/")[-1].rsplit(".", 1)[0]
    assert data[0]["latest_filename"] == root_stem


# ---------------------------------------------------------------------------
# fork_chain
# ---------------------------------------------------------------------------

def test_fork_root_image_updates_root_name(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "original", {}, workspace["root"], workspace["db"])

    result = fork_chain(root["uuid"], "new_folder", workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    row = con.execute(
        "SELECT root_name, root_uuid FROM images WHERE uuid = ?", (root["uuid"],)
    ).fetchone()
    con.close()

    assert row[0] == "new_folder"
    assert row[1] == root["uuid"]
    assert result["forked_count"] == 1
    assert result["new_root_uuid"] == root["uuid"]
    assert result["root_name"] == "new_folder"


def test_fork_propagates_to_descendants(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    grand_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "chain", grand_prompt, workspace["root"], workspace["db"])

    result = fork_chain(root["uuid"], "branched", workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    rows = con.execute(
        "SELECT root_name, root_uuid FROM images WHERE uuid IN (?, ?, ?)",
        (root["uuid"], child["uuid"], grandchild["uuid"])
    ).fetchall()
    con.close()

    assert all(r[0] == "branched" for r in rows)
    assert all(r[1] == root["uuid"] for r in rows)
    assert result["forked_count"] == 3


def test_fork_preserves_parent_uuid_and_updates_sidecar(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])

    fork_chain(child["uuid"], "fork_folder", workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    row = con.execute(
        "SELECT parent_uuid, root_name, root_uuid FROM images WHERE uuid = ?", (child["uuid"],)
    ).fetchone()
    con.close()

    # parent_uuid unchanged
    assert row[0] == root["uuid"]
    assert row[1] == "fork_folder"
    assert row[2] == child["uuid"]

    # sidecar on disk also updated
    sidecar_path = Path(child["abs_path"]).with_suffix(".json")
    meta = json.loads(sidecar_path.read_text())
    assert meta["root_name"] == "fork_folder"
    assert meta["root_uuid"] == child["uuid"]
    assert meta["parent_uuid"] == root["uuid"]


def test_fork_rejects_same_chain(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "mychain", {}, workspace["root"], workspace["db"])

    with pytest.raises(ValueError, match="already in chain"):
        fork_chain(root["uuid"], "mychain", workspace["db"], workspace["root"])


def test_fork_rejects_cycle(workspace):
    """Forking root into a chain that already has a descendant as its root is a cycle."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "parent_chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "parent_chain", child_prompt, workspace["root"], workspace["db"])

    # Fork the child into "child_chain" first (child becomes its own root)
    fork_chain(child["uuid"], "child_chain", workspace["db"], workspace["root"])

    # Now try to fork root into "child_chain" — child is a descendant of root,
    # so root would cross-link into a chain rooted at its own descendant.
    with pytest.raises(ValueError, match="cycle detected"):
        fork_chain(root["uuid"], "child_chain", workspace["db"], workspace["root"])


def test_fork_unknown_uuid_raises(workspace):
    with pytest.raises(ValueError, match="not found"):
        fork_chain("no-such-uuid", "anywhere", workspace["db"], workspace["root"])


@pytest.mark.asyncio
async def test_fork_api_returns_forked_count(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "original", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        f"/image-manager/api/fork/{root['uuid']}",
        json={"root_name": "forked_folder"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["forked_count"] == 1
    assert data["root_name"] == "forked_folder"


@pytest.mark.asyncio
async def test_fork_api_unknown_uuid_returns_404(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/fork/no-such-uuid",
        json={"root_name": "somewhere"},
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_tree_includes_cross_chain_children(workspace, aiohttp_client):
    """After a fork, the source chain's tree shows the fork point as a cross-chain child."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "source_chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "source_chain", child_prompt, workspace["root"], workspace["db"])

    # Fork the child into a new chain
    fork_chain(child["uuid"], "fork_chain_name", workspace["db"], workspace["root"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get(f"/image-manager/api/tree/{root['uuid']}")
    assert resp.status == 200
    tree = await resp.json()

    # Root's children should include the forked child as a cross-chain node
    assert len(tree["children"]) == 1
    cross = tree["children"][0]
    assert cross["uuid"] == child["uuid"]
    assert cross["cross_chain"] is True
    assert cross["root_name"] == "fork_chain_name"


# --- k_scale API ---

@pytest.mark.asyncio
async def test_cluster_missing_k_scale_defaults_to_1(workspace, aiohttp_client):
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "root", {}, workspace["root"], workspace["db"])

    captured = {}
    fake_embeddings = np.zeros((1, 192))

    def fake_auto_cluster(embeddings, k_scale=1.0):
        captured["k_scale"] = k_scale
        return [0]

    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", side_effect=fake_auto_cluster):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster")
        await resp.text()

    assert captured["k_scale"] == 1.0


@pytest.mark.asyncio
async def test_cluster_k_scale_clamped_at_max(workspace, aiohttp_client):
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "root", {}, workspace["root"], workspace["db"])

    captured = {}
    fake_embeddings = np.zeros((1, 192))

    def fake_auto_cluster(embeddings, k_scale=1.0):
        captured["k_scale"] = k_scale
        return [0]

    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", side_effect=fake_auto_cluster):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster?k_scale=999")
        await resp.text()

    assert captured["k_scale"] == 10.0


@pytest.mark.asyncio
async def test_cluster_k_scale_clamped_at_min(workspace, aiohttp_client):
    import numpy as np
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "root", {}, workspace["root"], workspace["db"])

    captured = {}
    fake_embeddings = np.zeros((1, 192))

    def fake_auto_cluster(embeddings, k_scale=1.0):
        captured["k_scale"] = k_scale
        return [0]

    with patch("clustering.embed_images", return_value=fake_embeddings), \
         patch("clustering.auto_cluster", side_effect=fake_auto_cluster):
        app = make_app(workspace["root"], workspace["db"])
        client = await aiohttp_client(app)
        resp = await client.post("/image-manager/api/cluster?k_scale=0.001")
        await resp.text()

    assert captured["k_scale"] == 0.1


# --- favicon ---

@pytest.mark.asyncio
async def test_favicon_returns_svg(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/favicon.svg")
    assert resp.status == 200
    assert "svg" in resp.content_type
    body = await resp.text()
    assert "<svg" in body


# --- move-chains ---

@pytest.mark.asyncio
async def test_move_chains_updates_root_name_in_db(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "old_folder", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "old_folder", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/move-chains",
        json={"root_uuids": [root["uuid"]], "target_folder": "new_folder"},
    )
    assert resp.status == 200

    con = sqlite3.connect(workspace["db"])
    rows = con.execute(
        "SELECT root_name FROM images WHERE root_uuid = ?", (root["uuid"],)
    ).fetchall()
    assert all(r[0] == "new_folder" for r in rows)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_move_chains_rewrites_sidecar_json(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "original", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    await client.post(
        "/image-manager/api/move-chains",
        json={"root_uuids": [root["uuid"]], "target_folder": "renamed"},
    )

    sidecar_path = Path(root["abs_path"]).with_suffix(".json")
    sidecar = json.loads(sidecar_path.read_text())
    assert sidecar["root_name"] == "renamed"


@pytest.mark.asyncio
async def test_move_chains_multiple_chains(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    chain_a = save_image(img, "alpha", {}, workspace["root"], workspace["db"])
    chain_b = save_image(img, "beta", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/move-chains",
        json={"root_uuids": [chain_a["uuid"], chain_b["uuid"]], "target_folder": "merged"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["moved"] == 2

    con = sqlite3.connect(workspace["db"])
    names = {r[0] for r in con.execute("SELECT root_name FROM images").fetchall()}
    assert names == {"merged"}


@pytest.mark.asyncio
async def test_move_chains_missing_fields_returns_400(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.post("/image-manager/api/move-chains", json={"root_uuids": []})
    assert resp.status == 400

    resp = await client.post("/image-manager/api/move-chains", json={"target_folder": "x"})
    assert resp.status == 400


# --- order-violations ---

# --- promote_child ---

def test_promote_child_unknown_parent_raises(workspace):
    img = torch.zeros(1, 8, 8, 3)
    child = save_image(img, "base", {}, workspace["root"], workspace["db"])
    with pytest.raises(ValueError, match="not found"):
        promote_child("no-such-uuid", child["uuid"], workspace["db"], workspace["root"])


def test_promote_child_unknown_promoted_raises(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    with pytest.raises(ValueError, match="not found"):
        promote_child(root["uuid"], "no-such-uuid", workspace["db"], workspace["root"])


def test_promote_child_not_direct_child_raises(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    other = save_image(img, "other", {}, workspace["root"], workspace["db"])
    with pytest.raises(ValueError, match="not a direct child"):
        promote_child(root["uuid"], other["uuid"], workspace["db"], workspace["root"])


def test_promote_child_root_becomes_new_root(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    grand_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "chain", grand_prompt, workspace["root"], workspace["db"])

    result = promote_child(root["uuid"], child["uuid"], workspace["db"], workspace["root"])

    assert result["chain_root_changed"] is True
    assert result["new_root_uuid"] == child["uuid"]

    con = sqlite3.connect(workspace["db"])
    rows = con.execute(
        "SELECT uuid, root_uuid, parent_uuid FROM images WHERE uuid IN (?, ?, ?)",
        (root["uuid"], child["uuid"], grandchild["uuid"])
    ).fetchall()
    con.close()
    by_uuid = {r[0]: r for r in rows}

    # child is now root: parent=None, root_uuid=child.uuid
    assert by_uuid[child["uuid"]][1] == child["uuid"]
    assert by_uuid[child["uuid"]][2] is None
    # root (P) is now child of promoted: parent=child.uuid
    assert by_uuid[root["uuid"]][2] == child["uuid"]
    assert by_uuid[root["uuid"]][1] == child["uuid"]
    # grandchild root_uuid cascaded
    assert by_uuid[grandchild["uuid"]][1] == child["uuid"]


def test_promote_child_reparents_siblings_under_promoted(workspace):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    promoted = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    sibling_a = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    sibling_b = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])

    promote_child(root["uuid"], promoted["uuid"], workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    rows = con.execute(
        "SELECT uuid, parent_uuid FROM images WHERE uuid IN (?, ?, ?)",
        (promoted["uuid"], sibling_a["uuid"], sibling_b["uuid"])
    ).fetchall()
    con.close()
    by_uuid = {r[0]: r for r in rows}

    # promoted: parent = None (root was root, no grandparent)
    assert by_uuid[promoted["uuid"]][1] is None
    # siblings re-parented to promoted
    assert by_uuid[sibling_a["uuid"]][1] == promoted["uuid"]
    assert by_uuid[sibling_b["uuid"]][1] == promoted["uuid"]


@pytest.mark.asyncio
async def test_order_violations_empty_when_no_pairs(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/order-violations")
    assert resp.status == 200
    data = await resp.json()
    assert data == []


@pytest.mark.asyncio
async def test_order_violations_returns_inverted_pair(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])

    # Violation: root (DB parent) has newer timestamp than child
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/order-violations")
    data = await resp.json()

    assert len(data) == 1
    v = data[0]
    assert v["parent_uuid"] == root["uuid"]
    assert v["child_uuid"] == child["uuid"]
    assert "parent_filename" in v
    assert "child_filename" in v
    assert "parent_created_at" in v
    assert "child_created_at" in v


@pytest.mark.asyncio
async def test_order_violations_suggested_uuid_is_earliest_sibling_or_child(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    # Two children of root; sibling is older than child
    sibling = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])

    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-20 00:00:00' WHERE uuid = ?", (root["uuid"],))   # parent (newest — violation)
    con.execute("UPDATE images SET created_at = '2020-01-05 00:00:00' WHERE uuid = ?", (sibling["uuid"],))  # older sibling
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))    # child
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/order-violations")
    data = await resp.json()

    # Both sibling and child are violations (root is newer than both)
    # For child's violation: suggested is sibling (earliest among siblings + child)
    child_violation = next(v for v in data if v["child_uuid"] == child["uuid"])
    assert child_violation["suggested_uuid"] == sibling["uuid"]


@pytest.mark.asyncio
async def test_order_violations_returns_multiple_violations(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root_a = save_image(img, "chain_a", {}, workspace["root"], workspace["db"])
    prompt_a = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_a["filename"]}}}
    child_a = save_image(img, "chain_a", prompt_a, workspace["root"], workspace["db"])

    root_b = save_image(img, "chain_b", {}, workspace["root"], workspace["db"])
    prompt_b = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root_b["filename"]}}}
    child_b = save_image(img, "chain_b", prompt_b, workspace["root"], workspace["db"])

    con = sqlite3.connect(workspace["db"])
    # Both chains have inverted order
    con.execute("UPDATE images SET created_at = '2020-01-10' WHERE uuid = ?", (root_a["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-01' WHERE uuid = ?", (child_a["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-20' WHERE uuid = ?", (root_b["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-05' WHERE uuid = ?", (child_b["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/order-violations")
    data = await resp.json()

    assert len(data) == 2
    parent_uuids = {v["parent_uuid"] for v in data}
    assert root_a["uuid"] in parent_uuids
    assert root_b["uuid"] in parent_uuids


@pytest.mark.asyncio
async def test_order_violations_excludes_correct_pairs(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "base", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "base", child_prompt, workspace["root"], workspace["db"])

    # Correct order: root older than child
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (root["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/order-violations")
    data = await resp.json()

    assert data == []


# --- set-parent chronological enforcement ---

@pytest.mark.asyncio
async def test_set_parent_enforce_chronological_rejects_newer_parent(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child = save_image(img, "child", {}, workspace["root"], workspace["db"])

    # Make parent newer than child — a chronological violation
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (parent["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/set-parent",
        json={"child_uuid": child["uuid"], "parent_uuid": parent["uuid"], "enforce_chronological": True},
    )
    assert resp.status == 422
    text = await resp.text()
    assert "Parent must be older than child" in text


@pytest.mark.asyncio
async def test_set_parent_enforce_chronological_allows_older_parent(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child = save_image(img, "child", {}, workspace["root"], workspace["db"])

    # Parent older than child — correct chronological order
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (parent["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/set-parent",
        json={"child_uuid": child["uuid"], "parent_uuid": parent["uuid"], "enforce_chronological": True},
    )
    assert resp.status == 200


@pytest.mark.asyncio
async def test_set_parent_enforcement_off_allows_backwards_link(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child = save_image(img, "child", {}, workspace["root"], workspace["db"])

    # Parent newer than child — would be blocked if enforcement were on
    con = sqlite3.connect(workspace["db"])
    con.execute("UPDATE images SET created_at = '2020-01-10 00:00:00' WHERE uuid = ?", (parent["uuid"],))
    con.execute("UPDATE images SET created_at = '2020-01-01 00:00:00' WHERE uuid = ?", (child["uuid"],))
    con.commit()
    con.close()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    # No enforce_chronological field — defaults to off
    resp = await client.post(
        "/image-manager/api/set-parent",
        json={"child_uuid": child["uuid"], "parent_uuid": parent["uuid"]},
    )
    assert resp.status == 200


# --- swap-adjacent ---

@pytest.mark.asyncio
async def test_swap_adjacent_route(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    a = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    b_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": a["filename"]}}}
    b = save_image(img, "chain", b_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/swap-adjacent",
        json={"uuid_a": a["uuid"], "uuid_b": b["uuid"]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "swapped"


def test_swap_adjacent_mid_chain_b_takes_a_slot(workspace):
    img = torch.zeros(1, 8, 8, 3)
    p = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    a_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": p["filename"]}}}
    a = save_image(img, "chain", a_prompt, workspace["root"], workspace["db"])
    b_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": a["filename"]}}}
    b = save_image(img, "chain", b_prompt, workspace["root"], workspace["db"])

    swap_adjacent(a["uuid"], b["uuid"], workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    b_row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (b["uuid"],)).fetchone()
    a_row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (a["uuid"],)).fetchone()
    con.close()

    assert b_row[0] == p["uuid"], "B should now be P's child"
    assert a_row[0] == b["uuid"], "A should be B's child"


def test_swap_adjacent_root_a_b_becomes_root(workspace):
    img = torch.zeros(1, 8, 8, 3)
    a = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": a["filename"]}}}
    b = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])

    swap_adjacent(a["uuid"], b["uuid"], workspace["db"], workspace["root"])

    con = sqlite3.connect(workspace["db"])
    b_row = con.execute("SELECT parent_uuid, root_uuid FROM images WHERE uuid = ?", (b["uuid"],)).fetchone()
    a_row = con.execute("SELECT parent_uuid, root_uuid FROM images WHERE uuid = ?", (a["uuid"],)).fetchone()
    con.close()

    assert b_row[0] is None, "B should have no parent (new root)"
    assert a_row[0] == b["uuid"], "A should be B's child"
    assert a_row[1] == b["uuid"], "A's root_uuid should cascade to B's uuid"


@pytest.mark.asyncio
async def test_set_parent_grandchild_returns_generic_cycle_error(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    grandchild_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "chain", grandchild_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    # Attempt to set a grandchild as the parent of the root — non-adjacent cycle
    resp = await client.post(
        "/image-manager/api/set-parent",
        json={"child_uuid": root["uuid"], "parent_uuid": grandchild["uuid"]},
    )
    assert resp.status == 400
    text = await resp.text()
    assert "cycle" in text.lower()


@pytest.mark.asyncio
async def test_set_parent_direct_child_returns_409(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": parent["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    # Attempt to set the direct child as the parent of the parent
    resp = await client.post(
        "/image-manager/api/set-parent",
        json={"child_uuid": parent["uuid"], "parent_uuid": child["uuid"]},
    )
    assert resp.status == 409
    data = await resp.json()
    assert data["code"] == "direct_child_proposed"


# --- all-images generation field ---

@pytest.mark.asyncio
async def test_all_images_root_has_generation_zero(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "portrait", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    assert len(data) == 1
    assert data[0]["generation"] == 0


@pytest.mark.asyncio
async def test_all_images_generation_depth(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "chain", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "chain", child_prompt, workspace["root"], workspace["db"])
    grand_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": child["filename"]}}}
    grandchild = save_image(img, "chain", grand_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    by_uuid = {d["uuid"]: d for d in data}
    assert by_uuid[root["uuid"]]["generation"] == 0
    assert by_uuid[child["uuid"]]["generation"] == 1
    assert by_uuid[grandchild["uuid"]]["generation"] == 2


@pytest.mark.asyncio
async def test_all_images_generation_crosses_chain_boundary(workspace, aiohttp_client):
    """After fork, the forked root's generation counts the full parent chain."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "source", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "source", child_prompt, workspace["root"], workspace["db"])

    # Fork child into its own chain — child.parent_uuid still points to root
    from lineage import fork_chain
    fork_chain(child["uuid"], "forked", workspace["db"], workspace["root"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    by_uuid = {d["uuid"]: d for d in data}
    # child is now a forked root but still has parent_uuid=root.uuid → generation 1
    assert by_uuid[child["uuid"]]["generation"] == 1


# --- all-images orphan field ---

@pytest.mark.asyncio
async def test_all_images_lone_image_is_orphan(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "solo", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    assert data[0]["orphan"] is True


@pytest.mark.asyncio
async def test_all_images_chain_of_two_not_orphan(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.get("/image-manager/api/all-images")
    data = await resp.json()

    by_uuid = {d["uuid"]: d for d in data}
    assert by_uuid[root["uuid"]]["orphan"] is False
    assert by_uuid[child["uuid"]]["orphan"] is False


@pytest.mark.asyncio
async def test_all_images_orphan_uses_global_count_not_filtered(workspace, aiohttp_client):
    """A filtered response for a chain with siblings elsewhere still shows orphan: false."""
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    save_image(img, "portrait", child_prompt, workspace["root"], workspace["db"])
    # separate lone image in a different folder
    lone = save_image(img, "sketches", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    # filter to portrait folder — shows root only (tip filter), but chain has 2 globally
    resp = await client.get("/image-manager/api/all-images?root_name=portrait")
    data = await resp.json()
    assert all(d["orphan"] is False for d in data)

    # lone sketches image is orphan even when unfiltered
    resp2 = await client.get("/image-manager/api/all-images?root_name=sketches")
    data2 = await resp2.json()
    assert data2[0]["orphan"] is True


# --- set-parents-batch ---

@pytest.mark.asyncio
async def test_set_parents_batch_single_valid_pair(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child = save_image(img, "child", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/set-parents-batch",
        json={"pairs": [{"child_uuid": child["uuid"], "parent_uuid": parent["uuid"]}]},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["errors"] == []
    assert len(data["results"]) == 1
    assert data["results"][0]["child_uuid"] == child["uuid"]
    assert data["results"][0]["parent_uuid"] == parent["uuid"]


@pytest.mark.asyncio
async def test_set_parents_batch_multiple_pairs(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child_a = save_image(img, "child_a", {}, workspace["root"], workspace["db"])
    child_b = save_image(img, "child_b", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/set-parents-batch",
        json={"pairs": [
            {"child_uuid": child_a["uuid"], "parent_uuid": parent["uuid"]},
            {"child_uuid": child_b["uuid"], "parent_uuid": parent["uuid"]},
        ]},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["errors"] == []
    assert len(data["results"]) == 2
    result_children = {r["child_uuid"] for r in data["results"]}
    assert result_children == {child_a["uuid"], child_b["uuid"]}


@pytest.mark.asyncio
async def test_set_parents_batch_partial_errors(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child = save_image(img, "child", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/set-parents-batch",
        json={"pairs": [
            {"child_uuid": child["uuid"], "parent_uuid": parent["uuid"]},
            {"child_uuid": "nonexistent-uuid", "parent_uuid": parent["uuid"]},
        ]},
    )

    assert resp.status == 200
    data = await resp.json()
    assert len(data["results"]) == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["child_uuid"] == "nonexistent-uuid"


@pytest.mark.asyncio
async def test_set_parents_batch_cycle_goes_to_errors(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    root = save_image(img, "root", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": root["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    # Trying to make root a child of its own child → cycle
    resp = await client.post(
        "/image-manager/api/set-parents-batch",
        json={"pairs": [{"child_uuid": root["uuid"], "parent_uuid": child["uuid"]}]},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["results"] == []
    assert len(data["errors"]) == 1
    assert "cycle" in data["errors"][0]["error"].lower()


# --- delete-images ---

@pytest.mark.asyncio
async def test_delete_images_removes_file_and_db_record(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    saved = save_image(img, "portrait", {}, workspace["root"], workspace["db"])
    abs_path = Path(saved["abs_path"])
    sidecar = abs_path.with_suffix(".json")
    assert abs_path.exists()
    assert sidecar.exists()

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/delete-images",
        json={"uuids": [saved["uuid"]]},
    )

    assert resp.status == 200
    data = await resp.json()
    assert data["deleted"] == 1
    assert not abs_path.exists()
    assert not sidecar.exists()

    # Verify gone from DB via roots endpoint
    roots = await (await client.get("/image-manager/api/roots")).json()
    assert roots == []


@pytest.mark.asyncio
async def test_delete_images_orphans_children(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    parent = save_image(img, "parent", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": parent["filename"]}}}
    child = save_image(img, "child", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/delete-images",
        json={"uuids": [parent["uuid"]]},
    )
    assert resp.status == 200

    roots = await (await client.get("/image-manager/api/roots")).json()
    assert len(roots) == 1
    assert roots[0]["uuid"] == child["uuid"]
    assert roots[0]["parent_uuid"] is None


@pytest.mark.asyncio
async def test_delete_images_batch(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    a = save_image(img, "a", {}, workspace["root"], workspace["db"])
    b = save_image(img, "b", {}, workspace["root"], workspace["db"])
    c = save_image(img, "c", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)
    resp = await client.post(
        "/image-manager/api/delete-images",
        json={"uuids": [a["uuid"], b["uuid"]]},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["deleted"] == 2

    roots = await (await client.get("/image-manager/api/roots")).json()
    assert len(roots) == 1


# --- PATCH /image-manager/api/folders/{root_name} ---

@pytest.mark.asyncio
async def test_patch_folder_renames_folder(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "oldfolder", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.patch(
        "/image-manager/api/folders/oldfolder",
        json={"name": "newfolder"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["new_name"] == "newfolder"

    folders = await (await client.get("/image-manager/api/folders")).json()
    names = [f["root_name"] for f in folders]
    assert "newfolder" in names
    assert "oldfolder" not in names


@pytest.mark.asyncio
async def test_patch_folder_empty_name_returns_400(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "myfolder", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.patch(
        "/image-manager/api/folders/myfolder",
        json={"name": "  "},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_patch_folder_duplicate_name_returns_409(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    save_image(img, "folder_a", {}, workspace["root"], workspace["db"])
    save_image(img, "folder_b", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.patch(
        "/image-manager/api/folders/folder_a",
        json={"name": "folder_b"},
    )
    assert resp.status == 409


# --- DELETE /image-manager/api/folders/{root_name} ---

@pytest.mark.asyncio
async def test_delete_folder_dry_run_via_api(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    result = save_image(img, "deleteme", {}, workspace["root"], workspace["db"])
    child_prompt = {"1": {"class_type": "ManagedLoadImage", "inputs": {"image": result["filename"]}}}
    save_image(img, "c", child_prompt, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.delete("/image-manager/api/folders/deleteme?dry_run=true")
    assert resp.status == 200
    data = await resp.json()
    assert data["count"] == 2
    assert data["dry_run"] is True

    # Files must still exist
    from pathlib import Path as P
    assert P(result["abs_path"]).exists()


@pytest.mark.asyncio
async def test_delete_folder_via_api_removes_everything(workspace, aiohttp_client):
    img = torch.zeros(1, 8, 8, 3)
    result = save_image(img, "gonefolder", {}, workspace["root"], workspace["db"])

    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.delete("/image-manager/api/folders/gonefolder")
    assert resp.status == 200
    data = await resp.json()
    assert data["deleted"] == 1

    from pathlib import Path as P
    assert not P(result["abs_path"]).exists()

    folders = await (await client.get("/image-manager/api/folders")).json()
    assert all(f["root_name"] != "gonefolder" for f in folders)


@pytest.mark.asyncio
async def test_delete_folder_nonexistent_returns_404(workspace, aiohttp_client):
    app = make_app(workspace["root"], workspace["db"])
    client = await aiohttp_client(app)

    resp = await client.delete("/image-manager/api/folders/no_such_folder")
    assert resp.status == 404
