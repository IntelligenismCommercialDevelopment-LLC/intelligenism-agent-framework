"""
Agent engine core — the Fundamental Loop.
Each agent owns an independent copy of this file.
Imports shared infrastructure from lib/, but all agent behavior lives here.
"""

import json
import os
import sys
import time
import importlib
import importlib.util
import glob

# === Paths ===
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMEWORK_ROOT = os.path.dirname(os.path.dirname(AGENT_DIR))

# Add framework root to path so we can import lib/
if FRAMEWORK_ROOT not in sys.path:
    sys.path.insert(0, FRAMEWORK_ROOT)

# === Shared infrastructure (from lib/) ===
from lib.llm_client import call_llm, LLMError, ContextTooLongError
from lib.token_utils import estimate_tokens

# === This agent's executor ===
from core.tool_executor import execute, get_tools_schema

# === Agent-local config ===
AGENT_CONFIG_PATH = os.path.join(AGENT_DIR, "agent_config.json")
GLOBAL_CONFIG_PATH = os.path.join(FRAMEWORK_ROOT, "config.json")
HISTORY_PATH = os.path.join(AGENT_DIR, "history.jsonl")
CONTEXT_DIR = os.path.join(AGENT_DIR, "context")


def _load_config():
    """Load agent config and merge with global config for provider info."""
    with open(GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as f:
        global_cfg = json.load(f)
    with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        agent_cfg = json.load(f)

    provider_name = agent_cfg.get("provider", global_cfg.get("default_provider"))
    provider = global_cfg["providers"][provider_name]

    # context_files: if not specified, fall back to SOUL.md for backward compatibility
    context_files = agent_cfg.get("context_files", None)
    if context_files is None:
        soul_path = os.path.join(AGENT_DIR, "SOUL.md")
        if os.path.exists(soul_path):
            context_files = ["SOUL.md"]
        else:
            context_files = []

    return {
        "url": provider["url"],
        "key": provider["key"],
        "model": agent_cfg.get("model", global_cfg.get("default_model")),
        "max_context": agent_cfg.get("max_context", 120000),
        "trim_strategy": agent_cfg.get("trim_strategy", "sliding_window"),
        "context_files": context_files,
        "skills": agent_cfg.get("skills", []),
        "display_name": agent_cfg.get("display_name", "Agent"),
    }


# ==============================
# Context trimming (auto-discover)
# ==============================

_TRIM_STRATEGIES = {}


def _discover_trim_strategies():
    """Scan context/ directory for trim strategy modules."""
    _TRIM_STRATEGIES.clear()
    for filepath in sorted(glob.glob(os.path.join(CONTEXT_DIR, "*.py"))):
        basename = os.path.basename(filepath)
        if basename.startswith("__"):
            continue
        name = basename[:-3]  # "sliding_window"
        spec = importlib.util.spec_from_file_location(name, filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "trim"):
            _TRIM_STRATEGIES[name] = module.trim


_discover_trim_strategies()


def _get_trim_func(strategy_name):
    """Get trim function by strategy name. Falls back to first available."""
    if strategy_name in _TRIM_STRATEGIES:
        return _TRIM_STRATEGIES[strategy_name]
    if _TRIM_STRATEGIES:
        return list(_TRIM_STRATEGIES.values())[0]
    # No strategies found — return identity function
    return lambda messages, budget: messages


# ==============================
# Context files loading
# ==============================

def _load_context_files(context_files):
    """
    Read all context files and concatenate into a single system prompt.

    Paths can be:
      - Relative to AGENT_DIR (e.g. "SOUL.md", "skills/review.md")
      - Relative to FRAMEWORK_ROOT (e.g. "agents/default/SOUL.md", "dispatch/roundtable/rules/default.md")
      - Absolute paths

    Resolution order: agent-relative first, then framework-relative, then absolute.
    """
    parts = []
    for path in context_files:
        resolved = _resolve_path(path)
        if resolved and os.path.exists(resolved):
            with open(resolved, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    parts.append(content)

    if parts:
        return "\n\n---\n\n".join(parts)
    return "You are a helpful assistant."


def _resolve_path(path):
    """Resolve a context file path. Try agent-relative, then framework-relative, then absolute."""
    if os.path.isabs(path):
        return path

    # Try relative to agent directory first
    agent_rel = os.path.join(AGENT_DIR, path)
    if os.path.exists(agent_rel):
        return agent_rel

    # Try relative to framework root
    framework_rel = os.path.join(FRAMEWORK_ROOT, path)
    if os.path.exists(framework_rel):
        return framework_rel

    return None


# ==============================
# Skill injection
# ==============================

def _match_skills(message, skill_configs):
    """Match message against skill trigger rules. Returns list of skill contents."""
    matched = []
    for skill in skill_configs:
        trigger = skill.get("trigger", "")
        match_type = skill.get("match_type", "contains")
        skill_file = skill.get("skill_file", "")

        hit = False
        if match_type == "contains" and trigger in message:
            hit = True
        elif match_type == "startswith" and message.startswith(trigger):
            hit = True
        elif match_type == "exact" and message == trigger:
            hit = True

        if hit:
            resolved = _resolve_path(skill_file)
            if resolved and os.path.exists(resolved):
                with open(resolved, "r", encoding="utf-8") as f:
                    matched.append(f.read().strip())

    return matched


# ==============================
# History management
# ==============================

def save_history(user_msg, reply):
    """Append one conversation turn to history.jsonl."""
    entry = {
        "user_message": user_msg,
        "reply": reply,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_history(max_turns=20):
    """Read recent history as user/assistant message pairs."""
    if not os.path.exists(HISTORY_PATH):
        return []
    messages = []
    turns = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                turns.append(json.loads(line))

    for turn in turns[-max_turns:]:
        messages.append({"role": "user", "content": turn["user_message"]})
        messages.append({"role": "assistant", "content": turn["reply"]})
    return messages


def clear_history():
    """Clear this agent's history."""
    if os.path.exists(HISTORY_PATH):
        os.remove(HISTORY_PATH)


# ==============================
# Core: build_messages
# ==============================

def build_messages(message, config, mode="chat"):
    """
    Construct the messages array sent to the LLM.
    Five layers: context files → skill injection → history → current input → trim.
    """
    messages = []

    # Layer 1: System prompt from context_files (configurable in agent_config.json)
    system_prompt = _load_context_files(config["context_files"])
    messages.append({"role": "system", "content": system_prompt})

    # Layer 2: Skill injection (if any trigger matches)
    skill_contents = _match_skills(message, config["skills"])
    for content in skill_contents:
        messages.append({"role": "user", "content": f"please follow the instructions below:\n\n{content}"})
        messages.append({"role": "assistant", "content": "Understood, I will execute the instructions."})

    # Layer 3: History (chat mode only)
    if mode == "chat":
        messages.extend(get_history())

    # Layer 4: Current input
    messages.append({"role": "user", "content": message})

    # Layer 5: Trim to budget
    budget = config["max_context"] - 8000  # Reserve for response
    trim_func = _get_trim_func(config["trim_strategy"])
    messages = trim_func(messages, budget)

    return messages


# ==============================
# Main entry: call_agent
# ==============================

def call_agent(message, mode="chat", max_loops=10):
    """
    The single public entry point.
    Chat UI and dispatch both call this function.
    """
    config = _load_config()
    tools_schema = get_tools_schema()

    # Build context
    messages = build_messages(message, config, mode)

    for i in range(max_loops):
        print(f"  [{config['display_name']}][Loop {i+1}] Calling LLM...")
        response = call_llm(
            url=config["url"],
            key=config["key"],
            model=config["model"],
            messages=messages,
            tools=tools_schema
        )

        # Case 1: text reply — done
        if response.get("content") and not response.get("tool_calls"):
            print(f"  [{config['display_name']}][Loop {i+1}] Got final text reply")
            reply = response["content"]
            if mode == "chat":
                save_history(message, reply)
            return reply

        # Case 2: tool call — execute and continue
        if response.get("tool_calls"):
            messages.append(response)
            for tc in response["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_args = json.loads(tc["function"]["arguments"])
                print(f"  [{config['display_name']}][Loop {i+1}] Tool: {tool_name}({tool_args})")
                result = execute(tool_name, tool_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result)
                })
            continue

        # Case 3: unexpected
        return response.get("content", "[empty response]")

    return "[max loops reached]"
