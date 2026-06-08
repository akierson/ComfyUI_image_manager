import pytest
from workflow_parser import parse_workflow


STANDARD_KSAMPLER = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a beautiful portrait", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality", "clip": ["4", 1]}},
    "3": {"class_type": "KSampler", "inputs": {
        "seed": 12345, "steps": 20, "cfg": 7.5,
        "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0,
        "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
        "latent_image": ["5", 0],
    }},
}

STANDARD_WITH_LORAS = {
    **STANDARD_KSAMPLER,
    "10": {"class_type": "LoraLoader", "inputs": {
        "lora_name": "detail.safetensors", "strength_model": 0.75, "strength_clip": 0.75,
        "model": ["4", 0], "clip": ["4", 1],
    }},
    "11": {"class_type": "LoraLoader", "inputs": {
        "lora_name": "style.safetensors", "strength_model": 0.5, "strength_clip": 0.5,
        "model": ["10", 0], "clip": ["10", 1],
    }},
}

FLUX_PROMPT = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux-dev.safetensors"}},
    "6": {"class_type": "CLIPTextEncodeFlux", "inputs": {
        "clip_l": "a scenic landscape", "t5xxl": "a scenic mountain landscape at dawn",
        "guidance": 3.5, "clip": ["4", 1],
    }},
    "7": {"class_type": "CLIPTextEncodeFlux", "inputs": {
        "clip_l": "", "t5xxl": "", "guidance": 3.5, "clip": ["4", 1],
    }},
    "3": {"class_type": "KSampler", "inputs": {
        "seed": 42, "steps": 28, "cfg": 1.0,
        "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
        "positive": ["6", 0], "negative": ["7", 0],
    }},
}


def test_standard_ksampler_extracts_checkpoint():
    result = parse_workflow(STANDARD_KSAMPLER)
    assert result["checkpoint"] == "v1-5.safetensors"


def test_standard_ksampler_extracts_prompts():
    result = parse_workflow(STANDARD_KSAMPLER)
    assert result["positive_prompt"] == "a beautiful portrait"
    assert result["negative_prompt"] == "blurry, low quality"


def test_standard_ksampler_extracts_sampler_params():
    result = parse_workflow(STANDARD_KSAMPLER)
    assert result["steps"] == 20
    assert result["cfg"] == 7.5
    assert result["sampler"] == "euler"
    assert result["scheduler"] == "karras"
    assert result["seed"] == 12345
    assert result["denoise"] == 1.0


def test_standard_ksampler_no_loras_omits_field():
    result = parse_workflow(STANDARD_KSAMPLER)
    assert "loras" not in result


def test_loras_extracted_with_name_and_strength():
    result = parse_workflow(STANDARD_WITH_LORAS)
    loras = result["loras"]
    assert len(loras) == 2
    names = {l["name"] for l in loras}
    assert names == {"detail.safetensors", "style.safetensors"}
    by_name = {l["name"]: l for l in loras}
    assert by_name["detail.safetensors"]["strength"] == 0.75
    assert by_name["style.safetensors"]["strength"] == 0.5


def test_flux_extracts_positive_prompt_from_t5xxl():
    result = parse_workflow(FLUX_PROMPT)
    assert result["positive_prompt"] == "a scenic mountain landscape at dawn"


def test_flux_checkpoint_extracted():
    result = parse_workflow(FLUX_PROMPT)
    assert result["checkpoint"] == "flux-dev.safetensors"


def test_missing_ksampler_leaves_params_none():
    prompt = {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}}}
    result = parse_workflow(prompt)
    assert result["steps"] is None
    assert result["sampler"] is None
    assert result["positive_prompt"] is None


def test_missing_checkpoint_leaves_field_none():
    result = parse_workflow({"3": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 10, "cfg": 7.0,
        "sampler_name": "dpm", "scheduler": "normal", "denoise": 0.8,
    }}})
    assert result["checkpoint"] is None
    assert result["steps"] == 10


def test_empty_prompt_returns_has_workflow_true_with_nulls():
    # An empty prompt dict means the prompt was stored but has no nodes — still has_workflow: True
    # (caller sets has_workflow based on chunk presence, not parser output)
    result = parse_workflow({})
    assert result["has_workflow"] is True
    assert result["checkpoint"] is None
