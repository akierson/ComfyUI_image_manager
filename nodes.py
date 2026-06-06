import folder_paths
from pathlib import Path
from .lineage import save_image, load_image
from . import DB_PATH, MANAGED_ROOT


class ManagedLoadImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (folder_paths.get_filename_list("managed_images"),),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "load"
    CATEGORY = "image_manager"
    DESCRIPTION = "Load a managed image by lineage path."

    def load(self, image):
        return load_image(image, MANAGED_ROOT)


class ManagedSaveImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "image"}),
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

    def save(self, images, filename_prefix, prompt=None, extra_pnginfo=None, unique_id=None):
        prompt = prompt or {}
        results = []
        for i in range(images.shape[0]):
            r = save_image(images[i:i+1], filename_prefix, prompt, MANAGED_ROOT, DB_PATH,
                           extra_pnginfo=extra_pnginfo)
            results.append(r)
        return {"ui": {"images": [{"filename": r["filename"], "uuid": r["uuid"]} for r in results]}}
