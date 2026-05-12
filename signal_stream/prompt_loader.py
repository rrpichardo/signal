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
    # 5 components. Values are max points; they must sum to 100.
    "components": {
        "priority_match": 25,
        "company_match": 25,
        "recency": 15,
        "event_strength": 25,
        "corroboration": 10,
    },
    "recency_bands": {
        "within_1_day": 15,
        "within_3_days": 12,
        "within_7_days": 9,
        "older": 6,
        "unknown": 7,
    },
    "event_strength_bands": {
        "none": 0,
        "opinion_or_listicle": 5,
        "useful_analysis": 10,
        "product_update_or_signal": 15,
        "launch_funding_regulation": 20,
        "major_platform_shift": 25,
    },
    "priority_match_bands": {
        "no_match": 0,
        "weak_incidental": 5,
        "one_relevant_not_central": 10,
        "one_central_or_two_weak": 15,
        "one_central_with_support": 20,
        "direct_high_impact": 25,
    },
    "company_match_bands": {
        "no_match": 0,
        "one_passing": 5,
        "relevant_not_central": 10,
        "watchlist_central": 15,
        "watchlist_in_title_or_lede": 20,
        "watchlist_strategic_action": 25,
    },
    "corroboration_bands": {
        "single": 0,
        "same_source_repeated": 3,
        "two_independent": 5,
        "three_or_more_independent": 8,
        "broad_cross_type": 10,
    },
}

# Display preferences shown on the dashboard Digest page.
# Editable via the Settings page (`[display]` block in agent_brain.toml).
DEFAULT_DISPLAY_SETTINGS: dict[str, Any] = {
    "page_size": 10,
    "default_scope": "latest",
}


DEFAULT_BEHAVIOR_SETTINGS: dict[str, Any] = {
    "scout_mode": "hybrid",
    "analyst_mode": "hybrid",
    "relevance_policy": "soft_keep",
    "scout_note_enabled": True,
    "model_score_adjustment_limit": 20,
    "summary_mode": "short_expanded",
    "visuals_mode": "image_icon",
    "entity_extraction": "hybrid",
    "analyst_review_limit": 8,
    "analyst_full_review": False,
    # Critic-loop defaults: opt-in. Existing runs keep the old three-agent shape.
    "enable_critic": True,
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
    # 5-component rubric sections — each is a flat int dict
    for section in ("components", "recency_bands", "event_strength_bands",
                    "priority_match_bands", "company_match_bands", "corroboration_bands"):
        _merge_int_section(rubric[section], scoring.get(section, {}))
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
        "entity_extraction",
    }
    for key in text_keys:
        value = str(behavior.get(key, "")).strip().lower()
        if value:
            settings[key] = value
    if "scout_note_enabled" in behavior:
        settings["scout_note_enabled"] = bool(behavior.get("scout_note_enabled"))
    if "analyst_full_review" in behavior:
        settings["analyst_full_review"] = bool(behavior.get("analyst_full_review"))
    if "model_score_adjustment_limit" in behavior:
        try:
            settings["model_score_adjustment_limit"] = max(0, min(100, int(behavior["model_score_adjustment_limit"])))
        except (TypeError, ValueError):
            pass
    if "analyst_review_limit" in behavior:
        try:
            settings["analyst_review_limit"] = max(1, min(100, int(behavior["analyst_review_limit"])))
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


def load_display_settings(path: str | Path | None) -> dict[str, Any]:
    """Load digest display preferences (page size, default scope) from the brain file.

    Plain English: these are the dashboard knobs the user can change in the
    Settings page to control how the digest list is displayed.
    """

    settings = dict(DEFAULT_DISPLAY_SETTINGS)
    raw = _load_raw(path)
    display = raw.get("display", {}) if raw else {}
    if not isinstance(display, dict):
        return settings

    # page_size: must be a positive int within a sane range
    if "page_size" in display:
        try:
            settings["page_size"] = max(1, min(100, int(display["page_size"])))
        except (TypeError, ValueError):
            pass
    # default_scope: must be one of two known values
    scope = str(display.get("default_scope", "")).strip().lower()
    if scope in ("latest", "all"):
        settings["default_scope"] = scope
    return settings


def load_brain_file(path: str | Path | None) -> dict[str, Any]:
    """Return everything the Settings screen needs in one friendly package."""

    raw = _load_raw(path)
    return {
        "prompts": load_prompt_set(path),
        "scoring": load_scoring_rubric(path),
        "behavior": load_behavior_settings(path),
        "display": load_display_settings(path),
        "raw": _render_brain_toml(
            load_prompt_set(path),
            load_scoring_rubric(path),
            load_behavior_settings(path),
            load_display_settings(path),
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
    for section in ("components", "recency_bands", "event_strength_bands",
                    "priority_match_bands", "company_match_bands", "corroboration_bands"):
        _merge_int_section(scoring[section], incoming_scoring.get(section, {}))

    behavior = dict(existing.get("behavior") or DEFAULT_BEHAVIOR_SETTINGS)
    behavior.update(dict(brain.get("behavior") or {}))

    # Merge display preferences from existing file + incoming payload.
    # Incoming values are validated (clamped) so a malformed POST cannot break the file.
    display = dict(existing.get("display") or DEFAULT_DISPLAY_SETTINGS)
    incoming_display = dict(brain.get("display") or {})
    if "page_size" in incoming_display:
        try:
            display["page_size"] = max(1, min(100, int(incoming_display["page_size"])))
        except (TypeError, ValueError):
            pass
    if "default_scope" in incoming_display:
        scope = str(incoming_display["default_scope"]).strip().lower()
        if scope in ("latest", "all"):
            display["default_scope"] = scope

    brain_path.write_text(_render_brain_toml(prompts, scoring, behavior, display), encoding="utf-8")


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


def _render_brain_toml(
    prompts: dict[str, str],
    scoring: dict[str, Any],
    behavior: dict[str, Any],
    display: dict[str, Any] | None = None,
) -> str:
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
            f"analyst_review_limit = {int(behavior.get('analyst_review_limit', 8))}",
            f"analyst_full_review = {_toml_bool(behavior.get('analyst_full_review', False))}",
            f'summary_mode = "{_toml_text(behavior.get("summary_mode", "short_expanded"))}"',
            f'visuals_mode = "{_toml_text(behavior.get("visuals_mode", "image_icon"))}"',
            f'entity_extraction = "{_toml_text(behavior.get("entity_extraction", "hybrid"))}"',
            # Critic-loop switches live next to the other behavior knobs so a non-
            # technical editor can flip them in one place.
            f"enable_critic = {_toml_bool(behavior.get('enable_critic', False))}",
            f"max_critic_rounds = {int(behavior.get('max_critic_rounds', 1))}",
            f"critic_score_threshold = {int(behavior.get('critic_score_threshold', 70))}",
            "",
        ]
    )

    # 5-component scoring rubric sections
    _scoring_sections = [
        ("scoring.components", "components",
         "# 5 components. Values are max points; they must sum to 100."),
        ("scoring.recency_bands", "recency_bands",
         "# How many points a story gets based on publication age."),
        ("scoring.event_strength_bands", "event_strength_bands",
         "# Strength bands for event type classification."),
        ("scoring.priority_match_bands", "priority_match_bands",
         "# Bands for weighted keyword intensity in priority groups."),
        ("scoring.company_match_bands", "company_match_bands",
         "# Bands for watchlist company prominence in the story."),
        ("scoring.corroboration_bands", "corroboration_bands",
         "# Bands for independent source coverage."),
    ]
    for toml_section, rubric_key, comment in _scoring_sections:
        lines.extend(["", f"[{toml_section}]", comment])
        current = dict(scoring.get(rubric_key, {}))
        for key, default_val in DEFAULT_SCORING_RUBRIC[rubric_key].items():
            lines.append(f"{key} = {int(current.get(key, default_val))}")

    # Display preferences for the dashboard digest. Editable via Settings page.
    display_safe = dict(DEFAULT_DISPLAY_SETTINGS)
    if isinstance(display, dict):
        display_safe.update({k: v for k, v in display.items() if k in DEFAULT_DISPLAY_SETTINGS})
    lines.extend(
        [
            "[display]",
            "# Dashboard display preferences. Edit here, save, and the digest reflects the new settings.",
            "# page_size = number of signal cards shown per page (1-100).",
            '# default_scope = "latest" or "all".',
            f"page_size = {int(display_safe.get('page_size', 10))}",
            f'default_scope = "{_toml_text(display_safe.get("default_scope", "latest"))}"',
            "",
        ]
    )
    return "\n".join(lines)


def _toml_text(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _toml_bool(value: object) -> str:
    return "true" if bool(value) else "false"
