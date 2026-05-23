from __future__ import annotations

# Tests for _format_http_error — proves Groq 4xx response bodies are surfaced
# in BrainClient.last_error so the dashboard timeline shows WHY a call failed
# (rate-limit, prompt-guard, billing hold) instead of just "HTTP 403: Forbidden".

import unittest
from io import BytesIO
from urllib.error import HTTPError

from signal_stream.llm import _format_http_error


def _make_http_error(code: int, reason: str, body: bytes) -> HTTPError:
    # HTTPError reads its body from the `fp` argument when .read() is called.
    # BytesIO is the simplest stand-in for a real response file object.
    return HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=code,
        msg=reason,
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(body),
    )


class FormatHttpErrorTest(unittest.TestCase):
    def test_403_body_is_included_in_message(self) -> None:
        # Realistic Groq 4xx body — the "real" reason lives here, not in exc.reason.
        body = b'{"error":{"message":"Account temporarily blocked due to abuse heuristic.","type":"forbidden"}}'
        exc = _make_http_error(403, "Forbidden", body)

        msg = _format_http_error(exc)

        self.assertIn("Groq HTTP 403: Forbidden", msg)
        self.assertIn("abuse heuristic", msg)
        self.assertIn("body:", msg)

    def test_body_is_truncated_at_600_chars(self) -> None:
        # Long bodies (HTML error pages, huge JSON) must not bloat event payloads.
        body = b"x" * 2000
        exc = _make_http_error(500, "Internal Server Error", body)

        msg = _format_http_error(exc)

        # Header + " — body: " + 600 x's. We assert the cap by checking the
        # 'x' run length, since the prefix length is fixed.
        x_count = msg.count("x")
        self.assertEqual(x_count, 600)

    def test_empty_body_falls_back_to_header_only(self) -> None:
        # When Groq returns no body (rare but possible), the old format is preserved.
        exc = _make_http_error(403, "Forbidden", b"")

        msg = _format_http_error(exc)

        self.assertEqual(msg, "Groq HTTP 403: Forbidden")

    def test_unreadable_body_does_not_raise(self) -> None:
        # If .read() blows up (e.g. fp already consumed), the helper must still
        # return the header-only message rather than propagate the exception.
        exc = _make_http_error(429, "Too Many Requests", b"some body")
        exc.read()  # exhaust the BytesIO so a second .read() returns b""

        msg = _format_http_error(exc)

        self.assertEqual(msg, "Groq HTTP 429: Too Many Requests")


if __name__ == "__main__":
    unittest.main()
