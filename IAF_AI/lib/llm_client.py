"""
Shared LLM HTTP client.
Pure infrastructure: HTTP calls + retry + error classification.
No agent behavior, no context management, no tool logic.
All agents import this; bug fixes here benefit everyone.
"""

import json
import requests
import time


class LLMError(Exception):
    pass


class ContextTooLongError(LLMError):
    pass


RETRYABLE_STATUS = {429, 500, 502, 503, 529}


def _is_responses_api(url):
    """Detect if the URL points to a Responses API endpoint (e.g. xAI reasoning models)."""
    return url.rstrip("/").endswith("/responses")


def _is_deepseek(url):
    """Detect DeepSeek 官方直连 endpoint。DeepSeek 用原生 reasoning 格式
    (thinking 开关 + 顶层 reasoning_effort),而非 OpenRouter 的 reasoning 对象;
    且不认 OpenRouter 的 provider 路由字段。"""
    return "api.deepseek.com" in (url or "")


def _is_anthropic_api(url):
    """Detect Anthropic Messages API endpoint。它的请求/响应形状与 Chat Completions
    完全不同:system 要提到顶层、max_tokens 必填、响应是 content blocks 数组、
    结束原因叫 stop_reason。本客户端负责把两边都归一成 Chat Completions 形状。"""
    return "api.anthropic.com" in (url or "")


def _build_provider(provider_routing):
    """Translate a config `provider_routing` value into an OpenRouter `provider` object.

    - falsy (None / "" / {})  → None：不下发 `provider` 字段，走 OpenRouter 默认路由。
    - dict                    → 原样透传（进阶用户可写完整 OpenRouter provider 对象）。
    - str "slug" 或 "slug/quant" → 钉死到该供应商（allow_fallbacks=False，可选限定量化）。
      钉死意味着这一 session 里供应商固定不漂移，cached input（前缀缓存）才能持续命中。

    例：
      "novita"      → {"order": ["novita"], "allow_fallbacks": False}
      "novita/fp8"  → {"order": ["novita"], "quantizations": ["fp8"], "allow_fallbacks": False}
    """
    if not provider_routing:
        return None
    if isinstance(provider_routing, dict):
        return provider_routing
    if isinstance(provider_routing, str):
        parts = [p.strip() for p in provider_routing.split("/") if p.strip()]
        if not parts:
            return None
        prov = {"order": [parts[0]], "allow_fallbacks": False}
        if len(parts) > 1:
            prov["quantizations"] = parts[1:]
        return prov
    return None


def call_llm(url, key, model, messages, tools=None, tool_choice=None,
             temperature=None, top_p=None, reasoning=None,
             max_tokens=None, max_retries=3, timeout=120,
             provider_routing=None, extra_params=None):
    """
    Send messages to LLM API. Returns the message dict from the response.
    Handles retry for transient errors, raises LLMError for fatal errors.
    Auto-detects Responses API (xAI reasoning) vs Chat Completions API by URL.
    Returned dict includes '_meta' with usage data and finish_reason when available.

    extra_params: optional dict merged verbatim into the request body — 任意透传字段
    (采样/路由/供应商专属旋钮)。最后合并,所以 config 覆盖本函数的推断值。这是不改
    本文件就能下发新字段的逃生口。
    """
    use_responses = _is_responses_api(url)
    use_anthropic = _is_anthropic_api(url)

    if use_anthropic:
        # Anthropic Messages API:system 提到顶层,OpenAI 的 tool 消息形状要翻译
        system_text = ""
        api_messages = []
        for m in messages:
            role = m.get("role", "")
            if role == "system":
                system_text = m.get("content", "")
            elif role == "assistant" and m.get("tool_calls"):
                # OpenAI {"role":"assistant","tool_calls":[...]}
                #   → Anthropic {"role":"assistant","content":[text, tool_use...]}
                blocks = []
                text = m.get("content")
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in m["tool_calls"]:
                    func = tc.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                                   "name": func.get("name", ""), "input": args})
                api_messages.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                # OpenAI {"role":"tool",...} → Anthropic 的 tool_result 走 user 回合
                api_messages.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }]})
            else:
                # content 可能是字符串,也可能已经是 blocks 数组(图像/文档多模态)
                api_messages.append({"role": role, "content": m.get("content", "")})
        payload = {
            "model": model,
            "max_tokens": int(max_tokens) if max_tokens is not None else 4096,
            "messages": api_messages,
        }
        if system_text:
            payload["system"] = system_text
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if tools:
            payload["tools"] = [{
                "name": x.get("function", {}).get("name", ""),
                "description": x.get("function", {}).get("description", ""),
                "input_schema": x.get("function", {}).get("parameters", {}),
            } for x in tools]
            if tool_choice:
                payload["tool_choice"] = tool_choice
    elif use_responses:
        # Responses API format (xAI reasoning models)
        payload = {"model": model, "input": messages}
    else:
        # Standard Chat Completions format
        payload = {"model": model, "messages": messages}
        # provider_routing 是 OpenRouter 专属路由字段;DeepSeek 官方接口不认此字段,
        # 即便 config 里残留 provider_routing,直连 DeepSeek 时一律不下发,避免 400。
        if not _is_deepseek(url):
            _prov = _build_provider(provider_routing)
            if _prov:
                payload["provider"] = _prov
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        # reasoning：DeepSeek 原生格式 vs OpenRouter reasoning 对象(按 endpoint 分支)
        if reasoning is not None and isinstance(reasoning, dict):
            effort = reasoning.get("effort")
            if _is_deepseek(url):
                # DeepSeek 官方接口:thinking 开关 + 顶层 reasoning_effort
                payload["thinking"] = {"type": "enabled"}
                if effort is not None:
                    payload["reasoning_effort"] = effort
            else:
                # OpenRouter reasoning 对象(effort 与 max_tokens 互斥)
                reasoning_payload = {}
                if effort is not None:
                    reasoning_payload["effort"] = effort
                if effort is None and reasoning.get("max_tokens") is not None:
                    reasoning_payload["max_tokens"] = reasoning["max_tokens"]
                if "exclude" in reasoning:
                    reasoning_payload["exclude"] = reasoning["exclude"]
                if reasoning_payload:
                    payload["reasoning"] = reasoning_payload

    # 最后合并,所以调用方的 extra_params 覆盖上面按端点推断出来的任何字段
    if extra_params:
        payload.update(extra_params)

    if use_anthropic:
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }

    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=timeout
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                    if use_anthropic:
                        # Anthropic content blocks → 归一成 Chat Completions 形状
                        text_parts, tool_calls = [], []
                        for blk in data.get("content", []):
                            if blk.get("type") == "text":
                                text_parts.append(blk.get("text", ""))
                            elif blk.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": blk.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": blk.get("name", ""),
                                        "arguments": json.dumps(blk.get("input", {}),
                                                                ensure_ascii=False),
                                    },
                                })
                        msg = {"role": "assistant",
                               "content": "\n".join(text_parts) if text_parts else None}
                        if tool_calls:
                            msg["tool_calls"] = tool_calls
                        # Anthropic 把结束原因叫 stop_reason,且住响应顶层
                        finish = data.get("stop_reason")
                    elif use_responses:
                        # Responses API: extract output_text
                        output_text = data.get("output_text", "")
                        msg = {"role": "assistant", "content": output_text}
                        finish = data.get("status")
                    else:
                        choice = data["choices"][0]
                        msg = choice["message"]
                        finish = choice.get("finish_reason") or choice.get("native_finish_reason")
                    # finish_reason 住 choice 层不住 message 层。不带出来,上层就分不清
                    # 「正常收尾」和「撞 max_tokens 被截断」——两者的 content 都像完整的。
                    msg["_meta"] = {"usage": data.get("usage", {}), "finish_reason": finish}
                    # ★静默失败闸:思考模式下正文可能整篇落进 reasoning_content 而 content
                    #   留空。上层若只读 content 就拿到空串,但钱已经付了、活已经干完了。
                    #   实测 deepseek-flash 同一 prompt 时而落 content 时而落 reasoning,
                    #   所以这是不稳定行为,必须在客户端这层喊出来。
                    if not (msg.get("content") or "").strip() and \
                            (msg.get("reasoning_content") or "").strip():
                        print(f"  [WARN] content 为空,而 reasoning_content 有 "
                              f"{len(msg['reasoning_content'])} 字符 —— 正文可能整篇落在 "
                              f"reasoning_content (finish_reason={finish})")
                    return msg
                except (KeyError, requests.exceptions.JSONDecodeError):
                    print(f"  [Retry] Invalid JSON in 200 response, attempt {attempt+1}/{max_retries}")
                    last_error = "Invalid JSON response"
                    time.sleep(2 ** (attempt + 1))
                    continue

            if response.status_code not in RETRYABLE_STATUS:
                error_body = response.text[:500]
                if response.status_code == 400 and "context" in error_body.lower():
                    raise ContextTooLongError(error_body)
                raise LLMError(f"HTTP {response.status_code}: {error_body}")

            retry_after = response.headers.get("Retry-After")
            wait = int(retry_after) if retry_after else 2 ** (attempt + 1)
            print(f"  [Retry] {response.status_code}, attempt {attempt+1}/{max_retries}, wait {wait}s")
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            time.sleep(wait)

        except requests.exceptions.Timeout:
            print(f"  [Retry] Timeout, attempt {attempt+1}/{max_retries}")
            last_error = "Timeout"
            time.sleep(2 ** (attempt + 1))

        except requests.exceptions.ConnectionError:
            print(f"  [Retry] Connection error, attempt {attempt+1}/{max_retries}")
            last_error = "Connection error"
            time.sleep(2 ** (attempt + 1))

        except requests.exceptions.ChunkedEncodingError:
            print(f"  [Retry] Chunked encoding error, attempt {attempt+1}/{max_retries}")
            last_error = "ChunkedEncodingError"
            time.sleep(2 ** (attempt + 1))

        except (LLMError, ContextTooLongError):
            raise

    raise LLMError(f"Failed after {max_retries} retries. Last: {last_error}")


# ═══════════════════════════════════════════════════════════════════════════
# 流式（stream_llm）—— 给需要边出边读的 dispatch 用
#
# 设计约束（rebuild_plan §5.2）：
#   ★`import httpx` 必须在函数内部，不许放模块顶层。
#     理由：本文件被压缩器的 dispatch_base.py 以 `from llm_client import call_llm`
#     导入。httpx 缺失时若在顶层 import，整个模块加载失败 → get_llm_caller() 返回
#     None → dispatch.py 的 `raise RuntimeError("No LLM caller available")` →
#     **压缩器也停了**。压缩器只用 requests，不该被 httpx 连坐。
#     speaker 没有退路，它拿不到 httpx 就该报错——那是对的。
#   ★不要把 import 合并成 `from llm_client import call_llm, stream_llm`。
#
# 分三层（rebuild_plan §0.4，为换供应商留缝）：
#   _iter_sse_lines(response)  行协议：跳注释行、认 [DONE]。换供应商基本不动。
#   _map_chunk(chunk)          字段映射：**每家不同，换供应商只改这里**。
#   stream_llm(...)            累加 + 吐信封。信封形状永不变，消费者不用认第二种。
# ═══════════════════════════════════════════════════════════════════════════


async def _iter_sse_lines(response, stats=None):
    """把 httpx 的流式响应拆成「有效载荷」的迭代。yield 形态：

        ("data", <dict>)   一个 JSON chunk
        ("done", None)     收到 `data: [DONE]`

    三类行要分开认（依 OpenRouter 的 SSE 约定，逐字核过接口文档）：
      `: OPENROUTER PROCESSING` —— SSE **注释行**（以 `:` 开头）。排队/选 provider 期间
            的心跳。文档说「can be safely ignored per the SSE specs」。
            ★绝不能喂给 json.loads，否则排队时必崩。
      `data: {…}`              —— 正常增量。
      `data: [DONE]`           —— 终止符，**不是 JSON**。必须先判等再解析。

    stats：调用方给的可变 dict，用来把「跳过了几行注释 / 几个坏 chunk」带出去。
    排队时才会出现注释行，所以这个计数是**唯一**能证明那条路径跑过的证据。
    """
    import json as _json
    if stats is None:
        stats = {}
    async for raw in response.aiter_lines():
        line = raw.strip()
        if not line:
            continue                      # SSE 事件分隔空行
        if line.startswith(":"):
            stats["comments"] = stats.get("comments", 0) + 1
            stats.setdefault("comment_samples", [])
            if len(stats["comment_samples"]) < 3:
                stats["comment_samples"].append(line[:60])
            continue                      # ★注释行/心跳，跳过
        if not line.startswith("data:"):
            stats["other_fields"] = stats.get("other_fields", 0) + 1
            continue                      # id: / event: / retry: 等字段，本协议不用
        payload = line[5:].strip()
        if payload == "[DONE]":
            yield ("done", None)
            return
        try:
            yield ("data", _json.loads(payload))
        except ValueError:
            # 单个坏 chunk 不该杀掉整条流（语音里已播出的部分是真实成本）。
            stats["bad_chunks"] = stats.get("bad_chunks", 0) + 1
            print(f"  [stream] 跳过无法解析的 chunk: {payload[:120]}")
            continue


def _map_chunk(chunk):
    """把一个供应商 chunk 映射成统一的中间形状。**换供应商只改这个函数。**

    返回 {"text":str, "reasoning":str, "finish":str|None, "usage":dict|None, "id":str|None}

    ★`id` 是 OpenRouter 的 generation id。它是**被打断之后唯一能回查这次调用真实计费的
      把手** —— 打断意味着永远收不到 usage chunk，只能事后查
      `GET /api/v1/generation?id=<id>`。语音打断是核心动作，所以这个字段必须带出来。

    ★OpenRouter 的字段位置（逐字确认，与 DeepSeek 直连不同，别混）：
        文本增量   choices[0].delta.content
        推理增量   choices[0].delta.reasoning_details   ← 不是 delta.reasoning
                                                        ← 更不是 DeepSeek 的 delta.reasoning_content
        结束原因   choices[0].finish_reason / native_finish_reason
        usage      顶层 usage（在 [DONE] 之前的最后一个 chunk 里，自动来）

    ★`choices` 可能是空列表 —— usage chunk 就是这样。所以绝不许无条件写
      `chunk["choices"][0]`，那会 IndexError 或（被 try 吞掉时）把 cost 和
      cached_tokens 一起丢掉，而 cached_tokens 是验证「钉 provider 有没有生效」
      的唯一信号。
    """
    out = {"text": "", "reasoning": "", "finish": None, "usage": None,
           "id": chunk.get("id")}

    usage = chunk.get("usage")
    if usage:
        out["usage"] = usage

    choices = chunk.get("choices") or []
    if not choices:
        return out                         # ★usage chunk 走这条路出去

    ch = choices[0] or {}
    delta = ch.get("delta") or {}

    txt = delta.get("content")
    if txt:
        out["text"] = txt

    # reasoning_details 是列表，元素形如 {"type":"reasoning.text","text":"..."}
    rd = delta.get("reasoning_details")
    if rd:
        parts = []
        for item in rd if isinstance(rd, list) else [rd]:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("summary") or "")
            elif isinstance(item, str):
                parts.append(item)
        out["reasoning"] = "".join(parts)

    out["finish"] = ch.get("finish_reason") or ch.get("native_finish_reason")
    return out


async def stream_llm(url, key, model, messages,
                     temperature=None, top_p=None, reasoning=None,
                     max_tokens=None, timeout=120, provider_routing=None):
    """流式调用，异步生成器。逐条 yield 统一信封：

        {"type": "delta", "text": "..."}                        文本增量
        {"type": "done",  "message": {...}, "usage": {...},
                          "finish_reason": "...", "reasoning": "..."}

    `done.message` **刻意做成 call_llm 的返回形状**：
        {"role": "assistant", "content": "...", "_meta": {"usage": {...}}}
    这样 dispatch_base._extract_text() 与历史落盘那一段照旧能用，接工具时
    _parse_llm_response 也不用为流式分叉。

    ★没有 stream 开关。本函数就是流式的；要非流式就调 call_llm。
    ★不要下发 stream_options / usage.include —— 文档逐字说这两个
      「deprecated and have no effect」。usage 是自动来的。
    ★一轮可以合法地【零文本增量】（只有 finish_reason + usage）。所以终止判据是
      流结束 / [DONE]，绝不是「收到过 delta」。
    取消：调用方退出 async for（或关掉外层 async with）即断流。OpenAI 在
      OpenRouter 的「取消支持」名单里，所以断流即停生成、停计费。
    """
    import httpx      # ★函数内 import，见本节顶部注释

    payload = {"model": model, "messages": messages, "stream": True}
    if not _is_deepseek(url):
        prov = _build_provider(provider_routing)
        if prov:
            payload["provider"] = prov
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if reasoning is not None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if _is_deepseek(url):
            payload["thinking"] = {"type": "enabled"}
            if effort is not None:
                payload["reasoning_effort"] = effort
        else:
            rp = {}
            if effort is not None:
                rp["effort"] = effort
            if effort is None and reasoning.get("max_tokens") is not None:
                rp["max_tokens"] = reasoning["max_tokens"]
            if "exclude" in reasoning:
                rp["exclude"] = reasoning["exclude"]
            if rp:
                payload["reasoning"] = rp

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    text_parts = []
    reasoning_parts = []
    usage = {}
    finish_reason = None
    gen_id = None
    stats = {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = (await resp.aread())[:500]
                if resp.status_code == 400 and b"context" in body.lower():
                    raise ContextTooLongError(body.decode("utf-8", "replace"))
                raise LLMError(f"HTTP {resp.status_code}: {body.decode('utf-8', 'replace')}")

            async for kind, chunk in _iter_sse_lines(resp, stats):
                if kind == "done":
                    break
                m = _map_chunk(chunk)
                if m["id"] and gen_id is None:
                    gen_id = m["id"]
                    # ★第一个 chunk 就吐一次 id：打断发生在 done 之前，消费者必须在
                    #   那之前就拿到把手，否则这次调用的真实计费无从查证。
                    yield {"type": "meta", "generation_id": gen_id}
                if m["usage"]:
                    usage = m["usage"]
                if m["finish"]:
                    finish_reason = m["finish"]
                if m["reasoning"]:
                    reasoning_parts.append(m["reasoning"])
                if m["text"]:
                    text_parts.append(m["text"])
                    yield {"type": "delta", "text": m["text"]}

    content = "".join(text_parts)
    yield {
        "type": "done",
        "message": {"role": "assistant", "content": content,
                    "_meta": {"usage": usage}},
        "usage": usage,
        "finish_reason": finish_reason,
        "reasoning": "".join(reasoning_parts),
        "generation_id": gen_id,
        "stream_stats": stats,      # {comments, comment_samples, other_fields, bad_chunks}
    }


async def text_only(stream):
    """只过 delta 的文本。给「我只想要字」的消费者用（例如喂分句缓冲）。

    ★注意它会吞掉 meta 与 done —— 所以需要 usage / generation_id 的消费者
      不能用这个包装，要自己认信封。
    """
    async for ev in stream:
        if ev.get("type") == "delta":
            yield ev["text"]
