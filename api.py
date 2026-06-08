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

    @routes.get("/image-manager/favicon.svg")
    async def favicon(request):
        path = Path(__file__).parent / "web" / "favicon.svg"
        return web.FileResponse(path)

    @routes.get("/image-manager/api/roots")
    async def get_roots(request):
        date_filter = request.rel_url.query.get("date")
        name_filter = request.rel_url.query.get("root_name")
        where = "i.parent_uuid IS NULL"
        params = []
        if date_filter:
            where += " AND date(tip.latest) = ?"
            params.append(date_filter)
        if name_filter:
            where += " AND i.root_name = ?"
            params.append(name_filter)
        con = sqlite3.connect(db_path)
        rows = con.execute(
            f"SELECT i.uuid, i.root_name, i.filename, i.abs_path, i.created_at, i.parent_uuid "
            f"FROM images i "
            f"JOIN (SELECT root_uuid, MAX(created_at) as latest FROM images GROUP BY root_uuid) tip "
            f"  ON tip.root_uuid = i.uuid "
            f"WHERE {where} ORDER BY tip.latest DESC",
            params
        ).fetchall()
        result = []
        for r in rows:
            count = con.execute(
                "SELECT COUNT(*) FROM images WHERE root_uuid = ? AND uuid != ?", (r[0], r[0])
            ).fetchone()[0]
            tip = con.execute(
                "SELECT uuid, created_at, filename FROM images WHERE root_uuid = ? ORDER BY created_at DESC LIMIT 1",
                (r[0],)
            ).fetchone()
            tip_stem = tip[2].split("/")[-1].rsplit(".", 1)[0] if tip[2] else None
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
                "latest_filename": tip_stem,
            })
        con.close()
        return web.json_response(result)

    @routes.get("/image-manager/api/all-images")
    async def get_all_images(request):
        date_filter = request.rel_url.query.get("date")
        name_filter = request.rel_url.query.get("root_name")
        con = sqlite3.connect(db_path)
        if date_filter:
            params = []
            where = "i.parent_uuid IS NULL AND date(tip.latest) = ?"
            params.append(date_filter)
            if name_filter:
                where += " AND i.root_name = ?"
                params.append(name_filter)
            rows = con.execute(
                f"SELECT i.uuid, i.root_name, i.filename, i.abs_path, i.created_at, i.parent_uuid, i.root_uuid "
                f"FROM images i "
                f"JOIN (SELECT root_uuid, MAX(created_at) as latest FROM images GROUP BY root_uuid) tip "
                f"  ON tip.root_uuid = i.uuid "
                f"WHERE {where}",
                params,
            ).fetchall()
        elif name_filter:
            rows = con.execute(
                "SELECT uuid, root_name, filename, abs_path, created_at, parent_uuid, root_uuid "
                "FROM images WHERE root_name = ? ORDER BY created_at DESC",
                (name_filter,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT uuid, root_name, filename, abs_path, created_at, parent_uuid, root_uuid FROM images"
            ).fetchall()
        all_rows = con.execute("SELECT uuid, parent_uuid, root_uuid FROM images").fetchall()
        con.close()
        parent_map = {r[0]: r[1] for r in all_rows}

        from collections import Counter
        global_root_counts = Counter(r[2] for r in all_rows)

        def compute_generation(uuid):
            depth = 0
            cur = parent_map.get(uuid)
            visited = {uuid}
            while cur and cur not in visited:
                visited.add(cur)
                depth += 1
                cur = parent_map.get(cur)
            return depth

        return web.json_response([
            {"uuid": r[0], "root_name": r[1], "filename": r[2],
             "abs_path": r[3], "created_at": r[4], "parent_uuid": r[5], "root_uuid": r[6],
             "generation": compute_generation(r[0]),
             "orphan": global_root_counts[r[6]] == 1}
            for r in rows
        ])

    @routes.get("/image-manager/api/folders")
    async def get_folders(request):
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT root_name, COUNT(*) as cnt, MAX(created_at) as latest_tip_at "
            "FROM images GROUP BY root_name ORDER BY latest_tip_at DESC"
        ).fetchall()
        con.close()
        return web.json_response([
            {"root_name": r[0], "count": r[1], "latest_tip_at": r[2]}
            for r in rows
        ])

    @routes.get("/image-manager/api/leaf-strips")
    async def get_leaf_strips(request):
        date_filter = request.rel_url.query.get("date")
        name_filter = request.rel_url.query.get("root_name")
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT uuid, root_uuid, root_name, filename, parent_uuid, created_at FROM images"
        ).fetchall()
        con.close()
        by_uuid = {r[0]: {"uuid": r[0], "root_uuid": r[1], "root_name": r[2],
                           "filename": r[3], "parent_uuid": r[4], "created_at": r[5]}
                   for r in rows}
        # Build same-chain children map
        children = {}  # parent_uuid -> [child_uuid, ...]
        for r in by_uuid.values():
            p = r["parent_uuid"]
            if p and by_uuid.get(p) and by_uuid[p]["root_uuid"] == r["root_uuid"]:
                children.setdefault(p, []).append(r["uuid"])
        leaves = [r for r in by_uuid.values() if r["uuid"] not in children]
        result = []
        for leaf in leaves:
            if name_filter and leaf["root_name"] != name_filter:
                continue
            if date_filter and leaf["created_at"][:10] != date_filter:
                continue
            strip = [leaf]
            cur = by_uuid.get(leaf["parent_uuid"])
            while cur and cur["root_uuid"] == leaf["root_uuid"]:
                strip.append(cur)
                cur = by_uuid.get(cur["parent_uuid"])
            result.append({
                "leaf_uuid": leaf["uuid"],
                "root_name": leaf["root_name"],
                "leaf_name": leaf["filename"],
                "generation": len(strip) - 1,
                "images": [{"uuid": i["uuid"], "filename": i["filename"],
                             "created_at": i["created_at"]} for i in strip],
            })
        result.sort(key=lambda s: s["images"][0]["created_at"], reverse=True)
        return web.json_response(result)

    @routes.get("/image-manager/api/tree/{root_uuid}")
    async def get_tree(request):
        root_uuid = request.match_info["root_uuid"]
        con = sqlite3.connect(db_path)

        def build_node(img_uuid, generation=0, cross_chain=False):
            row = con.execute(
                "SELECT uuid, root_name, filename, created_at, parent_uuid, root_uuid FROM images WHERE uuid = ?",
                (img_uuid,)
            ).fetchone()
            if not row:
                return None
            node_root_uuid = row[5]
            # Same-chain children: parent_uuid matches AND they belong to the same chain
            same_chain_rows = con.execute(
                "SELECT uuid FROM images WHERE parent_uuid = ? AND root_uuid = ?",
                (img_uuid, node_root_uuid)
            ).fetchall()
            # Cross-chain children: parent_uuid matches but belong to a different chain
            cross_chain_rows = con.execute(
                "SELECT uuid FROM images WHERE parent_uuid = ? AND root_uuid != ?",
                (img_uuid, node_root_uuid)
            ).fetchall()
            node = {
                "uuid": row[0],
                "root_name": row[1],
                "filename": row[2],
                "created_at": row[3],
                "parent_uuid": row[4],
                "generation": generation,
                "cross_chain": cross_chain,
                "children": [build_node(c[0], generation + 1) for c in same_chain_rows],
            }
            # Cross-chain children are appended as leaf nodes (not recursively expanded)
            for (cid,) in cross_chain_rows:
                child_node = build_node(cid, generation + 1, cross_chain=True)
                if child_node:
                    node["children"].append(child_node)
            return node

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
        try:
            from .lineage import import_image
        except ImportError:
            from lineage import import_image
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
        try:
            from .lineage import set_parent
        except ImportError:
            from lineage import set_parent
        body = await request.json()
        child_uuid = body.get("child_uuid")
        parent_uuid = body.get("parent_uuid")
        if not child_uuid or not parent_uuid:
            raise web.HTTPBadRequest(reason="child_uuid and parent_uuid required")
        # Detect direct-child-swap case before general cycle check
        import sqlite3 as _sqlite3
        con = _sqlite3.connect(db_path)
        proposed_parent_row = con.execute(
            "SELECT parent_uuid, filename FROM images WHERE uuid = ?", (parent_uuid,)
        ).fetchone()
        child_row = con.execute(
            "SELECT filename FROM images WHERE uuid = ?", (child_uuid,)
        ).fetchone()
        con.close()
        if proposed_parent_row and proposed_parent_row[0] == child_uuid:
            raise web.HTTPConflict(
                text=json.dumps({
                    "code": "direct_child_proposed",
                    "child_name": child_row[0] if child_row else child_uuid,
                    "parent_name": proposed_parent_row[1] if proposed_parent_row else parent_uuid,
                }),
                content_type="application/json",
            )
        if body.get("enforce_chronological", False):
            import sqlite3 as _sqlite3
            con = _sqlite3.connect(db_path)
            row = con.execute(
                "SELECT uuid, created_at FROM images WHERE uuid IN (?, ?)",
                (child_uuid, parent_uuid),
            ).fetchall()
            con.close()
            by_uuid = {r[0]: r[1] for r in row}
            if by_uuid.get(parent_uuid, "") > by_uuid.get(child_uuid, ""):
                raise web.HTTPUnprocessableEntity(reason="Parent must be older than child")
        try:
            result = set_parent(child_uuid, parent_uuid, db_path, managed_root)
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.json_response(result)

    @routes.post("/image-manager/api/set-parents-batch")
    async def set_parents_batch_route(request):
        try:
            from .lineage import set_parent
        except ImportError:
            from lineage import set_parent
        body = await request.json()
        pairs = body.get("pairs", [])
        results = []
        errors = []
        for pair in pairs:
            child_uuid = pair.get("child_uuid")
            parent_uuid = pair.get("parent_uuid")
            try:
                result = set_parent(child_uuid, parent_uuid, db_path, managed_root)
                results.append(result)
            except Exception as e:
                errors.append({"child_uuid": child_uuid, "parent_uuid": parent_uuid, "error": str(e)})
        return web.json_response({"results": results, "errors": errors})

    @routes.post("/image-manager/api/delete-images")
    async def delete_images_route(request):
        try:
            from .lineage import delete_images
        except ImportError:
            from lineage import delete_images
        body = await request.json()
        uuids = body.get("uuids", [])
        result = delete_images(uuids, db_path)
        return web.json_response(result)

    @routes.post("/image-manager/api/swap-adjacent")
    async def swap_adjacent_route(request):
        try:
            from .lineage import swap_adjacent
        except ImportError:
            from lineage import swap_adjacent
        body = await request.json()
        uuid_a = body.get("uuid_a")
        uuid_b = body.get("uuid_b")
        if not uuid_a or not uuid_b:
            raise web.HTTPBadRequest(reason="uuid_a and uuid_b required")
        try:
            swap_adjacent(uuid_a, uuid_b, db_path, managed_root)
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.json_response({"status": "swapped"})

    @routes.post("/image-manager/api/fork/{uuid}")
    async def fork_chain_route(request):
        try:
            from .lineage import fork_chain
        except ImportError:
            from lineage import fork_chain
        img_uuid = request.match_info["uuid"]
        body = await request.json()
        target_root_name = body.get("root_name", "").strip()
        if not target_root_name:
            raise web.HTTPBadRequest(reason="root_name required")
        try:
            result = fork_chain(img_uuid, target_root_name, db_path, managed_root)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise web.HTTPNotFound(reason=msg)
            raise web.HTTPBadRequest(reason=msg)
        return web.json_response(result)

    @routes.post("/image-manager/api/move-chains")
    async def move_chains_route(request):
        try:
            from .lineage import move_chains
        except ImportError:
            from lineage import move_chains
        body = await request.json()
        root_uuids = body.get("root_uuids", [])
        target_folder = body.get("target_folder", "").strip()
        if not root_uuids or not target_folder:
            raise web.HTTPBadRequest(reason="root_uuids and target_folder required")
        try:
            result = move_chains(root_uuids, target_folder, db_path, managed_root)
        except ValueError as e:
            raise web.HTTPBadRequest(reason=str(e))
        return web.json_response(result)

    @routes.post("/image-manager/api/import-folder")
    async def import_folder_route(request):
        import asyncio
        try:
            from .lineage import import_folder
        except ImportError:
            from lineage import import_folder
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
        try:
            from . import clustering as _clustering
        except ImportError:
            import clustering as _clustering

        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        async def emit(obj):
            await response.write(f"data: {json.dumps(obj)}\n\n".encode())
            await asyncio.sleep(0)

        date_filter = request.rel_url.query.get("date")
        name_filter = request.rel_url.query.get("root_name")
        k_scale = max(0.1, min(10.0, float(request.rel_url.query.get("k_scale", "1.0"))))
        con = sqlite3.connect(db_path)
        if date_filter or name_filter:
            where_clauses = []
            params = []
            if date_filter:
                where_clauses.append("date(tip.latest) = ?")
                params.append(date_filter)
            if name_filter:
                where_clauses.append("i.root_name = ?")
                params.append(name_filter)
            where = " AND ".join(where_clauses)
            rows = con.execute(
                f"SELECT i.uuid, i.abs_path, i.embedding, i.embedding_backend, "
                f"       i.root_uuid, i.parent_uuid, i.filename, i.created_at "
                f"FROM images i "
                f"JOIN (SELECT root_uuid, MAX(created_at) as latest FROM images GROUP BY root_uuid) tip "
                f"  ON tip.root_uuid = i.root_uuid "
                f"WHERE {where}",
                params,
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT uuid, abs_path, embedding, embedding_backend, root_uuid, parent_uuid, filename, created_at FROM images"
            ).fetchall()

        # Fetch all rows for generation computation (needed even in filtered mode)
        all_rows_for_gen = con.execute(
            "SELECT uuid, parent_uuid FROM images"
        ).fetchall()
        con.close()

        if not rows:
            await emit({"status": "done", "clusters": {}})
            return response

        # Compute generation depth via in-memory parent walk
        parent_map = {r[0]: r[1] for r in all_rows_for_gen}

        def compute_generation(img_uuid: str) -> int:
            depth = 0
            cur = parent_map.get(img_uuid)
            while cur is not None:
                depth += 1
                cur = parent_map.get(cur)
            return depth

        # Determine orphans: root_uuid that appears exactly once in the filtered set
        from collections import Counter
        root_uuid_counts = Counter(r[4] for r in rows)

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

        # Build enriched records
        row_meta = {
            r[0]: {"root_uuid": r[4], "parent_uuid": r[5], "filename": r[6], "created_at": r[7]}
            for r in rows
        }
        all_uuids = [r[0] for r in rows]
        embeddings = np.stack([cached[u] for u in all_uuids])
        labels = _clustering.auto_cluster(embeddings, k_scale=k_scale)

        clusters: dict = {}
        for img_uuid, label in zip(all_uuids, labels):
            meta = row_meta[img_uuid]
            root_uuid = meta["root_uuid"]
            generation = compute_generation(img_uuid)
            orphan = root_uuid_counts[root_uuid] == 1
            record = {
                "uuid": img_uuid,
                "root_uuid": root_uuid,
                "generation": generation,
                "parent_uuid": meta["parent_uuid"],
                "filename": meta["filename"],
                "orphan": orphan,
                "created_at": meta["created_at"],
            }
            clusters.setdefault(str(label), []).append(record)

        # Sort each cluster: orphans last, then by chain (root_uuid), generation, date
        def sort_key(item):
            return (item["orphan"], item["root_uuid"], item["generation"], item["created_at"])

        clusters = {k: sorted(v, key=sort_key) for k, v in clusters.items()}

        await emit({"status": "done", "clusters": clusters})
        return response

    @routes.get("/image-manager/api/metadata/{uuid}")
    async def get_metadata(request):
        img_uuid = request.match_info["uuid"]
        con = sqlite3.connect(db_path)
        row = con.execute(
            "SELECT uuid, root_name, root_uuid, parent_uuid, filename, abs_path, created_at "
            "FROM images WHERE uuid = ?",
            (img_uuid,),
        ).fetchone()
        if not row:
            con.close()
            raise web.HTTPNotFound()

        uuid, root_name, root_uuid, parent_uuid, filename, abs_path, created_at = row

        # Walk parent chain to compute generation depth
        generation = 0
        current = parent_uuid
        while current:
            parent_row = con.execute(
                "SELECT parent_uuid FROM images WHERE uuid = ?", (current,)
            ).fetchone()
            if not parent_row:
                break
            generation += 1
            current = parent_row[0]
        con.close()

        file_path = Path(abs_path)
        file_size = file_path.stat().st_size if file_path.exists() else 0

        # Parse workflow metadata from PNG "prompt" chunk
        workflow_data = {"has_workflow": False}
        if file_path.exists():
            from PIL import Image as _PilImage
            import json as _json
            try:
                from .workflow_parser import parse_workflow
            except ImportError:
                from workflow_parser import parse_workflow
            pil = _PilImage.open(file_path)
            raw_prompt = pil.text.get("prompt") if hasattr(pil, "text") else None
            if raw_prompt:
                try:
                    prompt_dict = _json.loads(raw_prompt)
                    workflow_data = parse_workflow(prompt_dict)
                except Exception:
                    pass

        # Width and height from actual PNG dimensions
        width, height = None, None
        if file_path.exists():
            from PIL import Image as _PilImage2
            try:
                pil2 = _PilImage2.open(file_path)
                width, height = pil2.size
            except Exception:
                pass

        result = {
            "uuid": uuid,
            "root_name": root_name,
            "root_uuid": root_uuid,
            "parent_uuid": parent_uuid,
            "filename": filename,
            "created_at": created_at,
            "generation": generation,
            "file_size": file_size,
            "width": width,
            "height": height,
        }
        result.update(workflow_data)
        return web.json_response(result)

    @routes.patch("/image-manager/api/folders/{root_name}")
    async def rename_folder_route(request):
        try:
            from .lineage import rename_folder
        except ImportError:
            from lineage import rename_folder
        old_name = request.match_info["root_name"]
        body = await request.json()
        new_name = body.get("name", "").strip()
        if not new_name:
            raise web.HTTPBadRequest(reason="name must not be empty")
        try:
            result = rename_folder(old_name, new_name, db_path, managed_root)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise web.HTTPNotFound(reason=msg)
            if "already exists" in msg:
                raise web.HTTPConflict(reason=msg)
            raise web.HTTPBadRequest(reason=msg)
        return web.json_response(result)

    @routes.delete("/image-manager/api/folders/{root_name}")
    async def delete_folder_route(request):
        try:
            from .lineage import delete_folder
        except ImportError:
            from lineage import delete_folder
        root_name = request.match_info["root_name"]
        dry_run = request.rel_url.query.get("dry_run", "false").lower() == "true"
        try:
            result = delete_folder(root_name, db_path, dry_run=dry_run)
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise web.HTTPNotFound(reason=msg)
            raise web.HTTPBadRequest(reason=msg)
        return web.json_response(result)

    @routes.post("/image-manager/api/rebuild")
    async def rebuild_index(request):
        try:
            from .lineage import scan_and_import
        except ImportError:
            from lineage import scan_and_import
        count = scan_and_import(managed_root, db_path)
        return web.json_response({"imported": count})

    @routes.get("/image-manager/api/order-violations")
    async def get_order_violations(request):
        con = sqlite3.connect(db_path)
        rows = con.execute(
            """
            SELECT p.uuid, p.filename, p.created_at,
                   c.uuid, c.filename, c.created_at,
                   c.parent_uuid
            FROM images c
            JOIN images p ON c.parent_uuid = p.uuid
            WHERE p.created_at > c.created_at
            ORDER BY c.created_at
            """
        ).fetchall()
        result = []
        for r in rows:
            parent_uuid, parent_filename, parent_created_at, \
                child_uuid, child_filename, child_created_at, _ = r
            # suggested: earliest created_at among siblings (same parent) + child itself
            candidates = con.execute(
                """
                SELECT uuid, filename, created_at FROM images
                WHERE parent_uuid = ?
                ORDER BY created_at ASC LIMIT 1
                """,
                (parent_uuid,)
            ).fetchone()
            result.append({
                "parent_uuid": parent_uuid,
                "parent_filename": parent_filename,
                "parent_created_at": parent_created_at,
                "child_uuid": child_uuid,
                "child_filename": child_filename,
                "child_created_at": child_created_at,
                "suggested_uuid": candidates[0] if candidates else child_uuid,
                "suggested_filename": candidates[1] if candidates else child_filename,
                "suggested_created_at": candidates[2] if candidates else child_created_at,
            })
        con.close()
        return web.json_response(result)

    @routes.post("/image-manager/api/promote-child")
    async def promote_child_route(request):
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(reason="invalid JSON")
        parent_uuid = data.get("parent_uuid")
        promoted_uuid = data.get("promoted_uuid")
        if not parent_uuid or not promoted_uuid:
            raise web.HTTPBadRequest(reason="parent_uuid and promoted_uuid required")
        try:
            from .lineage import promote_child
        except ImportError:
            from lineage import promote_child
        try:
            result = promote_child(parent_uuid, promoted_uuid, db_path, managed_root)
        except ValueError as e:
            return web.json_response({"reason": str(e)}, status=400)
        return web.json_response(result)

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
