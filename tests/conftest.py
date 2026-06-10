import sys
from pathlib import Path

# Package dir must be first so local nodes.py wins over ComfyUI's nodes.py
_pkg_dir = Path(__file__).parent.parent
if str(_pkg_dir) not in sys.path:
    sys.path.insert(0, str(_pkg_dir))

# Add ComfyUI root so `folder_paths` is importable in tests
_comfyui_root = _pkg_dir.parent.parent
if str(_comfyui_root) not in sys.path:
    sys.path.append(str(_comfyui_root))
