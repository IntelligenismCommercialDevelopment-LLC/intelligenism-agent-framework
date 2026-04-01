# Intelligenism Agent Framework (IAF)

**A ~550-line Python agent framework built on [Intelligenism](https://intelligenism.org) theory. Each agent owns a complete copy of the engine. Additive complexity. True parallel execution. Folder = product.**

## What is IAF?

IAF is not just another multi-agent framework. It is the first practical implementation of **Intelligenism** — a theoretical framework on how intelligence emerges in organisations, whether human, artificial, or hybrid.

Most multi-agent frameworks hardcode a specific collaboration paradigm into the framework itself. CrewAI is role-playing sequential execution. AutoGen is multi-round dialogue with consensus convergence. If you could swap these out, these frameworks would lose their reason to exist.

IAF practises **possibility management**: each agent owns a complete, independent copy of the entire engine. You can modify any line of code, making any agent fundamentally different from any other — without risking impact on other agents. Adding an agent is copying a folder. Adding a collaboration mode is adding a folder. Complexity grows additively, never multiplicatively.

> From single neurons to complex neural networks, intelligence exhibits a bottom-up construction pattern. The complexity and intelligence potential of a neural network comes from the connections between individuals, not from making the individuals internally more complex. Therefore, maintaining the independence of individuals or small units, and expressing complexity outside the individual, is the essence of connectionism.
>
> — *Thinking, Conception and Construction of Intelligenism*

## Current Status: v1.0

- ✅ **Agent Layer (Fundamental Loop)** — complete and functional
- ✅ **Dispatch Layer (Roundtable strategy)** — complete and functional
- ✅ **Tube Layer (Signal Topology)** — complete and functional
- ✅ **UI Layer (Yellow Pages + Chat + Tube Dashboard)** — complete and functional
- ✅ **Shared Infrastructure** (llm_client + token_utils) — complete
- ✅ **Agent Toolset** (file / shell / tube / dispatch) — complete
- ✅ **CLI Entry Points** — standardised
- ✅ **Architecture Validation Tests** — complete

## Quick Start

```bash
# Clone the repo
git clone https://github.com/IntelligenismCommercialDevelopment-LLC/intelligenism-agent-framework.git
cd intelligenism-agent-framework

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install flask requests croniter

# Configure your LLM provider (see Configuration below)
# Edit config.json with your API key

# Start the server
python3 chat_server.py

# Open http://localhost:5000
```

Every time you open a new terminal, activate the environment first:

```bash
cd intelligenism-agent-framework
source venv/bin/activate
python3 chat_server.py
```

## Configuration

Edit `config.json` in the project root:

```json
{
  "providers": {
    "openrouter": {
      "url": "https://openrouter.ai/api/v1/chat/completions",
      "key": "your-openrouter-api-key"
    }
  },
  "default_provider": "openrouter",
  "default_model": "google/gemini-3-flash-preview"
}
```

IAF connects to LLMs via [OpenRouter](https://openrouter.ai/), which gives you access to Gemini, Claude, GPT, Qwen, and many other models through a single API key. You can add multiple providers and override models per agent in each agent's `agent_config.json`.

## Four-Layer Architecture

The framework consists of four fully independent layers. Units within each layer are also isolated from each other. The four layers communicate through Flask APIs and the filesystem — no layer invades another.

| Layer | What It Does | Unit Granularity | Self-Containment |
|---|---|---|---|
| **Agent Layer** | Complete runtime for a single intelligent agent | One folder = one agent | Copy the folder to deploy |
| **Dispatch Layer** | Multi-agent collaboration orchestration | One folder = one collaboration strategy | Copy the folder to deploy |
| **Tube Layer** | Signal topology — wiring between building blocks | One JSON entry = one signal pathway | Edit JSON to activate |
| **UI Layer** | Human-machine interaction interfaces | One HTML file = one feature page | Drop the file to use |

Plus cross-cutting infrastructure:

- **chat_server.py** — Router + Tube Runner bootstrap (no business logic)
- **tube_routes.py** — Tube Layer Flask Blueprint
- **dispatch_routes.py** — Dispatch Layer Flask Blueprint

## Agent Layer

The core of IAF: the **Fundamental Loop**. Each agent is a self-contained runtime (~267 lines of engine code):

- **LLM Communication** — Message assembly, API calls via OpenRouter, structured response handling
- **Tool Executor** — Auto-discovery registry, whitelist-based execution, tool call loop
- **Context Management** — Sliding-window trimming strategy, chat history persistence
- **Identity & Knowledge** — Configurable `context_files` loads any `.md` files as system prompt
- **Skills** — Trigger-based dynamic injection of task instructions

### build_messages: Five-Layer Assembly Model

| Layer | Source | When Loaded | Purpose |
|---|---|---|---|
| 1 | All files listed in `context_files` | Every call | Agent's identity, knowledge, routing |
| 2 | Skill `.md` files matched by trigger rules | On keyword match | Scenario-specific task instructions |
| 3 | `history.jsonl` | Chat mode only | Conversation memory |
| 4 | Current user message | Every call | This turn's input |
| 5 | Trim pass | Every call | Ensure context fits the window |

### Agent Capability Model

| Layer | Carrier | When Loaded | Analogy |
|---|---|---|---|
| Identity & Knowledge | `context_files` (.md) | Every call | Knowledge in the brain |
| Conditional Instructions | `skills` (.md + trigger rules) | On keyword match | Conditioned reflexes |
| Execution Ability | `tools` (*_tools.py) | Every call (schema injected) | Hands and feet |

### Available Tool Files

| File | Tools | Capability | Ships by Default |
|---|---|---|---|
| file_tools.py | read_file, write_file, list_dir | Read/write files, list directories | Yes |
| shell_tools.py | run_shell | Execute terminal commands | No |
| tube_tools.py | trigger_tube, list_tubes, tube_log | Trigger tubes, check status, read logs | No |
| dispatch_tools.py | run_dispatch, list_dispatch_strategies | Initiate multi-agent collaboration | No |

Drop a tool file into `agents/xxx/tools/` — auto-discovered, zero code changes.

### Agent Run Modes

| Mode | Context Source | Typical Caller |
|---|---|---|
| chat | Loads history.jsonl + context_files + skills | chat_server.py (user interaction) |
| batch | Clean context: context_files + skills only, no history | tube_runner (scheduled tasks) |

## Dispatch Layer

The Dispatch Layer implements multi-agent collaboration. It calls `lib/llm_client.call_llm()` directly — it does not go through the Agent engine. Agent folders are treated as "asset libraries + model config", not as executors.

> *Analogy: Agents are actors, Dispatch is the director. The director has actors read their own persona (SOUL.md) but can also hand them an entirely new script. The performance records on set belong to the director (sessions/), not the actor's personal diary (history.jsonl).*

### Dispatch Folder Structure

| File | Role |
|---|---|
| dispatch.py | Orchestration logic: round control, agent call order, termination conditions |
| dispatch_base.py | Infrastructure: tool loop, LLM parsing, staging |
| context_injector.py | Context assembly: reads agent files per config |
| session_manager.py | Session CRUD |
| dispatch_config.json | Participating agents, context_files, round limits |
| rules/*.md | Agent role definitions within the collaboration |
| sessions/ | Complete records of collaboration processes |
| *.html | (Optional) Dedicated UI page |

### Three-Layer Context Isolation

| Context Type | Owned By | Storage Location | Purpose |
|---|---|---|---|
| Agent chat history | Agent | agents/xxx/history.jsonl | User-agent interaction memory |
| Dispatch collaboration record | Dispatch | dispatch/xxx/sessions/*.jsonl | Multi-agent collaboration process |
| Per-call context | context_injector | In memory (not persisted) | Assembly result for a single LLM call |

Agents are unaware they participated in a Dispatch. Dispatch never touches an agent's `history.jsonl`. Isolation is an architectural guarantee.

## Tube Layer

Tube is the framework's third dimension — the **signal topology layer**. It describes signal pathways between building blocks: when a condition is met, trigger a target to execute.

### Why Tubes Matter

Existing multi-agent frameworks share a common problem: the pipes between building blocks are cast in place.

| Dimension | CrewAI / LangGraph / Manus | Intelligenism |
|---|---|---|
| Collaboration protocol | Built-in fixed patterns / fixed three-role | dispatch/ pluggable folders |
| Signal topology | Hardcoded, untouchable | tubes.json declarative rewiring |

Same three agents plus two dispatches — different Tube configurations produce entirely different system behaviour: serial pipelines, parallel fan-out, feedback loops. The blocks haven't changed; the wiring has, and so the system behaves completely differently.

### Tube Anatomy

A Tube consists of three elements:

| Element | Description |
|---|---|
| Triggers | What condition activates it (pluggable modules: cron, manual, API, file watch, etc.) |
| Steps | What to do sequentially after activation (Agent, Dispatch, another Tube) |
| Payload | Data passed from trigger source to target (prompt, file path, upstream output, etc.) |

### tubes.json — Single Source of Topology Truth

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
        "payload": { "prompt": "Process and analyse documents" } },
      { "type": "dispatch", "id": "roundtable",
        "payload": { "message": "Brainstorm based on analysis results" } },
      { "type": "tube", "id": "send_report" }
    ]
  }
]
```

### Trigger Sources

| Source | Triggered By | Mechanism |
|---|---|---|
| Cron | tube_runner (automatic) | croniter time comparison, 15-second polling |
| API | Human / AI agent / external system | POST /api/tube/trigger → flag file |
| Tube chain | Upstream tube step | steps with type=tube → inline execution |

### Drive Targets

| Target Type | Execution |
|---|---|
| Agent | subprocess → run_agent.py |
| Dispatch | subprocess → run_dispatch.py |
| Tube | Inline recursive execution (depth limit: 5) |
| Custom | Drop .py in targets/, implement build_command() |

### Execution Model

- **Subprocess isolation:** tube_runner only spawns subprocesses, never imports business code
- **Serial steps:** Next step runs only if previous exits with code 0
- **Parallel tubes:** Different tubes execute in independent threads, non-blocking
- **Duplicate prevention:** running_tubes dictionary prevents re-firing the same tube
- **Hot reload:** tubes.json is re-read every polling cycle — no restart needed
- **Fire-and-forget:** Triggering does not block the main loop

### Tube API Endpoints

| Method | Path | Function |
|---|---|---|
| GET | /api/tubes | List all tubes + real-time status |
| GET | /api/tube/status | Lightweight status query |
| POST | /api/tube/trigger | Manual trigger |
| GET | /api/tube/log | Query logs (supports filtering) |
| GET | /api/tube/log/grouped | Logs grouped by tube |
| DELETE | /api/tube/log | Clear logs |

## UI Layer

A browser-based interface following the yellow-pages architecture. Every HTML page is a fully self-contained page. Zero coupling between pages. No unified SPA shell, no shared router.

| Type | Location | Route |
|---|---|---|
| Yellow pages | index.html | GET / |
| Basic chat | chat.html | GET /chat |
| User pages | pages/*.html | GET /pages/\<name\> |
| Dispatch UI | dispatch/xxx/*.html | GET /dispatch/\<name\> |
| Tube dashboard | pages/tube-dashboard.html | GET /pages/tube-dashboard |

The **Tube Dashboard** provides: tube list with live status, manual trigger buttons, expandable config details and step payloads, real-time scrolling execution logs, and per-tube log clearing. Auto-polls every 10 seconds.

## AI Operability

IAF is not just "an AI framework for humans" — it is **an AI framework that AI can also operate**. The design features — minimal codebase, filesystem communication, JSON declarative config, process isolation — make the framework fully controllable by both carbon-based and silicon-based intelligence.

A top-tier AI agent taking over a framework must pass four gates: understand, modify, deploy, monitor. IAF is clear on every gate:

| Gate | IAF | Traditional Frameworks |
|---|---|---|
| Understand | ~550 lines, fits in one context window | 10,000+ lines, requires modular comprehension |
| Modify | Edit JSON and Markdown files | Write Python/SDK code conforming to framework constraints |
| Deploy | `python3 chat_server.py` — one command | docker compose / kubernetes |
| Monitor | `cat tube_log.jsonl` — plain text | Dedicated dashboards / monitoring APIs |

## Directory Structure

```
intelligenism-agent-framework/
│
├── config.json                     # Global config (provider connections)
│
├── lib/                            # Shared infrastructure
│   ├── llm_client.py               # HTTP LLM calls + retry + error classification
│   └── token_utils.py              # Token estimation
│
├── template/                       # Fundamental Loop template (copy to create agents)
│   ├── core/
│   │   ├── direct_llm.py           # Engine core (~267 lines)
│   │   └── tool_executor.py        # Tool auto-discovery registry
│   ├── tools/
│   │   └── file_tools.py           # Default tool set
│   ├── context/
│   │   └── sliding_window.py       # Default trimming strategy
│   ├── skills/                     # Empty directory (populate per agent)
│   ├── SOUL.md                     # Identity template
│   └── agent_config.json           # Agent config template
│
├── agents/                         # Agent instance directory
│   └── default/                    # (same structure as template/)
│
├── dispatch/                       # Collaboration strategy library
│   └── roundtable/                 # Roundtable discussion strategy
│       ├── dispatch.py             # Orchestration logic
│       ├── dispatch_base.py        # Infrastructure (tool loop, LLM parsing)
│       ├── context_injector.py     # Context assembly
│       ├── session_manager.py      # Session management
│       ├── dispatch_config.json    # Participating agents + file read rules
│       ├── context/                # Dispatch-specific trimming
│       ├── rules/                  # Agent role definitions
│       ├── sessions/               # Collaboration records
│       ├── staging/                # Runtime temporary files
│       └── roundtable.html         # Dedicated UI page
│
├── tube/                           # Signal topology layer
│   ├── tube_runner.py              # Main loop engine
│   ├── triggers/                   # Pluggable trigger sources
│   │   ├── cron.py                 # Cron scheduling
│   │   └── manual.py              # Manual / API trigger
│   ├── targets/                    # Pluggable drive targets
│   │   ├── agent.py                # Drive agent
│   │   └── dispatch.py             # Drive dispatch
│   ├── run_agent.py                # Agent CLI entry
│   ├── run_dispatch.py             # Dispatch CLI entry
│   ├── tubes.json                  # Single source of topology truth
│   ├── tube_log.jsonl              # Execution log
│   └── manual_triggers/            # Flag file directory
│
├── chat_server.py                  # Web server / router + Tube Runner
├── dispatch_routes.py              # Dispatch Flask Blueprint
├── tube_routes.py                  # Tube Flask Blueprint
├── index.html                      # Yellow pages (feature index)
├── chat.html                       # Basic chat interface
├── pages/                          # User-created pages
│   └── tube-dashboard.html         # Tube monitoring panel
└── tests/
    └── test_template.py
```

## Four Orthogonal Extension Dimensions

The framework's four extension dimensions are fully orthogonal — a change in any one dimension does not affect the other three:

| Operation | How | Code Changes |
|---|---|---|
| Add new agent | `cp -r template/ agents/xxx/` | 0 lines |
| Add tool to agent | Drop `.py` in `agents/xxx/tools/` | 0 lines |
| Add knowledge to agent | Add `.md` file, update `context_files` in agent_config.json | 0 lines |
| Add skill to agent | Drop `.md` in `agents/xxx/skills/`, add trigger rule | 0 lines |
| Change trimming strategy | Swap `.py` in `agents/xxx/context/` | 0 lines |
| Add collaboration mode | Add folder in `dispatch/` | 0 lines |
| Add signal pathway | Add entry in `tube/tubes.json` | 0 lines |
| Add trigger type | Drop `.py` in `tube/triggers/` | 0 lines |
| Add drive target | Drop `.py` in `tube/targets/` | 0 lines |
| Add LLM provider | Add entry in `config.json` | 0 lines |
| Add UI page | Drop `.html` in `pages/` | 0 lines |

## Tradeable Product Units

The framework produces four types of tradeable product units:

| Product Unit | Form | Use Case |
|---|---|---|
| Agent | Folder | Domain-specific agents (news gathering, code review, translation, etc.) |
| Dispatch | Folder | Collaboration strategies (brainstorm, debate, serial conversion, etc.) |
| Tube | JSON config | Signal topology recipes and trigger chain designs |
| HTML | File | Purpose-built human-machine interaction interfaces |

## Tech Stack

Pure Python + HTML. No Node.js, no npm, no frontend build tools.

```
pip install flask requests croniter
```

That's it.

## Why Not CrewAI / AutoGen / LangGraph / Manus?

| Dimension | CrewAI / AutoGen / LangGraph | Manus | IAF |
|---|---|---|---|
| Code size | 10,000+ lines | Closed source | ~550 lines |
| Engine model | One shared engine | Fixed three-role | Each agent gets independent engine |
| Complexity growth | Multiplicative | Hidden internally | Additive |
| Parallelism | Async pseudo-parallel | Cloud parallel | Multi-process true parallel |
| Fault isolation | None (one crash = all crash) | Unknown | Complete isolation |
| Customisability | Within config parameters | Not customisable | Any line of code |
| Collaboration paradigm | Hardcoded in framework | Fixed three-role | Pluggable, independent folders |
| Signal topology | Hardcoded | Hardcoded | tubes.json declarative rewiring |
| AI operability | Difficult (10k+ lines + SDK) | Impossible (closed source) | Fully operable (550 lines + filesystem) |
| Product unit | Code (hard to circulate) | SaaS service | Folder / JSON entry (tradeable) |
| Dependencies | Many third-party libraries | N/A | flask + requests + croniter |

## Mapping to Intelligenism Theory

| Intelligenism Concept | Architectural Implementation |
|---|---|
| Autonomous units of intelligent consortiums | Each agent's independent Fundamental Loop |
| Carbon-silicon symbiosis, loose coupling | Process isolation + filesystem communication + AI operability |
| Pluggable collaboration paradigms | Independent strategy folders under dispatch/ |
| Rewirable signal topology | Declarative configuration via tubes.json |
| Deterministic scheduling without LLM | Tube orchestration is pure Python, not probabilistic LLM judgment |
| Possibility management over determinism | Copy full code instead of parameterised configuration |
| Value is in the wiring, not the blocks | Agent + Dispatch + Tube assembly knowledge is the competitive moat |

## Key Design Principles

- **Stateless API:** LLMs don't remember previous conversations. Full context is re-sent every call.
- **LLM decides, code executes:** LLMs return structured JSON tool requests, not executable code.
- **Auto-discovery pattern:** Tools, trimming strategies, agents, pages, trigger sources, drive targets — all discovered by scanning directories.
- **Layered isolation:** Each layer only knows its adjacent layer. Four layers + three-layer context isolation.
- **Process isolation:** Each agent, each Tube step runs in an independent process.
- **Filesystem communication:** Agents communicate via files. Naturally debuggable, future-distributable.
- **Possibility management:** Copy full code instead of parameterised configuration.
- **Folder as product:** Agents, Dispatches, HTML pages are independently distributable product units.
- **Additive complexity:** Adding agents, collaboration modes, tubes, pages — all additive.
- **Declarative topology:** tubes.json is the single source of signal topology truth.
- **AI operable:** Both carbon-based and silicon-based intelligence can fully control the framework.
- **Dual-side pluggable:** Tube input side (triggers/) and output side (targets/) are both extend-by-dropping-files.

## Links

- **Website**: [intelligenism.club](https://intelligenism.club)
- **Full Theory**: [intelligenism.org](https://intelligenism.org)

---

## Author

Designed and created by **Minghai Zhuo**

---


## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

© 2025-2026 Intelligenism Commercial Development LLC
