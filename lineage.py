import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import torch
from PIL import Image
import numpy as np


def init_db(db_path: Path):
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS images (
            uuid TEXT PRIMARY KEY,
            parent_uuid TEXT,
            root_uuid TEXT NOT NULL,
            root_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            abs_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            embedding BLOB,
            embedding_backend TEXT
        )
    """)
    existing = {row[1] for row in con.execute("PRAGMA table_info(images)")}
    if "embedding" not in existing:
        con.execute("ALTER TABLE images ADD COLUMN embedding BLOB")
    if "embedding_backend" not in existing:
        con.execute("ALTER TABLE images ADD COLUMN embedding_backend TEXT")
    con.commit()
    con.close()


def _png_created_at(path: Path) -> str:
    mtime = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _resolve_prefix(prefix: str) -> str:
    def replace_date(m):
        fmt = m.group(1).replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d")
        return datetime.now().strftime(fmt)
    return re.sub(r"%date:([^%]+)%", replace_date, prefix)


def _detect_parent(prompt: dict, db_path: Path):
    nodes = [v for v in prompt.values() if v.get("class_type") == "ManagedLoadImage"]
    if len(nodes) == 0:
        return None
    if len(nodes) > 1:
        print(f"[image_manager] WARNING: {len(nodes)} ManagedLoadImage nodes found — saving as root")
        return None
    filename = nodes[0]["inputs"]["image"]
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT uuid, root_uuid, root_name FROM images WHERE filename = ?", (filename,)
    ).fetchone()
    con.close()
    if row is None:
        print(f"[image_manager] WARNING: ManagedLoadImage filename '{filename}' not in DB — saving as root")
        return None
    return {"uuid": row[0], "root_uuid": row[1], "root_name": row[2]}


def save_image(image: torch.Tensor, filename_prefix: str, prompt: dict,
               managed_root: Path, db_path: Path, extra_pnginfo: dict | None = None) -> dict:
    parent = _detect_parent(prompt, db_path)
    img_uuid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    date_str = datetime.now().strftime("%Y-%m-%d")

    if parent:
        parent_uuid = parent["uuid"]
        root_uuid = parent["root_uuid"]
        root_name = parent["root_name"]
    else:
        parent_uuid = None
        root_uuid = img_uuid
        root_name = _resolve_prefix(filename_prefix)

    folder = Path(managed_root) / date_str / root_name
    folder.mkdir(parents=True, exist_ok=True)

    existing = len(list(folder.glob("*.png")))
    fname = f"{filename_prefix}_{existing + 1:05d}_.png"
    abs_path = folder / fname
    rel_path = str(Path(date_str) / root_name / fname)

    # Save PNG
    arr = (image[0].numpy() * 255).clip(0, 255).astype(np.uint8)
    pil_img = Image.fromarray(arr)
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("lineage_id", img_uuid)
    info.add_text("parent_id", parent_uuid or "")
    if prompt:
        info.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo:
        for k, v in extra_pnginfo.items():
            info.add_text(k, json.dumps(v))
    pil_img.save(abs_path, pnginfo=info)

    # Write sidecar
    sidecar = {
        "uuid": img_uuid,
        "parent_uuid": parent_uuid,
        "root_uuid": root_uuid,
        "root_name": root_name,
        "created_at": now,
        "filename": rel_path,
    }
    abs_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))

    # Upsert SQLite
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (img_uuid, parent_uuid, root_uuid, root_name, rel_path, str(abs_path), now)
    )
    con.commit()
    con.close()

    return {"uuid": img_uuid, "parent_uuid": parent_uuid, "root_uuid": root_uuid,
            "abs_path": str(abs_path), "filename": rel_path}


def import_image(pil_img: "Image.Image", original_filename: str,
                 managed_root: Path, db_path: Path) -> dict:
    """Import an external image as a root managed image."""
    img_uuid = str(uuid.uuid4())
    date_str = datetime.now().strftime("%Y-%m-%d")
    stem = Path(original_filename).stem
    root_name = stem

    folder = Path(managed_root) / date_str / root_name
    folder.mkdir(parents=True, exist_ok=True)

    existing = len(list(folder.glob("*.png")))
    fname = f"{stem}_{existing + 1:05d}_.png"
    abs_path = folder / fname
    rel_path = str(Path(date_str) / root_name / fname)

    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("lineage_id", img_uuid)
    info.add_text("parent_id", "")
    pil_img.convert("RGB").save(abs_path, pnginfo=info)

    created_at = _png_created_at(abs_path)

    sidecar = {
        "uuid": img_uuid,
        "parent_uuid": None,
        "root_uuid": img_uuid,
        "root_name": root_name,
        "created_at": created_at,
        "filename": rel_path,
    }
    abs_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (img_uuid, None, img_uuid, root_name, rel_path, str(abs_path), created_at)
    )
    con.commit()
    con.close()

    return {"uuid": img_uuid, "filename": rel_path, "abs_path": str(abs_path)}


def _create_missing_sidecars(managed_root: Path) -> int:
    """Write sidecar JSONs for any PNGs in managed_root that don't have one. Returns count created."""
    count = 0
    for png_path in Path(managed_root).rglob("*.png"):
        sidecar_path = png_path.with_suffix(".json")
        if sidecar_path.exists():
            continue
        try:
            img_uuid = str(uuid.uuid4())
            parts = png_path.relative_to(managed_root).parts
            if len(parts) >= 3:
                root_name = parts[1]
            elif len(parts) == 2:
                root_name = parts[0]
            else:
                root_name = png_path.stem
            try:
                created_at = _png_created_at(png_path)
            except OSError:
                created_at = datetime.now(timezone.utc).isoformat()

            rel_path = str(png_path.relative_to(managed_root))
            sidecar = {
                "uuid": img_uuid,
                "parent_uuid": None,
                "root_uuid": img_uuid,
                "root_name": root_name,
                "created_at": created_at,
                "filename": rel_path,
            }
            sidecar_path.write_text(json.dumps(sidecar, indent=2))
            count += 1
        except Exception as e:
            print(f"[image_manager] _create_missing_sidecars: skipping {png_path}: {e}")
    return count


def scan_and_import(managed_root: Path, db_path: Path) -> int:
    """Scan managed_root for sidecar JSONs and repopulate the DB. Returns count inserted."""
    count = 0
    con = sqlite3.connect(db_path)
    found_uuids = []
    for sidecar_path in Path(managed_root).rglob("*.json"):
        try:
            meta = json.loads(sidecar_path.read_text())
            abs_png = sidecar_path.with_suffix(".png")
            if not abs_png.exists():
                continue
            created_at = meta["created_at"]
            try:
                mtime_str = _png_created_at(abs_png)
                sidecar_dt = datetime.fromisoformat(created_at)
                mtime_dt = datetime.fromisoformat(mtime_str)
                if abs((mtime_dt - sidecar_dt).total_seconds()) > 1:
                    created_at = mtime_str
                    meta["created_at"] = mtime_str
                    sidecar_path.write_text(json.dumps(meta, indent=2))
            except Exception:
                pass
            con.execute(
                "INSERT INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uuid) DO UPDATE SET "
                "  parent_uuid = excluded.parent_uuid, "
                "  root_uuid = excluded.root_uuid, "
                "  root_name = excluded.root_name, "
                "  filename = excluded.filename, "
                "  abs_path = excluded.abs_path, "
                "  created_at = excluded.created_at",
                (meta["uuid"], meta.get("parent_uuid"), meta["root_uuid"],
                 meta["root_name"], meta["filename"], str(abs_png), created_at)
            )
            found_uuids.append(meta["uuid"])
            count += 1
        except Exception as e:
            print(f"[image_manager] scan_and_import: skipping {sidecar_path}: {e}")
    if found_uuids:
        placeholders = ",".join("?" * len(found_uuids))
        con.execute(f"DELETE FROM images WHERE uuid NOT IN ({placeholders})", found_uuids)
    else:
        con.execute("DELETE FROM images")
    con.commit()
    con.close()
    return count


def set_parent(child_uuid: str, parent_uuid: str, db_path: Path, managed_root: Path) -> dict:
    """
    Assign parent_uuid as the parent of child_uuid. Cascades root_uuid/root_name
    to all descendants. Updates both DB and sidecar JSONs. Returns info about child.
    Raises ValueError on cycle or unknown UUIDs.
    """
    con = sqlite3.connect(db_path)

    def _get(uid):
        return con.execute(
            "SELECT uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at FROM images WHERE uuid = ?",
            (uid,)
        ).fetchone()

    child_row = _get(child_uuid)
    parent_row = _get(parent_uuid)
    if not child_row:
        con.close()
        raise ValueError(f"child uuid {child_uuid!r} not found")
    if not parent_row:
        con.close()
        raise ValueError(f"parent uuid {parent_uuid!r} not found")

    # Cycle check: walk up from parent, ensure child_uuid never appears
    cursor = parent_uuid
    while cursor:
        if cursor == child_uuid:
            con.close()
            raise ValueError("cycle detected: parent is a descendant of child")
        row = _get(cursor)
        cursor = row[1] if row else None

    new_root_uuid = parent_row[2]
    new_root_name = parent_row[3]

    # Cascade: collect all descendants of child via BFS
    to_update = [child_uuid]
    queue = [child_uuid]
    while queue:
        cur = queue.pop()
        children = con.execute("SELECT uuid FROM images WHERE parent_uuid = ?", (cur,)).fetchall()
        for (cid,) in children:
            to_update.append(cid)
            queue.append(cid)

    # Update child's parent_uuid; update root_uuid/root_name for child + all descendants
    con.execute(
        "UPDATE images SET parent_uuid = ?, root_uuid = ?, root_name = ? WHERE uuid = ?",
        (parent_uuid, new_root_uuid, new_root_name, child_uuid)
    )
    for uid in to_update[1:]:
        con.execute(
            "UPDATE images SET root_uuid = ?, root_name = ? WHERE uuid = ?",
            (new_root_uuid, new_root_name, uid)
        )
    con.commit()

    # Rewrite sidecars for child and all descendants
    for uid in to_update:
        row = _get(uid)
        if not row:
            continue
        abs_path = Path(row[5])
        sidecar_path = abs_path.with_suffix(".json")
        if sidecar_path.exists():
            try:
                meta = json.loads(sidecar_path.read_text())
                if uid == child_uuid:
                    meta["parent_uuid"] = parent_uuid
                meta["root_uuid"] = new_root_uuid
                meta["root_name"] = new_root_name
                sidecar_path.write_text(json.dumps(meta, indent=2))
            except Exception as e:
                print(f"[image_manager] set_parent: could not rewrite sidecar {sidecar_path}: {e}")

    existed = child_row[1] is not None
    con.close()
    return {
        "child_uuid": child_uuid,
        "parent_uuid": parent_uuid,
        "root_uuid": new_root_uuid,
        "root_name": new_root_name,
        "had_existing_parent": existed,
        "descendants_updated": len(to_update) - 1,
    }


def promote_child(parent_uuid: str, promoted_uuid: str, db_path: Path, managed_root: Path) -> dict:
    """
    Promote promoted_uuid above parent_uuid to fix a chronological order violation.

    Result topology:
      A → promoted → P → (other children of P)
                  ↘ (promoted's existing descendants, unchanged)

    If P was the root (no A), promoted becomes the new root and root_uuid is
    updated for the entire chain.
    """
    con = sqlite3.connect(db_path)

    def _get(uid):
        return con.execute(
            "SELECT uuid, parent_uuid, root_uuid, root_name, filename, abs_path FROM images WHERE uuid = ?",
            (uid,)
        ).fetchone()

    p_row = _get(parent_uuid)
    promoted_row = _get(promoted_uuid)
    if not p_row:
        con.close()
        raise ValueError(f"parent uuid {parent_uuid!r} not found")
    if not promoted_row:
        con.close()
        raise ValueError(f"promoted uuid {promoted_uuid!r} not found")
    if promoted_row[1] != parent_uuid:
        con.close()
        raise ValueError(f"{promoted_uuid!r} is not a direct child of {parent_uuid!r}")

    a_uuid = p_row[1]  # P's former parent (may be None)
    old_root_uuid = p_row[2]
    root_name = p_row[3]

    other_children = [
        r[0] for r in con.execute(
            "SELECT uuid FROM images WHERE parent_uuid = ? AND uuid != ?",
            (parent_uuid, promoted_uuid)
        ).fetchall()
    ]

    with con:
        # promoted takes P's former parent
        con.execute(
            "UPDATE images SET parent_uuid = ? WHERE uuid = ?",
            (a_uuid, promoted_uuid)
        )
        # P becomes a child of promoted
        con.execute(
            "UPDATE images SET parent_uuid = ? WHERE uuid = ?",
            (promoted_uuid, parent_uuid)
        )
        # Other children of P are re-parented to promoted
        if other_children:
            con.executemany(
                "UPDATE images SET parent_uuid = ? WHERE uuid = ?",
                [(promoted_uuid, uid) for uid in other_children]
            )
        # If P was the root, promoted is the new root — cascade root_uuid
        chain_root_changed = a_uuid is None
        new_root_uuid = promoted_uuid if chain_root_changed else old_root_uuid
        if chain_root_changed:
            # BFS from promoted to update root_uuid for all chain images
            queue = [promoted_uuid]
            visited = []
            while queue:
                cur = queue.pop()
                visited.append(cur)
                children = con.execute(
                    "SELECT uuid FROM images WHERE parent_uuid = ?", (cur,)
                ).fetchall()
                queue.extend(c[0] for c in children)
            con.executemany(
                "UPDATE images SET root_uuid = ? WHERE uuid = ?",
                [(new_root_uuid, uid) for uid in visited]
            )

    # Rewrite sidecars for all affected images
    affected = [promoted_uuid, parent_uuid] + other_children
    if chain_root_changed:
        affected = list(visited)  # already includes all chain members

    images_updated = 0
    for uid in affected:
        row = _get(uid)
        if not row:
            continue
        abs_path = Path(row[5])
        sidecar_path = abs_path.with_suffix(".json")
        if sidecar_path.exists():
            try:
                meta = json.loads(sidecar_path.read_text())
                fresh = _get(uid)
                meta["parent_uuid"] = fresh[1]
                meta["root_uuid"] = fresh[2]
                meta["root_name"] = fresh[3]
                sidecar_path.write_text(json.dumps(meta, indent=2))
                images_updated += 1
            except Exception as e:
                print(f"[image_manager] promote_child: could not rewrite sidecar {sidecar_path}: {e}")

    con.close()
    return {
        "promoted_uuid": promoted_uuid,
        "former_parent_uuid": a_uuid,
        "chain_root_changed": chain_root_changed,
        "new_root_uuid": new_root_uuid,
        "images_updated": images_updated,
    }


def swap_adjacent(uuid_a: str, uuid_b: str, db_path: Path, managed_root: Path) -> None:
    """
    Swap two adjacent images where B is currently A's direct child.
    After the swap: B takes A's former position (inherits A's parent or becomes root),
    and A becomes B's child.
    """
    con = sqlite3.connect(db_path)
    a_row = con.execute("SELECT parent_uuid FROM images WHERE uuid = ?", (uuid_a,)).fetchone()
    con.close()
    if not a_row:
        raise ValueError(f"uuid_a {uuid_a!r} not found")
    a_parent = a_row[0]  # may be None if A is root

    if a_parent is None:
        # B becomes root: clear its parent
        con = sqlite3.connect(db_path)
        con.execute("UPDATE images SET parent_uuid = NULL WHERE uuid = ?", (uuid_b,))
        con.commit()
        con.close()
        # Cascade root_uuid for B and all its descendants (including A after next step)
        # We use set_parent logic by calling the DB directly first then going through set_parent for A
        # Step 1: make B a root by setting its root_uuid to itself
        con = sqlite3.connect(db_path)
        b_row = con.execute("SELECT root_name FROM images WHERE uuid = ?", (uuid_b,)).fetchone()
        root_name = b_row[0] if b_row else uuid_b
        con.execute("UPDATE images SET parent_uuid = NULL, root_uuid = ?, root_name = ? WHERE uuid = ?",
                    (uuid_b, root_name, uuid_b))
        con.commit()
        con.close()
    else:
        # B takes A's slot: B's parent becomes A's former parent
        set_parent(uuid_b, a_parent, db_path, managed_root)

    # A becomes B's child
    set_parent(uuid_a, uuid_b, db_path, managed_root)


def fork_chain(img_uuid: str, target_root_name: str, db_path: Path, managed_root: Path) -> dict:
    """
    Fork img_uuid and all its descendants into a new chain named target_root_name.
    The forked image's root_uuid becomes its own uuid; parent_uuid is preserved.
    Updates both DB and sidecar JSONs.
    Raises ValueError if target matches current chain or a cycle is detected.
    """
    con = sqlite3.connect(db_path)

    def _get(uid):
        return con.execute(
            "SELECT uuid, parent_uuid, root_uuid, root_name, filename, abs_path FROM images WHERE uuid = ?",
            (uid,)
        ).fetchone()

    row = _get(img_uuid)
    if not row:
        con.close()
        raise ValueError(f"uuid {img_uuid!r} not found")

    if row[3] == target_root_name:
        con.close()
        raise ValueError(f"image is already in chain {target_root_name!r}")

    # Collect forked image + all descendants via BFS
    to_update = [img_uuid]
    queue = [img_uuid]
    while queue:
        cur = queue.pop()
        for (cid,) in con.execute("SELECT uuid FROM images WHERE parent_uuid = ?", (cur,)).fetchall():
            to_update.append(cid)
            queue.append(cid)

    # Cycle check: target chain must not already exist as a descendant of img_uuid
    descendant_set = set(to_update[1:])
    for uid in descendant_set:
        r = _get(uid)
        if r and r[3] == target_root_name and r[2] == uid:
            con.close()
            raise ValueError(f"cycle detected: {target_root_name!r} is a descendant chain of the forked image")

    new_root_uuid = img_uuid

    for uid in to_update:
        con.execute(
            "UPDATE images SET root_uuid = ?, root_name = ? WHERE uuid = ?",
            (new_root_uuid, target_root_name, uid)
        )
    con.commit()

    for uid in to_update:
        r = _get(uid)
        if not r:
            continue
        sidecar_path = Path(r[5]).with_suffix(".json")
        if sidecar_path.exists():
            try:
                meta = json.loads(sidecar_path.read_text())
                meta["root_uuid"] = new_root_uuid
                meta["root_name"] = target_root_name
                sidecar_path.write_text(json.dumps(meta, indent=2))
            except Exception as e:
                print(f"[image_manager] fork_chain: could not rewrite sidecar {sidecar_path}: {e}")

    con.close()
    return {
        "forked_count": len(to_update),
        "new_root_uuid": new_root_uuid,
        "root_name": target_root_name,
    }


def import_folder(source_path: Path, managed_root: Path, db_path: Path):
    """Move all image files from source_path into managed_root. Yields (status, msg) tuples.

    Folder structure mapping:
    - source/img.png                     → managed_root/YYYY-MM-DD/<stem>/img.png
    - source/subdir/img.png              → managed_root/YYYY-MM-DD/subdir/img.png
    - source/YYYY-MM-DD/rootname/img.png → managed_root/YYYY-MM-DD/rootname/img.png
    Files whose destination already exists are skipped silently.
    """
    EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif'}
    image_files = sorted(p for p in source_path.rglob('*') if p.suffix.lower() in EXTS and p.is_file())

    if not image_files:
        yield ('done', 'No image files found')
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    imported = 0
    skipped = 0
    con = sqlite3.connect(db_path)

    try:
        for img_path in image_files:
            rel = img_path.relative_to(source_path)
            parts = rel.parts

            if len(parts) >= 3 and re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]):
                dest_date, root_name = parts[0], parts[1]
            elif len(parts) >= 2:
                dest_date, root_name = date_str, parts[0]
            else:
                dest_date, root_name = date_str, img_path.stem

            dest_folder = Path(managed_root) / dest_date / root_name
            dest_folder.mkdir(parents=True, exist_ok=True)
            dest_path = dest_folder / img_path.name

            if dest_path.exists():
                skipped += 1
                yield ('skip', str(rel))
                continue

            shutil.move(str(img_path), str(dest_path))

            img_uuid = str(uuid.uuid4())
            created_at = _png_created_at(dest_path)
            rel_managed = str(dest_path.relative_to(managed_root))

            sidecar = {
                "uuid": img_uuid,
                "parent_uuid": None,
                "root_uuid": img_uuid,
                "root_name": root_name,
                "created_at": created_at,
                "filename": rel_managed,
            }
            dest_path.with_suffix('.json').write_text(json.dumps(sidecar, indent=2))

            con.execute(
                "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (img_uuid, None, img_uuid, root_name, rel_managed, str(dest_path), created_at)
            )
            con.commit()
            imported += 1
            yield ('ok', str(rel))
    finally:
        con.close()

    yield ('done', f"imported: {imported}, skipped: {skipped}")


def move_chains(root_uuids: list, target_folder: str, db_path: Path, managed_root: Path) -> dict:
    """Reassign root_name for all images in the given chains; rewrite their sidecar JSONs."""
    if not target_folder or not target_folder.strip():
        raise ValueError("target_folder must not be empty")
    target_folder = target_folder.strip()
    con = sqlite3.connect(db_path)
    try:
        moved = 0
        for root_uuid in root_uuids:
            rows = con.execute(
                "SELECT uuid, abs_path FROM images WHERE root_uuid = ?", (root_uuid,)
            ).fetchall()
            for img_uuid, abs_path_str in rows:
                abs_path = Path(abs_path_str)
                sidecar_path = abs_path.with_suffix(".json")
                if sidecar_path.exists():
                    sidecar = json.loads(sidecar_path.read_text())
                    sidecar["root_name"] = target_folder
                    sidecar_path.write_text(json.dumps(sidecar, indent=2))
            con.execute(
                "UPDATE images SET root_name = ? WHERE root_uuid = ?",
                (target_folder, root_uuid),
            )
            moved += len(rows)
        con.commit()
    finally:
        con.close()
    return {"moved": moved, "target_folder": target_folder}


def delete_images(uuids: list, db_path: Path) -> dict:
    """Delete images by UUID: removes files, sidecars, DB records. Orphans direct children."""
    con = sqlite3.connect(db_path)
    deleted = 0
    try:
        for img_uuid in uuids:
            row = con.execute(
                "SELECT abs_path, root_name FROM images WHERE uuid = ?", (img_uuid,)
            ).fetchone()
            if not row:
                continue
            abs_path = Path(row[0])
            sidecar = abs_path.with_suffix(".json")
            # Orphan direct children: make each its own root and cascade to their subtrees
            children = con.execute(
                "SELECT uuid, abs_path FROM images WHERE parent_uuid = ?", (img_uuid,)
            ).fetchall()
            for child_uuid, child_abs in children:
                # Cascade new root_uuid down through child's subtree via BFS
                to_update = [child_uuid]
                queue = [child_uuid]
                while queue:
                    cur = queue.pop()
                    desc = con.execute(
                        "SELECT uuid FROM images WHERE parent_uuid = ?", (cur,)
                    ).fetchall()
                    for (did,) in desc:
                        to_update.append(did)
                        queue.append(did)
                con.execute(
                    "UPDATE images SET parent_uuid = NULL, root_uuid = ? WHERE uuid = ?",
                    (child_uuid, child_uuid)
                )
                for desc_uuid in to_update[1:]:
                    con.execute(
                        "UPDATE images SET root_uuid = ? WHERE uuid = ?",
                        (child_uuid, desc_uuid)
                    )
                # Update sidecar for the direct child
                child_sidecar = Path(child_abs).with_suffix(".json")
                if child_sidecar.exists():
                    try:
                        meta = json.loads(child_sidecar.read_text())
                        meta["parent_uuid"] = None
                        meta["root_uuid"] = child_uuid
                        child_sidecar.write_text(json.dumps(meta, indent=2))
                    except Exception:
                        pass
            con.execute("DELETE FROM images WHERE uuid = ?", (img_uuid,))
            if abs_path.exists():
                abs_path.unlink()
            if sidecar.exists():
                sidecar.unlink()
            deleted += 1
        con.commit()
    finally:
        con.close()
    return {"deleted": deleted}


def rename_folder(old_name: str, new_name: str, db_path: Path, managed_root: Path) -> dict:
    """Rename all images with root_name=old_name to new_name. Updates DB and sidecar JSONs.
    Raises ValueError if new_name is empty, equals old_name, or already in use."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("new_name must not be empty")
    if new_name == old_name:
        raise ValueError("new_name must differ from old_name")

    con = sqlite3.connect(db_path)
    try:
        # Check old_name exists
        count = con.execute(
            "SELECT COUNT(*) FROM images WHERE root_name = ?", (old_name,)
        ).fetchone()[0]
        if count == 0:
            raise ValueError(f"folder {old_name!r} not found")

        # Check new_name is not already in use
        conflict = con.execute(
            "SELECT COUNT(*) FROM images WHERE root_name = ?", (new_name,)
        ).fetchone()[0]
        if conflict > 0:
            raise ValueError(f"folder {new_name!r} already exists")

        rows = con.execute(
            "SELECT uuid, abs_path FROM images WHERE root_name = ?", (old_name,)
        ).fetchall()

        # Rewrite sidecars first; roll back DB on any failure
        updated_sidecars = []
        try:
            for img_uuid, abs_path_str in rows:
                sidecar_path = Path(abs_path_str).with_suffix(".json")
                if sidecar_path.exists():
                    meta = json.loads(sidecar_path.read_text())
                    meta["root_name"] = new_name
                    sidecar_path.write_text(json.dumps(meta, indent=2))
                    updated_sidecars.append((sidecar_path, old_name))
        except Exception as e:
            # Roll back written sidecars
            for sidecar_path, orig_name in updated_sidecars:
                try:
                    meta = json.loads(sidecar_path.read_text())
                    meta["root_name"] = orig_name
                    sidecar_path.write_text(json.dumps(meta, indent=2))
                except Exception:
                    pass
            raise ValueError(f"sidecar write failed, rolled back: {e}") from e

        con.execute(
            "UPDATE images SET root_name = ? WHERE root_name = ?", (new_name, old_name)
        )
        con.commit()
    finally:
        con.close()

    return {"renamed": count, "old_name": old_name, "new_name": new_name}


def delete_folder(root_name: str, db_path: Path, dry_run: bool = False) -> dict:
    """Delete all images with root_name. With dry_run=True, returns count without deleting.
    Raises ValueError if folder not found."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT uuid, abs_path FROM images WHERE root_name = ?", (root_name,)
        ).fetchall()
        if not rows:
            raise ValueError(f"folder {root_name!r} not found")
        count = len(rows)
        if dry_run:
            return {"count": count, "dry_run": True}

        for img_uuid, abs_path_str in rows:
            abs_path = Path(abs_path_str)
            sidecar = abs_path.with_suffix(".json")
            con.execute("DELETE FROM images WHERE uuid = ?", (img_uuid,))
            if abs_path.exists():
                abs_path.unlink()
            if sidecar.exists():
                sidecar.unlink()
        con.commit()
    finally:
        con.close()

    return {"deleted": count, "root_name": root_name}


def load_image(filename: str, managed_root: Path):
    abs_path = Path(managed_root) / filename
    pil_img = Image.open(abs_path).convert("RGBA")
    arr = np.array(pil_img).astype(np.float32) / 255.0
    image = torch.from_numpy(arr[:, :, :3]).unsqueeze(0)
    mask = torch.from_numpy(1.0 - arr[:, :, 3]).unsqueeze(0)
    return image, mask
