# IAF Shell 架构说明与改造开发指引

> **本文档是 `iaf_template` 的唯一权威指引**，覆盖两件事：
> ① **架构与工作原理**（引擎怎么跑）；② **操作指南**（怎么基于本模板新建 IAF、新增 dispatch、给它看图）。
> 后续一切基于本模板的改造开发，以本文档为准。

---

## 1. 概述

`iaf_template` 是一个**轻量、自包含、Shell 化的 Agent Dispatch 框架**，为各类 IAF dispatch 流水线提供底层 LLM 调用能力。只依赖 `requests` / `flask`，平铺顺序执行、无编排图，会话级状态、默认不跨会话持久化。

**当前形态 = Shell 四区结构**。核心理念一句话：

> **配置与引擎物理分离** —— 人和 agent 只编辑 `config_base/` 与 `prompt_base/`（正本），`IAF_AI/`（引擎）**默认不手改**，引擎内部运行所需的配置由 `sync_config.py` 从正本**生成**。

三条硬收益：改配置永不碰引擎代码；prompt 集中外置、随处引用；产出集中在 `workspace/`。

**自包含到什么程度**：`IAF_AI/lib/` 住在模板内部，所以整棵树拷到任何位置（集群内、集群外、任意深度）都能自己跑起来，不依赖外部共享目录（见 §12）。

---

## 2. Shell 四区结构

```
iaf_template/                          ← shell root
├── chain.py                    # ★ 运行入口：sync → dispatch → 写 workspace
├── sync_config.py              # ★ config_base → IAF_AI 内部（含路径翻译）
├── TOOL_CONTRACT.md            # 工具契约
├── internal-environment-setup.md
│
├── config_base/                # ★ 配置正本（source of truth，人/agent 只改这里）
│   ├── config.json             #   全局 provider + services（含 API key，勿外泄）
│   └── {dispatch}/
│       └── dispatch_config.json  # 该 dispatch 的 agent 配置
│
├── prompt_base/                # ★ prompt 唯一存放点（context_files 直接引用此处）
│   └── {dispatch}/
│       └── rules/*.md          #   persona.md、compact_v4.md 等
│
├── tools/_shared/              # ★ shell 级共用件的正本（不是 LLM 可见的工具）
│   ├── explore_tools.py        #   free_search 围栏读写（由 sync 铺进用得上的 dispatch）
│   ├── status_log.py           #   逐事件实时 STATUS 轨迹（同上）
│   ├── image_parts.py          #   图片 → 多模态 content parts（chain 直接 import·§14）
│   └── ledger_roots.json       #   status_log 的台账落点
│
├── workspace/                  # ★ 产出区
│   ├── {dispatch}/             #   output_*.md / tool_calls_*.json
│   ├── {session_id}/           #   本次运行的留痕档（STATUS.md 等）
│   └── current → {session_id}/ #   ★相对软链，指向本次运行档
│
└── IAF_AI/                     # ★ 引擎（内部 config 由 sync 生成，勿手改）
    ├── config.json             #   ← sync 生成，勿手改（正本在 config_base/config.json）
    ├── chain.py                #   占位 placeholder（raise NotImplementedError），已被 shell 根 chain.py 取代
    ├── validate.py             #   配置 & 工具校验 CLI
    ├── generate_manifest.py    #   生成 MANIFEST.json 系统地图
    ├── lib/                    #   ★ 模板自带的共用库（§12）
    │   ├── llm_client.py       #     HTTP LLM 客户端（多 provider、重试、错误分类、流式）
    │   ├── token_utils.py      #     token 估算
    │   ├── jev_client.py       #     JEV 判定客户端
    │   └── voice.py            #     TTS
    └── dispatch/
        └── {dispatch}/         # 引擎侧 dispatch（复制 template/ 而来）
            ├── dispatch.py             # ★ 核心调度器 TemplateDispatch
            ├── dispatch_base.py        # 共享基础设施（LLM 调用、工具循环）
            ├── dispatch_config.json    # ← sync 生成，勿手改
            ├── compaction.py           # 长会话压缩（merge_recompress）
            ├── v4_processor.py         # 共享 LLM 文本处理服务
            ├── session_io.py           # 会话读写（history.jsonl 等）
            ├── session_manager.py      # 会话格式（渲染成 markdown）
            ├── session_close.py        # 会话关闭 → 记忆更新
            ├── runtime.py              # 线程本地上下文 & SSE 事件队列
            ├── context/sliding_window.py   # 轻量裁剪
            ├── memory/                 # agent 私有记忆
            ├── sessions/               # 运行时会话目录 sessions/{sid}/
            ├── staging/                # 临时文件（当前轮）
            └── tools/                  # 工具（见 §7）
```

**四区职责：**

- **`config_base/`** —— 人 / agent 写；provider 全局配置 + 各 dispatch 配置；是正本，不被 sync 覆盖。
- **`prompt_base/`** —— 人 / agent 写；所有 prompt / rules；是正本，运行时直接读（`rules/` 另由 sync 下发一份给压缩器，见 §4.4）。
- **`workspace/`** —— 引擎与 chain 写；产出与留痕；不被 sync 覆盖。
- **`IAF_AI/`** —— 默认不手改；引擎代码 + sync 生成的内部 config（`config.json` 与各 `dispatch_config.json` **每次 sync 都被覆盖重写**）。

---

## 3. 核心原则（改造/开发铁律）

1. **`config_base` 是 source of truth** —— 一切配置修改只在 `config_base/` 里做。
2. **`prompt_base` 是 prompt 唯一存放点** —— dispatch 的 `context_files` 直接引用此处；改 prompt **不需要 sync**（运行时直接读）。
3. **`IAF_AI/` 内部配置由 `sync_config.py` 生成** —— 不手动编辑引擎内的 `dispatch_config.json` / `config.json`（会被覆盖）。
4. **引擎默认加法优先** —— 新增能力优先靠**新增 dispatch / 新增工具文件**，而非改 `dispatch.py` / `dispatch_base.py` / 工具内核（改内核有跨副本 blast radius）。确需改内核时：先征得同意 → 改母版 `iaf_template` 并同步各副本。**不是禁区。**
5. **路径约定** —— `config_base` 中所有相对路径以 **shell root** 为基准（sync 负责翻译成引擎能懂的相对路径，见 §4）。
6. **不依赖集群根的名字或位置** —— 配置里不写死绝对路径；要够外部共享物就用相对路径出壳。

---

## 4. 两层配置模型与路径系统 ★（最关键的心智模型）

同一份 `dispatch_config.json` 存在**两份**：`config_base/` 里是**正本**（人写，shell-root-relative），`IAF_AI/dispatch/{name}/` 里是**副本**（sync 生成，引擎读，agent_dir-relative）。理解这两层的路径差异，是本框架最容易踩坑也最重要的地方。

### 4.1 sync 时的路径翻译（`sync_config.py::_translate_path`）

`sync_config.sync()` 把 `config_base/config.json` 直接复制到 `IAF_AI/config.json`；遍历 `config_base/{dispatch}/dispatch_config.json`，**翻译其中每个 agent 的 `context_files` 路径**后写入引擎副本。翻译规则（正本写法 → 引擎副本里变成什么）：

- 绝对路径 `/Users/.../soul.md` → **不翻译**，原样。
- 带前缀 `wiki:` / `session:` / `briefs:` → **不翻译**，原样。
- shell-root 相对 `prompt_base/{d}/rules/x.md` → 前置 `../../../`，成为 `../../../prompt_base/{d}/rules/x.md`。
- shell-root 相对（指向上级）`../shared/x.md` → 同样前置，成为 `../../../../shared/x.md`。

**为什么是 `../../../`**：引擎侧 agent_dir = `IAF_AI/dispatch/{name}/`，距 shell root 正好 **3 层**。运行时引擎以 agent_dir 为基准解析相对路径，前置 `../../../` 即回到 shell root。判定逻辑：绝对路径（`/` 开头）跳过；含 `:` 且非 `./` 开头的（前缀路径）跳过；其余相对路径一律前置。

### 4.2 运行时的路径解析（`dispatch.py::_resolve_context_path`）

引擎读的是**副本**里的 `context_files`。核心解析器**只识别 `session:` 前缀**：

- `session:xxx` → `sessions/{sid}/xxx`（sid 为空则该项跳过）
- 其它（绝对路径、`../../../...` 相对路径）→ 以 **agent_dir** 为基准 join → 落到 shell 里的真身文件

> **⚠️ 精确边界**：核心 `context_files` 读取器**不解析 `wiki:` / `briefs:`**。这两个前缀是**记忆更新子系统（processor / session_close，§10）专用**，用于 `context_sources` / `output_target` / `rules_file`。核心 agent 的 `context_files` 只应使用：**绝对路径 / shell-root 相对路径 / `session:`**。误把 `wiki:` 写进核心 `context_files`，会被 join 成垃圾路径、**读成空串且不报错**。

### 4.3 一图看懂

```
config_base/{d}/dispatch_config.json          IAF_AI/dispatch/{d}/dispatch_config.json
  context_files（人写，shell-root 基准）  ──sync 翻译──►  context_files（引擎读，agent_dir 基准）
    "prompt_base/{d}/rules/persona.md"                     "../../../prompt_base/{d}/rules/persona.md"
    "../shared/x.md"                                        "../../../../shared/x.md"
    "/abs/soul.md"                                          "/abs/soul.md"（不变）
    "session:staging/compacted_history.md"                 "session:staging/compacted_history.md"（不变）
```

### 4.4 sync 还会下发两类文件（不只是翻译 config）

- **`prompt_base/{d}/rules/*` → `IAF_AI/dispatch/{d}/rules/`**（`_sync_rules`）。压缩器读 `v4_rules.compact`（缺省 `"rules/compact_v4.md"`），而那个相对路径是**相对引擎 dispatch 目录**解析的 —— 不下发则永远找不到，且 `_load_rules` 找不到时**不 raise**：它返回一句 `"(V4 rules file not found: ...)"`，压缩器拿这句话当 system prompt 继续调 LLM，把垃圾摘要写进 `compacted_history.md` 并给那几轮打上 `trimmed_marker`，之后加载历史永久跳过 ⇒ **静默毁历史**。
- **`tools/_shared/{explore_tools,status_log}.py` → `IAF_AI/dispatch/{d}/tools/`**（`_sync_tools`）。`explore_tools.py` 只在该 dispatch 配了 `free_search` 时铺；`status_log.py` 只要该 dispatch 带工具就铺（缺失是**静默降级**：工具照常工作，只是没有实时轨迹）。顺带清掉 `marker_*_tools.py` 探针。
  `tools/_shared/` 下的**其它**文件（如 `image_parts.py`）**不下发** —— 它们是给 shell 层（chain）用的，不是 LLM 可见的工具。

---

## 5. chain.py 运行入口（shell 层）

shell root 的 `chain.py` 是**真正的运行入口**（`IAF_AI/chain.py` 是抛 `NotImplementedError` 的占位）。流程：

```
run(dispatch_name="template", user_message="", agent_name="core",
    session_id=None, image_paths=None, image_detail=None)
  │
  ├─ Step 1: sync_config.sync()                      # config_base → 引擎内部（含路径翻译）
  │
  ├─ Step 1.5: 建 workspace/{session_id}/ + 把 workspace/current 软链指过去 + 开 STATUS 轨迹
  │
  ├─ Step 2: 定位 agent_dir = IAF_AI/dispatch/{dispatch_name}/
  │          importlib 动态加载 dispatch.py → TemplateDispatch
  │
  ├─ Step 3: disp = TemplateDispatch(agent_dir)
  │          disp.new_session(session_id, agent_name)
  │          content = image_parts.build_content(user_message, image_paths)   # §14
  │          disp.append_to_history("user", content)        # 若非空
  │          result = disp.run(agent_name)
  │
  └─ Step 4: 写 workspace/{dispatch_name}/
             output_{ts}.md       ← result["text_response"]（非空时）
             tool_calls_{ts}.json ← result["tool_calls"]（非空时）
```

命令行：

```bash
python chain.py --dispatch template --agent core --message "..." \
                [--session sid] [--image /abs/a.png --image /abs/b.png] [--image-detail auto]
```

- `--image` 可重复，顺序 = 送进模型的顺序；细节见 §14。
- `--agent` 必须与 `dispatch_config.json` 的 `agents.<key>` 逐字一致。

> **注意**：shell 根 `chain.py` **不调用 `close_session()`** —— 因此**记忆更新（§10）在默认 chain 流程中处于暂停状态**。需要记忆更新的场景，由具体 IAF 定制的 chain 显式调用 `close_session()`，或用 `run_single_message()`（它内含 close）。

---

## 6. 引擎核心：TemplateDispatch

**文件**：`IAF_AI/dispatch/{name}/dispatch.py`。`__init__(agent_dir)` 时：`agent_dir` 取绝对路径；`project_root = agent_dir 上两层 = IAF_AI/`（故全局 `config.json`、`lib/` 都从 `IAF_AI/` 读）；加载副本 `dispatch_config.json`；调 `_fix_sibling_paths()`。

### 6.1 公开 API

- `new_session(session_id=None, agent_name="core_chat")` —— 建会话 `sessions/{sid}/`，返回 sid。
- `append_to_history(role, content)` —— 注入 `user_input` / `agent_response` 到历史（chain 用它塞任务指令）。`content` 可以是**字符串**，也可以是**多模态 parts 列表**（§14）；引擎不解释它。
- `run(retry=False, agent_name="core_chat") -> dict` —— 执行一次调度，返回 `{tool_calls, text_response, duration_ms}`。
- `close_session()` —— 关闭会话 → 触发记忆更新（§10）。
- `run_single_message(msg, agent_name)` —— 一步到位：new → 注入 → run → **close**。
- `run_with_tools(msg, tool_defs, handler, exit_tools, max_tool_loops, agent_name)` —— 绕过常规工具加载，注入自定义工具的单轮调用。

> ⚠️ `agent_name` 引擎默认是 `"core_chat"`；shell 根 `chain.py` 默认传 `"core"`，与最小骨架 `dispatch_config.json` 的 `agents.core` 对齐。**三处必须逐字一致**（引擎默认 / chain 缺省 / config 键名），这是头号坑。

### 6.2 `run()` 全链路

```
run(agent_name)
  ├─ 解析 LLM caller（lib/llm_client.call_llm）+ endpoint（provider → url,key）
  ├─ 若 compaction.enabled → compaction.check_and_trigger()（§9.2）
  ├─ _build_context(context_files) → (system_prompt, external_tools)
  │     .md/文本  → 拼进 system prompt（以 "\n\n---\n\n" 连接，每份前贴 "# [文件名]"）
  │     *_tools.json → external_tools（LLM tools 定义）
  ├─ _load_conversation_history()  → messages（排除 trimmed_marker 轮，最多 50 轮）
  ├─ 加载工具（两种模式，二选一，见下）
  ├─ call_with_tool_loop(...)（§6.3）
  ├─ log_call + 把 agent_response 追加进 history + 按需存 reasoning
  └─ return { tool_calls（仅保留 exit_tools/动作类）, text_response, duration_ms }
```

**工具加载两模式（互斥）：**

- **Free Search 模式** —— 触发条件：agent 配置的 `free_search` 里 `free_search_path` 或 `write_allow` **任一非空**（★只写 `write_allow` 也会触发，照抄母版时注意）。工具来源 = `explore_tools.make_tools(read_roots, write_root)` 生成的**围栏**工具。`tool_choice` 取 `free_search.tool_choice`（默认 `auto`）；`max_loops` 取 `free_search.max_tool_loops`（默认 40）；`exit_tools` = `{"write_file"}`（当 `exit_on_write`，默认 True）。
  ★`read_roots` 取的是 `free_search_path`（不是 `read_allow`）；它为空时读工具全部消失，只剩写工具。
  ★`write_root` 由 `os.path.realpath(output_path)` 算，**相对路径按进程 CWD 解析**。
- **标准模式** —— 否则。工具来源 = `tools/*_tools.py` 的 `TOOLS` + `context_files` 里 `*_tools.json` 声明的外部工具（自动 ack handler）。`max_loops` 固定 10，`exit_tools` = 外部工具名集合。
  `tool_choice` **只在存在外部工具时**才强制，且按 provider 白名单判：`openrouter`/`openai`/`xai` → `"required"`，`anthropic` → `{"type":"any"}`，**其余 provider（含 `deepseek`）落到 `None`**。
  ⚠ 这意味着把 provider 换成白名单外的一家、同时又靠外部工具收产物时，强制工具会**静默消失**（模型可以只回文本）。模板出厂没有 `*_tools.json`，所以这条现在不触发；真要用就先核实那家认不认 `tool_choice: "required"`，再决定是加进白名单还是换收料方式。

### 6.3 工具循环（`dispatch_base.call_with_tool_loop`）

```
无工具 → 单次 call_llm，返回 text。
有工具 → 循环最多 max_tool_loops 次：
  call_llm(tools, tool_choice)
   ├─ 无 tool_calls：
   │     若有 exit_on_tools → 注入 "请使用工具提交你的行动…" 重试提示，continue
   │     否则 → 返回 text（正常结束）
   └─ 有 tool_calls：逐个执行 handler，把 assistant.tool_calls + tool 结果追加进 messages
         若调用了 exit_on_tools 里的工具（终止型动作）→ 立即返回
  达到 max_loops → 返回已有 text 或 "[Tool loop limit reached]"
```

**`exit_on_tools` 机制**：某些"终止型"工具一旦被调用，循环立即退出，避免多轮无意义对话。`_parse_llm_response` 兼容多种响应形态（纯文本、Anthropic content blocks、`{choices:[{message}]}` OpenAI 包裹等）。

### 6.4 `_fix_sibling_paths`

多个 `TemplateDispatch` 实例共存时，Python 模块缓存会让所有 agent 共享同一份 `session_io`/`runtime` 等兄弟模块。每次操作前调 `_fix_sibling_paths()` 把这些模块的 `_DISPATCH_DIR`/`_PROJECT_ROOT` 改回本实例，确保各 agent 读写自己的目录。

---

## 7. 工具系统

### 7.1 两类工具

- **Python 工具** —— `tools/*_tools.py` 导出 `TOOLS` dict，引擎 glob 自动发现。有 handler、能真执行。
- **JSON 工具** —— `*_tools.json` 放进 `context_files`，由 `_build_context` 解析成外部工具。只声明 schema，引擎自动生成 ack handler（返回 `{"status":"accepted","tool":<name>,"input":<args>}`），真实数据由 chain 从 `tool_calls` 提取。dispatch 与业务逻辑因此解耦。

**Python 工具规范**：
```python
# tools/xxx_tools.py
def _handler(args): return "结果字符串"
TOOLS = {
  "tool_name": {"description": "...", "parameters": {...JSON Schema...}, "handler": _handler}
}
```

★工具模块在 **import 期不许 raise** —— 引擎装载失败只打一行 warning 然后继续，该工具静默不存在。

### 7.2 模板自带工具

`IAF_AI/dispatch/template/tools/` 出厂一个示例工具 `write_document`（`write_document_tools.py`），作为「固定目标 + 原子写」领域工具的照抄范式：把 LLM 生成的 markdown 覆盖写入固定落点 `workspace/<agent_id>/document.md`；**只收 `content`、不收 path**；`agent_id` 从本 dispatch 的 `dispatch_config.json` 顶层读；同目录临时文件 + `os.replace` 原子写；围栏校验目标落在本 IAF `workspace/` 内。

> **新建 IAF 时**：照它改工具名 / 文件名 / 落点 / description；要写多个产物就加多个 `write_*` handler，各自固定目标。**领域工具只需 `.py` + `TOOLS` dict** —— 引擎从 `description` / `parameters` / `handler` 直接构建 LLM 可见定义，**不必再配 `_tools.json`**。

另外 `explore_tools.py` 与 `status_log.py` 由 sync 从 `tools/_shared/` 铺进来（§4.4），不要在引擎侧手改它们 —— 正本在 `tools/_shared/`。

### 7.3 Free Search 模式

围栏靠 `make_tools(read_roots, write_root)` 的工厂闭包 + `os.path.realpath` 白名单，**代码层杜绝越界，不靠自律**。模板最小骨架里 `free_search_path` / `read_allow` 都是空、`write_allow` 只有 `workspace/current` ⇒ 只有写工具、没有读工具。

要开读：填 `free_search_path`（★不是 `read_allow`，读根取的是前者）。要改写落点：改 `write_allow` 与 `output_path`。
围栏拒绝一次越界写是**围栏在工作**，不是故障 —— 轨迹里会留一行 `[越界] ... 不在允许范围内`。

---

## 8. 会话管理

**存储结构**（`IAF_AI/dispatch/{name}/sessions/{sid}/`）：`history.jsonl`（对话历史）、`call_log.jsonl`（调用计时/token 事件）、`meta.json`（元数据）、`staging/`（当前轮临时文件）、`search_archive/`。

**历史记录类型**：`session_start` / `user_input` / `agent_response` / `trimmed_marker`（压缩标记，带 `compacted_rounds`）。加载时（`_load_conversation_history`）自动**跳过被 `trimmed_marker` 覆盖的轮次**，并限制最多 50 轮。

`user_input` / `agent_response` 的 `content` 既可能是字符串，也可能是多模态 parts 列表（§14）。**凡只会读文本的旁路都要过 `_content_text()`**（`session_io` 的会话标题、`sliding_window` 的 token 估算、`session_manager` 的历史渲染各有一份同样的 helper）。新代码别直接对 `content` 做字符串操作。

---

## 9. 上下文管理（两级策略）

### 9.1 Sliding Window（轻量裁剪）

`context/sliding_window.py::trim_records`：按 token 预算逐轮淘汰旧记录，保护头部（session_start + 首条 user）与尾部（最后一条 user），在删除处插入 `trimmed_marker`。Token 估算 = `ASCII 数/4 + 非 ASCII 数/2`（它自带一份，不走 `lib/token_utils`）。

⚠ 它的唯一调用处是 `dispatch_base.write_staging_history`，而**那个函数当前在模板内没有调用方** —— 也就是说这条裁剪策略现在不参与任何真实运行。要用它就得自己在定制 chain 或 dispatch 里调 `write_staging_history`。

### 9.2 Compaction（长会话 LLM 压缩）

`compaction.py`：当会话 token 超 `trigger_threshold_tokens` 时，用 LLM 把旧轮次 `merge_recompress` 成结构化 markdown，写入 `session:staging/compacted_history.md`，原轮次标 `trimmed_marker`。免压保护区 = 最近 `exempt_min_rounds` 轮或 token 低于 `exempt_min_tokens` 的尾部。POSIX 文件锁防并发。在 `run()` 起点按 `compaction.enabled` 触发。规则文件的下发见 §4.4。

### 9.3 V4 Processor（共享文本处理服务）

`v4_processor.py`：**不是 agent，是单次 LLM 调用工具**，把"非结构化文本 → 结构化 markdown"抽象为可复用服务，切 `rules_file` 即换转换规则（模板默认 `prompt_base/template/rules/compact_v4.md`）。被 compaction、记忆写入、搜索结果处理等复用。

---

## 10. 记忆更新子系统（session_close）

`close_session()` → `session_close.close()` → 若干 **processor 型 writer agent** 用 LLM 更新个人记忆：

- `memory_experiences_writer` → `wiki:agents/{agent_id}/memory/experiences.md`
- `memory_relationships_writer` → `wiki:agents/{agent_id}/memory/relationships.md`
- `memory_emotional_state_writer` → `wiki:agents/{agent_id}/memory/emotional_state.md`

每个 writer 在 `dispatch_config.json` 里声明 `rules_file` / `context_sources` / `output_target`。**这里才是前缀的解析处**：`session:` → `sessions/{sid}/`；`briefs:` → `{project_root}/briefs/`；`wiki:` → `{wiki_root}/`；无前缀 → dispatch 目录下。

> **状态提醒**：如 §5 所述，默认 chain 流程**不调 `close_session()`**，故记忆更新暂停。要启用：配置 `session_close.enabled: true` **并**在 chain 里显式调用 `close_session()`（或走 `run_single_message`）。

---

## 11. 配置文件详解

### 11.1 全局 provider 配置（`config_base/config.json`）

`providers`（每个含 `url` + `api_key`/`key`）、`default_provider`、`default_model`、`services`（如 `jina_reader`、`brave_search`，供领域工具读 key）。

- **endpoint 解析**（`resolve_llm_endpoint`）：指定 provider → `default_provider` → 第一个可用；key 取 `p["api_key"] or p["key"]`（**两种键名都兼容**）。
- ⚠️ 此文件含真实 API key：**勿提交进任何仓库、勿写进 prompt / 文档**。查它一律 `grep -n 键名`，不要整段打印。

**端点形状由 URL 自动判别**（`lib/llm_client.py`），各家的差异都收在客户端这一层：

- `api.anthropic.com` → Messages API：`system` 提到顶层、`max_tokens` 必填、响应是 content blocks、结束原因叫 `stop_reason`；客户端把它归一成 Chat Completions 形状。
- 以 `/responses` 结尾（如 xAI / OpenAI 的 Responses API）→ payload 是 `{"model", "input"}`，不是 `messages`。
- `api.deepseek.com` → **官方直连**：不下发 OpenRouter 的 `provider` 路由字段；reasoning 走原生形状（`thinking: {"type":"enabled"}` + 顶层 `reasoning_effort`）。
- 其余 → 标准 Chat Completions（OpenRouter 等），`provider_routing` 在这里翻成 `provider` 对象。

★**思考模式下正文可能整篇落在 `reasoning_content` 而 `content` 为空**。客户端会打一行 WARN，但那只是打印 —— 活干完了、钱付了，上层拿到空串。注意区分：这一轮是**工具调用**轮时，`content` 为空是正常的（`finish_reason=tool_calls`），那行 WARN 是误报。

### 11.2 Dispatch 配置（`config_base/{dispatch}/dispatch_config.json`）

顶层：`display_name`、`agent_id`、`agents{}`、`max_context`、`session_close{enabled}`、`v4_rules{}`、`compaction{}`。

`agents{}` 内两类角色：

- **核心 agent（`role: core`）**：`provider` / `model` / `temperature` / `top_p` / `context_files` / 可选 `reasoning` / 可选 `reasoning_data_output_path` / 可选 `provider_routing` / 可选 `free_search`（§7.3）。
  - **`model` 的写法随 provider 变**：走 OpenRouter 要带供应商前缀（`deepseek/deepseek-v4-pro`）；走官方直连**不带前缀**（`deepseek-flash`）。写错不会被本地拦住，是服务端的 400。
  - **`provider_routing` 是 OpenRouter 专属**（供应商钉选，稳住前缀缓存）：空/缺 → 默认路由；`"slug"` → 钉死到该供应商（`allow_fallbacks:false`）；`"slug/quant"` → 再限定量化；也可写完整 OpenRouter `provider` 对象。
    ⛔ 走官方直连时**不要配它** —— 客户端会整个丢掉，留着只是个骗人的死键。
  - **`reasoning`** 在 OpenRouter 下翻成 `reasoning` 对象（`effort` 与 `max_tokens` 互斥，`exclude` 透传）；在 DeepSeek 直连下翻成 `thinking` + `reasoning_effort`，`exclude` 不参与。
- **processor agent（`role: processor`）**：记忆 writer 或 `v4_processor`，用 `rules_file` / `context_sources` / `output_target`（支持 `{agent_id}` 占位）。

`context_files` 路由（`_build_context`）：以 `_tools.json` 结尾 → 工具定义（不进 system prompt）；其余 → 拼进 system prompt。**目录条目**会展开为该目录顶层文件（排序·**不递归**）。读不到的条目是**静默空串**，不报错。

---

## 12. lib 与辅助引擎件

- **`IAF_AI/validate.py`** —— 配置 & 工具校验 CLI。⚠ 它已落后于引擎（文件夹型 `context_files`、`free_search` 型 dispatch 都会被它报成假 error），**不是门禁**，只当粗筛用。真要验收就自己 import `_build_context` / `_load_dispatch_tools` 复刻一遍。
- **`IAF_AI/generate_manifest.py`** —— 生成 `MANIFEST.json` 系统地图。
- **`IAF_AI/lib/llm_client.py`** —— HTTP LLM 客户端：多端点适配（§11.1）、指数退避重试（429/500/502/503/529）、错误分类（`ContextTooLongError` / `LLMError`）、`provider_routing` → OpenRouter `provider` 对象、流式 `stream_llm`。
- **`IAF_AI/lib/token_utils.py`** —— token 估算。对字符串按 3 字符/token 估；对**多模态 parts 列表只累加 text part**（图片不按字符计，见 §14）。
- **`IAF_AI/lib/jev_client.py`** / **`IAF_AI/lib/voice.py`** —— JEV 判定与 TTS。模板本身不引用它们，是给下游 IAF 备着的。

> **★lib 住模板内部，不靠外部共享目录。**
> 解析点共四处：`dispatch_base.get_llm_caller`、`v4_processor`、`session_close`（两处）、`compaction`，都取 `<project_root>/lib`，而 `project_root` = `IAF_AI/`。
> **为什么不上溯去找一份共享 lib**：本模板是「整体复制出一个新 IAF」的母版，拷到哪儿都要能自己跑起来。靠上溯找 marker 的话，拷出集群就只剩一条退路，而那条退路通向一个不存在的目录 ⇒ `get_llm_caller()` 返回 `None`，**LLM 悄悄不可用**（静默降级）。
> **代价**：`lib/` 里的修复不会自动流到已经拷出去的副本，要逐份带过去。换句话说，改 `lib/` 属于 §3 第 4 条说的"改内核"，有 blast radius。

---

## 13. 操作指南：新建 IAF / 新增 dispatch

模板本身已是 Shell 四区形态，故"改造"退化为几种日常操作。

### 13.1 新建一个 IAF（基于本模板整体复制）

1. **整体复制** `iaf_template/` → 新 IAF 目录（`IAF_AI/lib/` 跟着走，这是刻意的，见 §12）。
2. **改 `config_base/config.json`**：填自己的 provider key（或指向共享 key 来源）。
3. **改 `config_base/{dispatch}/dispatch_config.json`**：`agent_id` / `agents` 的 provider+model / `context_files`（按 §4 用 shell-root 相对或绝对路径）。
4. **改 `prompt_base/{dispatch}/rules/`**：写该 dispatch 的 persona / 规则。
5. **定制 `chain.py`**：默认参数、用户消息来源、产出格式、是否多步串联多个 dispatch。
6. `python sync_config.py` → `python chain.py ...` 跑通。

### 13.2 新增一个 dispatch（同一 IAF 内多 dispatch）

```bash
# 1. 复制引擎侧模板 dispatch
cp -r IAF_AI/dispatch/template  IAF_AI/dispatch/{新名}
# 2. 建正本目录
mkdir -p config_base/{新名}  prompt_base/{新名}/rules  workspace/{新名}
# 3. 写 config_base/{新名}/dispatch_config.json（context_files 用 shell-root 相对/绝对/session:）
# 4. 写 prompt_base/{新名}/rules/*.md
# 5. sync（会自动遍历 config_base 下所有子目录）
python sync_config.py
```

★复制引擎 dispatch 之后记得删掉跟过来的 `sessions/` / `staging/` 残留与 `__pycache__`。

### 13.3 复制后必查的"模板残留点"

`dispatch/template/` 出厂含若干**占位名**，逐字复制会残留：

- **`dispatch.py` 顶部 class 名** —— 出厂 `TemplateDispatch`。改了要同步改动态加载处取的 `mod.<类名>`（本模板 `chain.py` 取 `dispatch_mod.TemplateDispatch`）。
- **`dispatch.py` / `new_session` / `run` 的默认 `agent_name`** —— 出厂 `"core_chat"`，必须与 config 的 `agents.<key>` 逐字一致。**头号坑。**
- **`dispatch_config.json` 的 `agents.<key>`** —— 最小骨架为 `core`。改则同步 `dispatch.py` 默认与 `chain.py` 的 `--agent`。
- **`context_files`** —— 最小骨架仅 `prompt_base/template/rules/persona.md`。要列全真实文件。
- **`tools/`** —— 示例工具 `write_document_tools.py`。照它改名/改落点成领域 `write_*`。
- **`agent_id`** —— 出厂 `template`，它决定 `write_document` 的落点与记忆路径里的 `{agent_id}`。

### 13.4 常见问题

- **Q：引擎内部 `dispatch_config.json` 被 sync 覆盖了？** A：预期行为。改配置只在 `config_base/`。
- **Q：改了 `prompt_base` 要 sync 吗？** A：正文不用（运行时直接读）。但 `rules/` 下给压缩器用的那几份要（§4.4），所以习惯上改完就 sync 一次最省事。
- **Q：能保留引擎内 `config.json` 不被覆盖？** A：`config_base/config.json` 不存在时 sync 跳过全局同步。
- **Q：`--image` 给了图但回答明显没看图？** A：先确认那个 agent 的 `model` 是支持看图的那一档（§14）。

---

## 14. 读图（多模态入料）★

### 14.1 先决条件

给图之前，该 agent 的 `model` 必须是**支持看图的那一档**。本模板出厂配的 `deepseek` 直连 + `deepseek-flash` 实测可读 PNG。

⚠ **给了图而 model 不支持看图时，服务端不一定报错** —— 它会照常回一段看起来完全正常的答案。这是要花钱才看得见的一类失败，所以换 `model` 的时候要连着检查这件事。

### 14.2 怎么给

```bash
python chain.py --message "图里印着一个字符串，照抄出来。" --image /abs/path/a.png
python chain.py --message "比较这两张" --image /abs/a.png --image /abs/b.jpg --image-detail auto
```

程序内调用：`chain.run(user_message="...", image_paths=["/abs/a.png"], image_detail=None)`。

### 14.3 走的是哪条路（为什么引擎几乎不用改）

```
chain.py
  └─ image_parts.build_content(text, image_paths)   # tools/_shared/image_parts.py
       无图 → 返回字符串（与不带图时逐字一致）
       有图 → 返回 parts 列表：text 在前、image_url 在后
  └─ disp.append_to_history("user", content)
       └─ session_io.append_history → json.dumps 原样落 history.jsonl
  └─ disp.run()
       └─ _load_conversation_history → messages 里 content 原样透传
            └─ call_with_tool_loop → 只转发 messages，不碰 content
                 └─ lib/llm_client：chat-completions 分支 payload={"model","messages"} 逐字透传
```

**引擎不解释 `content`**，所以 parts 列表一路骑到服务端。anthropic 分支同样透传 content blocks ⇒ 换去 Claude 看图也是这条路。
⚠ 走 `/responses` 端点时形状不同（图片 part 叫 `input_image`），那条路要另写映射，当前没写。

### 14.4 parts 的形状

```json
[{"type": "text", "text": "..."},
 {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}]
```

`--image-detail` 给了才会多一个 `image_url.detail` 键；不给就用服务端默认。

### 14.5 规格与限额（`tools/_shared/image_parts.py` 里按这些数挡着）

- 格式只认 **PNG / JPEG / GIF / WebP**，**按文件内容的魔数判型**，不看扩展名（服务端也是按内容判型，而 data URL 里的 mime 声明必须和真实内容对得上）。认不出当场抛错。
- 单图 ≤ 32 MiB。
- 所有图的**原始**字节总量 ≤ 36 MiB —— base64 把体积放大到 4/3，而请求体上限是 48 MiB。
- 单次请求 ≤ 600 张。
- 空文件、路径不存在 → 当场抛错（不静默跳过）。

### 14.6 两条别走的路

- ⛔ **不要把图片放进 `context_files`。** `_read_context_file` 用 `encoding="utf-8"` 打开，而它只捕 `FileNotFoundError` / `IOError`，`UnicodeDecodeError` 捕不到 ⇒ 整个 dispatch 当场炸掉。而且 `context_files` 的产物是 system prompt 那个**字符串**，图片不属于那里。
- ⛔ **不要用工具送图。** `tools/*_tools.py` 产出的是 `role="tool"` 的消息，而 Chat Completions 的 tool 消息只认文本。图片必须在 **user 回合**里。

### 14.7 图片对账上的影响

- `lib/token_utils.estimate_tokens` 对 parts 列表**只累加 text part** —— 图片在本地预算里算 0。这是对的：服务端按**每图一个固定上限**计费，不按字符数。
- 实测参照：一张 534×200 的 PNG，`prompt_tokens` 从 35 涨到 231。
- 会话标题（`meta.json` 的 `title_hint`）取的是 parts 里的文本，不会把 base64 写进去。

### 14.8 想读 PDF

服务端的格式清单里**没有 PDF**。先把 PDF 每页渲成 PNG 再按上面走（`pymupdf` 可以：`page.get_pixmap(dpi=...).save(...)`），注意把 §14.5 的总量上限算进去 —— 一份几十页的 PDF 很容易顶破。

### 14.9 还没核实的

看图与工具调用能不能同时用，服务端文档没说。要用就先真打一发看结果，别照着推断建流程。

---

## 15. 关键设计决策（速查）

1. **配置/引擎物理分离**：`config_base`/`prompt_base` 正本 + `IAF_AI` 由 sync 生成 —— 改配置永不触碰内核。
2. **两层路径 + sync 翻译**：正本 shell-root 相对（人友好）↔ 引擎 agent_dir 相对（`../../../`）；`session:` 运行时解析，`wiki:`/`briefs:` 归记忆子系统。
3. **lib 住模板内部**：换来"拷到哪儿都能跑"，代价是修复要逐份带过去（§12）。
4. **`context_files` 一表两用**：`.md` 进 system prompt，`_tools.json` 进 tools 参数，由 `_build_context` 自动分流。
5. **`exit_on_tools`**：终止型工具一调即退，避免多轮空转。
6. **工具 handler 解耦**：JSON 工具只回 ack，真实数据由 chain 从 `tool_calls` 提取。
7. **Free Search 物理围栏**：`make_tools` 工厂闭包 + `realpath` 白名单，代码层杜绝越界，不靠自律。
8. **`_fix_sibling_paths`**：多实例共存时修正模块缓存路径，各 agent 写自己目录。
9. **Compaction 双保护**：POSIX 锁防并发 + 免压保护区保最近 N 轮原始对话。
10. **记忆默认暂停**：默认 chain 不调 `close_session`，需显式开启。
11. **多模态只改入料侧**：`content` 允许是 parts 列表，引擎一路透传；只会读文本的旁路各自过 `_content_text`（§8、§14）。
12. **端点差异收在 `lib/llm_client`**：config 只写 `provider` + `model`，Anthropic / Responses / DeepSeek 直连 / OpenRouter 四种形状的翻译都在客户端一层做完。
