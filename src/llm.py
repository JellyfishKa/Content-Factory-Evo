"""OpenAI-compatible LLM client (raw httpx, no SDK).

Reads LLM_BASE_URL / LLM_API_KEY from env. max_retries comes from config
(`llm.max_retries`, default 1). Used by all stages via `LLM.complete` /
`LLM.complete_json`.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx


class LLMContractError(Exception):
    """Raised when the LLM does not produce a valid JSON contract after retries."""


class LLM:
    def __init__(self, cfg: dict | None = None, timeout: float = 60.0):
        self.base_url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.timeout = timeout
        self.max_retries = 1
        if cfg:
            self.max_retries = cfg.get("llm", {}).get("max_retries", 1)

    def complete(self, model: str, system: str, user: str, *, json_mode: bool = False) -> str:
        """Single chat completion call. Returns the raw text content."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        print(f"[llm] -> model={model} system[:80]={system[:80]!r} user[:80]={user[:80]!r}")

        # Force IPv4 (containers often lack an IPv6 route -> ENETUNREACH) and
        # retry transient connection failures common on free endpoints.
        transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=2)
        with httpx.Client(timeout=self.timeout, transport=transport) as client:
            data = self._post_with_backoff(client, url, headers, payload)

        content = data["choices"][0]["message"]["content"]
        print(f"[llm] <- content[:120]={content[:120]!r}")
        return content

    def _post_with_backoff(self, client, url, headers, payload, attempts: int = 5) -> dict:
        """POST with backoff on 429/5xx — free endpoints are frequently busy."""
        for attempt in range(attempts):
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                wait = self._retry_after(resp) or min(2 ** attempt, 30)
                print(f"[llm] {resp.status_code} from provider, retry in {wait}s ({attempt + 1}/{attempts - 1})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")  # loop always returns or raises

    @staticmethod
    def _retry_after(resp) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def complete_json(self, model: str, system: str, user: str) -> dict | list:
        """Calls `complete` with json_mode=True, parses JSON. On parse failure,
        retries once with an instruction to return strict JSON without markdown.
        Raises LLMContractError if it still fails.
        """
        raw = self.complete(model, system, user, json_mode=True)
        parsed = self._try_parse_json(raw)
        if parsed is not None:
            return parsed

        retry_user = user + "\n\nВерни строго валидный JSON без markdown и без пояснений."
        raw_retry = self.complete(model, system, retry_user, json_mode=True)
        parsed_retry = self._try_parse_json(raw_retry)
        if parsed_retry is not None:
            return parsed_retry

        raise LLMContractError(
            f"Model '{model}' did not return valid JSON after 1 retry. "
            f"Last response: {raw_retry[:300]!r}"
        )

    @staticmethod
    def _try_parse_json(text: str) -> dict | list | None:
        text = text.strip()
        # Strip markdown code fences if present.
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
