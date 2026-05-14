from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request

from .models import SignalConfig


GROQ_USER_AGENT = "SignalStream/1.0"


class BrainClient:
    def __init__(self, config: SignalConfig):
        self.config = config.brain
        self.last_error: str | None = None
        # Raw assistant text from the most recent chat_json call. Captured so the
        # Orchestrator (or anyone debugging) can log exactly what Groq returned
        # before our JSON parser touched it.
        self.last_response_text: str = ""
        self._api_key: str = os.environ.get("GROQ_API_KEY", "")
        if not self._api_key:
            self.last_error = "GROQ_API_KEY not set. Export it before running: export GROQ_API_KEY=<your-key>"

    def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            req = request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": GROQ_USER_AGENT,
                },
                method="GET",
            )
            with request.urlopen(req, timeout=8) as response:
                return response.status == 200
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return False

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        temperature: float = 0.0,
        required_fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Call Groq and return parsed JSON, or None on failure.

        schema param is accepted for API compatibility but Groq uses
        response_format=json_object instead of a full schema object.
        required_fields: if supplied, retries once if any field is missing/empty.
        """
        if not self._api_key:
            self.last_error = "GROQ_API_KEY not set."
            return None

        self.last_response_text = ""
        result = self._call_groq(system, user, temperature)

        if result is not None and required_fields:
            missing = [f for f in required_fields if not result.get(f)]
            if missing:
                # Retry once with an explicit instruction about missing fields.
                user_retry = user + f"\n\nIMPORTANT: You MUST include these fields in your JSON response: {', '.join(missing)}"
                result = self._call_groq(system, user_retry, temperature)
                if result is not None:
                    still_missing = [f for f in required_fields if not result.get(f)]
                    if still_missing:
                        self.last_error = f"Required fields missing after retry: {', '.join(still_missing)}"
                        return None

        return result

    def _call_groq(self, system: str, user: str, temperature: float) -> dict[str, Any] | None:
        """Single Groq API call with 429 retry. Returns parsed dict or None."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": GROQ_USER_AGENT,
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 429:
                # Rate limit: sleep 15s and retry once. Was 60s, which caused
                # the analyst to blow the 2400s worker timeout when many articles
                # hit the limit in the same batch.
                time.sleep(15)
                try:
                    with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                        raw = json.loads(response.read().decode("utf-8"))
                except error.HTTPError as exc2:
                    if exc2.code == 429:
                        self.last_error = "Hit Groq rate limit twice. Wait and retry."
                    else:
                        self.last_error = f"Groq HTTP {exc2.code}: {exc2.reason}"
                    return None
                except Exception as exc2:  # noqa: BLE001
                    self.last_error = str(exc2)
                    return None
            else:
                self.last_error = f"Groq HTTP {exc.code}: {exc.reason}"
                return None
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return None

        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        self.last_response_text = content
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            self.last_error = str(exc)
            return None
        return parsed if isinstance(parsed, dict) else None
