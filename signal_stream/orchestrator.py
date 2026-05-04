from __future__ import annotations

from pathlib import Path

from .agents import (
    AgentContext,
    BriefingAgent,
    ClusterAgent,
    EntityAgent,
    FeedbackAgent,
    IngestAgent,
    NormalizeAgent,
    RelevanceAgent,
)
from .llm import OllamaClient
from .models import RunResult, Signal, SignalConfig, utc_now_iso
from .storage import SignalStorage


class SignalStreamOrchestrator:
    def __init__(self, config: SignalConfig):
        self.config = config
        self.storage = SignalStorage(config.storage_path)

    def run(self, output_path: str | None = None) -> RunResult:
        self.storage.init()
        started_at = utc_now_iso()
        ctx = AgentContext(config=self.config, storage=self.storage, llm=OllamaClient(self.config))

        FeedbackAgent().run(ctx)
        articles = IngestAgent().run(ctx)
        normalized = NormalizeAgent().run(ctx, articles)
        clusters = ClusterAgent().run(ctx, normalized)
        insights = EntityAgent().run(ctx, clusters)
        drafts = RelevanceAgent().run(ctx, insights)
        signals = BriefingAgent().run(ctx, drafts)

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


def render_digest(config: SignalConfig, signals: list[Signal], events: object) -> str:
    top_signals = signals[: config.digest_limit]
    critical = [signal for signal in top_signals if signal.urgency == "critical"]

    lines = [
        "# Signal Stream Briefing",
        "",
        f"Generated: {utc_now_iso()}",
        f"Organization: {config.organization or config.name}",
        f"Audience: {config.audience}",
        "",
        "## Executive Snapshot",
        "",
        f"- Signals reviewed: {len(signals)}",
        f"- Critical alerts: {len(critical)}",
        f"- Delivery mode: local Markdown digest",
        "",
    ]

    if critical:
        lines.extend(["## Critical Alerts", ""])
        for signal in critical:
            lines.extend(_signal_block(signal))

    lines.extend(["## Ranked Signals", ""])
    for signal in top_signals:
        lines.extend(_signal_block(signal))

    lines.extend(["## Agent Trace", ""])
    for event in events:
        meta = f" {event.metadata}" if event.metadata else ""
        lines.append(f"- {event.agent}: {event.message}{meta}")
    lines.append("")
    return "\n".join(lines)


def _signal_block(signal: Signal) -> list[str]:
    source = signal.source
    if signal.published_at:
        source = f"{source}, {signal.published_at}"
    duplicate_note = f" ({signal.duplicate_count} related item{'s' if signal.duplicate_count != 1 else ''})" if signal.duplicate_count else ""
    lines = [
        f"### {signal.score}/100 - {signal.title}",
        "",
        f"- Urgency: {signal.urgency}",
        f"- Event type: {signal.event_type}{duplicate_note}",
        f"- Source: {source}",
    ]
    if signal.url:
        lines.append(f"- Link: {signal.url}")
    lines.extend(
        [
            "",
            f"Summary: {signal.short_summary or signal.summary}",
            "",
            f"Expanded summary: {signal.expanded_summary or signal.short_summary or signal.summary}",
            "",
            f"Why it matters: {signal.why_it_matters}",
            "",
        ]
    )
    if signal.scout_note:
        lines.extend([f"Scout note: {signal.scout_note}", ""])
    lines.append("")
    return lines
