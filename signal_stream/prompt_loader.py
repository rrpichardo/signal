from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tomllib
from typing import Any

from .prompts import ANALYST_PROMPT, CRITIC_PROMPT, ORCHESTRATOR_PROMPT, SCOUT_PROMPT


# Adding "critic" here makes the Critic worker discoverable everywhere prompts
# are loaded: agent runtime, worker startup, dashboard settings page, and the
# brain-file render below.
DEFAULT_PROMPTS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "scout": SCOUT_PROMPT,
    "analyst": ANALYST_PROMPT,
    "critic": CRITIC_PROMPT,
}

DEFAULT_SCORING_RUBRIC: dict[str, Any] = {
    "freshness": {
        "within_1_day": 20,
        "within_3_days": 17,
        "within_7_days": 13,
        "older": 8,
        "unknown": 10,
    },
    "max_points": {
        "priority_match": 25,
        "major_player": 15,
        "corroboration": 10,
        "repeat_penalty": 20,
        "low_value_penalty": 15,
    },
    "event_strength": {
        "default": 8,
        "platform_shift": 18,
        "competitor_move": 17,
        "regulatory_risk": 18,
        "asset_risk": 18,
        "infrastructure_signal": 16,
        "market_opportunity": 15,
        "startup_signal": 14,
        "builder_tactic": 11,
        "industry_signal": 10,
        "general_signal": 8,
    },
    "low_value_phrases": [
        "webinar",
        "conference",
        "register now",
        "sponsored",
        "sponsor",
        "course",
        "top 10",
        "roundup",
        "job opening",
        "we are hiring",
    ],
}

DEFAULT_BEHAVIOR_SETTINGS: dict[str, Any] = {
    "scout_mode": "hybrid",
    "analyst_mode": "hybrid",
    "relevance_policy": "soft_keep",
    "scout_note_enabled": True,
    "model_score_adjustment_limit": 20,
    "summary_mode": "short_expanded",
    "visuals_mode": "image_icon",
    "repeat_penalty_strength": "strong",
    "entity_extraction": "hybrid",
    # Critic-loop defaults: opt-in. Existing runs keep the old three-agent shape.
    "enable_critic": False,
    "max_critic_rounds": 1,
    "critic_score_threshold": 70,
}


def load_prompt_set(path: str | Path | None) -> dict[str, str]:
    """Load editable prompts from disk with safe fallback defaults."""

    prompts = dict(DEFAULT_PROMPTS)
    raw = _load_raw(path)
    if not raw:
        return prompts

    for name in prompts:
        section = raw.get(name, {})
        value = str(section.get("prompt", "")).strip()
        if value:
            prompts[name] = value
    return prompts


def load_scoring_rubric(path: str | Path | None) -> dict[str, Any]:
    """Load the editable scoring rubric from the brain file.

    Plain English: this lets a non-technical person tune the scoring system in
    one TOML file without opening the Python code.
    """

    rubric = deepcopy(DEFAULT_SCORING_RUBRIC)
    raw = _load_raw(path)
    if not raw:
        return rubric

    scoring = raw.get("scoring", {})
    _merge_int_section(rubric["freshness"], scoring.get("freshness", {}))
    _merge_int_section(rubric["max_points"], scoring.get("max_points", {}))
    _merge_int_section(rubric["event_strength"], scoring.get("event_strength", {}))

    phrases = scoring.get("low_value_phrases")
    if isinstance(phrases, list):
        cleaned = [str(item).strip().lower() for item in phrases if str(item).strip()]
        if cleaned:
            rubric["low_value_phrases"] = cleaned
    return rubric


def load_behavior_settings(path: str | Path | None) -> dict[str, Any]:
    """Load plain-English behavior switches from the brain file."""

    settings = dict(DEFAULT_BEHAVIOR_SETTINGS)
    raw = _load_raw(path)
    behavior = raw.get("behavior", {}) if raw else {}
    if not isinstance(behavior, dict):
        return settings

    text_keys = {
        "scout_mode",
        "analyst_mode",
        "relevance_policy",
        "summary_mode",
        "visuals_mode",
        "repeat_penalty_strength",
        "entity_extraction",
    }
    for key in text_keys:
        value = str(behavior.get(key, "")).strip().lower()
        if value:
            settings[key] = value
    if "scout_note_enabled" in behavior:
        settings["scout_note_enabled"] = bool(behavior.get("scout_note_enabled"))
    if "model_score_adjustment_limit" in behavior:
        try:
            settings["model_score_adjustment_limit"] = max(0, min(100, int(behavior["model_score_adjustment_limit"])))
        except (TypeError, ValueError):
            pass
    # Critic-loop switches: bool toggle and two bounded integers.
    if "enable_critic" in behavior:
        settings["enable_critic"] = bool(behavior.get("enable_critic"))
    if "max_critic_rounds" in behavior:
        try:
            settings["max_critic_rounds"] = max(0, min(5, int(behavior["max_critic_rounds"])))
        except (TypeError, ValueError):
            pass
    if "critic_score_threshold" in behavior:
        try:
            settings["critic_score_threshold"] = max(0, min(100, int(behavior["critic_score_threshold"])))
        except (TypeError, ValueError):
            pass
    return settings


def load_brain_file(path: str | Path | None) -> dict[str, Any]:
    """Return everything the Settings screen needs in one friendly package."""

    raw = _load_raw(path)
    return {
        "prompts": load_prompt_set(path),
        "scoring": load_scoring_rubric(path),
        "behavior": load_behavior_settings(path),
        "raw": _render_brain_toml(
            load_prompt_set(path),
            load_scoring_rubric(path),
            load_behavior_settings(path),
        )
        if not raw
        else Path(path).expanduser().resolve().read_text(encoding="utf-8"),
    }


def save_brain_file(path: str | Path, brain: dict[str, Any]) -> None:
    """Save dashboard-edited brain settings back to TOML.

    Plain English: the dashboard sends simple JSON. This function turns it back
    into the editable TOML file that Signal Stream reads on the next run.
    """

    brain_path = Path(path).expanduser().resolve()
    existing = load_brain_file(path)

    prompts = dict(existing.get("prompts") or DEFAULT_PROMPTS)
    prompts.update({key: str(value) for key, value in dict(brain.get("prompts") or {}).items() if key in prompts})

    scoring = deepcopy(existing.get("scoring") or DEFAULT_SCORING_RUBRIC)
    incoming_scoring = dict(brain.get("scoring") or {})
    _merge_int_section(scoring["freshness"], incoming_scoring.get("freshness", {}))
    _merge_int_section(scoring["max_points"], incoming_scoring.get("max_points", {}))
    _merge_int_section(scoring["event_strength"], incoming_scoring.get("event_strength", {}))
    phrases = incoming_scoring.get("low_value_phrases")
    if isinstance(phrases, list):
        scoring["low_value_phrases"] = [str(item).strip().lower() for item in phrases if str(item).strip()]

    behavior = dict(existing.get("behavior") or DEFAULT_BEHAVIOR_SETTINGS)
    behavior.update(dict(brain.get("behavior") or {}))
    brain_path.write_text(_render_brain_toml(prompts, scoring, behavior), encoding="utf-8")


def save_raw_brain_file(path: str | Path, raw_text: str) -> None:
    """Save the advanced editor contents after confirming it is valid TOML."""

    # Parse first so a typo in the advanced editor does not break future runs.
    tomllib.loads(raw_text)
    Path(path).expanduser().resolve().write_text(raw_text, encoding="utf-8")


def _load_raw(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}

    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        return {}

    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    return raw if isinstance(raw, dict) else {}


def _merge_int_section(target: dict[str, Any], overrides: Any) -> None:
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        try:
            target[str(key)] = int(value)
        except (TypeError, ValueError):
            continue


def _render_brain_toml(prompts: dict[str, str], scoring: dict[str, Any], behavior: dict[str, Any]) -> str:
    lines = [
        "# Signal Stream Brain File",
        "#",
        "# This is the main non-technical edit file.",
        "# Change prompts, behavior, and scoring here, then run Signal Stream again.",
        "# The dashboard Settings page edits this same file.",
        "",
    ]
    # Iterate over all four agent prompt sections so the editable brain file
    # always exposes the Critic alongside the other three.
    for name in ("orchestrator", "scout", "analyst", "critic"):
        lines.extend([f"[{name}]", 'prompt = """', str(prompts.get(name, "")).strip(), '"""', ""])

    lines.extend(
        [
            "[behavior]",
            "# Friendly switches that control how agent judgment behaves.",
            f'scout_mode = "{_toml_text(behavior.get("scout_mode", "hybrid"))}"',
            f'analyst_mode = "{_toml_text(behavior.get("analyst_mode", "hybrid"))}"',
            f'relevance_policy = "{_toml_text(behavior.get("relevance_policy", "soft_keep"))}"',
            f"scout_note_enabled = {_toml_bool(behavior.get('scout_note_enabled', True))}",
            f"model_score_adjustment_limit = {int(behavior.get('model_score_adjustment_limit', 20))}",
            f'summary_mode = "{_toml_text(behavior.get("summary_mode", "short_expanded"))}"',
            f'visuals_mode = "{_toml_text(behavior.get("visuals_mode", "image_icon"))}"',
            f'repeat_penalty_strength = "{_toml_text(behavior.get("repeat_penalty_strength", "strong"))}"',
            f'entity_extraction = "{_toml_text(behavior.get("entity_extraction", "hybrid"))}"',
            # Critic-loop switches live next to the other behavior knobs so a non-
            # technical editor can flip them in one place.
            f"enable_critic = {_toml_bool(behavior.get('enable_critic', False))}",
            f"max_critic_rounds = {int(behavior.get('max_critic_rounds', 1))}",
            f"critic_score_threshold = {int(behavior.get('critic_score_threshold', 70))}",
            "",
            "[scoring.freshness]",
            "# Newer stories score higher.",
        ]
    )
    for key in ("within_1_day", "within_3_days", "within_7_days", "older", "unknown"):
        lines.append(f"{key} = {int(dict(scoring.get('freshness', {})).get(key, DEFAULT_SCORING_RUBRIC['freshness'][key]))}")

    lines.extend(["", "[scoring.max_points]", "# These are the caps for the base code rubric."])
    for key in ("priority_match", "major_player", "corroboration", "repeat_penalty", "low_value_penalty"):
        lines.append(f"{key} = {int(dict(scoring.get('max_points', {})).get(key, DEFAULT_SCORING_RUBRIC['max_points'][key]))}")

    lines.extend(["", "[scoring.event_strength]", "# Different kinds of stories can start stronger or weaker."])
    event_strength = dict(scoring.get("event_strength", {}))
    for key in DEFAULT_SCORING_RUBRIC["event_strength"]:
        lines.append(f"{key} = {int(event_strength.get(key, DEFAULT_SCORING_RUBRIC['event_strength'][key]))}")

    lines.extend(["", "[scoring]", "# If an article looks like this kind of content, it gets a low-value penalty.", "low_value_phrases = ["])
    for phrase in scoring.get("low_value_phrases", []):
        lines.append(f'  "{_toml_text(phrase)}",')
    lines.extend(["]", ""])
    return "\n".join(lines)


def _toml_text(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _toml_bool(value: object) -> str:
    return "true" if bool(value) else "false"
