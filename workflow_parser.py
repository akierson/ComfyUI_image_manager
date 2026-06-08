"""Parse a ComfyUI API prompt dict (stored in the PNG 'prompt' text chunk) into flat metadata fields."""


def parse_workflow(prompt: dict) -> dict:
    """
    Returns a flat dict of generation metadata extracted from a ComfyUI API prompt.
    Omits keys whose values cannot be determined (e.g. 'loras' when none are present).
    """
    result = {
        "has_workflow": True,
        "checkpoint": None,
        "positive_prompt": None,
        "negative_prompt": None,
        "steps": None,
        "cfg": None,
        "sampler": None,
        "scheduler": None,
        "seed": None,
        "denoise": None,
    }

    def find_nodes(*class_types):
        return [v for v in prompt.values() if isinstance(v, dict) and v.get("class_type") in class_types]

    def resolve(node_ref):
        """Follow a [node_id, slot] reference and return the referenced node dict."""
        if isinstance(node_ref, list) and len(node_ref) == 2:
            return prompt.get(str(node_ref[0]))
        return None

    # Checkpoint
    ckpt_nodes = find_nodes("CheckpointLoaderSimple", "CheckpointLoader")
    if ckpt_nodes:
        result["checkpoint"] = ckpt_nodes[0].get("inputs", {}).get("ckpt_name")

    # KSampler — extract params and follow positive/negative references
    ksampler_nodes = find_nodes("KSampler", "KSamplerAdvanced")
    if ksampler_nodes:
        inputs = ksampler_nodes[0].get("inputs", {})
        result["steps"] = inputs.get("steps")
        result["cfg"] = inputs.get("cfg")
        result["sampler"] = inputs.get("sampler_name")
        result["scheduler"] = inputs.get("scheduler")
        result["seed"] = inputs.get("seed")
        result["denoise"] = inputs.get("denoise")

        pos_node = resolve(inputs.get("positive"))
        neg_node = resolve(inputs.get("negative"))

        if pos_node:
            ct = pos_node.get("class_type", "")
            pos_inputs = pos_node.get("inputs", {})
            if ct == "CLIPTextEncodeFlux":
                result["positive_prompt"] = pos_inputs.get("t5xxl") or pos_inputs.get("clip_l")
            else:
                result["positive_prompt"] = pos_inputs.get("text")

        if neg_node:
            ct = neg_node.get("class_type", "")
            neg_inputs = neg_node.get("inputs", {})
            if ct == "CLIPTextEncodeFlux":
                result["negative_prompt"] = neg_inputs.get("t5xxl") or neg_inputs.get("clip_l")
            else:
                result["negative_prompt"] = neg_inputs.get("text")

    # LoRAs — only included in result when at least one is present
    lora_nodes = find_nodes("LoraLoader", "LoraLoaderModelOnly")
    if lora_nodes:
        loras = []
        for node in lora_nodes:
            inp = node.get("inputs", {})
            name = inp.get("lora_name")
            strength = inp.get("strength_model", inp.get("strength", 1.0))
            if name:
                loras.append({"name": name, "strength": strength})
        if loras:
            result["loras"] = loras

    return result
