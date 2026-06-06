import sys
import types
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import clustering


# --- detect_backend ---

def test_detect_backend_returns_histogram_when_no_clip(monkeypatch):
    monkeypatch.setitem(sys.modules, "open_clip", None)
    monkeypatch.setitem(sys.modules, "transformers", None)
    import importlib
    importlib.reload(clustering)
    assert clustering.detect_backend() == "histogram"


# --- embed_images (histogram) ---

@pytest.fixture
def small_images(tmp_path):
    paths = []
    for i in range(3):
        img = Image.fromarray(
            np.random.randint(0, 255, (32, 32, 3), dtype=np.uint8)
        )
        p = tmp_path / f"img_{i}.png"
        img.save(p)
        paths.append(str(p))
    return paths


def test_histogram_embed_shape(small_images):
    embeddings = clustering.embed_images(small_images, backend="histogram")
    assert embeddings.shape == (3, 192)


def test_histogram_embed_unit_norm(small_images):
    embeddings = clustering.embed_images(small_images, backend="histogram")
    norms = np.linalg.norm(embeddings, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-6)


# --- auto_cluster ---

def test_auto_cluster_returns_label_per_image(small_images):
    embeddings = clustering.embed_images(small_images, backend="histogram")
    labels = clustering.auto_cluster(embeddings)
    assert len(labels) == len(small_images)
    assert all(isinstance(l, (int, np.integer)) for l in labels)


def test_auto_cluster_empty_input():
    labels = clustering.auto_cluster(np.empty((0, 192)))
    assert labels == []
