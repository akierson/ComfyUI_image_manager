import json
from pathlib import Path

import folder_paths

_PKG_DIR = Path(__file__).parent
_COMFYUI_ROOT = _PKG_DIR.parent.parent

# --- Managed folder config ---

def _read_managed_root() -> Path:
    settings_path = _COMFYUI_ROOT / "user" / "default" / "comfy.settings.json"
    if settings_path.exists():
        try:
            val = json.loads(settings_path.read_text()).get("ImageManager.managed_folder")
            if val:
                return Path(val)
        except Exception:
            pass
    return _COMFYUI_ROOT / "managed_output"

MANAGED_ROOT = _read_managed_root()
MANAGED_ROOT.mkdir(parents=True, exist_ok=True)

DB_PATH = _PKG_DIR / "image_manager.db"

# --- Register managed folder with ComfyUI ---
folder_paths.add_model_folder_path("managed_images", str(MANAGED_ROOT))

# --- Init SQLite schema ---
from .lineage import init_db, scan_and_import, _create_missing_sidecars
init_db(DB_PATH)

# --- Startup scan: create sidecars for bare PNGs, then rebuild DB from all sidecars ---
try:
    _create_missing_sidecars(MANAGED_ROOT)
    scan_and_import(MANAGED_ROOT, DB_PATH)
except Exception as _e:
    print(f"[image_manager] WARNING: startup scan failed: {_e}")

# --- Register web extension ---
WEB_DIRECTORY = "./web"

# --- Mount API routes ---
try:
    from server import PromptServer
    from .api import make_app
    _api_app = make_app(MANAGED_ROOT, DB_PATH)
    for resource in _api_app.router.resources():
        for route in resource:
            PromptServer.instance.app.router.add_route(route.method, resource.canonical, route.handler)
except Exception as _e:
    print(f"[image_manager] WARNING: could not mount API routes: {_e}")

# --- Node exports ---
from .nodes import ManagedSaveImage, ManagedLoadImage

NODE_CLASS_MAPPINGS = {
    "ManagedSaveImage": ManagedSaveImage,
    "ManagedLoadImage": ManagedLoadImage,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ManagedSaveImage": "Save Image (Managed)",
    "ManagedLoadImage": "Load Image (Managed)",
}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
