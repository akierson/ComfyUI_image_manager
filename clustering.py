import sys
import importlib

import numpy as np


def detect_backend() -> str:
    for name in ("open_clip", "transformers"):
        mod = sys.modules.get(name, ...)
        if mod is ...:
            try:
                importlib.import_module(name)
                return "open_clip" if name == "open_clip" else "transformers"
            except ImportError:
                pass
        elif mod is not None:
            return "open_clip" if name == "open_clip" else "transformers"
    return "histogram"


def embed_images(abs_paths: list, backend: str = None) -> np.ndarray:
    from PIL import Image

    if backend is None:
        backend = detect_backend()

    if backend == "histogram":
        rows = []
        for path in abs_paths:
            img = Image.open(path).convert("RGB")
            hist = []
            for ch in img.split():
                h = ch.histogram()[:256]
                bins = [sum(h[i * 4 : (i + 1) * 4]) for i in range(64)]
                hist.extend(bins)
            arr = np.array(hist, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr /= norm
            rows.append(arr)
        return np.stack(rows)

    if backend == "open_clip":
        import torch
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        model.eval()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device)

        rows = []
        with torch.no_grad():
            for path in abs_paths:
                img = (
                    preprocess(Image.open(path).convert("RGB")).unsqueeze(0).to(device)
                )
                feat = model.encode_image(img)
                feat = feat / feat.norm(dim=-1, keepdim=True)
                rows.append(feat.squeeze(0).cpu().float().numpy())
        return np.stack(rows)

    if backend == "transformers":
        import torch
        from transformers import CLIPProcessor, CLIPModel

        model_id = "openai/clip-vit-base-patch32"
        model = CLIPModel.from_pretrained(model_id)
        processor = CLIPProcessor.from_pretrained(model_id)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device)
        model.eval()

        rows = []
        with torch.no_grad():
            for path in abs_paths:
                img = Image.open(path).convert("RGB")
                inputs = processor(images=img, return_tensors="pt")
                inputs = {k: v.to(device) for k, v in inputs.items()}
                feat = model.get_image_features(**inputs)
                feat = feat / feat.norm(dim=-1, keepdim=True)
                rows.append(feat.squeeze(0).cpu().float().numpy())
        return np.stack(rows)

    raise ValueError(f"Unknown backend: {backend}")


def auto_cluster(embeddings: np.ndarray, k_scale: float = 1.0) -> list:
    import math
    from sklearn.cluster import KMeans

    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]

    # Silhouette scoring fails for CLIP embeddings in high dimensions — it consistently
    # picks k=2 regardless of semantic content. Scale k with image count instead.
    k = max(2, min(n / 3, round(math.sqrt(n / 2) * k_scale)))
    k = min(k, n)

    km = KMeans(n_clusters=k, n_init="auto", random_state=0)
    labels = km.fit_predict(embeddings)
    return [int(l) for l in labels]
