from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(*parts: object, prefix: str = "") -> str:
    raw = "|".join(str(part or "").strip().lower() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}" if prefix else digest


@dataclass
class Priority:
    name: str
    description: str = ""
    weight: float = 1.0
    keywords: list[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    name: str
    kind: str
    url: str | None = None
    path: str | None = None
    group: str = "general"
    channel_id: str | None = None
    on_demand: bool = False
    limit: int = 25
    enabled: bool = True
    # Used by html_scrape kind: regex substring that article hrefs must contain.
    article_link_pattern: str | None = None


@dataclass
class BrainConfig:
    model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    timeout_seconds: int = 60


@dataclass
class AgentConfig:
    max_iterations: int = 6
    dashboard_port: int = 8765
    worker_timeout_seconds: int = 1800
    brain_file: str = "configs/agent_brain.toml"
    prompt_file: str = "configs/agent_brain.toml"
    scout_mode: str = "code"
    analyst_mode: str = "code"
    require_brain: bool = True
    allow_mock_brain: bool = False
    # Critic-loop configuration. Defaults to off so existing runs are unchanged.
    # Flip enable_critic in configs/agent_brain.toml [behavior] to activate.
    enable_critic: bool = False
    max_critic_rounds: int = 1
    critic_score_threshold: int = 70


@dataclass
class SignalConfig:
    name: str
    organization: str
    audience: str
    mission: str
    competitors: list[str]
    markets: list[str]
    priorities: list[Priority]
    sources: list[SourceConfig]
    storage_path: str
    output_dir: str
    digest_limit: int = 10
    critical_threshold: int = 82
    similarity_threshold: float = 0.52
    brain: BrainConfig = field(default_factory=BrainConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


@dataclass
class Article:
    id: str
    source: str
    title: str
    url: str
    published_at: str
    body: str
    fetched_at: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fields(
        cls,
        source: str,
        title: str,
        url: str = "",
        published_at: str = "",
        body: str = "",
        raw: dict[str, Any] | None = None,
    ) -> "Article":
        article_id = stable_id(source, url, title, published_at, prefix="art")
        return cls(
            id=article_id,
            source=source.strip() or "Unknown",
            title=title.strip(),
            url=url.strip(),
            published_at=published_at.strip(),
            body=body.strip(),
            fetched_at=utc_now_iso(),
            raw=raw or {},
        )


@dataclass
class Cluster:
    id: str
    articles: list[Article]
    similarity: float = 1.0


@dataclass
class ClusterInsight:
    cluster: Cluster
    entities: dict[str, list[str]]
    text: str


@dataclass
class SignalDraft:
    cluster: Cluster
    entities: dict[str, list[str]]
    matched_priorities: list[dict[str, Any]]
    event_type: str
    text: str


@dataclass
class Signal:
    id: str
    cluster_id: str
    article_id: str
    title: str
    url: str
    source: str
    published_at: str
    score: int
    urgency: str
    event_type: str
    summary: str
    why_it_matters: str
    next_steps: list[str]
    matched_priorities: list[dict[str, Any]]
    entities: dict[str, list[str]]
    duplicate_count: int = 0
    score_breakdown: list[dict[str, Any]] = field(default_factory=list)
    short_summary: str = ""
    expanded_summary: str = ""
    image_url: str = ""
    icon_key: str = ""
    scout_note: str = ""
    relevance_label: str = ""
    # Phase 2: richer per-signal artifacts written by the Analyst review pass.
    # Optional JSON blob containing mechanism, key_actors, evidence_excerpts,
    # confidence, truncation metadata, etc. None when the model review didn't
    # populate it (old rows, code-path runs, brain unavailable).
    analyst_artifact: dict[str, Any] | None = None


@dataclass
class AgentEvent:
    agent: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class ToolCall:
    id: str
    run_id: str
    agent: str
    tool: str
    status: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    confidence: float = 0.0
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class AgentDecision:
    thought: str
    action: str
    target: str = ""
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerResult:
    task_id: str
    agent: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    confidence: float = 0.0


@dataclass
class AgentRunLog:
    events: list[AgentEvent] = field(default_factory=list)

    def add(self, agent: str, message: str, **metadata: Any) -> None:
        self.events.append(AgentEvent(agent=agent, message=message, metadata=metadata))


@dataclass
class RunResult:
    output_path: str
    article_count: int
    cluster_count: int
    signal_count: int
    top_signals: list[Signal]
    trace: AgentRunLog
