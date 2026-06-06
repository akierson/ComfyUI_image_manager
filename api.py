import json
import sqlite3
from pathlib import Path

from aiohttp import web


def make_app(managed_root: Path, db_path: Path) -> web.Application:
    app = web.Application()
    routes = web.RouteTableDef()

    @routes.get("/image-manager")
    async def index(request):
        html_path = Path(__file__).parent / "web" / "index.html"
        if html_path.exists():
            return web.FileResponse(html_path)
        return web.Response(text="<html><body>Image Manager</body></html>", content_type="text/html")

    @routes.get("/image-manager/api/roots")
    async def get_roots(request):
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT uuid, root_name, filename, abs_path, created_at, parent_uuid "
            "FROM images WHERE parent_uuid IS NULL ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for r in rows:
            count = con.execute(
                "SELECT COUNT(*) FROM images WHERE root_uuid = ? AND uuid != ?", (r[0], r[0])
            ).fetchone()[0]
            tip = con.execute(
                "SELECT uuid, created_at FROM images WHERE root_uuid = ? ORDER BY created_at DESC LIMIT 1",
                (r[0],)
            ).fetchone()
            result.append({
                "uuid": r[0],
                "root_name": r[1],
                "filename": r[2],
                "abs_path": r[3],
                "created_at": r[4],
                "parent_uuid": r[5],
                "descendant_count": count,
                "latest_uuid": tip[0],
                "latest_created_at": tip[1],
            })
        con.close()
        return web.json_response(result)

    @routes.get("/image-manager/api/chains")
    async def get_chains(request):
        con = sqlite3.connect(db_path)
        roots = con.execute(
            "SELECT uuid, root_name FROM images WHERE parent_uuid IS NULL ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for root_uuid, root_name in roots:
            images = con.execute(
                "SELECT uuid, filename, created_at FROM images WHERE root_uuid = ? ORDER BY created_at DESC",
                (root_uuid,)
            ).fetchall()
            result.append({
                "root_uuid": root_uuid,
                "root_name": root_name,
                "images": [{"uuid": r[0], "filename": r[1], "created_at": r[2]} for r in images],
            })
        con.close()
        return web.json_response(result)

    @routes.get("/image-manager/api/tree/{root_uuid}")
    async def get_tree(request):
        root_uuid = request.match_info["root_uuid"]
        con = sqlite3.connect(db_path)

        def build_node(img_uuid):
            row = con.execute(
                "SELECT uuid, root_name, filename, created_at, parent_uuid FROM images WHERE uuid = ?",
                (img_uuid,)
            ).fetchone()
            if not row:
                return None
            children_rows = con.execute(
                "SELECT uuid FROM images WHERE parent_uuid = ?", (img_uuid,)
            ).fetchall()
            return {
                "uuid": row[0],
                "root_name": row[1],
                "filename": row[2],
                "created_at": row[3],
                "parent_uuid": row[4],
                "children": [build_node(c[0]) for c in children_rows],
            }

        tree = build_node(root_uuid)
        con.close()
        if not tree:
            raise web.HTTPNotFound()
        return web.json_response(tree)

    @routes.get("/image-manager/api/workflow/{uuid}")
    async def get_workflow(request):
        img_uuid = request.match_info["uuid"]
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT abs_path, filename FROM images WHERE uuid = ?", (img_uuid,)
        ).fetchone()
        con.close()
        if not row or not Path(row[0]).exists():
            raise web.HTTPNotFound()

        from PIL import Image as PilImage
        pil = PilImage.open(row[0])
        raw_workflow = pil.text.get("workflow") if hasattr(pil, "text") else None
        if not raw_workflow:
            raise web.HTTPNotFound(reason="No workflow metadata in PNG")

        import json as _json
        workflow = _json.loads(raw_workflow)

        # Replace any LoadImage node with ManagedLoadImage pointing to this image
        for node_id, node in list(workflow.items()):
            if node.get("class_type") == "LoadImage":
                node["class_type"] = "ManagedLoadImage"
                node["inputs"]["image"] = row[1]  # relative filename
                break
        else:
            # No LoadImage found — inject a new ManagedLoadImage node
            new_id = str(max((int(k) for k in workflow if k.isdigit()), default=0) + 1)
            workflow[new_id] = {
                "class_type": "ManagedLoadImage",
                "inputs": {"image": row[1]},
            }

        return web.json_response(workflow)

    @routes.post("/image-manager/api/import")
    async def import_image_route(request):
        from .lineage import import_image
        from PIL import Image as PilImage
        import io
        reader = await request.multipart()
        pil_img = None
        filename = "imported.png"
        async for part in reader:
            if part.name == "file":
                data = await part.read()
                pil_img = PilImage.open(io.BytesIO(data))
            elif part.name == "filename":
                filename = (await part.read()).decode()
        if pil_img is None:
            raise web.HTTPBadRequest(reason="No file provided")
        result = import_image(pil_img, filename, managed_root, db_path)
        return web.json_response(result)

    @routes.post("/image-manager/api/set-parent")
    async def set_parent_route(request):
        from .lineage import set_parent
        body = await request.json()
        child_uuid = body.get("child_uuid")
        parent_uuid = body.get("parent_uuid")
        if not child_uuid or not parent_uuid:
            raise web.HTTPBadRequest(reason="child_uuid and parent_uuid required")
        try:
            result = set_parent(child_uuid, parent_uuid, db_path, managed_root)
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.json_response(result)

    @routes.post("/image-manager/api/import-folder")
    async def import_folder_route(request):
        import asyncio
        from .lineage import import_folder
        body = await request.json()
        source_str = body.get("path", "").strip()
        if not source_str:
            raise web.HTTPBadRequest(reason="path required")
        source_path = Path(source_str)
        if not source_path.exists():
            raise web.HTTPBadRequest(reason=f"Path not found: {source_str}")
        if not source_path.is_dir():
            raise web.HTTPBadRequest(reason=f"Not a directory: {source_str}")
        if source_path.resolve() == Path(managed_root).resolve():
            raise web.HTTPBadRequest(reason="Source path must not be the managed folder")

        response = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        })
        await response.prepare(request)
        for status, msg in import_folder(source_path, managed_root, db_path):
            await response.write(f"data: {json.dumps({'status': status, 'msg': msg})}\n\n".encode())
            await asyncio.sleep(0)
        return response

    @routes.post("/image-manager/api/cluster")
    async def cluster_images(request):
        import asyncio
        import numpy as np
        from . import clustering as _clustering

        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        async def emit(obj):
            await response.write(f"data: {json.dumps(obj)}\n\n".encode())
            await asyncio.sleep(0)

        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT uuid, abs_path, embedding, embedding_backend FROM images"
        ).fetchall()
        con.close()

        if not rows:
            await emit({"status": "done", "clusters": {}})
            return response

        backend = _clustering.detect_backend()
        await emit({"status": "backend", "msg": f"Using {backend}"})

        need_embed = [(r[0], r[1]) for r in rows if r[2] is None or r[3] != backend]
        cached = {r[0]: np.frombuffer(r[2], dtype=np.float32) for r in rows if r[2] is not None and r[3] == backend}

        if need_embed:
            await emit({"status": "progress", "current": 0, "total": len(need_embed)})
            new_uuids = [r[0] for r in need_embed]
            new_paths = [r[1] for r in need_embed]
            new_embeddings = _clustering.embed_images(new_paths, backend=backend)
            await emit({"status": "progress", "current": len(need_embed), "total": len(need_embed)})

            con = sqlite3.connect(db_path)
            for img_uuid, emb in zip(new_uuids, new_embeddings):
                con.execute(
                    "UPDATE images SET embedding = ?, embedding_backend = ? WHERE uuid = ?",
                    (emb.astype(np.float32).tobytes(), backend, img_uuid),
                )
                cached[img_uuid] = emb
            con.commit()
            con.close()

        all_uuids = [r[0] for r in rows]
        embeddings = np.stack([cached[u] for u in all_uuids])
        labels = _clustering.auto_cluster(embeddings)

        clusters: dict = {}
        for img_uuid, label in zip(all_uuids, labels):
            clusters.setdefault(str(label), []).append(img_uuid)

        await emit({"status": "done", "clusters": clusters})
        return response

    @routes.post("/image-manager/api/rebuild")
    async def rebuild_index(request):
        from .lineage import scan_and_import
        count = scan_and_import(managed_root, db_path)
        return web.json_response({"imported": count})

    @routes.get("/image-manager/api/image/{uuid}")
    async def get_image(request):
        img_uuid = request.match_info["uuid"]
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT abs_path FROM images WHERE uuid = ?", (img_uuid,)).fetchone()
        con.close()
        if not row or not Path(row[0]).exists():
            raise web.HTTPNotFound()
        return web.FileResponse(row[0])

    app.add_routes(routes)
    return app


def mount_routes(server_app, managed_root: Path, db_path: Path):
    """Mount image manager routes onto an existing aiohttp Application."""
    img_app = make_app(managed_root, db_path)
    server_app.add_subapp("/", img_app)
