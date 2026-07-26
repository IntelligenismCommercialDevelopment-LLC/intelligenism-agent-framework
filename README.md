# Intelligenism Agent Framework (IAF)

**AI Operability Edition — v1.1**

IAF is a minimal, file-based framework for building **multi-agent collaboration modes** that are designed to be **operated and extended by AI agents themselves**, not just by humans.

A running IAF instance is just a directory. You create a new collaboration pattern by **copying a folder**, you give an agent new abilities by **dropping a `*_tools.py` file into it**, and you reconfigure the whole system by **editing plain JSON** — no framework code changes required. Every extension point has an explicit contract and a machine-checkable validator, so an LLM can safely read the system, modify it, and verify its own work.

---

## Table of Contents

- [Core Concepts](#core-concepts)
- [Why "AI Operability Edition"](#why-ai-operability-edition)
- [Architecture: the Shell Four-Zone Layout](#architecture-the-shell-four-zone-layout)
- [Repository Layout](#repository-layout)
- [Quick Start](#quick-start)
- [The Dispatch Kernel](#the-dispatch-kernel)
- [Creating a New Dispatch](#creating-a-new-dispatch)
- [Writing Tools](#writing-tools)
- [Configuration Reference](#configuration-reference)
- [AI-Operability Tooling](#ai-operability-tooling)
- [Running as a Server](#running-as-a-server-optional)
- [Design Principles](#design-principles)

---

## Core Concepts

| Term | Meaning |
|------|---------|
| **Dispatch** | A *collaboration mode* — one self-contained pattern of how one or more agents cooperate to do a job (e.g. a chat agent, a script-writer → critic → scorer pipeline, a free-search researcher). Each dispatch is a folder under `IAF_AI/dispatch/`. |
| **Agent** | A single configured LLM persona *inside* a dispatch, defined by an entry in `dispatch_config.json` under `agents` (its provider, model, system context, tools). One dispatch can host several agents. |
| **Tool** | A Python function an agent can call, exported from a `*_tools.py` file via a `TOOLS` dict. Auto-discovered — no registration code. |
| **Shell** | The four-zone project layout (`config_base` / `prompt_base` / `workspace` / `IAF_AI`) that separates *what you edit* from *the engine that runs*. |
| **Engine** | `IAF_AI/` — the runtime. **You never edit it.** It is driven entirely by config and prompts. |

The central idea: **a dispatch is a copyable unit.** To build a new kind of agent collaboration, you copy the `template` dispatch, adjust its config and prompts, and it runs. Dozens or hundreds of specialized dispatches can be spun off the same prototype.

---

## Why "AI Operability Edition"

This edition is tuned so that an **AI agent can operate the framework end-to-end**:

- **Explicit contracts.** [`TOOL_CONTRACT.md`](TOOL_CONTRACT.md) specifies exactly how a new tool file must be written, so an LLM can author one correctly on the first try.
- **Machine-checkable validation.** [`validate.py`](IAF_AI/validate.py) reports `OK` or a precise list of `FAIL` reasons — an agent can self-check after editing.
- **A machine-readable system map.** [`generate_manifest.py`](IAF_AI/generate_manifest.py) emits `MANIFEST.json` describing every dispatch, agent, and tool — an agent can discover the system without reading source.
- **Config/engine separation.** All edits happen in `config_base/` and `prompt_base/` (plain JSON and Markdown). The engine is a black box, so an agent cannot break the runtime by reconfiguring it.
- **Copy-to-extend.** New capability = a copied folder or a dropped-in file, never a patch to core code.

In short: the framework's extension surface is small, declarative, contract-bound, and verifiable — the properties an autonomous agent needs to modify a system safely.

---

## Architecture: the Shell Four-Zone Layout

An IAF instance separates configuration, prompts, outputs, and the engine into four zones:

```
{iaf_instance}/
├── chain.py                 # Headless run entry (sync → dispatch → workspace)
├── sync_config.py           # Propagates config_base → engine internals
│
├── config_base/             # ZONE 1 — CONFIG (source of truth; humans/agents edit here)
│   ├── config.json          #   Global provider & service credentials
│   └── {dispatch}/
│       └── dispatch_config.json
│
├── prompt_base/             # ZONE 2 — PROMPTS (the only home for prompt/rule files)
│   └── {dispatch}/
│       └── rules/*.md
│
├── workspace/               # ZONE 3 — OUTPUTS (run artifacts land here)
│   └── {dispatch}/
│
└── IAF_AI/                  # ZONE 4 — ENGINE (runtime; never edited by hand)
    ├── dispatch/{dispatch}/ #   Per-dispatch kernel + its generated config
    ├── lib/                 #   llm_client, token_utils
    ├── validate.py          #   Contract validator
    └── generate_manifest.py #   System-map generator
```

**The flow of truth is one-directional:**

```
   config_base/  ──sync_config.py──►  IAF_AI/dispatch/{name}/dispatch_config.json
   config_base/config.json ─────────►  IAF_AI/config.json
   prompt_base/  ──(referenced directly, no sync)──►  read at runtime by the engine
```

Four rules make this work:

1. **`config_base/` is the source of truth.** Humans and agents only ever edit files here.
2. **`prompt_base/` is the single home for prompts.** A dispatch's `context_files` point straight at it; editing a prompt takes effect immediately, **no sync needed**.
3. **Engine-internal config is generated.** `IAF_AI/.../dispatch_config.json` and `IAF_AI/config.json` are written by `sync_config.py`. Never hand-edit them — they get overwritten.
4. **The engine is never modified.** `dispatch.py`, `dispatch_base.py`, and tool code stay untouched across every specialization.

### Path translation

`sync_config.py` translates the `context_files` paths in `config_base/{dispatch}/dispatch_config.json` from **shell-root-relative** (convenient to write) into **engine-dispatch-relative** (what the runtime needs). Because a dispatch directory (`IAF_AI/dispatch/{name}/`) sits three levels below the shell root, the translation prepends `../../../`:

| Path form in `config_base` | Example | Sync behavior |
|----------------------------|---------|---------------|
| Absolute | `/abs/path/to/wiki/soul.md` | Left unchanged |
| Prefixed | `wiki:...`, `session:...`, `briefs:...` | Left unchanged (resolved at runtime) |
| Shell-root-relative | `prompt_base/chat/rules/persona.md` | Prepended with `../../../` |
| Shell-root-relative (parent) | `../shared/data.md` | Prepended with `../../../` |

The `session:` prefix is special: it resolves to the current run's session directory, so an agent can read files produced earlier in the same session.

---

## Repository Layout

```
.
├── chain.py                          # Headless entry
├── sync_config.py                    # config_base → engine sync
├── config_base/                      # Config source of truth
│   ├── config.json
│   └── template/
├── prompt_base/                      # Prompt files
│   └── template/rules/
├── workspace/                        # Run outputs
├── TOOL_CONTRACT.md                  # Contract for writing tools
└── IAF_AI/                           # ENGINE
    ├── chain.py
    ├── dispatch_routes.py            # Flask blueprint (server/UI mode)
    ├── validate.py                   # Contract validator
    ├── generate_manifest.py          # System-map generator
    ├── config.example.json           # Provider config template
    ├── requirements.txt
    ├── lib/
    │   ├── llm_client.py             # Multi-provider LLM calls + retries
    │   └── token_utils.py            # Token estimation
    └── dispatch/
        └── template/                 # The copyable prototype dispatch
            ├── dispatch.py           #   Kernel (TemplateDispatch)
            ├── dispatch_base.py      #   Tool loop, endpoint resolution
            ├── dispatch_config.json  #   (generated by sync)
            ├── compaction.py         #   Context compaction
            ├── session_io.py         #   Session/history storage
            ├── session_manager.py
            ├── session_close.py
            ├── runtime.py            #   Per-run event/sid state
            ├── v4_processor.py       #   Single-call summarizer service
            ├── context/              #   Sliding-window context helpers
            └── tools/                #   Example tools (see below)
```

---

## Quick Start

### 1. Install

```bash
cd IAF_AI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # requests, flask, croniter
```

### 2. Configure providers

Copy the example config and fill in your API keys:

```bash
cp IAF_AI/config.example.json config_base/config.json
```

`config.json` holds provider endpoints/keys and optional service keys (see [Configuration Reference](#configuration-reference)). **This file contains secrets — keep it out of version control.**

### 3. Run a dispatch

```bash
python3 chain.py --dispatch template --agent script_writer --message "Hello"
```

`chain.py` runs the full cycle: **sync config → instantiate the dispatch → execute the agent → write outputs to `workspace/{dispatch}/`**, and prints the result.

---

## The Dispatch Kernel

Every dispatch's `dispatch.py` exposes the same kernel class, `TemplateDispatch`, with a stable API:

```python
disp = TemplateDispatch(agent_dir)        # load dispatch_config.json
disp.new_session(agent_name="script_writer")  # start a session
disp.append_to_history("user", "...")         # inject input
result = disp.run(agent_name="script_writer")  # → {text_response, tool_calls, duration_ms}
disp.close_session()
# convenience: disp.run_single_message("...")  does new→inject→run→close in one call
```

Inside `run()`, each turn:

1. **Builds context** from the agent's `context_files` — regular files are concatenated into the system prompt; `*_tools.json` files are loaded as external tool definitions.
2. **Loads conversation history**, transparently excluding rounds already folded into a compaction summary.
3. **Runs a tool loop** against the LLM (`call_with_tool_loop`), executing tool calls until the agent finishes or an exit-tool fires.
4. **Records** the response to session history and returns a structured result.

The kernel has **two tool-loading modes**, chosen by config:

- **Standard mode** (chat / pipeline / TCR): loads the dispatch's local `tools/*_tools.py` plus any external `_tools.json` tools. Used for structured cooperation where agents call domain tools.
- **Free-search mode** (set `free_search.free_search_path` on the agent): loads a **path-fenced** file-exploration toolset via `explore_tools.make_tools(read_roots, write_root)` — the agent can read within allowed roots and write to a single output path, ideal for autonomous research/summarization.

**Multi-provider** is built in: the same agent config runs on `openrouter`, `openai`, `xai`, or `anthropic`; the kernel adapts tool-call semantics per provider, and [`lib/llm_client.py`](IAF_AI/lib/llm_client.py) handles endpoints, retries, and token accounting.

---

## Creating a New Dispatch

*(This section folds in the former Chinese “IAF Shell 改造指引.”)*

To build a new collaboration mode, **copy the `template` dispatch and wire up its Shell zones.**

### Step 1 — Copy the prototype

```bash
cp -R IAF_AI/dispatch/template IAF_AI/dispatch/{new_name}
```

### Step 2 — Create the Shell folders

```bash
mkdir -p config_base/{new_name}
mkdir -p prompt_base/{new_name}/rules
mkdir -p workspace/{new_name}
```

### Step 3 — Place prompts in `prompt_base`

Move every prompt/rule file out of the engine and into `prompt_base/{new_name}/rules/`. Prompts live **only** here; the engine reads them directly at runtime.

### Step 4 — Write `config_base/{new_name}/dispatch_config.json`

Define the dispatch's agents. Point each agent's `context_files` at `prompt_base` using **shell-root-relative** paths (sync will translate them):

```json
{
  "agent_id": "my_dispatch",
  "agents": {
    "script_writer": {
      "provider": "openrouter",
      "model": "google/gemini-3-flash-preview",
      "context_files": [
        "prompt_base/my_dispatch/rules/persona.md",
        "prompt_base/my_dispatch/rules/task.md"
      ]
    }
  },
  "compaction": { "enabled": false }
}
```

Absolute paths and prefixed paths (`wiki:`, `session:`, `briefs:`) are left as-is; only shell-root-relative paths are rewritten.

### Step 5 — Sync and verify

```bash
python3 sync_config.py          # generate engine-internal config
python3 IAF_AI/validate.py all  # expect: OK
```

After sync, confirm that the engine's `dispatch_config.json` has `context_files` prefixed with `../../../`, that absolute/prefixed paths were left untouched, and that `IAF_AI/config.json` was written.

> **Notes**
> - Editing a **prompt** never requires a re-sync — it is read live. Only **config** changes need `python3 sync_config.py`.
> - The engine-internal `dispatch_config.json` is intentionally overwritten by sync. Always edit the copy in `config_base/`.
> - A single `config.json` in `config_base/` is shared by all dispatches; `sync_config.py` walks every subfolder of `config_base/` and syncs each dispatch.

---

## Writing Tools

Give an agent a new ability by dropping a file into its `tools/` directory. The full rules are in [`TOOL_CONTRACT.md`](TOOL_CONTRACT.md); the essentials:

- **File name** must end in `_tools.py` (others are ignored by discovery).
- **Export a module-level `TOOLS` dict.** Each entry needs `description`, `parameters` (OpenAI function-calling JSON Schema), and a `handler`.
- **The handler takes one `dict` and returns a `str`** — success text, or a string beginning with `"Error: "` on failure. It must catch its own exceptions, never call `exit()`, set timeouts on long operations, and truncate output beyond ~5000 chars.
- **No registration needed** — the engine auto-discovers `tools/*_tools.py` and merges every `TOOLS` entry.

```python
"""Example tool: greeting generator."""

def _greet(args):
    name = args.get("name", "World")
    return f"Hello, {name}!"

TOOLS = {
    "greet": {
        "description": "Generate a greeting message for the given name.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Name to greet"}},
        },
        "handler": _greet,
    }
}
```

Validate it: `python3 IAF_AI/validate.py tool` → `OK`.

The `template` dispatch ships example tools that demonstrate the patterns: `file_tools.py` (read/write), `explore_tools.py` (path-fenced free search), and `propose_tools.py` / `score_tools.py` / `bet_tools.py` with their `emit_*_tools.json` counterparts (structured multi-agent actions).

---

## Configuration Reference

### `config.json` (global — one per instance)

```json
{
  "providers": {
    "openrouter": { "url": "https://openrouter.ai/api/v1/chat/completions", "key": "..." },
    "xai":        { "url": "https://api.x.ai/v1/chat/completions", "api_key": "...", "model": "..." },
    "openai":     { "url": "https://api.openai.com/v1/responses", "api_key": "...", "model": "..." },
    "anthropic":  { "url": "https://api.anthropic.com/v1/messages", "api_key": "...", "model": "..." }
  },
  "default_provider": "openrouter",
  "default_model": "google/gemini-3-flash-preview",
  "services": {
    "jina_reader":  { "url": "https://r.jina.ai/", "api_key": "..." },
    "brave_search": { "url": "https://api.search.brave.com/res/v1/web/search", "api_key": "..." }
  }
}
```

### `dispatch_config.json` (per dispatch)

Key fields:

| Field | Purpose |
|-------|---------|
| `agent_id` | Identifier recorded on this dispatch's outputs. |
| `agents.{name}` | One agent config: `provider`, `model`, `temperature`, `top_p`, `max_tokens`, `reasoning`, `context_files`. |
| `agents.{name}.context_files` | Ordered list of files → system prompt (and `_tools.json` → external tools). |
| `agents.{name}.free_search` | Optional; enables free-search mode with `free_search_path` (read roots) and `output_path`. |
| `compaction` | Optional context-compaction settings (`enabled`, thresholds). |

---

## AI-Operability Tooling

| Command | Purpose |
|---------|---------|
| `python3 IAF_AI/validate.py dispatch` | Check every dispatch: config present, valid JSON, `dispatch.py` present, `context_files` resolve. |
| `python3 IAF_AI/validate.py tool` | Check every `*_tools.py`: imports cleanly, exports a `TOOLS` dict, each entry has `description`/`parameters`/callable `handler`. |
| `python3 IAF_AI/validate.py all` | Both. Prints `OK` or a precise `FAIL` list. |
| `python3 IAF_AI/generate_manifest.py` | Write `MANIFEST.json` — a machine-readable map of dispatches, agents, and tools. |

These give an operating agent a **read → modify → verify** loop it can run without human help.

---

## Running as a Server (optional)

Beyond headless `chain.py`, the engine ships a Flask blueprint, [`dispatch_routes.py`](IAF_AI/dispatch_routes.py), that auto-discovers dispatches and exposes them over HTTP (create session, send message, fetch history) plus per-dispatch UI pages. Mount it in a host Flask app:

```python
from dispatch_routes import dispatch_bp
app.register_blueprint(dispatch_bp)
```

> The host server app is not included in this template — `dispatch_routes.py` is provided as the integration surface for embedding IAF into a web service.

---

## Design Principles

1. **A dispatch is a copyable folder.** Specialization is duplication + reconfiguration, never a fork of the engine.
2. **Config is the source of truth; the engine is a black box.** Edits are declarative and reversible.
3. **Every extension point has a contract and a validator.** Tools, dispatches, and config are all machine-checkable.
4. **Prompts are live; config is synced.** Fast iteration on behavior, deliberate changes to structure.
5. **Provider-agnostic.** Swap models/providers per agent with a config edit.

---

## License & Versioning

This is the **AI Operability Edition, v1.1** of the Intelligenism Agent Framework.
