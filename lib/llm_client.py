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


def call_llm(url, key, model, messages, tools=None, max_retries=3, timeout=120):
    """
    Send messages to LLM API. Returns the message dict from the response.
    Handles retry for transient errors, raises LLMError for fatal errors.
    """
    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools

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
                    return response.json()["choices"][0]["message"]
                except (KeyError, requests.exceptions.JSONDecodeError):
                    raise LLMError(f"Invalid response: {response.text[:200]}")

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

        except (LLMError, ContextTooLongError):
            raise

    raise LLMError(f"Failed after {max_retries} retries. Last: {last_error}")
