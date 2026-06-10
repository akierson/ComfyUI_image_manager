import hashlib
import folder_paths
from pathlib import Path

try:
    from .lineage import save_image, load_image, import_from_input
    from . import DB_PATH, MANAGED_ROOT
except ImportError:
    from lineage import save_image, load_image, import_from_input
    DB_PATH = None
    MANAGED_ROOT = None


class ManagedLoadImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (folder_paths.get_filename_list("managed_images"),),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "managed_name")
    FUNCTION = "load"
    CATEGORY = "image_manager"
    DESCRIPTION = "Load a managed image by lineage path."

    def load(self, image):
        img, mask = load_image(image, MANAGED_ROOT)
        preview = {
            "filename": Path(image).name,
            "subfolder": str(Path(image).parent),
            "type": "managed",
        }
        return {"ui": {"images": [preview]}, "result": (img, mask, image)}


class ManagedLoadImageFromInput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (folder_paths.get_filename_list("input"),),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "managed_name")
    FUNCTION = "load"
    CATEGORY = "image_manager"
    DESCRIPTION = "Load an image from input/ and track it as a managed image."

    @classmethod
    def IS_CHANGED(cls, image):
        input_dir = Path(folder_paths.get_input_directory())
        src = input_dir / image
        if not src.exists():
            return float("nan")
        return hashlib.sha256(src.read_bytes()).hexdigest()

    def load(self, image):
        input_dir = Path(folder_paths.get_input_directory())
        src = input_dir / image
        result = import_from_input(src, MANAGED_ROOT, DB_PATH)
        managed_name = result["filename"]
        img, mask = load_image(managed_name, MANAGED_ROOT)
        preview = {
            "filename": Path(managed_name).name,
            "subfolder": str(Path(managed_name).parent),
            "type": "managed",
        }
        return {"ui": {"images": [preview]}, "result": (img, mask, managed_name)}


class ManagedSaveImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "image"}),
            },
            "optional": {
                "parent_name": ("STRING", {"forceInput": True}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "image_manager"
    DESCRIPTION = "Save images with lineage tracking."

    def save(self, images, filename_prefix, parent_name=None, prompt=None, extra_pnginfo=None, unique_id=None):
        prompt = prompt or {}
        results = []
        for i in range(images.shape[0]):
            r = save_image(images[i:i+1], filename_prefix, prompt, MANAGED_ROOT, DB_PATH,
                           extra_pnginfo=extra_pnginfo, parent_name=parent_name)
            results.append(r)
        ui_images = [
            {
                "filename": Path(r["filename"]).name,
                "subfolder": str(Path(r["filename"]).parent),
                "type": "managed",
                "uuid": r["uuid"],
            }
            for r in results
        ]
        return {"ui": {"images": ui_images}}
