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
    _e("behavior.summary_mode", file="brain", group="reader", label="Summary length",
       help=(
           "Controls how much text shows on each signal card. "
           "'short_expanded' shows a short headline summary plus a follow-on paragraph with more detail. "
           "'short_only' shows just the one-line summary — useful if you want a faster, denser feed."
       ),
       control="select", options=["short_expanded", "short_only"], timing="next_run"),

    _e("behavior.visuals_mode", file="brain", group="reader", label="Images & icons",
       help=(
           "Controls whether signal cards show images and/or icons. "
           "'image_icon' tries to pull the article's thumbnail image and falls back to a topic icon if none is found. "
           "'icon_only' always uses an icon, never an image. "
           "'none' shows text cards with no visuals — fastest to load."
       ),
       control="select", options=["image_icon", "icon_only", "none"], timing="next_run"),

    _e("behavior.scout_note_enabled", file="brain", group="reader", label="Show Scout note",
       help=(
           "When on, each signal card includes a short note from the Scout agent explaining "
           "where the article came from and why it was kept. Useful for understanding sourcing; "
           "turn off if you want a cleaner feed."
       ),
       control="switch", timing="next_run"),

    _e("behavior.entity_extraction", file="brain", group="reader", label="Entity extraction",
       help=(
           "How the system identifies companies, people, and tools mentioned in articles. "
           "'hybrid' uses a known-names list first, then asks the AI to fill gaps — most accurate. "
           "'model' hands everything to the AI — catches more obscure names but uses more tokens. "
           "'known_list' only uses the pre-built list — fastest, misses new names."
       ),
       control="select", options=["hybrid", "model", "known_list"], timing="next_run"),

    # ----- Agent (brain, next run) -----
    _e("behavior.scout_mode", file="brain", group="agent", label="Scout mode",
       help=(
           "How the Scout agent decides which articles are worth keeping. "
           "'code' = pure Python rules, no AI — fastest. "
           "'hybrid' = Python rules first, then AI judgment for borderline cases — recommended default. "
           "'model' = AI makes most decisions — slowest but most nuanced."
       ),
       control="select", options=_MODE_OPTS, timing="next_run"),

    _e("behavior.analyst_mode", file="brain", group="agent", label="Analyst mode",
       help=(
           "How the Analyst agent scores and evaluates signals. "
           "'code' = Python scoring rubric only, no Groq calls — very fast. "
           "'hybrid' = Python scoring first, then Groq reviews the top candidates — recommended. "
           "'model' = Groq does more of the evaluation — more token usage."
       ),
       control="select", options=_MODE_OPTS, timing="next_run"),

    _e("behavior.relevance_policy", file="brain", group="agent", label="Borderline article policy",
       help=(
           "What to do with articles the Scout marks as only borderline relevant. "
           "'soft_keep' keeps them in the pool so the Analyst can still score and possibly surface them. "
           "'hard_drop' removes them immediately — results in a tighter, smaller pool but may miss edge cases."
       ),
       control="select", options=["soft_keep", "hard_drop"], timing="next_run"),

    _e("behavior.model_score_adjustment_limit", file="brain", group="agent", label="Max AI score adjustment",
       help=(
           "After Python scores an article, Groq reviews it and can nudge the score up or down. "
           "This caps how many points Groq can move it. "
           "20 (default) gives the AI real influence. "
           "Set to 0 to trust only the Python score. "
           "Set higher if you want the AI's qualitative judgment to dominate."
       ),
       control="slider", min=0, max=100, step=5, timing="next_run"),

    _e("behavior.enable_critic", file="brain", group="agent", label="Critic review pass",
       help=(
           "When on, a Critic agent reads the full proposed digest before it's finalized and scores it. "
           "If the digest scores below the threshold, the Critic sends notes back and the system tries one more round. "
           "Turn off for faster runs; turn on when digest quality feels inconsistent."
       ),
       control="switch", timing="next_run"),

    _e("behavior.max_critic_rounds", file="brain", group="agent", label="Max Critic revision rounds",
       help=(
           "How many times the Critic can send the digest back for revision before it ships anyway. "
           "1 = one revision attempt (recommended — more rounds add latency). "
           "0 = Critic reads and scores but never triggers a revision."
       ),
       control="slider", min=0, max=5, step=1, timing="next_run"),

    _e("behavior.critic_score_threshold", file="brain", group="agent", label="Critic revision threshold",
       help=(
           "The quality score (0-100) the Critic uses to decide whether the digest needs revision. "
           "If the Critic scores the digest below this number, it requests a rewrite. "
           "70 (default) is moderate — most digests pass. "
           "Raise it to 85+ for stricter quality control; lower it to 50 to almost never revise."
       ),
       control="slider", min=0, max=100, step=5, timing="next_run"),

    _e("behavior.analyst_full_review", file="brain", group="agent", label="Send full article text to Groq",
       help=(
           "When on, the system fetches the full text of each top-scoring article and sends it to Groq for review. "
           "This produces better summaries and more accurate scores. "
           "Turn off to use only the title and metadata — much faster, but summaries will be shallower."
       ),
       control="switch", timing="next_run"),

    _e("behavior.analyst_retry_max_attempts", file="brain", group="agent", label="Groq retry attempts",
       help=(
           "If a Groq review request fails (rate limit, timeout, bad response), how many times to retry before giving up. "
           "0 = no retry, move on immediately. "
           "1 = one retry (recommended — handles transient failures without stalling the run)."
       ),
       control="slider", min=0, max=1, step=1, timing="next_run"),

    _e("behavior.max_article_tokens_for_llm", file="brain", group="agent", label="Article length cap (tokens)",
       help=(
           "The maximum amount of article text sent to Groq for each review, measured in tokens. "
           "One token is roughly 4 characters or ¾ of a word. "
           "12,000 tokens ≈ a 9,000-word article. "
           "Higher = better summaries for long articles, but slower and uses more of your Groq token budget. "
           "Lower = faster reviews, but very long articles get cut off."
       ),
       control="number", min=1000, max=120000, step=500, timing="next_run"),

    # ----- Scoring: top-N (brain, next run) -----
    _e("behavior.analyst_review_limit", file="brain", group="scoring", label="Articles sent to Groq for review",
       help=(
           "After Python scores all fetched articles, this many top-scoring ones get sent to Groq for a full AI review. "
           "The rest are scored by Python alone. "
           "40 (default) gives Groq a large pool to work with. "
           "Lower it to 10-15 to save token budget; raise it to give Groq more visibility."
       ),
       control="slider", min=1, max=100, step=1, timing="next_run"),

    _e("behavior.analyst_review_batch_size", file="brain", group="scoring", label="Articles per Groq request",
       help=(
           "How many articles are bundled into a single Groq API call. "
           "1 (recommended) = one article per request — most reliable, easiest to debug. "
           "Higher values reduce API call count but make the prompt very large and increase parse failures."
       ),
       control="slider", min=1, max=10, step=1, timing="next_run"),

    _e("behavior.executive_summary_limit", file="brain", group="scoring", label="Signals in executive briefing",
       help=(
           "How many top-ranked signals feed the Executive Briefing view and get saved to memory. "
           "12 (default) = a crisp, scannable briefing. "
           "Raise it if you want a wider briefing; lower it for an ultra-focused top-signal view."
       ),
       control="slider", min=1, max=40, step=1, timing="next_run"),

    _e("behavior.executive_summary_min_score", file="brain", group="scoring", label="Briefing minimum score",
       help=(
           "A signal must score at least this high to appear in the Executive Briefing. "
           "65 (default) means only above-average signals make the briefing. "
           "Set to 0 to always show the top-N regardless of score. "
           "Raise to 80+ to make the briefing very selective."
       ),
       control="slider", min=0, max=100, step=5, timing="next_run"),

    # ----- Scoring: V2 sections (brain, next run) -----
    _e("scoring.value_weights", file="brain", group="scoring", label="Value dimension weights",
       help=(
           "Six dimensions that define what makes a signal valuable to you. "
           "Each weight is a number 0–20; together they must add up to exactly 20. "
           "Think of them as sliders for: how relevant it is to you personally, how strategically important it is, "
           "whether you can act on it today, how credible the source is, how new the information is, and how urgent it is. "
           "Raise a weight to make that dimension matter more in ranking; lower it to de-emphasize it."
       ),
       control="weights", min=0, max=20, step=0.5, validation="sum_to_20", timing="next_run"),

    _e("scoring.trust_weights", file="brain", group="scoring", label="Trust concern weights",
       help=(
           "Three types of credibility problems that can reduce an article's score. "
           "'Claim support deficit' = the article makes big claims without evidence. "
           "'Hype or manipulation' = the writing feels promotional, sensationalist, or misleading. "
           "'Source credibility' = the publication or author is low-quality or anonymous. "
           "The three weights must add up to 1.0. Raise a weight to penalize that problem more heavily."
       ),
       control="weights", min=0, max=1, step=0.05, validation="sum_to_1", timing="next_run"),

    _e("scoring.trust_penalty", file="brain", group="scoring", label="Trust penalty strength",
       help=(
           "Controls how aggressively trust problems reduce an article's final score. "
           "0.0 = trust issues are noted but don't affect ranking. "
           "0.25 (default) = a fully untrustworthy article loses up to 25 points. "
           "1.0 = maximum penalty — a fully untrustworthy article could lose all its points."
       ),
       control="scale", min=0, max=1, step=0.05, timing="next_run"),

    _e("scoring.hard_caps", file="brain", group="scoring", label="Content type score ceilings",
       help=(
           "Hard limits that prevent certain types of low-value content from ranking too high, no matter how well they score otherwise. "
           "'Generic tutorial' caps how-to articles with no new information. "
           "'Sponsored or PR' caps press releases and sponsored content. "
           "'Duplicate or rehash' caps obvious rewrites of stories already in the digest. "
           "'Pure opinion, no signal' caps opinion pieces with no factual news hook. "
           "Lower a cap to suppress that content type harder; raise it to let it compete normally."
       ),
       control="caps", min=0, max=100, step=1, timing="next_run"),

    _e("scoring.priority_match_bands", file="brain", group="scoring", label="Priority topic match — point bands",
       help=(
           "Controls how many points an article earns based on how directly it matches your configured priority topics. "
           "These six levels go from 'no match' (0 points) up to 'direct, high-impact match' (25 points). "
           "Raise 'direct_high_impact' to make on-topic articles rank significantly higher. "
           "Lower 'weak_incidental' to stop loosely-related articles from getting credit."
       ),
       control="bands", min=0, max=25, step=1, timing="next_run"),

    _e("scoring.company_match_bands", file="brain", group="scoring", label="Watchlist company match — point bands",
       help=(
           "Controls how many points an article earns based on how centrally it features a company on your watchlist. "
           "Levels go from 'no match' (0 points) up to 'watchlist company takes a major strategic action' (25 points). "
           "Raise 'watchlist_strategic_action' to strongly boost acquisition, launch, or partnership news. "
           "Lower 'one_passing' to stop articles that just mention a company in passing from getting credit."
       ),
       control="bands", min=0, max=25, step=1, timing="next_run"),

    _e("scoring.event_strength_bands", file="brain", group="scoring", label="Event strength — point bands",
       help=(
           "Controls how many points an article earns based on the strength of the underlying event it reports on. "
           "Levels go from 'no real event' (0 points) up to 'major platform shift' (25 points). "
           "Raise 'launch_funding_regulation' to heavily reward hard news. "
           "Lower 'opinion_or_listicle' to stop listicles and takes from earning event-strength points."
       ),
       control="bands", min=0, max=25, step=1, timing="next_run"),

    # Legacy bands — not consumed by V2 _base_score_card.
    _e("scoring.recency_bands", file="brain", group="scoring", label="Recency bands (legacy — no effect)",
       help="Legacy V1 setting. Has no effect on current scores — the V2 scorer does not use this.",
       control="bands", timing="next_run", exposure="advanced",
       reason="Consumed only by _score_recency, which _base_score_card no longer calls; has no effect in V2."),

    _e("scoring.corroboration_bands", file="brain", group="scoring", label="Corroboration bands (legacy — no effect)",
       help="Legacy V1 setting. Has no effect on current scores — the V2 scorer does not use this.",
       control="bands", timing="next_run", exposure="advanced",
       reason="Consumed only by _score_corroboration, which _base_score_card no longer calls; has no effect in V2."),

    # ----- Display (brain, next page load) -----
    _e("display.page_size", file="brain", group="display", label="Cards per page",
       help=(
           "How many signal cards are shown per page in the digest view. "
           "10 (default) is a comfortable scroll. "
           "Raise to 25-50 for a denser view you scroll through less; lower to 5 for a focused, one-at-a-time feel."
       ),
       control="slider", min=1, max=100, step=1, timing="next_page"),

    _e("display.default_scope", file="brain", group="display", label="Default view scope",
       help=(
           "What the digest shows when you first open the dashboard. "
           "'latest' shows only signals from the most recent completed run — recommended for daily use. "
           "'all' shows every signal ever stored, across all runs — useful for research but gets large fast."
       ),
       control="select", options=["latest", "all"], timing="next_page"),

    # ----- Prompts (brain, next run) — edited in the Prompts tab -----
    _e("prompts.orchestrator", file="brain", group="prompts", label="Orchestrator prompt",
       help=(
           "The instructions given to the Orchestrator agent, which decides what the system should do "
           "at each step of a run (collect sources, analyze, critique, or finalize). "
           "Edit to change how it reasons about priorities or when it decides to stop."
       ),
       control="textarea", timing="next_run"),

    _e("prompts.scout", file="brain", group="prompts", label="Scout prompt",
       help=(
           "The instructions given to the Scout agent, which fetches articles and decides what's worth keeping. "
           "Edit to adjust what counts as relevant, how it writes sourcing notes, or what it filters out."
       ),
       control="textarea", timing="next_run"),

    _e("prompts.analyst", file="brain", group="prompts", label="Analyst prompt",
       help=(
           "The instructions given to the Analyst agent when it reviews each article with Groq. "
           "This drives the quality of per-signal summaries and Groq's score adjustments. "
           "Edit to change what the summaries focus on or how the AI frames strategic importance."
       ),
       control="textarea", timing="next_run"),

    _e("prompts.critic", file="brain", group="prompts", label="Critic prompt",
       help=(
           "The instructions given to the Critic agent when it reviews the full proposed digest. "
           "Edit to change what the Critic checks for — e.g. balance, coverage gaps, redundancy, signal quality."
       ),
       control="textarea", timing="next_run"),

    _e("prompts.editor", file="brain", group="prompts", label="Editor prompt",
       help=(
           "The instructions given to the Editor agent when it writes the Executive Briefing "
           "from the day's top signals. Edit to change tone, structure, or what the briefing emphasizes."
       ),
       control="textarea", timing="next_run"),

    # ----- Runtime knobs (ai_tech.toml, restart required) -----
    _e("brain.model", file="runtime", group="runtime", label="Groq model",
       help=(
           "The specific AI model used for all agent calls and article reviews. "
           "This is the Groq model ID string (e.g. 'meta-llama/llama-4-scout-17b-16e-instruct'). "
           "Changing this requires a restart and may affect output quality and token costs."
       ),
       control="text", timing="restart"),

    _e("brain.timeout_seconds", file="runtime", group="runtime", label="Groq request timeout (seconds)",
       help=(
           "How long the system waits for a single Groq API response before giving up and retrying. "
           "60 seconds (default) handles most slow responses. "
           "Lower it if you want the system to fail fast; raise it on slow connections or with large articles."
       ),
       control="number", min=5, max=300, step=5, timing="restart"),

    _e("agent.max_iterations", file="runtime", group="runtime", label="Max Orchestrator loops",
       help=(
           "The Orchestrator runs a loop: observe → reason → act → repeat. "
           "This caps how many loops it runs before the run is forced to finalize, even if it isn't done. "
           "30 (default) is plenty for a normal run. "
           "Lower it to force faster, shorter runs; raise it if the Orchestrator keeps hitting the limit before finishing."
       ),
       control="slider", min=1, max=20, step=1, timing="restart"),

    _e("agent.dashboard_port", file="runtime", group="runtime", label="Dashboard port",
       help=(
           "The network port the dashboard listens on. "
           "8765 (default) means you access it at http://127.0.0.1:8765. "
           "Change this if something else on your machine is already using 8765. Requires a dashboard restart."
       ),
       control="number", min=1, max=65535, step=1, timing="restart"),

    _e("agent.worker_timeout_seconds", file="runtime", group="runtime", label="Worker timeout (seconds)",
       help=(
           "How long the Orchestrator waits for a worker (Scout, Analyst, or Critic) to finish before "
           "declaring it stuck and moving on. "
           "2400 seconds (40 minutes) is the default — long enough to handle slow Groq rate-limit retries. "
           "Lower it if you want stalled workers to fail faster. "
           "Raise it if you have many articles and Groq is rate-limiting frequently."
       ),
       control="number", min=60, max=7200, step=60, timing="restart"),

    _e("delivery.digest_limit", file="runtime", group="runtime", label="Max signals in digest",
       help=(
           "The maximum number of signals written to the final Markdown digest file. "
           "40 (default) is a full day's feed. "
           "Lower it to keep the digest focused on only the best signals; raise it to capture more."
       ),
       control="slider", min=1, max=100, step=1, timing="restart"),

    _e("delivery.critical_threshold", file="runtime", group="runtime", label="Critical signal threshold",
       help=(
           "Signals scoring at or above this number get flagged as 'critical' in the digest. "
           "80 (default) means only the very best content gets the critical badge. "
           "Lower to 65-70 to flag more signals; raise to 90+ for an extremely selective critical flag."
       ),
       control="slider", min=0, max=100, step=1, timing="restart"),

    _e("delivery.similarity_threshold", file="runtime", group="runtime", label="Duplicate detection sensitivity",
       help=(
           "How similar two articles have to be before the system treats them as the same story and removes one. "
           "0.75 (default) is moderate — catches clear duplicates without being too aggressive. "
           "Raise toward 1.0 to only deduplicate near-identical articles. "
           "Lower toward 0.5 to remove more articles that are on the same topic even if worded differently."
       ),
       control="number", min=0, max=1, step=0.01, timing="restart"),

    # ----- Sources (ai_tech.toml) — edited on the dedicated Sources page -----
    _e("sources[]", file="runtime", group="sources", label="Sources",
       help=(
           "The list of RSS feeds, YouTube channels, and scraped pages the Scout fetches from. "
           "Edit on the Sources page — each source has its own name, URL, type, and article limit."
       ),
       control="external", timing="restart"),

    # ----- Advanced-only (ai_tech.toml), each with a reason -----
    _e("agent.brain_file", file="runtime", group="advanced", label="Brain file path",
       help="File path pointing to agent_brain.toml. Changing this repoints all live settings to a different file.",
       control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints the whole brain config. Edit in ai_tech.toml."),

    _e("agent.scout_mode", file="runtime", group="advanced", label="Scout mode (runtime fallback)",
       help="A fallback copy of Scout mode stored in ai_tech.toml. The live setting is Scout mode on the Agent tab.",
       control="select", options=_MODE_OPTS, timing="restart", exposure="advanced",
       reason="Fallback copy; the live Scout mode is behavior.scout_mode, edited above."),

    _e("agent.analyst_mode", file="runtime", group="advanced", label="Analyst mode (runtime fallback)",
       help="A fallback copy of Analyst mode stored in ai_tech.toml. The live setting is Analyst mode on the Agent tab.",
       control="select", options=_MODE_OPTS, timing="restart", exposure="advanced",
       reason="Fallback copy; the live Analyst mode is behavior.analyst_mode, edited above."),

    _e("agent.require_brain", file="runtime", group="advanced", label="Require Groq connection",
       help="When on, the agent refuses to run if Groq is unreachable. Safety flag — rarely changed.",
       control="switch", timing="restart", exposure="advanced",
       reason="Safety flag; rarely changed. Edit in ai_tech.toml."),

    _e("agent.allow_mock_brain", file="runtime", group="advanced", label="Allow mock AI (offline demo)",
       help="Lets the agent run with a fake AI brain for offline demos. Never enable this for live runs.",
       control="switch", timing="restart", exposure="advanced",
       reason="Offline-demo only; not for live runs. Edit in ai_tech.toml."),

    _e("agent.enable_critic", file="runtime", group="advanced", label="Critic default (runtime file)",
       help="A fallback copy of the Critic toggle stored in ai_tech.toml. The live toggle is on the Agent tab.",
       control="switch", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live toggle is behavior.enable_critic, edited above."),

    _e("agent.max_critic_rounds", file="runtime", group="advanced", label="Critic rounds default (runtime file)",
       help="A fallback copy of Max Critic Rounds stored in ai_tech.toml. The live setting is on the Agent tab.",
       control="number", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live value is behavior.max_critic_rounds, edited above."),

    _e("agent.critic_score_threshold", file="runtime", group="advanced", label="Critic threshold default (runtime file)",
       help="A fallback copy of the Critic threshold stored in ai_tech.toml. The live setting is on the Agent tab.",
       control="number", timing="restart", exposure="advanced",
       reason="ai_tech.toml default; the live value is behavior.critic_score_threshold, edited above."),

    _e("delivery.output_dir", file="runtime", group="advanced", label="Digest output directory",
       help="The folder where Markdown digest files are written after each run. Changing this moves future output.",
       control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints digest output. Edit in ai_tech.toml."),

    _e("storage.path", file="runtime", group="advanced", label="Database file path",
       help="The SQLite database file that stores all articles, signals, and memory. Changing this repoints the entire memory store.",
       control="text", timing="restart", exposure="advanced",
       reason="Filesystem path; changing it repoints the whole memory store. Edit in ai_tech.toml."),

    _e("profile.name", file="runtime", group="advanced", label="Profile name",
       help="Your name, used to personalize relevance scoring and briefings.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),

    _e("profile.organization", file="runtime", group="advanced", label="Organization",
       help="Your organization, used for framing and context in prompts.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),

    _e("profile.audience", file="runtime", group="advanced", label="Audience",
       help="Who you're producing signals for — shapes how the briefing is written.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),

    _e("profile.mission", file="runtime", group="advanced", label="Mission",
       help="Your personal or organizational mission — used to frame what counts as relevant.",
       control="text", timing="restart", exposure="advanced",
       reason="Identity/framing; low-churn. Edit in ai_tech.toml."),

    _e("profile.competitors", file="runtime", group="advanced", label="Competitor watchlist",
       help="Companies you track closely. Articles featuring these get a boost in company-match scoring.",
       control="list", timing="restart", exposure="advanced",
       reason="Long list feeding company-match; edit the array in ai_tech.toml."),

    _e("profile.markets", file="runtime", group="advanced", label="Market keywords",
       help="Market or sector terms that shape how the AI frames relevance and importance.",
       control="list", timing="restart", exposure="advanced",
       reason="Long list feeding framing; edit the array in ai_tech.toml."),

    _e("priorities[]", file="runtime", group="advanced", label="Priority topic groups",
       help=(
           "The named topic groups with keyword lists that the priority-match scorer uses. "
           "Edit these directly in ai_tech.toml — each group has a name, weight, and keyword array."
       ),
       control="external", timing="restart", exposure="advanced",
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
