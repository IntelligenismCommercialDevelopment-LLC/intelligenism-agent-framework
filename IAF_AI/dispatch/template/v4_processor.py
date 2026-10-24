"""
V4 Processor — Shared service to transform raw text into V4-structured markdown.

This module is NOT an agent. It is a single-call LLM function.
It is used by multiple scenarios (search results, context compaction,
session index generation, memory consolidation) by varying the rules_file.

Public API:
    process(raw_text, rules_file, provider, model) -> str
"""

import os
import sys
import time

_DISPATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_DISPATCH_DIR))


# Make lib/ importable for llm_client
_LIB_DIR = os.path.join(_PROJECT_ROOT, "lib")   # 本模板自带的 lib（为什么不上溯：见 dispatch_base.get_llm_caller）
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from dispatch_base import load_global_config, resolve_llm_endpoint


def _resolve_rules_path(rules_file):
    """Rules file path can be relative to dispatch dir or absolute."""
    if os.path.isabs(rules_file):
        return rules_file
    return os.path.join(_DISPATCH_DIR, rules_file)


def _load_rules(rules_file):
    path = _resolve_rules_path(rules_file)
    if not os.path.exists(path):
        return f"(V4 rules file not found: {rules_file})"
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def process(raw_text, rules_file, provider="", model="", temperature=None, top_p=None):
    """
    Transform raw_text into V4-structured markdown.

    Returns V4 text on success, or a string starting with
    "[V4 Processor Error]" on failure (caller must handle).
    """
    if not raw_text or not raw_text.strip():
        return "[V4 Processor Error] Empty input"

    # --- Load global LLM config ---
    try:
        global_config = load_global_config(_PROJECT_ROOT)
    except Exception as e:
        return f"[V4 Processor Error] Cannot load config: {e}"

    # Resolve provider/model — fall back to defaults if empty
    if not provider:
        provider = global_config.get("default_provider", "")
    if not model:
        model = global_config.get("default_model", "")

    url, key = resolve_llm_endpoint(provider, global_config)
    if not url or not key:
        return f"[V4 Processor Error] No endpoint for provider={provider}"

    # --- Load rules as system prompt ---
    system_prompt = _load_rules(rules_file)

    # --- Build messages ---
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": raw_text},
    ]

    # --- Single LLM call ---
    try:
        from llm_client import call_llm
    except ImportError as e:
        return f"[V4 Processor Error] llm_client not importable: {e}"

    t0 = time.time()
    try:
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        response = call_llm(url, key, model, messages, **kwargs)
    except Exception as e:
        return f"[V4 Processor Error] LLM call failed: {e}"

    duration_ms = int((time.time() - t0) * 1000)

    if isinstance(response, dict):
        text = response.get("content", "") or ""
    elif isinstance(response, str):
        text = response
    else:
        text = str(response)

    text = text.strip()
    if not text:
        return "[V4 Processor Error] LLM returned empty content"

    # Prepend a meta comment for traceability (harmless for the LLM
    # that reads this in the next tool loop)
    header = f"<!-- v4_processor: provider={provider} model={model} duration_ms={duration_ms} -->\n"
    return header + text
