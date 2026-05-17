import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class SourceRecord:
    # Stable ID that survives renames and restarts. Derived from origin + name + kind + connection params.
    id: str
    name: str
    kind: str
    group_name: str
    url: str | None
    path: str | None
    channel_id: str | None
    article_link_pattern: str | None
    limit_count: int
    enabled: bool
    on_demand: bool
    origin: str          # "toml" | "discovered" | "manual"
    created_at: str      # ISO-8601 UTC
    updated_at: str      # ISO-8601 UTC
    deleted_at: str | None = None


def generate_source_id(
    origin: str, name: str, kind: str,
    url: str | None, path: str | None, channel_id: str | None,
) -> str:
    """
    Return a stable, content-derived ID that survives restarts and renames.
    Hashes origin + name + kind + connection params to produce a deterministic ID.
    """
    # Build identity string from non-null connection params; treat None as empty string.
    identity = f"{origin}:{name}:{kind}:{url or ''}:{path or ''}:{channel_id or ''}"
    # SHA1 is fast and fine for stable IDs; take first 12 chars of hex digest.
    digest = hashlib.sha1(identity.encode()).hexdigest()[:12]
    return "src_" + digest


def source_config_to_record(source, origin: str = "toml") -> SourceRecord:
    """
    Convert a SourceConfig (from TOML or runtime) to a SourceRecord.
    SourceConfig is the existing runtime representation; SourceRecord is the registry record.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Extract optional fields with defaults.
    url = getattr(source, "url", None)
    path = getattr(source, "path", None)
    channel_id = getattr(source, "channel_id", None)
    article_link_pattern = getattr(source, "article_link_pattern", None)
    on_demand = getattr(source, "on_demand", False)

    return SourceRecord(
        id=generate_source_id(origin, source.name, source.kind, url, path, channel_id),
        name=source.name,
        kind=source.kind,
        group_name=source.group,
        url=url,
        path=path,
        channel_id=channel_id,
        article_link_pattern=article_link_pattern,
        limit_count=source.limit,
        enabled=source.enabled,
        on_demand=on_demand,
        origin=origin,
        created_at=now,
        updated_at=now,
    )


def source_record_to_config(record: SourceRecord):
    """
    Convert a SourceRecord back to a SourceConfig for the existing fetch pipeline.
    This allows registry records to be re-used by the scout/analyst without schema duplication.
    """
    from signal_stream.models import SourceConfig

    return SourceConfig(
        name=record.name,
        kind=record.kind,
        url=record.url,
        path=record.path,
        group=record.group_name,
        channel_id=record.channel_id,
        article_link_pattern=record.article_link_pattern,
        limit=record.limit_count,
        enabled=record.enabled,
        on_demand=record.on_demand,
    )
