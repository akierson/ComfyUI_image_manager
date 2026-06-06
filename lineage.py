import json
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
    now = datetime.now(timezone.utc).isoformat()
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

    sidecar = {
        "uuid": img_uuid,
        "parent_uuid": None,
        "root_uuid": img_uuid,
        "root_name": root_name,
        "created_at": now,
        "filename": rel_path,
    }
    abs_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (img_uuid, None, img_uuid, root_name, rel_path, str(abs_path), now)
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
            # Derive date and root_name from folder structure
            if len(parts) >= 3:
                date_candidate = parts[0]
                try:
                    datetime.strptime(date_candidate, "%Y-%m-%d")
                    created_at = datetime.strptime(date_candidate, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc).isoformat()
                except ValueError:
                    created_at = datetime.now(timezone.utc).isoformat()
                root_name = parts[1]
            elif len(parts) == 2:
                root_name = parts[0]
                created_at = datetime.now(timezone.utc).isoformat()
            else:
                root_name = png_path.stem
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
    con.execute("DELETE FROM images")
    con.commit()
    for sidecar_path in Path(managed_root).rglob("*.json"):
        try:
            meta = json.loads(sidecar_path.read_text())
            abs_png = sidecar_path.with_suffix(".png")
            if not abs_png.exists():
                continue
            con.execute(
                "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (meta["uuid"], meta.get("parent_uuid"), meta["root_uuid"],
                 meta["root_name"], meta["filename"], str(abs_png), meta["created_at"])
            )
            count += 1
        except Exception as e:
            print(f"[image_manager] scan_and_import: skipping {sidecar_path}: {e}")
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
            now = datetime.now(timezone.utc).isoformat()
            rel_managed = str(dest_path.relative_to(managed_root))

            sidecar = {
                "uuid": img_uuid,
                "parent_uuid": None,
                "root_uuid": img_uuid,
                "root_name": root_name,
                "created_at": now,
                "filename": rel_managed,
            }
            dest_path.with_suffix('.json').write_text(json.dumps(sidecar, indent=2))

            con.execute(
                "INSERT OR REPLACE INTO images (uuid, parent_uuid, root_uuid, root_name, filename, abs_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (img_uuid, None, img_uuid, root_name, rel_managed, str(dest_path), now)
            )
            con.commit()
            imported += 1
            yield ('ok', str(rel))
    finally:
        con.close()

    yield ('done', f"imported: {imported}, skipped: {skipped}")


def load_image(filename: str, managed_root: Path):
    abs_path = Path(managed_root) / filename
    pil_img = Image.open(abs_path).convert("RGBA")
    arr = np.array(pil_img).astype(np.float32) / 255.0
    image = torch.from_numpy(arr[:, :, :3]).unsqueeze(0)
    mask = torch.from_numpy(1.0 - arr[:, :, 3]).unsqueeze(0)
    return image, mask
