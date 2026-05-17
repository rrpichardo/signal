from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, request

from .models import SignalConfig


GROQ_USER_AGENT = "SignalStream/1.0"


def _format_http_error(exc: error.HTTPError) -> str:
    # Capture a bounded slice of the response body. Groq's 4xx bodies carry the
    # real reason (rate-limit text, prompt-guard rejection, billing hold) that
    # `exc.reason` ("Forbidden") never reveals. Cap at 600 chars so noisy HTML
    # error pages don't bloat event payloads.
    try:
        body = exc.read().decode("utf-8", errors="replace")[:600].strip()
    except Exception:  # noqa: BLE001 - body read must never mask the original error
        body = ""
    base = f"Groq HTTP {exc.code}: {exc.reason}"
    return f"{base} — body: {body}" if body else base


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
                # Use Groq's retry-after header when present; fall back to 15s.
                try:
                    wait = int(exc.headers.get("retry-after") or exc.headers.get("x-ratelimit-reset-requests") or 15)
                except (TypeError, ValueError):
                    wait = 15
                wait = max(1, min(wait, 120))  # clamp to [1, 120]
                time.sleep(wait)
                try:
                    with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                        raw = json.loads(response.read().decode("utf-8"))
                except error.HTTPError as exc2:
                    if exc2.code == 429:
                        # Second hit: read header again and wait once more before giving up.
                        try:
                            wait2 = int(exc2.headers.get("retry-after") or exc2.headers.get("x-ratelimit-reset-requests") or 30)
                        except (TypeError, ValueError):
                            wait2 = 30
                        wait2 = max(1, min(wait2, 120))
                        time.sleep(wait2)
                        try:
                            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                                raw = json.loads(response.read().decode("utf-8"))
                        except Exception as exc3:  # noqa: BLE001
                            self.last_error = f"Hit Groq rate limit three times: {exc3}"
                            return None
                    else:
                        self.last_error = _format_http_error(exc2)
                        return None
                except Exception as exc2:  # noqa: BLE001
                    self.last_error = str(exc2)
                    return None
            else:
                self.last_error = _format_http_error(exc)
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
