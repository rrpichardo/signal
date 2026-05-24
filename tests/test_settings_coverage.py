"""Coverage contract: every live config key is editable in Settings, or
explicitly marked advanced-only with a stated reason.

This is the keystone guard for the Settings operator surface. It enumerates the
full universe of config keys across BOTH config files (the brain file defaults +
the actual ai_tech.toml) and asserts each one is covered by the settings
manifest. If a future change adds a config key without wiring it into Settings
(or marking it advanced with a reason), this test fails.
"""

from __future__ import annotations

from pathlib import Path
import tomllib
import unittest

from signal_stream.prompt_loader import (
    DEFAULT_BEHAVIOR_SETTINGS,
    DEFAULT_DISPLAY_SETTINGS,
    DEFAULT_PROMPTS,
    DEFAULT_SCORING_RUBRIC,
)
from signal_stream.settings_manifest import SETTINGS_MANIFEST, find_entry

REPO_ROOT = Path(__file__).resolve().parents[1]
AI_TECH = REPO_ROOT / "configs" / "ai_tech.toml"


def _brain_universe() -> set[str]:
    """Every editable key the brain file (agent_brain.toml) exposes."""
    keys: set[str] = set()
    keys.update(f"behavior.{k}" for k in DEFAULT_BEHAVIOR_SETTINGS)
    for section, sub in DEFAULT_SCORING_RUBRIC.items():
        if isinstance(sub, dict):
            keys.update(f"scoring.{section}.{k}" for k in sub)
        else:
            keys.add(f"scoring.{section}")
    keys.update(f"display.{k}" for k in DEFAULT_DISPLAY_SETTINGS)
    keys.update(f"prompts.{k}" for k in DEFAULT_PROMPTS)
    return keys


def _runtime_universe() -> set[str]:
    """Every key actually present in ai_tech.toml, including array item fields."""
    with AI_TECH.open("rb") as fh:
        raw = tomllib.load(fh)
    keys: set[str] = set()
    for section in ("profile", "storage", "delivery", "brain", "agent"):
        block = raw.get(section, {})
        if isinstance(block, dict):
            keys.update(f"{section}.{k}" for k in block)
    # Array-of-table sections: cover the array plus each item's field keys.
    for arr_name in ("priorities", "sources"):
        items = raw.get(arr_name, [])
        keys.add(f"{arr_name}[]")
        for item in items:
            if isinstance(item, dict):
                keys.update(f"{arr_name}[].{k}" for k in item)
    return keys


class SettingsCoverageTest(unittest.TestCase):
    def test_every_live_key_is_covered_by_the_manifest(self) -> None:
        universe = _brain_universe() | _runtime_universe()
        missing = sorted(k for k in universe if find_entry(k) is None)
        self.assertEqual(
            missing,
            [],
            f"Config keys not covered by SETTINGS_MANIFEST (wire into Settings or "
            f"mark advanced with a reason): {missing}",
        )

    def test_advanced_entries_have_a_reason(self) -> None:
        offenders = [
            e["id"]
            for e in SETTINGS_MANIFEST
            if e["exposure"] == "advanced" and not str(e.get("reason", "")).strip()
        ]
        self.assertEqual(offenders, [], f"Advanced-only entries missing a reason: {offenders}")

    def test_manifest_entries_are_well_formed(self) -> None:
        seen: set[str] = set()
        valid_timing = {"next_run", "next_page", "restart"}
        valid_exposure = {"editable", "advanced"}
        for e in SETTINGS_MANIFEST:
            self.assertNotIn(e["id"], seen, f"duplicate manifest id: {e['id']}")
            seen.add(e["id"])
            self.assertIn(e["timing"], valid_timing, f"{e['id']} has bad timing {e['timing']}")
            self.assertIn(e["exposure"], valid_exposure, f"{e['id']} has bad exposure {e['exposure']}")
            for field in ("file", "group", "label", "help", "control"):
                self.assertTrue(str(e.get(field, "")).strip(), f"{e['id']} missing {field}")

    def test_the_two_dead_keys_are_not_advertised_as_live(self) -> None:
        # repeat_penalty_strength and editor_fulltext_fallback_cap were removed;
        # they must not reappear as editable knobs.
        for dead in ("behavior.repeat_penalty_strength", "behavior.editor_fulltext_fallback_cap"):
            entry = find_entry(dead)
            self.assertIsNone(entry, f"dead key {dead} should not be in the manifest")


if __name__ == "__main__":
    unittest.main()
