#!/usr/bin/env python3
"""
Generate config.yaml with [1m] variants for Claude Code.

For every model_name X in MAIN_GROUPS, we also emit model_name
"X[1m]" pointing at the same deployments. Claude Code accepts
"X[1m]" as a hint for "use 1M-token context window"; without the
literal model_name registered, the request is rejected with
"model does not exist" even though routing would otherwise work.
"""

# Each entry: (model_name, [(provider/model, env_var_key), ...])
GROUPS = [
    ("gateway-main", [
        ("nvidia_nim/moonshotai/kimi-k3", "NVIDIA_NIM_API_KEY"),
        ("nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b", "NVIDIA_NIM_API_KEY"),
        ("openrouter/z-ai/glm-5.2:free", "OPENROUTER_API_KEY_1"),
        ("openrouter/minimax/minimax-m3:free", "OPENROUTER_API_KEY_1"),
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "OPENROUTER_API_KEY_1"),
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "OPENROUTER_API_KEY_2"),
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "OPENROUTER_API_KEY_3"),
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "OPENROUTER_API_KEY_4"),
        ("openrouter/minimax/minimax-m2.7:free", "OPENROUTER_API_KEY_1"),
        ("openrouter/cohere/north-mini-code:free", "OPENROUTER_API_KEY_1"),
    ] + [
        ("gemini/gemini-2.5-flash", f"GEMINI_API_KEY_{i}") for i in range(1, 11)
    ]),
    ("gateway-fast", [
        ("nvidia_nim/openai/gpt-oss-120b", "NVIDIA_NIM_API_KEY"),
        ("nvidia_nim/openai/gpt-oss-20b", "NVIDIA_NIM_API_KEY"),
        ("nvidia_nim/nvidia/nemotron-3-super-120b-a12b", "NVIDIA_NIM_API_KEY"),
        ("nvidia_nim/nvidia/nemotron-3-nano-30b-a3b", "NVIDIA_NIM_API_KEY"),
        ("groq/openai/gpt-oss-20b", "GROQ_API_KEY_1"),
        ("groq/openai/gpt-oss-20b", "GROQ_API_KEY_2"),
    ]),
]


def gen():
    out = ["model_list:"]
    for name, deps in GROUPS:
        out.append("")
        out.append(f"  ###########################################################################")
        out.append(f"  # {name} (with [1m] variant for Claude Code 1M context hint)")
        out.append(f"  ###########################################################################")
        # Emit the plain name
        for model, key in deps:
            out.append(f"  - model_name: {name}")
            out.append(f"    litellm_params: {{model: {model}, api_key: os.environ/{key}, timeout: 60}}")
        # Emit the [1m] variant pointing at the same deployments
        for model, key in deps:
            out.append(f"  - model_name: {name}[1m]")
            out.append(f"    litellm_params: {{model: {model}, api_key: os.environ/{key}, timeout: 60}}")
    out.append("")
    out.append("router_settings:")
    out.append("  routing_strategy: simple-shuffle")
    out.append("  num_retries: 2")
    out.append("  allowed_fails: 2")
    out.append("  cooldown_time: 60")
    out.append("")
    out.append("litellm_settings:")
    out.append("  drop_params: true")
    out.append("  max_tokens: 8000")
    out.append("  context_window_fallback: true")
    out.append("")
    out.append("general_settings:")
    out.append("  master_key: os.environ/LITELLM_MASTER_KEY")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(gen())
