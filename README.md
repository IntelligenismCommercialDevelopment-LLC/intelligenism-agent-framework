# Intelligenism Agent Framework (IAF)

**AI Operability Edition — v1.2**

IAF is a minimal, file-based framework for building **multi-agent collaboration modes** that are designed to be **operated and extended by AI agents themselves**, not just by humans.

A running IAF instance is just a directory. You create a new collaboration pattern by **copying a folder**, you give an agent new abilities by **dropping a `*_tools.py` file into it**, and you reconfigure the whole system by **editing plain JSON** — no framework code changes required. Every extension point has an explicit contract, so an LLM can read the system, modify it, and verify its own work.

---

## Table of Contents

- [What's New in v1.2](#whats-new-in-v12)
- [Core Concepts](#core-concepts)
- [Why "AI Operability Edition"](#why-ai-operability-edition)
- [Architecture: the Shell Four-Zone Layout](#architecture-the-shell-four-zone-layout)
- [Repository Layout](#repository-layout)
- [Quick Start](#quick-start)
- [The Dispatch Kernel](#the-dispatch-kernel)
- [Creating a New Dispatch](#creating-a-new-dispatch)
- [Writing Tools](#writing-tools)
- [Reading Images](#reading-images)
- [Configuration Reference](#configuration-reference)
- [AI-Operability Tooling](#ai-operability-tooling)
- [Design Principles](#design-principles)

---

## What's New in v1.2

- **The instance is now fully self-contained.** `lib/` (LLM client, token estimation) lives inside `IAF_AI/`, resolved as `<engine root>/lib`. Copy the folder anywhere and it runs — nothing is resolved from an enclosing directory.
- **Image input.** An agent can be handed local image files alongside its text instruction: `chain.py --image a.png`. See [Reading Images](#reading-images).
- **A fourth provider shape: official direct endpoints.** `lib/llm_client.py` now detects the endpoint from its URL and translates per family — Anthropic Messages, OpenAI-style `/responses`, DeepSeek direct, and standard Chat Completions (OpenRouter and friends). Config still only says `provider` + `model`.
- **Shell-level shared components** under `tools/_shared/`: path-fenced exploration tools, a live per-event run trace (`STATUS.md`), and the image-part builder. `sync_config.py` distributes the first two into whichever dispatches need them.
- **One worked example tool instead of five demos.** The factory tool is now `write_document_tools.py` — a "fixed destination, atomic write" pattern meant to be copied. The old game-specific demo tools (`file_tools`, `propose/score/bet` + their `emit_*_tools.json`) are gone.
- **Prompt rules are distributed, not just referenced.** `prompt_base/{dispatch}/rules/` is copied into the engine dispatch directory by sync, because the context-compaction subsystem resolves its rules file relative to the engine, not the shell.
- **Removed:** `IAF_AI/dispatch_routes.py` (the Flask blueprint for serving dispatches over HTTP) is no longer shipped. This edition is headless; `chain.py` is the entry point.

A Chinese-language deep reference, [`IAF_AI_架构说明.md`](IAF_AI_架构说明.md), ships alongside this README and documents the engine function-by-function.

---

## Core Concepts

- **Dispatch** — a *collaboration mode*: one self-contained pattern of how one or more agents cooperate to do a job (a chat agent, a writer → critic → scorer pipeline, a free-search researcher). Each dispatch is a folder under `IAF_AI/dispatch/`.
- **Agent** — a single configured LLM persona *inside* a dispatch, defined by an entry in `dispatch_config.json` under `agents` (its provider, model, system context, tools). One dispatch can host several agents.
- **Tool** — a Python function an agent can call, exported from a `*_tools.py` file via a `TOOLS` dict. Auto-discovered; no registration code.
- **Shell** — the four-zone project layout (`config_base` / `prompt_base` / `workspace` / `IAF_AI`) that separates *what you edit* from *the engine that runs*.
- **Engine** — `IAF_AI/`. Driven entirely by config and prompts; you normally never edit it.

The central idea: **a dispatch is a copyable unit.** To build a new kind of agent collaboration, you copy the `template` dispatch, adjust its config and prompts, and it runs. Dozens of specialized dispatches can be spun off the same prototype.

---

## Why "AI Operability Edition"

This edition is tuned so that an **AI agent can operate the framework end-to-end**:

- **Explicit contracts.** [`TOOL_CONTRACT.md`](TOOL_CONTRACT.md) specifies exactly how a new tool file must be written, so an LLM can author one correctly on the first try.
- **A machine-readable system map.** [`generate_manifest.py`](IAF_AI/generate_manifest.py) emits `MANIFEST.json` describing every dispatch, agent, and tool — an agent can discover the system without reading source.
- **Config/engine separation.** All edits happen in `config_base/` and `prompt_base/` (plain JSON and Markdown).
- **Copy-to-extend.** New capability = a copied folder or a dropped-in file, never a patch to core code.
- **Named failure modes.** The places where this framework fails *silently* rather than loudly — an unreadable `context_files` entry, a missing compaction rules file, a provider that drops an unknown field — are documented at the point where they bite, because a silent failure is the one an agent cannot self-correct.

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
├── tools/_shared/           # Shell-level shared components (not LLM-visible tools)
│
├── workspace/               # ZONE 3 — OUTPUTS (run artifacts land here)
│   ├── {dispatch}/
│   └── current → {run_id}/  #   symlink to the current run's trace directory
│
└── IAF_AI/                  # ZONE 4 — ENGINE (runtime; normally never edited by hand)
    ├── dispatch/{dispatch}/ #   Per-dispatch kernel + its generated config
    ├── lib/                 #   llm_client, token_utils (ships inside the instance)
    ├── validate.py          #   Contract validator
    └── generate_manifest.py #   System-map generator
```

**The flow of truth is one-directional:**

```
   config_base/{d}/dispatch_config.json ──sync_config.py──►  IAF_AI/dispatch/{d}/dispatch_config.json
   config_base/config.json ─────────────────────────────────►  IAF_AI/config.json
   prompt_base/{d}/rules/ ──────────────────────────────────►  IAF_AI/dispatch/{d}/rules/
   prompt_base/{d}/*.md  ──(referenced directly, no sync)───►  read at runtime by the engine
```

Four rules make this work:

1. **`config_base/` is the source of truth.** Humans and agents only ever edit files here.
2. **`prompt_base/` is the single home for prompts.** A dispatch's `context_files` point straight at it; editing a prompt takes effect immediately, **no sync needed**.
3. **Engine-internal config is generated.** `IAF_AI/.../dispatch_config.json` and `IAF_AI/config.json` are written by `sync_config.py`. Never hand-edit them — they get overwritten.
4. **The engine stays untouched across specializations.** `dispatch.py`, `dispatch_base.py`, and the tool kernel are the same in every copy. Changing them is possible but has a blast radius across every instance derived from the same template.

### Path translation

`sync_config.py` translates the `context_files` paths in `config_base/{dispatch}/dispatch_config.json` from **shell-root-relative** (convenient to write) into **engine-dispatch-relative** (what the runtime needs). Because a dispatch directory (`IAF_AI/dispatch/{name}/`) sits three levels below the shell root, the translation prepends `../../../`:

- **Absolute** — `/abs/path/to/wiki/soul.md` → left unchanged.
- **Prefixed** — `wiki:...`, `session:...`, `briefs:...` → left unchanged (resolved at runtime).
- **Shell-root-relative** — `prompt_base/chat/rules/persona.md` → `../../../prompt_base/chat/rules/persona.md`.
- **Shell-root-relative, pointing up** — `../shared/data.md` → `../../../../shared/data.md`.

The `session:` prefix is special: it resolves to the current run's session directory, so an agent can read files produced earlier in the same session.

> **Precise boundary:** the core `context_files` reader resolves **only** `session:`. The `wiki:` and `briefs:` prefixes belong to the memory-update subsystem (`session_close`) and are valid only in its `context_sources` / `output_target` / `rules_file` fields. A `wiki:` path in a core agent's `context_files` is joined into a nonsense path and **read as an empty string with no error**.

### What sync distributes besides config

- **`prompt_base/{d}/rules/*` → `IAF_AI/dispatch/{d}/rules/`.** The compaction subsystem reads `v4_rules.compact` (default `rules/compact_v4.md`) **relative to the engine dispatch directory**. If the file is missing, the loader does not raise — it returns the string `"(V4 rules file not found: ...)"`, and compaction happily uses that as its system prompt, writes a garbage summary, and marks those rounds as compacted. That is a silent loss of history, which is why the rules file is distributed rather than merely referenced.
- **`tools/_shared/{explore_tools,status_log}.py` → `IAF_AI/dispatch/{d}/tools/`.** `explore_tools.py` only for dispatches that declare `free_search`; `status_log.py` for any dispatch with tools (missing it degrades silently — tools still work, you just lose the live trace). Other files in `tools/_shared/` are **not** distributed: they are for the shell layer, not for the LLM.

---

## Repository Layout

```
.
├── chain.py                          # Headless entry
├── sync_config.py                    # config_base → engine sync
├── README.md
├── IAF_AI_架构说明.md                 # Chinese deep reference (engine, function by function)
├── TOOL_CONTRACT.md                  # Contract for writing tools
├── internal-environment-setup.md     # venv / dependency notes
├── config_base/                      # Config source of truth
│   ├── config.json                   #   providers + services (SECRETS — keep untracked)
│   └── template/dispatch_config.json
├── prompt_base/
│   └── template/rules/               # persona.md, compact_v4.md
├── tools/_shared/
│   ├── explore_tools.py              # Path-fenced read/write toolset (distributed by sync)
│   ├── status_log.py                 # Live per-event run trace (distributed by sync)
│   ├── image_parts.py                # Local image → multimodal content parts
│   └── ledger_roots.json             # Optional watch roots for the trace
├── workspace/                        # Run outputs and traces
└── IAF_AI/                           # ENGINE
    ├── chain.py                      # Placeholder; the real entry is the shell-root chain.py
    ├── validate.py                   # Contract validator
    ├── generate_manifest.py          # System-map generator
    ├── config.example.json           # Provider config template
    ├── requirements.txt
    ├── lib/
    │   ├── llm_client.py             # Multi-endpoint LLM calls, retries, streaming
    │   ├── token_utils.py            # Token estimation
    │   ├── jev_client.py             # Optional: external judgment-service client
    │   └── voice.py                  # Optional: text-to-speech client
    └── dispatch/
        └── template/                 # The copyable prototype dispatch
            ├── dispatch.py           #   Kernel (TemplateDispatch)
            ├── dispatch_base.py      #   Tool loop, endpoint resolution
            ├── dispatch_config.json  #   (generated by sync)
            ├── compaction.py         #   Context compaction
            ├── v4_processor.py       #   Single-call text-structuring service
            ├── session_io.py         #   Session/history storage
            ├── session_manager.py    #   History rendering
            ├── session_close.py      #   Session close → memory update
            ├── runtime.py            #   Per-run event/sid state
            ├── context/              #   Sliding-window context helpers
            ├── rules/                #   (distributed by sync from prompt_base)
            └── tools/                #   write_document_tools.py + distributed shared tools
```

`lib/jev_client.py` and `lib/voice.py` are not referenced by the template itself; they ship as ready clients for instances that need an external judgment service or TTS.

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

`config.json` holds provider endpoints/keys and optional service keys (see [Configuration Reference](#configuration-reference)). **This file contains secrets — keep it out of version control.** The copy shipped here contains `your-api-key-here` placeholders only.

### 3. Run a dispatch

```bash
python3 chain.py --dispatch template --agent core --message "Hello"
```

`chain.py` runs the full cycle: **sync config → instantiate the dispatch → execute the agent → write outputs to `workspace/{dispatch}/`**, and prints the result. A per-event trace of the run is written to `workspace/current/STATUS.md` while it happens, so an interrupted run still leaves a record of what had occurred.

---

## The Dispatch Kernel

Every dispatch's `dispatch.py` exposes the same kernel class, `TemplateDispatch`, with a stable API:

```python
disp = TemplateDispatch(agent_dir)            # load dispatch_config.json
disp.new_session(agent_name="core")           # start a session
disp.append_to_history("user", "...")         # inject input (str, or multimodal parts)
result = disp.run(agent_name="core")          # → {text_response, tool_calls, duration_ms}
disp.close_session()
# convenience: disp.run_single_message("...") does new→inject→run→close in one call
```

> The agent name must match a key under `agents` in `dispatch_config.json` **character for character**. The engine's own default is `"core_chat"` while the shipped skeleton and `chain.py` both use `"core"` — if you rename the key, change all three. This is the single most common setup mistake.

Inside `run()`, each turn:

1. **Builds context** from the agent's `context_files` — regular files are concatenated into the system prompt (each labelled with its filename); `*_tools.json` files are loaded as external tool definitions. A directory entry expands to the files directly inside it (sorted, **not** recursive).
2. **Loads conversation history**, transparently excluding rounds already folded into a compaction summary.
3. **Runs a tool loop** against the LLM (`call_with_tool_loop`), executing tool calls until the agent finishes or an exit-tool fires.
4. **Records** the response to session history and returns a structured result.

The kernel has **two tool-loading modes**, chosen by config:

- **Standard mode** (chat / pipeline / domain tools): loads the dispatch's local `tools/*_tools.py` plus any external `_tools.json` tools. Tool use is forced only when external tools are present, and only for providers in a known list (`openrouter`, `openai`, `xai` → `"required"`; `anthropic` → `{"type":"any"}`). For any other provider the forcing silently falls back to `None`, meaning the model is free to answer with text instead of calling your tool — check this before pointing an external-tool dispatch at a new provider.
- **Free-search mode**: enabled when the agent's `free_search` block has a non-empty `free_search_path` **or** a non-empty `write_allow`. Loads a **path-fenced** file-exploration toolset via `explore_tools.make_tools(read_roots, write_root)` — the agent reads within allowed roots and writes to a single output path. Read roots come from `free_search_path`; if that list is empty, the read tools simply do not exist and only the write tool remains. A rejected out-of-fence write is the fence working, not a bug; it shows up in the trace.

**Multi-endpoint** support is built in, and the differences are absorbed by [`lib/llm_client.py`](IAF_AI/lib/llm_client.py) rather than by your config — see [Configuration Reference](#configuration-reference).

---

## Creating a New Dispatch

To build a new collaboration mode, **copy the `template` dispatch and wire up its Shell zones.**

### Step 1 — Copy the prototype

```bash
cp -R IAF_AI/dispatch/template IAF_AI/dispatch/{new_name}
```

Delete any `sessions/`, `staging/`, and `__pycache__` residue that came along with the copy.

### Step 2 — Create the Shell folders

```bash
mkdir -p config_base/{new_name}
mkdir -p prompt_base/{new_name}/rules
mkdir -p workspace/{new_name}
```

### Step 3 — Place prompts in `prompt_base`

Move every prompt/rule file out of the engine and into `prompt_base/{new_name}/rules/`. Prompts live **only** here.

### Step 4 — Write `config_base/{new_name}/dispatch_config.json`

Define the dispatch's agents. Point each agent's `context_files` at `prompt_base` using **shell-root-relative** paths (sync will translate them):

```json
{
  "agent_id": "my_dispatch",
  "agents": {
    "core": {
      "provider": "openrouter",
      "model": "google/gemini-3-flash-preview",
      "role": "core",
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

### Step 5 — Sync and run

```bash
python3 sync_config.py          # generate engine-internal config + distribute rules/tools
python3 chain.py --dispatch {new_name} --agent core --message "..."
```

After sync, confirm that the engine's `dispatch_config.json` has `context_files` prefixed with `../../../`, that absolute/prefixed paths were left untouched, and that `IAF_AI/config.json` was written.

> **Notes**
> - Editing a **prompt** body never requires a re-sync — it is read live. Files under `prompt_base/{d}/rules/` *are* distributed by sync, so after touching those, sync once.
> - The engine-internal `dispatch_config.json` is intentionally overwritten by sync. Always edit the copy in `config_base/`.
> - A single `config.json` in `config_base/` is shared by all dispatches; `sync_config.py` walks every subfolder of `config_base/` and syncs each dispatch. Subfolders starting with `_` are skipped.

---

## Writing Tools

Give an agent a new ability by dropping a file into its `tools/` directory. The full rules are in [`TOOL_CONTRACT.md`](TOOL_CONTRACT.md); the essentials:

- **File name** must end in `_tools.py` (others are ignored by discovery).
- **Export a module-level `TOOLS` dict.** Each entry needs `description`, `parameters` (OpenAI function-calling JSON Schema), and a `handler`.
- **The handler takes one `dict` and returns a `str`** — success text, or a string beginning with `"Error: "` on failure. It must catch its own exceptions, never call `exit()`, set timeouts on long operations, and truncate output beyond ~5000 chars.
- **Never raise at import time.** A tool module that throws while loading is reported as one warning line and then silently does not exist.
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

The `template` dispatch ships one worked example, `write_document_tools.py`, demonstrating the pattern worth copying: a **fixed destination with an atomic write** — the tool accepts only `content`, never a path; the destination is derived from the dispatch's own `agent_id`; the write goes to a temp file in the same directory followed by `os.replace`; and the resolved target is fence-checked to be inside this instance's `workspace/`. For a dispatch that produces several artifacts, add several `write_*` handlers, each with its own fixed destination, rather than one handler that takes a path.

---

## Reading Images

An agent can be handed local image files along with its text instruction. The model you point it at must be a vision-capable one.

```bash
python3 chain.py --dispatch template --agent core \
                 --message "Transcribe the string printed in this image." \
                 --image /abs/path/page.png
```

`--image` is repeatable (order is preserved); `--image-detail` is passed through to the provider as the image-fidelity knob.

**How it travels.** [`tools/_shared/image_parts.py`](tools/_shared/image_parts.py) turns the text and the files into a content-parts list; `append_to_history` stores it as-is; the kernel passes `content` through untouched; `lib/llm_client.py` forwards `messages` verbatim on the Chat Completions and Anthropic paths. The engine never interprets `content`, which is why multimodal input needed no kernel changes.

```json
[{"type": "text", "text": "..."},
 {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}]
```

**Constraints enforced locally**, so a bad input fails before it costs a request: formats are PNG / JPEG / GIF / WebP, **detected from file content rather than the extension**; 32 MiB per image; 36 MiB of raw bytes in total (base64 inflates by 4/3 against a 48 MiB request body); 600 images per request; empty files and missing paths raise.

**Two things not to do:**

- **Do not put an image in `context_files`.** That reader opens files as UTF-8 and catches only `FileNotFoundError` / `IOError`, so a binary file raises `UnicodeDecodeError` and takes the dispatch down. `context_files` produces the system-prompt *string*; images do not belong there.
- **Do not deliver an image through a tool.** A `*_tools.py` handler returns a `role="tool"` message, and Chat Completions tool messages carry text only. Images must ride in a **user** turn.

**Accounting.** `lib/token_utils.estimate_tokens` counts only the text parts of a multimodal message, which matches how providers bill images (a flat per-image cap rather than a character count). Session titles also take the text parts, so base64 never lands in `meta.json`.

**PDFs** are not an accepted image format. Rasterize each page to PNG first (`PyMuPDF`: `page.get_pixmap(dpi=...).save(...)`) and keep the total-size limit above in mind.

> If a model that **cannot** see images is handed one, providers do not reliably return an error — you get a fluent answer that simply did not look at the picture. Re-check the agent's `model` whenever you change it.

---

## Configuration Reference

### `config.json` (global — one per instance)

```json
{
  "providers": {
    "openrouter": { "url": "https://openrouter.ai/api/v1/chat/completions", "key": "..." },
    "deepseek":   { "url": "https://api.deepseek.com/chat/completions", "key": "..." },
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

Endpoint resolution tries the agent's named `provider`, then `default_provider`, then the first entry; the key is read from `api_key` **or** `key` (both spellings work).

**The request shape is chosen from the URL**, so your config only ever names a provider and a model:

- `api.anthropic.com` — Messages API: `system` is hoisted to the top level, `max_tokens` is mandatory, the response is a content-block array and the stop field is `stop_reason`. The client normalizes all of it back to Chat Completions shape.
- a URL ending in `/responses` — the payload is `{"model", "input"}`, not `messages`.
- `api.deepseek.com` — official direct endpoint: OpenRouter's `provider` routing field is **not** sent, and reasoning is expressed natively as `thinking: {"type":"enabled"}` plus a top-level `reasoning_effort`.
- anything else — standard Chat Completions, where `provider_routing` becomes a `provider` object.

Two failure modes worth knowing:

- **`model` naming follows the endpoint.** Through OpenRouter a model carries a vendor prefix (`deepseek/deepseek-v4-pro`); against an official direct endpoint it must not (`deepseek-flash`). Nothing local catches the wrong form — it comes back as a provider-side 400.
- **In thinking mode, the prose can land entirely in `reasoning_content` while `content` is empty.** The client prints a warning, but a warning is all it is: the work was done and billed while the caller received an empty string. On a tool-call round an empty `content` is normal and that warning is a false alarm.

### `dispatch_config.json` (per dispatch)

Top level: `display_name`, `agent_id`, `agents{}`, `max_context`, `session_close{}`, `v4_rules{}`, `compaction{}`.

Per-agent fields:

- `agents.{name}` — one agent: `provider`, `model`, `temperature`, `top_p`, `max_tokens`, `reasoning`, `role`, `context_files`.
- `agents.{name}.context_files` — ordered list of files → system prompt (and `_tools.json` → external tools). An unreadable entry is an empty string, not an error.
- `agents.{name}.provider_routing` — **OpenRouter only**: pin the upstream vendor so prefix caching stays warm. `"slug"` pins it (`allow_fallbacks: false`), `"slug/quant"` also constrains quantization, or pass a full OpenRouter `provider` object. Against a direct endpoint the client discards it, so leaving it configured there is a key that lies.
- `agents.{name}.free_search` — optional; enables free-search mode with `free_search_path` (read roots), `write_allow` / `output_path` (write fence), `exit_on_write`, `max_tool_loops`, `tool_choice`.
- `compaction` — optional context-compaction settings (`enabled`, thresholds, output file).
- `v4_rules.compact` — path to the compaction rules file, resolved **relative to the engine dispatch directory**; sync is what puts it there.

---

## AI-Operability Tooling

- `python3 IAF_AI/generate_manifest.py` — write `MANIFEST.json`, a machine-readable map of dispatches, agents, and tools.
- `python3 IAF_AI/validate.py all` — contract checks over dispatches and `*_tools.py` files.

> **Honest status of `validate.py`:** it predates two engine features and **is not a gate**. Directory entries in `context_files` and free-search-style dispatches both make it report errors that are not errors. Treat it as a coarse first pass; for real verification, import the engine's own `_build_context` and `_load_dispatch_tools` and assert against what they return.

What an operating agent actually gets as a read → modify → verify loop: the contract in `TOOL_CONTRACT.md`, the system map from `generate_manifest.py`, and the fact that every config path either resolves to a real file or is visibly empty.

---

## Design Principles

1. **A dispatch is a copyable folder.** Specialization is duplication + reconfiguration, never a fork of the engine.
2. **Config is the source of truth; the engine is driven, not edited.** Edits are declarative and reversible.
3. **An instance is self-contained.** `lib/` ships inside `IAF_AI/`; copying the directory is a complete install. The cost is deliberate: a fix in `lib/` does not propagate to copies already made.
4. **Prompts are live; config is synced.** Fast iteration on behavior, deliberate changes to structure.
5. **Endpoint-agnostic.** Swap models, providers, or whole API families with a config edit; the translation lives in one file.
6. **Multimodal input changes only the input side.** A message's `content` may be a parts list, which the engine passes through; every text-only bypass extracts the text parts explicitly.
7. **Fences are code, not instructions.** Path access is bounded by a factory closure over `realpath` whitelists, so an agent cannot talk its way out of one.
8. **Silent failures are documented where they bite.** An empty `context_files` read, a missing compaction rules file, a discarded provider field — each is called out at the place it would otherwise be mistaken for success.

---

## License & Versioning

This is the **AI Operability Edition, v1.2** of the Intelligenism Agent Framework.
