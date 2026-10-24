"""
lib/jev_client.py —— JEV#1 客户端（结构化判断，零生成）

与 llm_client.py / stt_client.py 同层：纯客户端，不含任何 agent 行为。
问的那道题住 prompt_base/listener/question.md，连接参数住
config_base/listener/dispatch_config.json —— 本文件只负责「怎么发、怎么读回参」。

★★四条实现纪律（每条都是查证过才写的，照直觉写会踩）

1. **JEV 不是 chat 模型，不能走 llm_client.call_llm。**
   它走 Decisions API：`POST https://openrouter.ai/api/alpha/decisions`
   （或 TypeSafe 兼容形状 `/api/v1/systemone`），请求体 `{state, model, questions}`。
   打 `/chat/completions` 会 **400**，而且它不出现在 `/api/v1/models` 里。
   → build_plan §1.5 原写的「直调 lib/llm_client.call_llm」已作废，理由记在
   config_base/listener/dispatch_config.json 的 `_api_note`。

2. **noul 答案没有独立的 confidence 字段。** 回参是 `{"type":"noul","noul":0.0..1.0}`，
   那个 noul 本身就是校准概率；`confidence` 只在 choice/score 答案里有。
   → 所以对外返回的是 `noul`，不是 `confidence`。**不留 confidence 别名** ——
   两个键装同一个数，早晚有一处读错。

3. **★读不出 noul 一律 raise，绝不降级成 False。**
   服务端改字段名 / 换端点 / 限流，若吞掉当 `meaningful=False`，症状是
   「人说完了机器永远不开口」，而且**不报错、查不到原因**。这与 stt_client
   「字段名以真实事件为准」是同一条纪律（build_plan §8.7 第 2 条），所以
   `raw` 原样带回来，出问题时错误消息里就有服务端原文。
   调用方（第 5 步的 ticker）该把异常当「这一拍没拿到裁决」跳过，**不是判不够**。

4. **连接必须预热。** 参考实现实测首次请求 ≈700ms（TLS 握手），之后 ≈300ms
   （self_free_talk.md §9.4）—— 不预热第一句话必崩预算。所以这里是一个持有
   httpx.AsyncClient 的类（连接池复用），不是每次新建；`warmup()` 单独暴露。

★为什么是 async：§3.1 定了「新一拍发出时掐掉上一拍」（AbortSignal 那条）。
  在 asyncio 里就是 `task.cancel()`，它要求整条链路可取消 —— httpx 的 async
  请求可以，requests 的同步调用不行。
"""

import json
import os
import re
import time

import httpx

DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_TIMEOUT_S = 5.0
QUESTION_KEY = "complete"          # question.md 里那道题的键名
DEFAULT_THRESHOLD = 0.5


class JevError(Exception):
    """协议 / HTTP / 回参形状问题。★绝不在内部被吞成 meaningful=False（纪律 3）。"""


# ---------------------------------------------------------------- 纯函数（可离线测）

def extract_question_block(md_text):
    """从 question.md 抽出唯一那个 ```json 块。

    ★块外的中文一律不进模型 —— 那是给人看的依据。刻意用「正本里嵌一个 JSON 块」
      而不是另起一个 .json 文件：措辞就是策略（实测结论），策略和它的依据必须
      住在一起，否则改了措辞没人知道为什么原来那样写。
    """
    blocks = re.findall(r"```json\s*\n(.*?)\n```", md_text, re.S)
    if not blocks:
        raise JevError("question.md 里找不到 ```json 块")
    if len(blocks) > 1:
        # 多于一个就报，不猜哪个是正本 —— 猜错会静默用错题面
        raise JevError(f"question.md 里有 {len(blocks)} 个 ```json 块，正本必须唯一")
    try:
        questions = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise JevError(f"question.md 的 json 块解析失败：{e}") from e
    if not isinstance(questions, dict) or not questions:
        raise JevError("question.md 的 json 块必须是非空对象（键 = 问题名）")
    for key, q in questions.items():
        if not isinstance(q, dict) or "type" not in q:
            raise JevError(f"问题 {key!r} 缺 type 字段")
    return questions


def should_skip(state):
    """U 空就跳过这一拍，不发调用（§5.5：静默期零成本）。

    ★同时挡住实测会来的两类垃圾：空 partial/空 final（§8.7 第 2 条）和
      孤立标点自成一段（§8.7 第 1 条）—— 对着一个 `.` 认真判一轮是真花钱。
    """
    text = (state or "").strip()
    if not text:
        return "empty_state"
    # 去掉所有标点和空白后没内容 = 只有标点，没有可判的语义
    if not re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE):
        return "punctuation_only"
    return None


def verdict_from_answer(payload, question_key=QUESTION_KEY, threshold=DEFAULT_THRESHOLD):
    """从回参取 noul 并与阈值比。读不出就 raise（纪律 3）。"""
    if not isinstance(payload, dict):
        raise JevError(f"回参不是对象：{payload!r}")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevError(f"回参里没有 answers 对象：{json.dumps(payload, ensure_ascii=False)[:400]}")
    ans = answers.get(question_key)
    if not isinstance(ans, dict):
        raise JevError(
            f"回参 answers 里没有 {question_key!r}（有的键：{list(answers)}）"
        )
    noul = ans.get("noul")
    if not isinstance(noul, (int, float)) or isinstance(noul, bool):
        raise JevError(
            f"answers[{question_key!r}] 里读不出 noul 数值：{json.dumps(ans, ensure_ascii=False)[:400]}"
        )
    noul = float(noul)
    return noul > float(threshold), noul


# ---------------------------------------------------------------- 配置 / 密钥

def resolve_key(shell_root, provider="openrouter"):
    """从 `<壳根>/IAF_AI/config.json` 取 `providers.<provider>.api_key`（缺则回落 `key`）。

    ★入参是**壳根**，与 `stt_client.resolve_key` 同一个约定（2026-09-26 统一）。
      在此之前这里收的是 IAF_AI 目录、那边收的是壳根 —— 同名参数两套含义，
      而传错的后果是 `读不到 .../config.json`，报错指向一个看着很像对的路径。

    ★与 stt_client.resolve_key 同约定：**客户端不自己找配置**，钥匙也绝不进
      dispatch_config.json —— 那份要过镜像律下发，钥匙会跟着走（§8.7 第 3 条）。
    """
    path = os.path.join(shell_root, "IAF_AI", "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    p = cfg.get("providers", {}).get(provider) or {}
    # ★2026-09-30：`api_key` 优先，回落 `key` —— 与本壳 dispatch_base.py 的
    #   `p.get("api_key") or p.get("key", "")` 同一约定。原来只认 `api_key` 是个静默陷阱：
    #   本壳已并入集群 sync_to_shells 级联，config.json 由集群 global_config.json 逐字节下发，
    #   而权威源里 openrouter 只有 `key` 没有 `api_key` ⇒ 只认 api_key 会让三个 jev agent
    #   （jev1 / jev2 / tool_caller.jev）在下发后【全部】拿不到钥匙。
    key = p.get("api_key") or p.get("key")
    if not key:
        raise JevError(f"{path} 里 providers.{provider} 既没有 api_key 也没有 key")
    return key


def load_dispatch_config(dispatch_dir):
    with open(os.path.join(dispatch_dir, "dispatch_config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_questions(dispatch_dir, agent_conf):
    """按 agent_conf.context_files 第一项读题面（路径相对 dispatch_dir）。"""
    files = agent_conf.get("context_files") or []
    if not files:
        raise JevError("agents.<name>.context_files 为空，没有题面可读")
    path = files[0]
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(dispatch_dir, path))
    if not os.path.isfile(path):
        # 读不到一律报。引擎的 context_files 读不到是静默返回空串，
        # 那条在这里会表现成「对着空题面判」→ 必须显式失败。
        raise JevError(f"题面读不到：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return extract_question_block(f.read())


# ---------------------------------------------------------------- 客户端

class JevClient:
    """持有一条复用连接的 JEV 客户端。

        async with JevClient(dispatch_dir, "jev1") as jev:
            await jev.warmup()
            r = await jev.ask("你知道 Kubernetes 是干什么的吗？")
    """

    def __init__(self, dispatch_dir, agent_name="jev1", shell_root=None,
                 api_key=None, questions=None, threshold=None, question_key=QUESTION_KEY):
        self.dispatch_dir = dispatch_dir
        self.agent_name = agent_name
        cfg = load_dispatch_config(dispatch_dir)
        agents = cfg.get("agents") or {}
        if agent_name not in agents:
            raise JevError(f"dispatch_config.json 的 agents 里没有 {agent_name!r}（有：{list(agents)}）")
        self.agent_conf = agents[agent_name]
        self.knobs = cfg.get("knobs") or {}

        self.endpoint = self.agent_conf.get("endpoint") or DEFAULT_ENDPOINT
        self.model = self.agent_conf.get("model") or DEFAULT_MODEL
        self.timeout_s = float(self.agent_conf.get("timeout_s") or DEFAULT_TIMEOUT_S)
        self.question_key = question_key
        self.questions = questions if questions is not None else load_questions(dispatch_dir, self.agent_conf)
        if threshold is None:
            threshold = self.knobs.get("meaningful_threshold")
        # ★阈值缺省不能是 None：这是个要比大小的数（config 里也写死了理由）
        self.threshold = DEFAULT_THRESHOLD if threshold is None else float(threshold)

        if api_key is None:
            if shell_root is None:
                # dispatch_dir = <壳根>/IAF_AI/dispatch/<name> → 上三级
                shell_root = os.path.normpath(os.path.join(dispatch_dir, "..", "..", ".."))
            api_key = resolve_key(shell_root, self.agent_conf.get("provider") or "openrouter")
        self._key = api_key
        self._client = None
        self.calls = 0
        self.total_input_tokens = 0
        self.total_cost_usd = 0.0

    # -- 生命周期 --------------------------------------------------

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        return False

    async def warmup(self):
        """预热 TLS + 连接池（纪律 4）。用一条最小状态真发一次，返回耗时 ms。

        ★不能只开 TCP 不发请求 —— 参考实现那 ≈700ms 是首个**完整请求**的数，
          握手之外还有服务端冷启。所以预热就是一次真调用，成本 ≈$0.00001。
        """
        t0 = time.monotonic()
        await self.ask("你知道 Kubernetes 是干什么的吗？", _warmup=True)
        return (time.monotonic() - t0) * 1000.0

    # -- 主入口 ----------------------------------------------------

    async def ask(self, state, *, timeout=None, _warmup=False):
        """问 JEV#1：`state`（= U 的文本）里内容完整了吗。

        返回 dict：
            meaningful  bool        noul > threshold
            noul        float|None  校准概率（跳过时 None）
            threshold   float
            skipped     str|None    "empty_state" / "punctuation_only" 时没发请求
            latency_ms  float|None
            usage       dict
            raw         dict|None   服务端原文（字段改名时看这里，纪律 3）

        ★取消语义：这是普通协程，外层 `task.cancel()` 即可掐掉在飞的这一拍
          （§3.1 那条「新一拍发出时掐掉上一拍」）。掐掉不会留下半个状态 ——
          本方法除了计数器不改任何东西。
        """
        skip = should_skip(state)
        if skip:
            return {"meaningful": False, "noul": None, "threshold": self.threshold,
                    "skipped": skip, "latency_ms": None, "usage": {}, "raw": None,
                    "state": state}

        body = {"model": self.model, "state": state, "questions": self.questions}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        client = self._client
        owned = client is None
        if owned:
            client = httpx.AsyncClient(timeout=timeout or self.timeout_s)
        t0 = time.monotonic()
        try:
            resp = await client.post(self.endpoint, json=body, headers=headers,
                                     timeout=timeout or self.timeout_s)
        except httpx.HTTPError as e:
            raise JevError(f"JEV 请求失败（{self.endpoint}）：{type(e).__name__}: {e}") from e
        finally:
            if owned:
                await client.aclose()
        latency_ms = (time.monotonic() - t0) * 1000.0

        if resp.status_code != 200:
            raise JevError(
                f"JEV HTTP {resp.status_code}（{self.endpoint}）：{resp.text[:600]}"
            )
        try:
            payload = resp.json()
        except ValueError as e:
            raise JevError(f"JEV 回参不是 JSON：{resp.text[:400]}") from e

        meaningful, noul = verdict_from_answer(payload, self.question_key, self.threshold)

        usage = payload.get("usage") or {}
        if not _warmup:
            self.calls += 1
            self.total_input_tokens += int(usage.get("input_tokens") or 0)
            self.total_cost_usd += float(usage.get("cost") or 0.0)

        return {"meaningful": meaningful, "noul": noul, "threshold": self.threshold,
                "skipped": None, "latency_ms": latency_ms, "usage": usage,
                "raw": payload, "state": state}
