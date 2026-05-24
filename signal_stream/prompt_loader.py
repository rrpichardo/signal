from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tomllib
from typing import Any

from .prompts import ANALYST_PROMPT, CRITIC_PROMPT, EDITOR_PROMPT, ORCHESTRATOR_PROMPT, SCOUT_PROMPT


# Adding a new agent here makes its prompt discoverable everywhere they are
# loaded: agent runtime, worker startup, dashboard Settings page, and the
# brain-file render below. "editor" drives the executive-briefing generator.
DEFAULT_PROMPTS = {
    "orchestrator": ORCHESTRATOR_PROMPT,
    "scout": SCOUT_PROMPT,
    "analyst": ANALYST_PROMPT,
    "critic": CRITIC_PROMPT,
    "editor": EDITOR_PROMPT,
}

DEFAULT_SCORING_RUBRIC: dict[str, Any] = {
    # Richard Signal Score V2. These are multipliers for 1-5 dimensions.
    # Sum must be 20 so a perfect 5/5 across six dimensions produces 100.
    "value_weights": {
        "relevance_to_richard": 5.0,
        "strategic_importance": 5.0,
        "actionability": 3.0,
        "credibility": 3.0,
        "novelty": 2.0,
        "time_sensitivity": 2.0,
    },
    # Weighted trust deficit, on a 0-100 scale before the penalty scale is applied.
    "trust_weights": {
        "claim_support_deficit": 0.50,
        "hype_or_manipulation_deficit": 0.30,
        "source_credibility_deficit": 0.20,
    },
    "trust_penalty": {
        "scale": 0.25,
    },
    "hard_caps": {
        "promo_deal_event_registration": 25,
        "random_gadget_consumer_deal": 30,
        "generic_tutorial": 45,
        "direct_builder_tutorial": 60,
        "generic_funding_announcement": 55,
        "single_source_sensational_claim": 60,
        "unsupported_opinion_prediction": 50,
        "minor_product_launch": 55,
        "duplicate_or_stale_repeat": 40,
    },
    # Internal helper bands. These are still loaded for backwards compatibility,
    # but the visible score now comes from the V2 value/trust rubric above.
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
    "analyst_review_limit": 40,
    "analyst_review_batch_size": 1,
    "analyst_full_review": True,
    # How many times to retry a signal whose first Groq review fails with a
    # rate-limit/timeout. 0 = no retry. Capped at 1 to keep runtime bounded.
    "analyst_retry_max_attempts": 1,
    "executive_summary_limit": 12,
    # Minimum score a signal must clear (0-100) for the latest run to refresh
    # the Briefing. If no signal in the latest run clears this floor, the
    # Briefing falls back to the most recent prior run, tagged as stale.
    "executive_summary_min_score": 45,
    # Critic-loop defaults: opt-in. Existing runs keep the old three-agent shape.
    "enable_critic": True,
    "max_critic_rounds": 1,
    "critic_score_threshold": 70,
    # Max article body sent to Groq per review request, in tokens (~4 chars each).
    # 18000 tokens ≈ 72k chars. Upper bound 120000 stays under the ~128k model context.
    "max_article_tokens_for_llm": 18000,
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
    for section in ("value_weights", "trust_weights", "trust_penalty"):
        _merge_number_section(rubric[section], scoring.get(section, {}))
    for section in ("hard_caps", "recency_bands", "event_strength_bands",
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
    if "analyst_retry_max_attempts" in behavior:
        # 0 = no retry, 1 = one retry. Capped at 1 so a run can't spin on retries.
        try:
            settings["analyst_retry_max_attempts"] = max(0, min(1, int(behavior["analyst_retry_max_attempts"])))
        except (TypeError, ValueError):
            pass
    if "model_score_adjustment_limit" in behavior:
        try:
            settings["model_score_adjustment_limit"] = max(0, min(100, int(behavior["model_score_adjustment_limit"])))
        except (TypeError, ValueError):
            pass
    if "analyst_review_limit" in behavior:
        try:
            settings["analyst_review_limit"] = max(1, min(200, int(behavior["analyst_review_limit"])))
        except (TypeError, ValueError):
            pass
    if "analyst_review_batch_size" in behavior:
        try:
            settings["analyst_review_batch_size"] = max(1, min(20, int(behavior["analyst_review_batch_size"])))
        except (TypeError, ValueError):
            pass
    if "executive_summary_limit" in behavior:
        try:
            settings["executive_summary_limit"] = max(1, min(100, int(behavior["executive_summary_limit"])))
        except (TypeError, ValueError):
            pass
    if "executive_summary_min_score" in behavior:
        # Bounded 0-100. At 0, the floor is effectively off (any signal qualifies).
        try:
            settings["executive_summary_min_score"] = max(0, min(100, int(behavior["executive_summary_min_score"])))
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
    if "max_article_tokens_for_llm" in behavior:
        try:
            settings["max_article_tokens_for_llm"] = max(1000, min(120000, int(behavior["max_article_tokens_for_llm"])))
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
    for section in ("value_weights", "trust_weights", "trust_penalty"):
        _merge_number_section(scoring[section], incoming_scoring.get(section, {}))
    for section in ("hard_caps", "recency_bands", "event_strength_bands",
                    "priority_match_bands", "company_match_bands", "corroboration_bands"):
        _merge_int_section(scoring[section], incoming_scoring.get(section, {}))

    # Validate that V2 value weights still produce a 0-100 value score after
    # merging. Raise immediately so the dashboard surfaces a clear error.
    value_weight_sum = sum(float(v) for v in scoring["value_weights"].values())
    if abs(value_weight_sum - 20.0) > 0.001:
        raise ValueError(
            f"Value weights must sum to 20 (got {value_weight_sum:g}). "
            "Adjust relevance_to_richard, strategic_importance, actionability, "
            "credibility, novelty, or time_sensitivity."
        )
    trust_weight_sum = sum(float(v) for v in scoring["trust_weights"].values())
    if abs(trust_weight_sum - 1.0) > 0.001:
        raise ValueError(
            f"Trust weights must sum to 1.0 (got {trust_weight_sum:g}). "
            "Adjust claim_support_deficit, hype_or_manipulation_deficit, or source_credibility_deficit."
        )
    scale = float(scoring["trust_penalty"].get("scale", 0.25))
    if scale < 0 or scale > 1:
        raise ValueError("Trust penalty scale must be between 0 and 1.")
    for cap_name, cap_value in scoring["hard_caps"].items():
        cap_int = int(cap_value)
        if cap_int < 0 or cap_int > 100:
            raise ValueError(f"Hard cap {cap_name} must be between 0 and 100.")

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


def _merge_number_section(target: dict[str, Any], overrides: Any) -> None:
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        if key not in target:
            continue
        try:
            target[str(key)] = float(value)
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
    # Iterate over every agent prompt section so the editable brain file
    # always exposes all of them (Editor included — it drives the briefing).
    for name in ("orchestrator", "scout", "analyst", "critic", "editor"):
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
            f"analyst_review_limit = {int(behavior.get('analyst_review_limit', 40))}",
            f"analyst_review_batch_size = {int(behavior.get('analyst_review_batch_size', 1))}",
            f"analyst_full_review = {_toml_bool(behavior.get('analyst_full_review', True))}",
            f"analyst_retry_max_attempts = {int(behavior.get('analyst_retry_max_attempts', 1))}",
            f"executive_summary_limit = {int(behavior.get('executive_summary_limit', 12))}",
            f"executive_summary_min_score = {int(behavior.get('executive_summary_min_score', 45))}",
            f'summary_mode = "{_toml_text(behavior.get("summary_mode", "short_expanded"))}"',
            f'visuals_mode = "{_toml_text(behavior.get("visuals_mode", "image_icon"))}"',
            f'entity_extraction = "{_toml_text(behavior.get("entity_extraction", "hybrid"))}"',
            # Critic-loop switches live next to the other behavior knobs so a non-
            # technical editor can flip them in one place.
            f"enable_critic = {_toml_bool(behavior.get('enable_critic', False))}",
            f"max_critic_rounds = {int(behavior.get('max_critic_rounds', 1))}",
            f"critic_score_threshold = {int(behavior.get('critic_score_threshold', 70))}",
            # Max article body sent to Groq per review (tokens, ~4 chars each).
            f"max_article_tokens_for_llm = {int(behavior.get('max_article_tokens_for_llm', 18000))}",
            "",
        ]
    )

    # Richard Signal Score V2 sections. The editable brain file exposes the V2
    # value/trust weights, Python-owned hard caps, and the three bands that
    # _base_score_card actually consumes (priority/company/event strength).
    # recency_bands and corroboration_bands are intentionally NOT rendered here:
    # they feed the legacy _score_recency / _score_corroboration helpers, which
    # _base_score_card no longer calls, so exposing them would imply a false effect.
    _scoring_sections = [
        ("scoring.value_weights", "value_weights",
         "# V2 value multipliers. Six 1-5 dimensions; weights must sum to 20."),
        ("scoring.trust_weights", "trust_weights",
         "# V2 trust deficit weights. Weighted deficits are 0-100; weights must sum to 1.0."),
        ("scoring.trust_penalty", "trust_penalty",
         "# Penalty scale applied to the weighted trust deficit. 0.25 means max penalty is 25 points."),
        ("scoring.hard_caps", "hard_caps",
         "# Python-owned hard caps for low-value patterns."),
        ("scoring.priority_match_bands", "priority_match_bands",
         "# Points for how directly an article matches your priority groups (0-25)."),
        ("scoring.company_match_bands", "company_match_bands",
         "# Points for how centrally a watchlist company features (0-25)."),
        ("scoring.event_strength_bands", "event_strength_bands",
         "# Points for how strong the underlying event is (0-25)."),
    ]
    for toml_section, rubric_key, comment in _scoring_sections:
        lines.extend(["", f"[{toml_section}]", comment])
        current = dict(scoring.get(rubric_key, {}))
        for key, default_val in DEFAULT_SCORING_RUBRIC[rubric_key].items():
            lines.append(f"{key} = {_toml_number(current.get(key, default_val))}")

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


def _toml_number(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.4f}".rstrip("0").rstrip(".")


def _toml_bool(value: object) -> str:
    return "true" if bool(value) else "false"
