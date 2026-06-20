"""OpenAI-compatible LLM client (raw httpx, no SDK).

Reads LLM_BASE_URL / LLM_API_KEY from env. max_retries comes from config
(`llm.max_retries`, default 1). Used by all stages via `LLM.complete` /
`LLM.complete_json`.
"""
from __future__ import annotations

import json
import os
import re

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

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        print(f"[llm] <- content[:120]={content[:120]!r}")
        return content

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
