# Intelligenism Agent Framework

## Architecture Overview v3.0

Design Philosophy · Architecture Advantages · Agent Layer · Dispatch Layer · Tube Layer · UI Layer · AI Operability · Engineering Roadmap

2026-04-01 | Version 3.0

---

## 1. Design Philosophy

### 1.1 The Fundamental Loop

The Fundamental Loop is the core concept of this framework. It is a complete, self-contained Agent runtime containing everything an Agent needs to run independently:

- **LLM Communication Engine:** Assembles the messages array, sends API requests, processes responses
- **Tool Executor:** Auto-discovers and registers tools, executes LLM-requested tool calls
- **Context Manager:** History management, context trimming strategies
- **Tool Library:** Pluggable tool modules (file I/O, shell commands, tube triggering, dispatch collaboration, etc.)
- **Identity & Knowledge:** Loads arbitrary .md files as system prompt via configurable context_files
- **Skill Files:** Dynamically injected task instructions based on trigger rules

The current engine implementation is approximately 267 lines of Python, plus 89 lines of shared infrastructure.

### 1.2 Possibility Management vs. Certainty Management

Traditional multi-agent frameworks use certainty management: one shared engine serves all Agents, with configuration parameters controlling differences. User freedom is limited to what the framework designer foresaw.

This framework uses possibility management: each Agent owns a complete copy of the Fundamental Loop code. Users can modify any line, making each Agent fundamentally different from another at any level — without worrying about affecting other Agents.

> *The core distinction: Traditional frameworks say "We're flexible, you can configure many parameters." The Intelligenism Framework says "You own all the code, you can change anything." The former is freedom inside a fence. The latter is true freedom.*

### 1.3 Complexity Growth Model

**Shared engine complexity grows multiplicatively.** 3 Agents × 2 trimming strategies × 4 tool sets = 24 combinations. Each combination may have interaction bugs. Adding a new Agent doesn't add complexity — it multiplies it.

**Independent loop complexity grows additively.** Each Agent's Fundamental Loop is always ~267 lines. Adding a 4th Agent is just another 267-line copy. Each copy's internal complexity evolves independently.

### 1.4 AI Operability

IAF is not just "an AI framework for humans." It is **an AI framework that AI can also operate**. Its design characteristics — minimal codebase, filesystem communication, declarative JSON configuration, process isolation — make the framework equally transparent and controllable by both carbon-based and silicon-based intelligence.

For a top-tier AI agent to take over a framework, it must pass four gates: read, modify, deploy, monitor. IAF is open at every gate:

| Gate | IAF's Approach | Traditional Frameworks |
|------|---------------|----------------------|
| Read | ~550 lines, fits in one context window | 10K+ lines, requires modular understanding |
| Modify | Edit JSON and Markdown files | Write Python/SDK code within framework constraints |
| Deploy | `python3 chat_server.py` — one command | docker compose / kubernetes |
| Monitor | `cat tube_log.jsonl` — plain text | Proprietary dashboards / monitoring APIs |

### 1.5 Mapping to Intelligenism Theory

| Intelligenism Theory Concept | Architecture Implementation |
|-----------------------------|---------------------------|
| Autonomous units of Intelligent Consortiums | Each Agent's independent Fundamental Loop |
| Carbon-silicon symbiosis, loose coupling | Process isolation + filesystem communication + AI operability |
| Pluggable collaboration paradigms | Independent strategy folders under dispatch/ |
| Rewirable signal topology | Declarative tubes.json configuration |
| Deterministic scheduling without LLM | Tube orchestration is pure Python, not LLM probabilistic judgment |
| Possibility management over certainty management | Complete code copies instead of parameterized configuration |
| Value in assembly, not in blocks | Assembly knowledge of Agent + Dispatch + Tube is the competitive moat |

---

## 2. Architecture Advantages

### 2.1 True Parallel Execution

Since each Fundamental Loop is an independent process, multi-Agent collaboration achieves OS-level true parallelism:

- **No GIL limitation:** Each Agent is an independent Python process
- **No shared memory:** No locks, no race conditions, no state pollution
- **Naturally distributed rate limits:** Different Agents can call different LLM providers
- **Fault isolation:** One Agent crashing does not affect others

### 2.2 Four-Dimensional Orthogonal Extension

The framework's four extension dimensions are fully orthogonal — changes in any dimension do not affect the others:

| Dimension | Operation | Python Code Changes |
|-----------|-----------|-------------------|
| Agent | cp -r template/ agents/xxx/ | 0 lines |
| Capability | Add .py to agents/xxx/tools/; modify context_files in agent_config.json | 0 lines |
| Collaboration | Add folder to dispatch/ | 0 lines (only modify orchestration logic region) |
| Signal Topology | Add entry to tube/tubes.json | 0 lines |

### 2.3 Filesystem as Communication Protocol

Agents communicate not through shared memory or function calls, but through the filesystem: output files, status files, log files, flag files. Naturally debuggable (intermediate files are directly readable), future-ready for distribution (swap file directory for NFS/S3/HTTP API).

### 2.4 Pure Python Stack

The entire framework depends only on Python + HTML files. No Node.js, no npm, no frontend build tools. Dependencies: `pip install flask requests croniter`.

---

## 3. Four-Layer Architecture Overview

The framework consists of four completely independent layers, with units within each layer also isolated from each other. Layers communicate through Flask APIs and the filesystem, never intruding on each other.

| Layer | Responsibility | Unit Granularity | Self-Containment |
|-------|---------------|-----------------|-----------------|
| Agent Layer | Complete runtime for a single intelligence | One folder = one Agent | Copy folder to deploy |
| Dispatch Layer | Multi-Agent collaboration orchestration | One folder = one collaboration strategy | Copy folder to deploy |
| Tube Layer | Signal topology — connections between blocks | One tubes.json entry = one signal pathway | Edit JSON to activate |
| UI Layer | Human-machine interface | One HTML = one functional page | Drop file to enable |

Plus cross-cutting infrastructure:

- **chat_server.py** — Router + Tube Runner startup, no business logic
- **tube_routes.py** — Tube Layer Flask Blueprint
- **dispatch_routes.py** — Dispatch Layer Flask Blueprint

---

## 4. Complete Directory Structure

```
intelligenism-agent-framework/
│
├── config.json                     # Global config (provider connections)
│
├── lib/                            # Shared infrastructure
│   ├── llm_client.py               # HTTP LLM calls + retry + error classification
│   └── token_utils.py              # Token estimation
│
├── template/                       # Fundamental Loop template (copy to create Agents)
│   ├── core/
│   │   ├── direct_llm.py           # Engine core
│   │   └── tool_executor.py        # Tool auto-discovery registry
│   ├── tools/
│   │   └── file_tools.py           # Default tool set
│   ├── context/
│   │   └── sliding_window.py       # Default trimming strategy
│   ├── skills/                     # Empty directory (to be populated)
│   ├── SOUL.md                     # Identity template
│   └── agent_config.json           # Agent config template
│
├── agents/                         # Agent instance directory
│   └── default/                    # (same structure as template/)
│
├── dispatch/                       # Collaboration strategy library
│   └── roundtable/                 # Example strategy
│       ├── dispatch.py             # Orchestration logic
│       ├── dispatch_base.py        # Infrastructure (tool loops, LLM parsing)
│       ├── context_injector.py     # Context assembly
│       ├── session_manager.py      # Session management
│       ├── dispatch_config.json    # Participating Agents + file read rules
│       ├── context/                # Dispatch-specific trimming
│       ├── rules/                  # Agent role definitions
│       ├── sessions/               # Collaboration records
│       ├── staging/                # Runtime temporary files
│       └── roundtable.html         # Dedicated UI page
│
├── tube/                           # Signal topology layer [v3.0 new]
│   ├── tube_runner.py              # Main loop engine
│   ├── triggers/                   # Pluggable trigger sources
│   │   ├── cron.py                 # Cron scheduling
│   │   └── manual.py              # Manual / API trigger
│   ├── targets/                    # Pluggable drive targets
│   │   ├── agent.py                # Drive Agent
│   │   └── dispatch.py             # Drive Dispatch
│   ├── run_agent.py                # Agent CLI entry
│   ├── run_dispatch.py             # Dispatch CLI entry
│   ├── tubes.json                  # Single source of topology truth
│   ├── tube_log.jsonl              # Execution log
│   └── manual_triggers/            # Flag file directory
│
├── chat_server.py                  # Web server / router + Tube Runner
├── dispatch_routes.py              # Dispatch Flask Blueprint
├── tube_routes.py                  # Tube Flask Blueprint [v3.0 new]
├── index.html                      # Yellow pages (feature index)
├── chat.html                       # Basic chat interface
├── pages/                          # User-created pages
│   └── tube-dashboard.html         # Tube monitoring panel [v3.0 new]
└── tests/
    └── test_template.py
```

---

## 5. Agent Layer Design

### 5.1 Fundamental Loop Template

Create a new Agent by running `cp -r template/ agents/new_agent/`.

**Engine Core direct_llm.py (~267 lines)**

| Function | Lines | Responsibility |
|----------|-------|---------------|
| call_agent(message, mode, max_loops) | ~40 | Single public entry point. Build context → call LLM → handle tool calls → save history |
| build_messages(message, config, mode) | ~25 | Five-layer assembly: context_files → skills → history → current → trim |
| _load_config() | ~25 | Read global + agent config, resolve context_files |
| _load_context_files(context_files) | ~20 | Read all configured files, concatenate as system prompt |
| _resolve_path(path) | ~12 | Three-level path resolution: agent dir → framework root → absolute |
| _match_skills(message, skills) | ~25 | Match skills by trigger rules, load corresponding .md files |

**build_messages Five-Layer Assembly Model**

| Layer | Source | When Loaded | Purpose |
|-------|--------|------------|---------|
| Layer 1 | All files in context_files list | Every call | Agent identity, knowledge, routing table |
| Layer 2 | Skill .md files matching triggers | On keyword match | Scenario-specific task instructions |
| Layer 3 | history.jsonl | Chat mode only | Conversation memory |
| Layer 4 | Current user message | Every call | Current input |
| Layer 5 | Trim to budget | Every call | Ensure within context window |

### 5.2 agent_config.json Configuration

```json
{
  "display_name": "Research Agent",
  "provider": "openrouter",
  "model": "google/gemini-3-flash-preview",
  "max_context": 200000,
  "trim_strategy": "sliding_window",
  "context_files": [
    "SOUL.md",
    "knowledge/domain_guide.md",
    "skills/skill_router.md"
  ],
  "skills": [
    {
      "trigger": "deep review",
      "match_type": "exact",
      "skill_file": "skills/deep_review_checklist.md"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| display_name | string | Name shown in UI and logs |
| provider | string | References a provider in config.json |
| model | string | LLM model identifier |
| max_context | int | Maximum context window in tokens |
| trim_strategy | string | Trimming strategy, corresponds to .py in context/ |
| context_files | array | File paths loaded on every call (backward compatible: falls back to ["SOUL.md"]) |
| skills | array | Conditional trigger rules: trigger (keyword), match_type (contains/startswith/exact), skill_file (path) |

**Path resolution:** Agent directory relative → framework root relative → absolute path.

### 5.3 Agent Capability Three-Layer Model

| Layer | Carrier | When Loaded | Analogy |
|-------|---------|------------|---------|
| Identity & Knowledge | context_files (.md) | Every call | Knowledge in the brain |
| Conditional Instructions | skills (.md + trigger rules) | On keyword match | Conditioned reflexes |
| Execution Capability | tools (*_tools.py) | Every call (schema injected) | Hands and feet |

**Available Tool Files:**

| File | Tools | Capability | Default |
|------|-------|-----------|---------|
| file_tools.py | read_file, write_file, list_dir | File I/O, directory listing | Yes |
| shell_tools.py | run_shell | Execute terminal commands | No |
| tube_tools.py | trigger_tube, list_tubes, tube_log | Trigger tubes, check status, read logs | No |
| dispatch_tools.py | run_dispatch, list_dispatch_strategies | Initiate multi-Agent collaboration | No |

Drop tool files into `agents/xxx/tools/`. Auto-discovered, no code changes needed.

---

## 6. Dispatch Layer Design

### 6.1 Core Principle

The Dispatch layer orchestrates multi-Agent collaboration but does not call Agents through their engine. It directly calls lib/llm_client.call_llm(), assembling context itself. Agent folders are just "data sources + model config" to Dispatch, not executors.

### 6.2 Dispatch Folder Structure

| File | Responsibility |
|------|---------------|
| dispatch.py | Orchestration logic: round control, Agent call order, termination conditions |
| dispatch_base.py | Infrastructure: tool loops, LLM parsing, staging |
| context_injector.py | Context assembly: read Agent files per config |
| session_manager.py | Session CRUD |
| dispatch_config.json | Participating Agents, context_files, rounds |
| rules/*.md | Agent role definitions for this collaboration |
| sessions/ | Complete collaboration records |

### 6.3 Three-Layer Context Isolation

| Context Type | Owned By | Storage | Purpose |
|-------------|----------|---------|---------|
| Agent chat history | Agent | agents/xxx/history.jsonl | User-Agent conversation memory |
| Dispatch collaboration records | Dispatch | dispatch/xxx/sessions/*.jsonl | Multi-Agent collaboration process |
| Per-call context | context_injector | Memory (not persisted) | Single LLM call assembly |

Agents don't know they participated in Dispatch. Dispatch never touches Agent history. Isolation is an architectural guarantee.

---

## 7. Tube Layer Design [v3.0 New]

### 7.1 What is Tube

Tube is the framework's third dimension — the signal topology layer. It describes signal pathways between blocks: when a condition is met, trigger a target to execute.

A Tube consists of three elements:

| Element | Description |
|---------|-------------|
| Triggers | What conditions activate this tube (pluggable: cron, manual, API, file watch, etc.) |
| Steps | What to do when activated, in order (Agent, Dispatch, other Tubes) |
| Payload | Data passed from trigger to target (prompt, file path, upstream output, etc.) |

### 7.2 Why Tube Matters

Existing multi-agent frameworks share a common problem: the pipelines between blocks are cast in stone.

| Dimension | CrewAI / LangGraph / Manus | Intelligenism |
|-----------|---------------------------|---------------|
| Collaboration protocols | Built-in fixed patterns / fixed roles | Pluggable dispatch/ folders |
| Signal topology | Hardcoded, untouchable | Declarative tubes.json, rewirable |

Same three Agents and two Dispatches, different Tube configurations yield completely different system behavior — serial pipeline, parallel fan-out, feedback loop. Blocks unchanged, wiring changed, behavior transformed.

### 7.3 tubes.json — Single Source of Topology Truth

```json
[
  {
    "id": "doc_analysis_pipeline",
    "enabled": true,
    "triggers": [
      { "type": "cron", "config": { "expr": "30 9 * * *" } },
      { "type": "manual" }
    ],
    "steps": [
      { "type": "agent", "id": "doc_processor", "mode": "batch",
        "payload": { "prompt": "Process and analyze the document" } },
      { "type": "dispatch", "id": "roundtable",
        "payload": { "message": "Brainstorm based on analysis results" } },
      { "type": "tube", "id": "send_report" }
    ]
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique identifier |
| enabled | boolean | Default true |
| triggers | array | Trigger source array, OR logic |
| steps | array | Execution steps array, serial by default |

### 7.4 Trigger Landscape

| Trigger Source | Triggered By | Mechanism |
|---------------|-------------|-----------|
| Cron schedule | tube_runner automatic | croniter time matching, polled every 15 seconds |
| API call | Human / AI agent / external system | POST /api/tube/trigger → flag file |
| Tube chain | Upstream tube's step | type=tube in steps → inline execution |

### 7.5 Drive Target Landscape

| Target Type | Execution Method |
|------------|-----------------|
| Agent | subprocess → run_agent.py |
| Dispatch | subprocess → run_dispatch.py |
| Tube | Inline recursive execution (depth limit: 5) |
| User-defined | Drop .py in targets/, implement build_command() |

### 7.6 Execution Model

- **Subprocess isolation:** tube_runner only spawns subprocesses, never imports business code
- **Serial steps:** Previous step exit code 0 before next step runs; non-zero halts
- **Parallel tubes:** Different tubes execute in independent threads, non-blocking
- **Duplicate prevention:** running_tubes dict prevents same tube from firing twice
- **Hot reload:** tubes.json re-read every polling cycle, no restart needed
- **Fire-and-forget:** Trigger doesn't block the main loop

### 7.7 Tube API Endpoints

| Method | Path | Function |
|--------|------|----------|
| GET | /api/tubes | List all tubes with real-time status |
| GET | /api/tube/status | Lightweight status query |
| POST | /api/tube/trigger | Manual trigger |
| GET | /api/tube/log | Query logs (supports filtering) |
| GET | /api/tube/log/grouped | Logs grouped by tube |
| DELETE | /api/tube/log | Clear logs (per-tube or all) |

---

## 8. UI Layer Design

### 8.1 Philosophy

Each HTML page is a self-contained complete page with zero coupling between pages. No unified SPA shell, no shared router.

### 8.2 Page Types

| Type | Location | Route |
|------|----------|-------|
| Yellow Pages | index.html | GET / |
| Basic Chat | chat.html | GET /chat |
| User Pages | pages/*.html | GET /pages/<name> |
| Dispatch UI | dispatch/xxx/*.html | GET /dispatch/<name> |
| Tube Dashboard | pages/tube-dashboard.html | GET /pages/tube-dashboard |

---

## 9. Data Flows

### 9.1 Single Agent Chat

```
User → chat.html → POST /api/chat → chat_server.py
    → dynamically load agents/{id}/core/direct_llm.py
    → call_agent(message, mode="chat")
    → build_messages() (five-layer assembly)
    → lib/llm_client.call_llm()
    → LLM returns → save_history() → return to browser
```

### 9.2 Dispatch Collaboration

```
User/tube → dispatch UI / API → dispatch.py
    → context_injector reads Agent files per config
    → assemble collaboration context → lib/llm_client.call_llm()
    → write results to sessions/*.jsonl
    → next round or terminate
```

### 9.3 Tube Signal Flow

```
Trigger (cron / API / upstream tube)
    → tube_runner detects
    → independent thread executes steps serially
    → each step: targets/{type}.py builds command → subprocess executes
    → log written to tube_log.jsonl
    → step complete → next step / all done
```

### 9.4 Agent Driving Other Modules

```
Agent determines action needed during conversation
    → LLM calls tube_tools.py trigger_tube → fires a tube
    → LLM calls dispatch_tools.py run_dispatch → initiates collaboration
    → LLM calls shell_tools.py run_shell → executes command
    → results return to Agent → conversation continues
```

---

## 10. Comparison with Existing Frameworks

| Dimension | CrewAI / AutoGen / LangGraph | Manus | Intelligenism Framework |
|-----------|---------------------------|-------|----------------------|
| Codebase | 10K+ lines | Closed source | ~550 lines (incl. shared layer) |
| Engine model | One shared engine | Fixed three roles | Each Agent has independent engine |
| Complexity | Multiplicative growth | Hidden internally | Additive growth |
| Parallelism | Async pseudo-parallel | Cloud parallel | Multi-process true parallel |
| Customizability | Within config parameters | Not customizable | Any code level |
| Collaboration paradigms | Framework built-in fixed | Fixed three roles | Pluggable any paradigm via dispatch/ |
| Signal topology | Hardcoded | Hardcoded | Declarative tubes.json, rewirable |
| AI operability | Difficult (10K+ lines + SDK) | Impossible (closed source) | Fully operable (550 lines + filesystem) |
| Product unit | Code (hard to distribute) | SaaS service | Folders / JSON entries (tradeable) |

---

## 11. Product Units and Tradeability

The framework has four tradeable product units:

| Product Unit | Form | Trading Scenario |
|-------------|------|-----------------|
| Agent | Folder | Domain-specific intelligences (news gathering, code review, translation, etc.) |
| Dispatch | Folder | Collaboration strategies (brainstorm, debate, serial conversion, etc.) |
| Tube | JSON config | Signal topology designs and trigger chain recipes |
| HTML | File | Specialized human-machine interface pages |

---

## 12. Key Design Principles

- **Stateless API:** LLMs don't remember previous conversations. Every call sends complete context
- **LLM decides, code executes:** LLMs return structured JSON tool requests, not executable code
- **Auto-discovery pattern:** Tools, trimming strategies, Agents, pages, triggers, targets — all discovered by directory scanning
- **Layered isolation:** Each layer knows only adjacent layers. Four layers + three-layer context isolation
- **Process isolation:** Each Agent, each Tube step runs in an independent process
- **Filesystem communication:** Agents communicate through files. Naturally debuggable, future-distributable
- **Possibility management:** Complete code copies instead of parameterized configuration
- **Folder as product:** Agents, Dispatches, HTML pages are independently distributable product units
- **Additive complexity:** Adding Agents, strategies, Tubes, pages — all additive, never multiplicative
- **Declarative topology:** tubes.json is the single source of signal topology truth
- **AI operable:** Both carbon-based and silicon-based intelligence can fully operate the framework
- **Dual-side pluggable:** Tube's input side (triggers/) and output side (targets/) are both drop-file-to-extend

---

## 13. Engineering Roadmap

### Completed

| Item | Status |
|------|--------|
| Agent Layer Fundamental Loop | ✅ |
| Dispatch Layer roundtable strategy | ✅ |
| Tube Layer complete implementation | ✅ v3.0 |
| CLI entry point standardization | ✅ v3.0 |
| Configurable context_files mechanism | ✅ v3.0 |
| Agent tool suite (file/shell/tube/dispatch) | ✅ v3.0 |
| Tube Dashboard visualization | ✅ v3.0 |
| AI Operation Navigation Map MAP.md | ✅ v0.3 |

### Planned

| Item | Priority | Description |
|------|----------|-------------|
| Self-check tool validate.py | Medium | One-command validation of all configurations |
| Error classification refinement | High | AI determines "retry / skip / adjust strategy" |
| Result verification interface | Medium | Standardized reading and evaluation of task output quality |
| web_tools.py web search | High | Agent internet search capability |
| API authentication + rate limit | Low | Production environment security |
| Dispatch template standardization | Medium | More collaboration strategy templates |

---

*This document is the Architecture Overview v3.0 for the Intelligenism Agent Framework, covering the complete design of the Agent Layer, Dispatch Layer, Tube Layer, UI Layer, and the AI Operability characteristic.*
