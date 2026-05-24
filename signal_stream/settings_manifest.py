"""The Settings manifest — one source of truth for every config knob.

Plain English: this file lists every configuration key the operator can touch,
what it means, how to render it, when a change takes effect, and whether it's
exposed in the UI or deliberately kept advanced-only (with a reason). The React
Settings page renders its scalar fields from this manifest (served at
/api/settings/manifest), and `tests/test_settings_coverage.py` asserts that
EVERY live config key across both TOML files is covered here — editable, or
advanced with a stated reason. If someone adds a new config key later, the
coverage test fails until they list it here.

Each entry is keyed by a full dotted path:
  - brain file (agent_brain.toml):  behavior.*, scoring.<section>[.<key>],
    display.*, prompts.*
  - runtime file (ai_tech.toml):    brain.*, agent.*, delivery.*, profile.*,
    storage.*, priorities[], sources[]

timing:    "next_run"  -> applies on the next agent run (brain file knobs)
           "next_page" -> applies on the next dashboard page load (display)
           "restart"   -> applies after restarting the agent/dashboard process
exposure:  "editable"  -> rendered as a control in Settings (or the Sources page)
           "advanced"  -> not a simple control; reason explains why
"""

from __future__ import annotations

from typing import Any


def _e(
    id: str,
    *,
    file: str,
    group: str,
    label: str,
    help: str,
    control: str,
    timing: str,
    exposure: str = "editable",
    reason: str = "",
    options: list[str] | None = None,
    min: float | None = None,
    max: float | None = None,
    step: float | None = None,
    validation: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": id,
        "file": file,
        "group": group,
        "label": label,
        "help": help,
        "control": control,
        "timing": timing,
        "exposure": exposure,
    }
    if reason:
        entry["reason"] = reason
    if options is not None:
        entry["options"] = options
    if min is not None:
        entry["min"] = min
    if max is not None:
        entry["max"] = max
    if step is not None:
        entry["step"] = step
    if validation is not None:
        entry["validation"] = validation
    return entry


_MODE_OPTS = ["code", "hybrid", "model"]

SETTINGS_MANIFEST: list[dict[str, Any]] = [
    # ----- Reader (brain, next run) -----
    _e("behavior.summary_mode", file="brain", group="reader", label="Summary mode",
       help="Whether digest cards show only the short summary or also the expanded one.",
       control="select", options=["short_expanded", "short_only"], timing="next_run"),
    _e("behavior.visuals_mode", file="brain", group="reader", label="Visuals mode",
       help="Show article images with icon fallback, icons only, or no visuals.",
       control="select", options=["image_icon", "icon_only", "none"], timing="next_run"),
    _e("behavior.scout_note_enabled", file="brain", group="reader", label="Scout note",
       help="Show the Scout's sourcing note on each signal.", control="switch", timing="next_run"),
    _e("behavior.entity_extraction", file="brain", group="reader", label="Entity extraction",
       help="How named entities (companies, people, tools) are identified.",
       control="select", options=["hybrid", "model", "known_list"], timing="next_run"),

    # ----- Agent (brain, next run) -----
    _e("behavior.scout_mode", file="brain", group="agent", label="Scout mode",
       help="How the Scout fetches and filters articles.", control="select", options=_MODE_OPTS, timing="next_run"),
    _e("behavior.analyst_mode", file="brain", group="agent", label="Analyst mode",
       help="How the Analyst scores and reviews signals.", control="select", options=_MODE_OPTS, timing="next_run"),
    _e("behavior.relevance_policy", file="brain", group="agent", label="Relevance policy",
       help="What to do with articles the Scout labels below the relevance bar.",
       control="select", options=["soft_keep", "hard_drop"], timing="next_run"),
    _e("behavior.model_score_adjustment_limit", file="brain", group="agent", label="Model score adjustment limit",
       help="Max points the model may add or subtract from the Python base score.",
       control="slider", min=0, max=100, step=5, timing="next_run"),
    _e("behavior.enable_critic", file="brain", group="agent", label="Critic (reflection loop)",
       help="Critic reviews the digest, scores it, and can request a revision round.",
       control="switch", timing="next_run"),
    _e("behavior.max_critic_rounds", file="brain", group="agent", label="Max critic rounds",
       help="How many revision loops before the Critic ships anyway.",
       control="slider", min=0, max=5, step=1, timing="next_run"),
    _e("behavior.critic_score_threshold", file="brain", group="agent", label="Critic score threshold",
       help="Digests scoring below this trigger a revision request (0-100).",
       control="slider", min=0, max=100, step=5, timing="next_run"),
    _e("behavior.analyst_full_review", file="brain", group="agent", label="Full Groq review",
       help="Fetch full article pages and send the top-N to Groq for review. Off = Python scoring only.",
       control="switch", timing="next_run"),
    _e("behavior.analyst_retry_max_attempts", file="brain", group="agent", label="Analyst retry attempts",
       help="Retries for a Groq review that fails on rate-limit/timeout. 0 = none, 1 = one retry.",
       control="slider", min=0, max=1, step=1, timing="next_run"),
    _e("behavior.max_article_tokens_for_llm", file="brain", group="agent", label="Max article tokens per review",
       help="Largest article body sent to Groq per review, in tokens (~4 chars each).",
       control="number", min=1000, max=120000, step=500, timing="next_run"),

    # ----- Scoring: top-N (brain, next run) -----
    _e("behavior.analyst_review_limit", file="brain", group="scoring", label="Articles sent to Groq",
       help="Top-N articles by Python score that get a full Groq review.",
       control="slider", min=1, max=100, step=1, timing="next_run"),
    _e("behavior.analyst_review_batch_size", file="brain", group="scoring", label="Groq batch size",
       help="Articles per Groq request. 1 = most reliable.", control="slider", min=1, max=10, step=1, timing="next_run"),
    _e("behavior.executive_summary_limit", file="brain", group="scoring", label="Executive summary size",
       help="Number of top signals fed into the executive briefing and memory.",
       control="slider", min=1, max=40, step=1, timing="next_run"),
    _e("behavior.executive_summary_min_score", file="brain", group="scoring", label="Briefing refresh floor",
       help="Lowest score a latest-run signal must clear to refresh the Briefing. 0 = always refresh.",
       control="slider", min=0, max=100, step=5, timing="next_run"),

    # ----- Scoring: V2 sections (brain, next run). Section-level entries; the UI
    # iterates each section's keys. Coverage test prefix-matches subkeys here. -----
    _e("scoring.value_weights", file="brain", group="scoring", label="Value weights",
       help="Six 1-5 value dimensions. Weights must sum to 20.",
       control="weights", min=0, max=20, step=0.5, validation="sum_to_20", timing="next_run"),
    _e("scoring.trust_weights", file="brain", group="scoring", label="Trust weights",
       help="Three trust-deficit weights. Must sum to 1.0.",
       control="weights", min=0, max=1, step=0.05, validation="sum_to_1", timing="next_run"),
    _e("scoring.trust_penalty", file="brain", group="scoring", label="Trust penalty scale",
       help="Scale on the weighted trust deficit. 0.25 means max 25-point penalty.",
       control="scale", min=0, max=1, step=0.05, timing="next_run"),
    _e("scoring.hard_caps", file="brain", group="scoring", label="Hard caps",
       help="Score ceilings (0-100) for low-value patterns.",
       control="caps", min=0, max=100, step=1, timing="next_run"),
    _e("scoring.priority_match_bands", file="brain", group="scoring", label="Priority-match bands",
       help="Points (0-25) for how directly an article matches your priority groups. Live in V2.",
       control="bands", min=0, max=25, step=1, timing="next_run"),
    _e("scoring.company_match_bands", file="brain", group="scoring", label="Company-match bands",
       help="Points (0-25) for how centrally a watchlist company features. Live in V2.",
       control="bands", min=0, max=25, step=1, timing="next_run"),
    _e("scoring.event_strength_bands", file="brain", group="scoring", label="Event-strength bands",
       help="Points (0-25) for how strong the underlying event is. Live in V2.",
       control="bands", min=0, max=25, step=1, timing="next_run"),
    # Legacy bands — not consumed by V2 _base_score_card.
    _e("scoring.recency_bands", file="brain", group="scoring", label="Recency bands (legacy)",
       help="Legacy V1 recency points.", control="bands", timing="next_run",
       exposure="advanced",
       reason="Consumed only by _score_recency, which _base_score_card no longer calls; has no effect in V2."),
    _e("scoring.corroboration_bands", file="brain", group="scoring", label="Corroboration bands (legacy)",
       help="Legacy V1 corroboration points.", control="bands", timing="next_run",
       exposure="advanced",
       reason="Consumed only by _score_corroboration, which _base_score_card no longer calls; has no effect in V2."),

    # ----- Display (brain, next page load) -----
    _e("display.page_size", file="brain", group="display", label="Cards per page",
       help="Signal cards shown per page in the dashboard (1-100).",
       control="slider", min=1, max=100, step=1, timing="next_page"),
    _e("display.default_scope", file="brain", group="display", label="Default scope",
       help="Show only the latest run's signals, or all stored signals.",
       control="select", options=["latest", "all"], timing="next_page"),

    # ----- Prompts (brain, next run) — edited in the Prompts tab -----
    _e("prompts.orchestrator", file="brain", group="prompts", label="Orchestrator prompt",
       help="Shapes how the Orchestrator decides what to do next.", control="textarea", timing="next_run"),
    _e("prompts.scout", file="brain", group="prompts", label="Scout prompt",
       help="Guides the Scout when filtering and summarizing articles.", control="textarea", timing="next_run"),
    _e("prompts.analyst", file="brain", group="prompts", label="Analyst prompt",
       help="Drives scoring and the per-signal summaries the cards show.", control="textarea", timing="next_run"),
    _e("prompts.critic", file="brain", group="prompts", label="Critic prompt",
       help="Reviews the digest and flags weak signals (when Critic is on).", control="textarea", timing="next_run"),
    _e("prompts.editor", file="brain", group="prompts", label="Editor prompt",
       help="Writes the executive briefing from the day's top signals.", control="textarea", timing="next_run"),

    # ----- Runtime knobs (ai_tech.toml, restart required) -----
    _e("brain.model", file="runtime", group="runtime", label="Groq model",
       help="The Groq model id used for all agent and review calls.", control="text", timing="restart"),
    _e("brain.timeout_seconds", file="runtime", group="runtime", label="Groq timeout (s)",
       help="Per-request timeout for Groq calls.", control="number", min=5, max=300, step=5, timing="restart"),
    _e("agent.max_iterations", file="runtime", group="runtime", label="Max orchestrator iterations",
       help="How many observe-reason-act loops before the run finalizes.",
       control="slider", min=1, max=20, step=1, timing="restart"),
    _e("agent.dashboard_port", file="runtime", group="runtime", label="Dashboard port",
       help="TCP port the dashboard serves on. Changing it requires a dashboard restart.",
       control="number", min=1, max=65535, step=1, timing="restart"),
    _e("agent.worker_timeout_seconds", file="runtime", group="runtime", label="Worker timeout (s)",
       help="How long the Orchestrator waits for a worker before declaring it stalled.",
       control="number", min=60, max=7200, step=60, timing="restart"),
    _e("delivery.digest_limit", file="runtime", group="runtime", label="Digest size",
       help="Max signals written to the Markdown digest.", control="slider", min=1, max=100, step=1, timing="restart"),
    _e("delivery.critical_threshold", file="runtime", group="runtime", label="Critical alert threshold",
       help="Score (0-100) at/above which a signal is flagged critical.",
       control="slider", min=0, max=100, step=1, timing="restart"),
    _e("delivery.similarity_threshold", file="runtime", group="runtime", label="Dedup similarity threshold",
       help="Cosine-ish similarity (0-1) above which two articles are treated as the same story.",
       control="number", min=0, max=1, step=0.01, timing="restart"),

    # ----- Sources (ai_tech.toml) — edited on the dedicated Sources page -----
    _e("sources[]", file="runtime", group="sources", label="Sources",
       help="RSS/Atom/YouTube/scrape feeds and their limits.", control="external", timing="restart"),

    # ----- Advanced-only (ai_tech.toml), each with a reason -----
    _e("agent.brain_file", file="runtime", group="advanced", label="Brain file path",
       help="Path to agent_brain.toml.", control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints the whole brain config. Edit in ai_tech.toml."),
    _e("agent.scout_mode", file="runtime", group="advanced", label="Scout mode (fallback)",
       help="ai_tech.toml fallback.", control="select", options=_MODE_OPTS, timing="restart", exposure="advanced",
       reason="Fallback copy; the live Scout mode is behavior.scout_mode, edited above."),
    _e("agent.analyst_mode", file="runtime", group="advanced", label="Analyst mode (fallback)",
       help="ai_tech.toml fallback.", control="select", options=_MODE_OPTS, timing="restart", exposure="advanced",
       reason="Fallback copy; the live Analyst mode is behavior.analyst_mode, edited above."),
    _e("agent.require_brain", file="runtime", group="advanced", label="Require brain",
       help="Abort a run if Groq is unavailable.", control="switch", timing="restart", exposure="advanced",
       reason="Safety flag; rarely changed. Edit in ai_tech.toml."),
    _e("agent.allow_mock_brain", file="runtime", group="advanced", label="Allow mock brain",
       help="Permit the offline mock brain.", control="switch", timing="restart", exposure="advanced",
       reason="Offline-demo only; not for live runs. Edit in ai_tech.toml."),
    _e("agent.enable_critic", file="runtime", group="advanced", label="Critic default (runtime)",
       help="ai_tech.toml default for the Critic.", control="switch", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live toggle is behavior.enable_critic, edited above."),
    _e("agent.max_critic_rounds", file="runtime", group="advanced", label="Critic rounds default (runtime)",
       help="ai_tech.toml default.", control="number", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live value is behavior.max_critic_rounds, edited above."),
    _e("agent.critic_score_threshold", file="runtime", group="advanced", label="Critic threshold default (runtime)",
       help="ai_tech.toml default.", control="number", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live value is behavior.critic_score_threshold, edited above."),
    _e("delivery.output_dir", file="runtime", group="advanced", label="Output directory",
       help="Where Markdown digests are written.", control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints digest output. Edit in ai_tech.toml."),
    _e("storage.path", file="runtime", group="advanced", label="Database path",
       help="SQLite database location.", control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints the whole memory store. Edit in ai_tech.toml."),
    _e("profile.name", file="runtime", group="advanced", label="Profile name", help="Identity.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),
    _e("profile.organization", file="runtime", group="advanced", label="Organization", help="Identity.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),
    _e("profile.audience", file="runtime", group="advanced", label="Audience", help="Framing.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),
    _e("profile.mission", file="runtime", group="advanced", label="Mission", help="Framing.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),
    _e("profile.competitors", file="runtime", group="advanced", label="Competitors", help="Watchlist companies.",
       control="list", timing="restart", exposure="advanced",
       reason="Long list feeding company-match; edit the array in ai_tech.toml."),
    _e("profile.markets", file="runtime", group="advanced", label="Markets", help="Market keywords.",
       control="list", timing="restart", exposure="advanced",
       reason="Long list feeding framing; edit the array in ai_tech.toml."),
    _e("priorities[]", file="runtime", group="advanced", label="Priority groups",
       help="Priority groups with weights and keyword arrays.", control="external", timing="restart",
       exposure="advanced",
       reason="Priority groups carry large keyword arrays; edit in ai_tech.toml until a dedicated Priorities editor exists."),
]


def manifest_index() -> dict[str, dict[str, Any]]:
    """Map of dotted id -> entry."""
    return {entry["id"]: entry for entry in SETTINGS_MANIFEST}


def find_entry(dotted_key: str) -> dict[str, Any] | None:
    """Return the manifest entry covering a dotted key.

    Exact match wins; otherwise the longest entry id that is a section/array
    prefix of the key (e.g. `scoring.value_weights` covers
    `scoring.value_weights.novelty`; `priorities[]` covers `priorities[].weight`).
    """
    index = manifest_index()
    if dotted_key in index:
        return index[dotted_key]
    best: dict[str, Any] | None = None
    for entry in SETTINGS_MANIFEST:
        eid = entry["id"]
        if dotted_key.startswith(eid) and len(dotted_key) > len(eid) and dotted_key[len(eid)] == ".":
            if best is None or len(eid) > len(best["id"]):
                best = entry
    return best


def is_covered(dotted_key: str) -> bool:
    return find_entry(dotted_key) is not None
