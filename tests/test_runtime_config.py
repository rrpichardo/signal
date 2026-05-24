"""Tests for the surgical ai_tech.toml runtime editor.

The runtime config holds the [[sources]] and [[priorities]] arrays. The writer
must update only the targeted scalar lines and leave everything else — arrays,
comments, blank lines — byte-for-byte intact.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from signal_stream.runtime_config import load_runtime_settings, save_runtime_settings

FIXTURE = """\
[profile]
name = "Test"

[storage]
path = "../.signal_stream/signal_stream.db"

[delivery]
output_dir = "../outputs"
digest_limit = 40
critical_threshold = 86
similarity_threshold = 0.48

[brain]
model = "meta-llama/llama-4-scout-17b-16e-instruct"
timeout_seconds = 60

[agent]
max_iterations = 6
dashboard_port = 8765
worker_timeout_seconds = 2400
brain_file = "agent_brain.toml"

# A comment inside a priorities block must survive.
[[priorities]]
name = "Frontier AI"
weight = 2.8
keywords = ["Anthropic", "OpenAI", "Gemini"]

[[sources]]
name = "Towards AI"
kind = "rss"
url = "https://pub.towardsai.net/feed"
limit = 8
enabled = true

[[sources]]
name = "ByteByteGo"
kind = "rss"
url = "https://blog.bytebytego.com/feed"
limit = 8
enabled = true
"""


class RuntimeConfigTest(unittest.TestCase):
    def _write(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "ai_tech.toml"
        p.write_text(FIXTURE, encoding="utf-8")
        return p

    def test_scalar_update_preserves_arrays_and_comments(self) -> None:
        p = self._write()
        before = p.read_text()
        save_runtime_settings(
            p,
            {"agent": {"worker_timeout_seconds": 1800}, "brain": {"model": "new-model"}, "delivery": {"digest_limit": 25}},
        )
        after = p.read_text()

        # Arrays and their content survive untouched.
        self.assertEqual(after.count("[[sources]]"), 2)
        self.assertEqual(after.count("[[priorities]]"), 1)
        self.assertIn('keywords = ["Anthropic", "OpenAI", "Gemini"]', after)
        self.assertIn("# A comment inside a priorities block must survive.", after)
        self.assertIn('url = "https://blog.bytebytego.com/feed"', after)

        # Exactly the three targeted lines changed.
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        self.assertEqual(len(before_lines), len(after_lines))
        changed = {b for b, a in zip(before_lines, after_lines) if b != a}
        self.assertEqual(
            {line.split("=")[0].strip() for line in changed},
            {"worker_timeout_seconds", "model", "digest_limit"},
        )

        reread = load_runtime_settings(p)
        self.assertEqual(reread["agent"]["worker_timeout_seconds"], 1800)
        self.assertEqual(reread["brain"]["model"], "new-model")
        self.assertEqual(reread["delivery"]["digest_limit"], 25)

    def test_rejects_unknown_or_non_editable_keys(self) -> None:
        p = self._write()
        with self.assertRaises(ValueError):
            save_runtime_settings(p, {"agent": {"totally_made_up": 5}})
        # The file must be unchanged after a rejected save.
        self.assertEqual(p.read_text(), FIXTURE)

    def test_clamps_out_of_range_values(self) -> None:
        p = self._write()
        save_runtime_settings(p, {"agent": {"max_iterations": 9999}, "delivery": {"similarity_threshold": 5.0}})
        reread = load_runtime_settings(p)
        self.assertEqual(reread["agent"]["max_iterations"], 20)  # clamped to max
        self.assertEqual(reread["delivery"]["similarity_threshold"], 1.0)  # clamped to max

    def test_inserts_missing_key_under_section_header(self) -> None:
        # critical_threshold is removed from this fixture's delivery block.
        p = self._write()
        text = p.read_text().replace("critical_threshold = 86\n", "")
        p.write_text(text, encoding="utf-8")
        save_runtime_settings(p, {"delivery": {"critical_threshold": 90}})
        self.assertEqual(load_runtime_settings(p)["delivery"]["critical_threshold"], 90)


if __name__ == "__main__":
    unittest.main()
