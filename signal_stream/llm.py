from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .models import SignalConfig, SignalDraft


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "why_it_matters", "next_steps"],
}


class OllamaClient:
    def __init__(self, config: SignalConfig):
        self.config = config.ollama
        self.last_error: str | None = None
        # Raw assistant text from the most recent chat_json call. Captured so the
        # Orchestrator (or anyone debugging) can log exactly what Ollama returned
        # before our JSON parser touched it. Mirrors the last_error pattern.
        self.last_response_text: str = ""

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            req = request.Request(f"{self.config.host}/api/tags", method="GET")
            with request.urlopen(req, timeout=min(8, self.config.timeout_seconds)) as response:
                return response.status == 200
        except Exception as exc:  # noqa: BLE001 - convert local server failures into fallback behavior.
            self.last_error = str(exc)
            return False

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            self.last_error = "Ollama is disabled in config."
            return None

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if schema:
            payload["format"] = schema
        else:
            payload["format"] = "json"

        # Reset the raw-response capture at the start of every call so a previous
        # success doesn't bleed into the trace if this call fails mid-flight.
        self.last_response_text = ""
        try:
            req = request.Request(
                f"{self.config.host}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            content = raw.get("message", {}).get("content", "{}")
            # Save the raw assistant text BEFORE parsing so debug traces still get
            # something useful even if the model returned malformed JSON.
            self.last_response_text = content
            parsed = json.loads(content)
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            self.last_error = str(exc)
            return None
        return parsed if isinstance(parsed, dict) else None

    def summarize_signal(self, draft: SignalDraft, config: SignalConfig) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        priority_names = ", ".join(item["name"] for item in draft.matched_priorities) or "none"
        prompt = f"""
You are Signal Stream, a strategic intelligence analyst.
Audience: {config.audience}
Mission: {config.mission}

Create a concise executive signal brief as JSON.
Keep summary under 55 words.
Keep why_it_matters under 45 words.
Return 2 or 3 practical next_steps.

Title: {draft.cluster.articles[0].title}
Event type: {draft.event_type}
Score: {draft.score}
Urgency: {draft.urgency}
Matched priorities: {priority_names}
Entities: {json.dumps(draft.entities)}
Article text:
{draft.text[:4500]}
""".strip()

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": SUMMARY_SCHEMA,
            "options": {"temperature": 0},
        }

        try:
            req = request.Request(
                f"{self.config.host}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
            content = raw.get("message", {}).get("content", "")
            parsed = json.loads(content)
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            self.last_error = str(exc)
            return None

        if not isinstance(parsed, dict):
            return None
        if not isinstance(parsed.get("next_steps"), list):
            parsed["next_steps"] = []
        return parsed
