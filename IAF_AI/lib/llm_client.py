"""
Shared LLM HTTP client.
Pure infrastructure: HTTP calls + retry + error classification.
No agent behavior, no context management, no tool logic.
All agents import this; bug fixes here benefit everyone.
"""

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


def call_llm(url, key, model, messages, tools=None, tool_choice=None,
             temperature=None, top_p=None, reasoning=None,
             max_tokens=None, max_retries=3, timeout=120):
    """
    Send messages to LLM API. Returns the message dict from the response.
    Handles retry for transient errors, raises LLMError for fatal errors.
    Auto-detects Responses API (xAI reasoning) vs Chat Completions API by URL.
    Returned dict includes '_meta' with usage data when available.
    """
    use_responses = _is_responses_api(url)

    if use_responses:
        # Responses API format (xAI reasoning models)
        payload = {"model": model, "input": messages}
    else:
        # Standard Chat Completions format
        payload = {"model": model, "messages": messages}
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
        # reasoning 对象：effort 与 max_tokens 互斥
        if reasoning is not None and isinstance(reasoning, dict):
            reasoning_payload = {}
            effort = reasoning.get("effort")
            if effort is not None:
                reasoning_payload["effort"] = effort
            if effort is None and reasoning.get("max_tokens") is not None:
                reasoning_payload["max_tokens"] = reasoning["max_tokens"]
            if "exclude" in reasoning:
                reasoning_payload["exclude"] = reasoning["exclude"]
            if reasoning_payload:
                payload["reasoning"] = reasoning_payload

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
                    if use_responses:
                        # Responses API: extract output_text
                        output_text = data.get("output_text", "")
                        msg = {"role": "assistant", "content": output_text}
                    else:
                        msg = data["choices"][0]["message"]
                    # 附带 usage 元数据供上层提取 reasoning 统计
                    msg["_meta"] = {"usage": data.get("usage", {})}
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
            last_error = f"HTTP {response.status_code}"
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
