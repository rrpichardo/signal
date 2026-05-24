"""Read and surgically edit the runtime config (ai_tech.toml).

Plain English: the brain file (agent_brain.toml) is fully re-rendered on every
save, which is fine because it has no big arrays. The runtime config is
different — it holds the `[[sources]]` and `[[priorities]]` arrays that the
Sources page and priority groups depend on. Re-rendering it would risk dropping
those, so this module edits it *surgically*: it rewrites only the specific
`key = value` lines under `[brain]` / `[agent]` / `[delivery]` and leaves every
other byte (arrays, comments, blank lines) untouched.

These knobs are read once when a process starts (load_config), so the dashboard
labels them "restart required."
"""

from __future__ import annotations

from pathlib import Path
import re
import tomllib
from typing import Any


# The editable runtime fields, keyed by dotted id. Each entry says which TOML
# table + key it maps to, its type, and validation bounds. Only these keys may
# be written — anything else in a save payload is rejected.
RUNTIME_FIELDS: dict[str, dict[str, Any]] = {
    "brain.model": {"section": "brain", "key": "model", "type": "str"},
    "brain.timeout_seconds": {"section": "brain", "key": "timeout_seconds", "type": "int", "min": 5, "max": 300},
    "agent.max_iterations": {"section": "agent", "key": "max_iterations", "type": "int", "min": 1, "max": 20},
    "agent.dashboard_port": {"section": "agent", "key": "dashboard_port", "type": "int", "min": 1, "max": 65535},
    "agent.worker_timeout_seconds": {"section": "agent", "key": "worker_timeout_seconds", "type": "int", "min": 60, "max": 7200},
    "delivery.digest_limit": {"section": "delivery", "key": "digest_limit", "type": "int", "min": 1, "max": 100},
    "delivery.critical_threshold": {"section": "delivery", "key": "critical_threshold", "type": "int", "min": 0, "max": 100},
    "delivery.similarity_threshold": {"section": "delivery", "key": "similarity_threshold", "type": "float", "min": 0.0, "max": 1.0},
}

# Sections this module is allowed to touch. Array-of-table sections
# ([[sources]], [[priorities]]) are never modified here.
_EDITABLE_SECTIONS = {"brain", "agent", "delivery"}


def load_runtime_settings(config_path: str | Path) -> dict[str, dict[str, Any]]:
    """Return the current values of the editable runtime fields, grouped by section.

    Shape: {"brain": {...}, "agent": {...}, "delivery": {...}}. Missing keys fall
    back to the value load_config would use, so the UI always shows something.
    """

    path = Path(config_path).expanduser().resolve()
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as handle:
            raw = tomllib.load(handle)

    out: dict[str, dict[str, Any]] = {"brain": {}, "agent": {}, "delivery": {}}
    for spec in RUNTIME_FIELDS.values():
        section, key = spec["section"], spec["key"]
        section_data = raw.get(section, {}) if isinstance(raw.get(section), dict) else {}
        if key in section_data:
            out[section][key] = section_data[key]
    return out


def _coerce(spec: dict[str, Any], value: Any) -> Any:
    """Coerce + clamp one value to its field spec, raising on bad input."""

    kind = spec["type"]
    if kind == "str":
        text = str(value).strip()
        if not text:
            raise ValueError("value cannot be empty")
        return text
    if kind == "int":
        try:
            num = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected an integer, got {value!r}") from exc
        return max(spec["min"], min(spec["max"], num))
    if kind == "float":
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected a number, got {value!r}") from exc
        return max(spec["min"], min(spec["max"], num))
    raise ValueError(f"unknown field type {kind!r}")


def _format_scalar(value: Any) -> str:
    """Render a Python scalar as a TOML scalar literal."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    # String: escape backslashes and double quotes for a basic TOML string.
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_runtime_settings(config_path: str | Path, patch: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Surgically update scalar keys under [brain]/[agent]/[delivery] in ai_tech.toml.

    `patch` is nested: {"brain": {"model": "..."}, "agent": {"max_iterations": 8}, ...}.
    Only keys in RUNTIME_FIELDS may be set; unknown section/key pairs raise
    ValueError. Existing key lines are rewritten in place; a key not present in
    its section is inserted right after the section header. Array-of-table
    sections and all comments/spacing are preserved byte-for-byte.
    """

    path = Path(config_path).expanduser().resolve()

    # Validate + coerce the whole patch up front so a single bad value aborts the
    # write before we touch the file.
    coerced: dict[str, dict[str, Any]] = {}
    for section, fields in (patch or {}).items():
        if not isinstance(fields, dict):
            raise ValueError(f"section {section!r} must map to an object")
        for key, value in fields.items():
            dotted = f"{section}.{key}"
            spec = RUNTIME_FIELDS.get(dotted)
            if spec is None:
                raise ValueError(f"unknown or non-editable runtime field: {dotted}")
            coerced.setdefault(section, {})[key] = _coerce(spec, value)

    original = path.read_text(encoding="utf-8")
    had_trailing_newline = original.endswith("\n")
    lines = original.splitlines()

    out: list[str] = []
    current_section: str | None = None
    current_is_array = False
    applied: set[tuple[str, str]] = set()

    array_header = re.compile(r"^\[\[(.+)\]\]$")
    table_header = re.compile(r"^\[([^\[].*?)\]$")

    for line in lines:
        stripped = line.strip()
        m_arr = array_header.match(stripped)
        m_tbl = table_header.match(stripped)
        if m_arr:
            current_section, current_is_array = m_arr.group(1).strip(), True
            out.append(line)
            continue
        if m_tbl:
            current_section, current_is_array = m_tbl.group(1).strip(), False
            out.append(line)
            continue
        # Replace a scalar assignment when we're inside a targeted single table.
        if (
            current_section in coerced
            and not current_is_array
            and "=" in stripped
            and not stripped.startswith("#")
        ):
            key = stripped.split("=", 1)[0].strip()
            if key in coerced[current_section]:
                out.append(f"{key} = {_format_scalar(coerced[current_section][key])}")
                applied.add((current_section, key))
                continue
        out.append(line)

    # Insert any requested keys that did not already exist, right under their header.
    for section, fields in coerced.items():
        for key, value in fields.items():
            if (section, key) in applied:
                continue
            header = f"[{section}]"
            try:
                idx = out.index(header)
            except ValueError:
                # Section header absent entirely — append a fresh table at the end.
                out.extend(["", header, f"{key} = {_format_scalar(value)}"])
                applied.add((section, key))
                continue
            out.insert(idx + 1, f"{key} = {_format_scalar(value)}")
            applied.add((section, key))

    text = "\n".join(out)
    if had_trailing_newline:
        text += "\n"
    path.write_text(text, encoding="utf-8")

    return load_runtime_settings(path)
