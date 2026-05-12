from __future__ import annotations

from pathlib import Path

from .agents import (
    AgentContext,
    ClusterAgent,
    EntityAgent,
    FeedbackAgent,
    IngestAgent,
    NormalizeAgent,
)
from .analysis_tools import (
    _base_score_card,
    _icon_key,
    _urgency,
    build_drafts_from_insights,
    render_digest,
)
from .llm import BrainClient
from .models import RunResult, Signal, SignalConfig, stable_id, utc_now_iso
from .storage import SignalStorage
from .text import first_sentences


class SignalStreamOrchestrator:
    def __init__(self, config: SignalConfig):
        self.config = config
        self.storage = SignalStorage(config.storage_path)

    def run(self, output_path: str | None = None) -> RunResult:
        self.storage.init()
        started_at = utc_now_iso()
        ctx = AgentContext(config=self.config, storage=self.storage, llm=BrainClient(self.config))
        from .prompt_loader import load_scoring_rubric  # noqa: PLC0415 - deferred to avoid top-level dep on TOML config paths

        FeedbackAgent().run(ctx)
        articles = IngestAgent().run(ctx)
        normalized = NormalizeAgent().run(ctx, articles)
        clusters = ClusterAgent().run(ctx, normalized)
        insights = EntityAgent().run(ctx, clusters)
        drafts = build_drafts_from_insights(ctx, insights)
        rubric = load_scoring_rubric(self.config.agent.brain_file)

        # _base_score_card is the single source of truth for Signal.score.
        signals: list[Signal] = []
        for draft in drafts:
            article = draft.cluster.articles[0]
            score, score_breakdown = _base_score_card(article, draft, rubric)
            urgency = _urgency(score, self.config.critical_threshold)
            short_summary = first_sentences(article.body, max_sentences=2, max_chars=200)
            signals.append(Signal(
                id=stable_id(draft.cluster.id, article.title, score, prefix="sig"),
                cluster_id=draft.cluster.id,
                article_id=article.id,
                title=article.title,
                url=article.url,
                source=article.source,
                published_at=article.published_at,
                score=score,
                urgency=urgency,
                event_type=draft.event_type,
                summary=short_summary,
                why_it_matters="",
                next_steps=[],
                matched_priorities=draft.matched_priorities,
                entities=draft.entities,
                duplicate_count=max(0, len(draft.cluster.articles) - 1),
                score_breakdown=score_breakdown,
                short_summary=short_summary,
                expanded_summary=first_sentences(article.body, max_sentences=4, max_chars=600),
                image_url=str(article.raw.get("image_url", "")),
                icon_key=_icon_key(draft.event_type),
            ))
        signals.sort(key=lambda s: s.score, reverse=True)

        output = Path(output_path).expanduser().resolve() if output_path else self._default_output_path()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_digest(self.config, signals, ctx.trace.events), encoding="utf-8")

        self.storage.save_run(normalized, signals, len(clusters), str(output), started_at)
        return RunResult(
            output_path=str(output),
            article_count=len(normalized),
            cluster_count=len(clusters),
            signal_count=len(signals),
            top_signals=signals[: self.config.digest_limit],
            trace=ctx.trace,
        )

    def _default_output_path(self) -> Path:
        stamp = utc_now_iso().replace(":", "").replace("-", "").replace("Z", "")
        return Path(self.config.output_dir) / f"signal_stream_digest_{stamp}.md"


