from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
import pathlib
from pathlib import Path
import signal
import threading
from urllib.parse import parse_qs, urlparse

from .agent_runtime import AgentRuntimeError, SignalAgentRuntime
from .cli import _load_dotenv
from .models import SignalConfig
from .prompt_loader import (
    DEFAULT_DISPLAY_SETTINGS,
    load_brain_file,
    load_display_settings,
    save_brain_file,
    save_raw_brain_file,
)
from .storage import SignalStorage

# ---------------------------------------------------------------------------
# Background agent-run state — shared across all request threads.
# Only one run can be active at a time; the lock prevents double-starts.
# ---------------------------------------------------------------------------
_run_lock = threading.Lock()
_run_state: dict[str, object] = {"running": False, "error": ""}


# ---------------------------------------------------------------------------
# PID-file helpers — guarantee exactly one dashboard process at a time.
# ---------------------------------------------------------------------------

def _pid_path(storage_path: str) -> Path:
    """Return the path to the dashboard PID file, next to the SQLite DB."""
    return Path(storage_path).parent / ".dashboard.pid"


def _kill_existing_dashboard(storage_path: str) -> None:
    """Kill any previously running dashboard process recorded in the PID file."""
    pid_file = _pid_path(storage_path)
    if not pid_file.exists():
        return
    try:
        old_pid = int(pid_file.read_text().strip())
        # Send SIGTERM — polite shutdown; the process cleans up its own PID file.
        os.kill(old_pid, signal.SIGTERM)
        # Brief wait so the port is released before we try to bind.
        import time
        time.sleep(0.5)
    except (ProcessLookupError, ValueError):
        # Process already gone or PID file was corrupt — ignore.
        pass
    finally:
        pid_file.unlink(missing_ok=True)


def _write_dashboard_pid(storage_path: str) -> None:
    """Write the current process PID so future launches can kill this one."""
    _pid_path(storage_path).write_text(str(os.getpid()))


def _remove_dashboard_pid(storage_path: str) -> None:
    """Remove the PID file on clean shutdown."""
    _pid_path(storage_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# React/Vite static file serving helpers.
# ---------------------------------------------------------------------------

# Allowed extensions when serving files from web/dist/assets/.
# Kept tight so the handler can't accidentally serve .py or .toml files.
_ALLOWED_EXTENSIONS = {".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2", ".map", ".txt"}


def _static_dist() -> pathlib.Path:
    """Return the path to web/dist relative to the repo root.

    Plain English: walk up from dashboard.py (signal_stream/) to the repo root,
    then append web/dist.  This works however the package is installed because
    __file__ is always the real file on disk.
    """
    # __file__ is  <repo>/signal_stream/dashboard.py  →  parents[1] is <repo>/
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    return repo_root / "web" / "dist"


def dashboard_settings(config: SignalConfig) -> dict[str, object]:
    """Return dashboard-editable settings.

    Plain English: this is the same data the Settings screen reads, split out so
    tests can check it without starting a web server.
    """

    return load_brain_file(config.agent.brain_file)


def save_dashboard_settings(config: SignalConfig, payload: dict[str, object]) -> dict[str, object]:
    """Save friendly Settings form data back to the brain file."""

    save_brain_file(config.agent.brain_file, payload)
    return dashboard_settings(config)


def serve_dashboard(
    config: SignalConfig,
    host: str = "127.0.0.1",
    port: int | None = None,
    config_path: str = "configs/ai_tech.toml",
) -> None:
    """Start the local dashboard.

    Plain English: this is a tiny local website for watching what the agents did.
    It reads SQLite and does not send your data anywhere. The Run button triggers
    a real agent run in a background thread without blocking the dashboard server.
    """

    # Kill any previous dashboard instance so there is always exactly one
    # process running on exactly the configured port.
    _kill_existing_dashboard(config.storage_path)

    storage = SignalStorage(config.storage_path)
    storage.init()

    target_port = port or config.agent.dashboard_port
    server = ThreadingHTTPServer((host, target_port), _handler(storage, config, config_path))

    # Record our PID so the *next* launch can cleanly replace us.
    _write_dashboard_pid(config.storage_path)

    # SIGTERM handler: mark any in-flight runs as failed then shut down cleanly.
    # Without this, killing the dashboard process with SIGTERM leaves run rows
    # stuck at status='running' forever.
    def _on_sigterm(signum: int, frame: object) -> None:
        storage.mark_stale_runs_failed()
        server.shutdown()

    signal.signal(signal.SIGTERM, _on_sigterm)

    # Background sweeper: every minute, mark any 'running' row whose timeline
    # has been silent for >5 minutes as failed. Catches orphaned runs whose
    # cleanup path failed silently — without this, only a dashboard restart
    # would unstick them. 5 minutes is well past worker_timeout_seconds (120s)
    # so a healthy run is never swept by mistake.
    _stop_sweeper = threading.Event()

    def _sweep_loop() -> None:
        while not _stop_sweeper.is_set():
            try:
                swept = storage.mark_runs_failed_if_idle(max_idle_seconds=300)
                if swept:
                    print(
                        f"[signal_stream] swept {len(swept)} stale run(s): {', '.join(swept)}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001 - keep the sweeper alive across DB hiccups.
                print(f"[signal_stream] sweeper error: {exc}", flush=True)
            # Event.wait lets the sweeper exit promptly on shutdown instead of
            # holding the process open for a full minute.
            _stop_sweeper.wait(timeout=60)

    threading.Thread(target=_sweep_loop, daemon=True, name="stale-run-sweeper").start()

    print(f"Signal Stream dashboard: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Stop the sweeper so the process can exit cleanly; mark any leftover
        # in-flight runs failed; remove the PID file.
        _stop_sweeper.set()
        storage.mark_stale_runs_failed()
        _remove_dashboard_pid(config.storage_path)


def _start_agent_run(config: SignalConfig, config_path: str) -> bool:
    """Spawn the agent in a daemon thread. Returns False if already running."""

    with _run_lock:
        if _run_state["running"]:
            return False
        _run_state["running"] = True
        _run_state["error"] = ""

    def _run() -> None:
        try:
            _load_dotenv(config_path)  # pick up .env if dashboard was started without the key
            SignalAgentRuntime(config, config_path=config_path).run()
        except AgentRuntimeError as exc:
            _run_state["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - dashboard must stay alive regardless.
            _run_state["error"] = f"Unexpected error: {exc}"
        finally:
            _run_state["running"] = False

    thread = threading.Thread(target=_run, daemon=True, name="signal-agent-run")
    thread.start()
    return True


def _handler(storage: SignalStorage, config: SignalConfig, config_path: str = "configs/ai_tech.toml") -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name.
            path = urlparse(self.path).path
            dist = _static_dist()

            # --- Static asset serving (only when web/dist/ exists) ----------
            if dist.is_dir():
                # Serve hashed asset files from web/dist/assets/*.
                # Path traversal guard: reject any segment that is ".." so a
                # crafted URL like /assets/../../signal_stream/models.py can't
                # escape the dist directory.
                if path.startswith("/assets/") or path in ("/favicon.svg", "/favicon.ico", "/vite.svg"):
                    # Resolve the file path inside dist without following symlinks.
                    rel = path.lstrip("/")
                    if ".." in rel.split("/"):
                        self.send_response(403)
                        self.end_headers()
                        return
                    file_path = dist / rel
                    ext = file_path.suffix.lower()
                    if ext not in _ALLOWED_EXTENSIONS or not file_path.is_file():
                        self.send_response(404)
                        self.end_headers()
                        return
                    data = file_path.read_bytes()
                    mime = mimetypes.types_map.get(ext, "application/octet-stream")
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    # Hashed asset files can be cached indefinitely; index.html never.
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # SPA fallback: serve index.html for every non-API path so
                # react-router deep links survive a hard reload.
                if not path.startswith("/api/"):
                    index = dist / "index.html"
                    if index.is_file():
                        data = index.read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(data)
                        return

            # --- Legacy dashboard (served when web/dist/ is absent) ----------
            if path == "/" and not dist.is_dir():
                self._send("text/html; charset=utf-8", LEGACY_DASHBOARD_HTML)
                return

            # --- JSON API routes (unchanged) ---------------------------------
            if path == "/api/run/latest":
                run = storage.latest_agent_run() or {}
                self._json(run)
                return
            if path == "/api/events":
                run = storage.latest_agent_run() or {}
                self._json(storage.agent_events(run.get("id"), limit=250) if run else [])
                return
            if path == "/api/tool-calls":
                run = storage.latest_agent_run() or {}
                self._json(storage.tool_calls(run.get("id"), limit=250) if run else [])
                return
            if path == "/api/executive-briefing":
                # Returns the Editor-generated briefing for the latest complete run.
                # Frontend uses this for the BriefingBlock above the exec summary list.
                # Falls back gracefully when no briefing exists (status="skipped").
                self._json(storage.get_latest_briefing())
                return
            if path == "/api/signals/executive":
                # Top N signals by score from the latest complete run.
                # Drives the executive summary block at the top of the digest page.
                run = storage.latest_run()
                run_started_at = run["started_at"] if run else None
                behavior = load_brain_file(config.agent.brain_file).get("behavior") or {}
                exec_limit = int(behavior.get("executive_summary_limit", 12))
                self._json(storage.list_signals_executive(limit=exec_limit, run_started_at=run_started_at))
                return
            if path.startswith("/api/signals/") and len(path) > len("/api/signals/"):
                # Detail endpoint — returns one signal with full score_breakdown.
                signal_id = path[len("/api/signals/"):]
                signal = storage.get_signal(signal_id)
                if signal is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "signal not found"}).encode("utf-8"))
                    return
                self._json(signal)
                return
            if path == "/api/signals":
                # Parse query string for paged + scoped digest list:
                #   scope=latest -> only signals from the most recent run
                #   scope=all    -> every signal across all runs
                #   page, page_size -> pagination knobs (page_size respects display settings if absent)
                # Note: list items omit score_breakdown (slim mode). Use /api/signals/<id> for the full detail.
                qs = parse_qs(urlparse(self.path).query)
                scope = (qs.get("scope", ["latest"])[0] or "latest").lower()
                if scope not in ("latest", "all"):
                    scope = "latest"

                display = load_display_settings(config.agent.brain_file)
                try:
                    page = max(1, int(qs.get("page", ["1"])[0]))
                except (TypeError, ValueError):
                    page = 1
                try:
                    page_size = int(qs.get("page_size", [str(display["page_size"])])[0])
                except (TypeError, ValueError):
                    page_size = int(display["page_size"])
                page_size = max(1, min(100, page_size))

                # When scope=latest, use the most recent run's started_at as the cursor.
                run_started_at = None
                run_info = None
                if scope == "latest":
                    run = storage.latest_run()
                    if run:
                        run_started_at = run["started_at"]
                        run_info = {
                            "id": run["id"],
                            "started_at": run["started_at"],
                            "completed_at": run["completed_at"],
                            "signal_count": run["signal_count"],
                        }

                result = storage.list_signals_paged(
                    run_started_at=run_started_at,
                    page=page,
                    page_size=page_size,
                )
                result["scope"] = scope
                result["run"] = run_info
                self._json(result)
                return
            if path == "/api/display-settings":
                # Returns the user-editable display preferences as a small JSON
                # object so the frontend can default-select scope and page size.
                self._json(load_display_settings(config.agent.brain_file))
                return
            if path == "/api/memory":
                self._json(storage.list_memory(limit=25))
                return
            if path == "/api/settings":
                self._json(dashboard_settings(config))
                return
            if path == "/api/brain":
                self._json({"raw": load_brain_file(config.agent.brain_file)["raw"]})
                return
            if path == "/api/run/state":
                # Polled by the Run button to show live running indicator.
                self._json({"running": _run_state["running"], "error": _run_state["error"]})
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name.
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/run":
                    # Start the agent in a background thread. Returns immediately.
                    started = _start_agent_run(config, config_path)
                    self._json({"status": "started" if started else "already_running"})
                    return
                if path == "/api/settings":
                    self._json({"status": "ok", "settings": save_dashboard_settings(config, payload)})
                    return
                if path == "/api/brain":
                    save_raw_brain_file(config.agent.brain_file, str(payload.get("raw", "")))
                    self._json({"status": "ok", "brain": load_brain_file(config.agent.brain_file)})
                    return
                if path == "/api/display-settings":
                    # Persist display preferences. We piggyback on the brain-file save
                    # path so the [display] block stays in agent_brain.toml alongside
                    # the other Settings the user edits.
                    save_brain_file(
                        config.agent.brain_file,
                        {"display": dict(payload or {})},
                    )
                    self._json(
                        {
                            "status": "ok",
                            "display": load_display_settings(config.agent.brain_file),
                        }
                    )
                    return
            except Exception as exc:  # noqa: BLE001 - dashboard should report save errors as JSON.
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "error": str(exc)}).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, value: object) -> None:
            self._send("application/json; charset=utf-8", json.dumps(value, default=str))

        def _read_json(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            parsed = json.loads(raw or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object.")
            return parsed

        def _send(self, content_type: str, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return DashboardHandler


# Kept as a safety net — served when web/dist/ hasn't been built yet.
LEGACY_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Signal Stream</title>
  <style>
    :root { color-scheme: dark; --bg:#101314; --panel:#181d1f; --panel2:#121719; --line:#2d363a; --text:#eef3f4; --muted:#99a7ad; --accent:#79d2c0; --warn:#f0b35a; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    header { padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; }
    h1 { margin:0; font-size:20px; letter-spacing:0; }
    nav { display:flex; gap:8px; align-items:center; }
    main { display:grid; grid-template-columns: 320px 1fr; min-height:calc(100vh - 62px); }
    aside { border-right:1px solid var(--line); padding:18px; background:var(--panel2); }
    section { padding:18px; }
    h2 { font-size:14px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; margin:0 0 12px; }
    .metric { padding:12px; border:1px solid var(--line); margin-bottom:10px; border-radius:8px; background:var(--panel); }
    .metric b { display:block; font-size:22px; margin-top:4px; }
    .grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:14px; }
    .card { border:1px solid var(--line); background:var(--panel); border-radius:8px; padding:14px; min-height:110px; }
    .signal { border-left:3px solid var(--accent); display:grid; grid-template-columns:86px 1fr; gap:14px; }
    .signal.no-visual { grid-template-columns:1fr; }
    .signal h3 { margin:10px 0 8px; font-size:17px; line-height:1.35; }
    .signal p { margin:8px 0; color:var(--text); }
    .signal small { color:var(--muted); }
    .visual { width:86px; height:86px; border:1px solid var(--line); border-radius:8px; background:#0d1112; display:flex; align-items:center; justify-content:center; overflow:hidden; color:var(--accent); font-weight:800; }
    .visual img { width:100%; height:100%; object-fit:cover; display:block; }
    details { margin-top:10px; border-top:1px solid var(--line); padding-top:10px; }
    details summary { cursor:pointer; color:var(--accent); }
    .breakdown { margin:10px 0 0; padding:0; list-style:none; }
    .breakdown li { padding:6px 0; border-bottom:1px solid rgba(255,255,255,.06); }
    .breakdown b { color:var(--text); }
    .featured { grid-column:1 / -1; min-height:0; }
    .why { color:var(--muted); }
    .event { color:var(--muted); font-size:13px; border-bottom:1px solid var(--line); padding:8px 0; }
    .event b { color:var(--text); }
    .pill { display:inline-block; padding:2px 7px; border:1px solid var(--line); border-radius:999px; color:var(--accent); font-size:12px; }
    a { color:var(--accent); }
    button { background:var(--accent); color:#09201c; border:0; border-radius:6px; padding:8px 11px; font-weight:700; cursor:pointer; }
    button.secondary { background:transparent; color:var(--text); border:1px solid var(--line); }
    button.active { border-color:var(--accent); color:var(--accent); }
    label { display:block; color:var(--muted); font-size:13px; margin:12px 0 6px; }
    input, select, textarea { width:100%; background:#0d1112; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:9px; font:inherit; }
    textarea { min-height:132px; resize:vertical; }
    .settings-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }
    .settings-actions { display:flex; gap:8px; margin:12px 0 0; flex-wrap:wrap; }
    .hidden { display:none; }
    .status { min-height:20px; color:var(--warn); margin-top:8px; }
    @media (max-width: 860px) { main { grid-template-columns:1fr; } aside { border-right:0; border-bottom:1px solid var(--line); } .grid, .settings-grid { grid-template-columns:1fr; } .signal { grid-template-columns:1fr; } .visual { width:100%; height:140px; } }
  </style>
</head>
<body>
  <header>
    <h1>Signal Stream</h1>
    <nav>
      <button class="secondary active" id="signals-tab" onclick="showView('signals')">Signals</button>
      <button class="secondary" id="settings-tab" onclick="showView('settings')">Settings</button>
      <button onclick="load()">Refresh</button>
      <button id="run-btn" onclick="runAgent()">▶ Run Agent</button>
    </nav>
  </header>
  <main>
    <aside>
      <h2>Run</h2>
      <div id="run-error" class="status" style="display:none;margin-bottom:8px"></div>
      <div class="metric">Status <b id="status">-</b></div>
      <div class="metric">Started <b id="started">-</b></div>
      <div class="metric">Articles <b id="article-count">-</b></div>
      <div class="metric">Signals <b id="signal-count">-</b></div>
      <div class="metric" style="font-size:12px;color:var(--muted)" id="run-goal"></div>
      <h2>Memory</h2>
      <div id="memory"></div>
    </aside>
    <section id="signals-view">
      <h2>Ranked Signals</h2>
      <div id="signals" class="grid"></div>
      <h2 style="margin-top:18px">Agent Timeline</h2>
      <div id="events"></div>
      <h2 style="margin-top:18px">Tool Calls</h2>
      <div id="tools"></div>
    </section>
    <section id="settings-view" class="hidden">
      <h2>Settings</h2>
      <div class="settings-grid">
        <div class="card">
          <h3>Agent Behavior</h3>
          <label>Scout mode <small>(code = rules only · hybrid = code + LLM polish · model = LLM only)</small></label>
          <select id="scout_mode"><option>code</option><option>hybrid</option><option>model</option></select>
          <label>Analyst mode <small>(same meaning — controls who writes summaries)</small></label>
          <select id="analyst_mode"><option>code</option><option>hybrid</option><option>model</option></select>
          <label>Relevance policy</label>
          <select id="relevance_policy"><option value="soft_keep">soft keep borderline items</option><option value="hard_drop">hard drop model-labeled drop items</option></select>
          <label>Model score adjustment limit <small>(hybrid mode only — caps how much the LLM can swing the base score)</small></label>
          <input id="model_score_adjustment_limit" type="number" min="0" max="100">
          <label>Analyst review limit</label>
          <input id="analyst_review_limit" type="number" min="1" max="100">
          <label>Full Analyst review</label>
          <select id="analyst_full_review"><option value="false">off for faster local runs</option><option value="true">on for stronger models</option></select>
          <label>Repeat penalty strength</label>
          <select id="repeat_penalty_strength"><option>light</option><option>medium</option><option>strong</option></select>

          <h3 style="margin-top:18px">Critic (Reflection Loop)</h3>
          <label>Enable Critic <small>(reviews each digest before publishing; opt-in)</small></label>
          <select id="enable_critic"><option value="true">enabled</option><option value="false">disabled</option></select>
          <label>Max critic rounds <small>(0–5; how many revision loops before shipping anyway)</small></label>
          <input id="max_critic_rounds" type="number" min="0" max="5">
          <label>Critic score threshold <small>(0–100; digests below this score trigger a revision)</small></label>
          <input id="critic_score_threshold" type="number" min="0" max="100">
        </div>
        <div class="card">
          <h3>Reader Experience</h3>
          <label>Summary mode</label>
          <select id="summary_mode"><option value="short_expanded">short + expanded</option><option value="short_only">short only</option></select>
          <label>Visuals</label>
          <select id="visuals_mode"><option value="image_icon">article image + icon fallback</option><option value="icon_only">icon only</option><option value="none">no visuals</option></select>
          <label>Scout note</label>
          <select id="scout_note_enabled"><option value="true">enabled</option><option value="false">disabled</option></select>
          <label>Entity extraction</label>
          <select id="entity_extraction"><option>hybrid</option><option value="model">model only</option><option value="known_list">known list only</option></select>
        </div>
      </div>
      <div class="settings-grid" style="margin-top:14px">
        <div class="card">
          <h3>Prompts <small>(this is where agent judgment lives — edit here, not in Python)</small></h3>
          <label>Orchestrator prompt <small>(decides what the system does next)</small></label><textarea id="prompt_orchestrator"></textarea>
          <label>Scout prompt <small>(controls how raw articles get enriched/labeled)</small></label><textarea id="prompt_scout"></textarea>
          <label>Analyst prompt <small>(writes summaries + why-it-matters; word counts go here)</small></label><textarea id="prompt_analyst"></textarea>
          <label>Critic prompt <small>(reviews the digest; flags weak signals — only used when Critic is enabled)</small></label><textarea id="prompt_critic"></textarea>
        </div>
        <div class="card">
          <h3>Scoring</h3>
          <label>Priority match max points</label><input id="score_priority_match" type="number">
          <label>Major-player max points</label><input id="score_major_player" type="number">
          <label>Corroboration max points</label><input id="score_corroboration" type="number">
          <label>Repeat penalty max points</label><input id="score_repeat_penalty" type="number">
          <label>Low-value penalty max points</label><input id="score_low_value_penalty" type="number">
          <label>Low-value phrases</label><textarea id="low_value_phrases"></textarea>
        </div>
      </div>
      <div class="card" style="margin-top:14px">
        <h3>Advanced Brain File</h3>
        <label>Raw TOML</label>
        <textarea id="raw_brain" style="min-height:260px"></textarea>
      </div>
      <div class="settings-actions">
        <button onclick="saveSettings()">Save Friendly Settings</button>
        <button class="secondary" onclick="saveRawBrain()">Save Advanced TOML</button>
        <button class="secondary" onclick="loadSettings()">Reload Settings</button>
      </div>
      <div id="settings-status" class="status"></div>
    </section>
  </main>
  <script>
    async function get(path) { const r = await fetch(path); return r.json(); }
    async function post(path, value) { const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(value)}); return r.json(); }
    function esc(s) { return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function showView(name) {
      document.getElementById('signals-view').classList.toggle('hidden', name !== 'signals');
      document.getElementById('settings-view').classList.toggle('hidden', name !== 'settings');
      document.getElementById('signals-tab').classList.toggle('active', name === 'signals');
      document.getElementById('settings-tab').classList.toggle('active', name === 'settings');
      if (name === 'settings') loadSettings();
    }
    function renderBreakdown(items) {
      if (!items || !items.length) return '<div class="event">No score breakdown saved yet.</div>';
      return `<ul class="breakdown">${items.map(item => `<li><b>${esc(item.name)}</b>: ${esc(item.points)}<br><span class="why">${esc(item.reason)}</span></li>`).join('')}</ul>`;
    }
    function renderVisual(s) {
      if (s.image_url) return `<div class="visual"><img src="${esc(s.image_url)}" alt=""></div>`;
      if (!s.icon_key) return '';
      return `<div class="visual">${esc(String(s.icon_key || s.event_type || 'signal').slice(0,2).toUpperCase())}</div>`;
    }
    function renderSignal(s, featured=false) {
      return `<article class="card signal ${featured ? 'featured' : ''} ${!s.image_url && !s.icon_key ? 'no-visual' : ''}">
        ${renderVisual(s)}
        <div>
          <span class="pill">${esc(s.score)}/100 ${esc(s.urgency)}</span>
          <h3>${esc(s.title)}</h3>
          <p>${esc(s.short_summary || s.summary)}</p>
          <small>${esc(s.source)}${s.url ? ` · <a href="${esc(s.url)}" target="_blank" rel="noreferrer">source</a>` : ''}${s.event_type ? ` · ${esc(s.event_type)}` : ''}${s.relevance_label ? ` · ${esc(s.relevance_label)}` : ''}</small>
          <details>
            <summary>Open signal details</summary>
            <p>${esc(s.expanded_summary || s.summary)}</p>
            <p class="why">${esc(s.why_it_matters || '')}</p>
            ${s.scout_note ? `<p class="why"><b>Scout note:</b> ${esc(s.scout_note)}</p>` : ''}
            ${renderBreakdown(s.score_breakdown)}
          </details>
        </div>
      </article>`;
    }
    async function load() {
      const [run, events, tools, signals, memory, runState] = await Promise.all([
        get('/api/run/latest'), get('/api/events'), get('/api/tool-calls'), get('/api/signals'), get('/api/memory'), get('/api/run/state')
      ]);

      // --- sidebar run summary (Fix 2) ---
      const summary = (() => { try { return JSON.parse(run.summary_json || '{}'); } catch(e) { return {}; } })();
      document.getElementById('status').textContent = run.status || 'none';
      document.getElementById('started').textContent = run.started_at || '-';
      document.getElementById('article-count').textContent = summary.articles != null ? summary.articles : '-';
      document.getElementById('signal-count').textContent = signals.length;
      const goalEl = document.getElementById('run-goal');
      if (run.goal) { goalEl.textContent = 'Goal: ' + run.goal; goalEl.style.display = ''; }
      else goalEl.style.display = 'none';

      // --- run button state ---
      const btn = document.getElementById('run-btn');
      const errEl = document.getElementById('run-error');
      if (runState.running) {
        btn.textContent = '⏳ Running…';
        btn.disabled = true;
      } else {
        btn.textContent = '▶ Run Agent';
        btn.disabled = false;
      }
      if (runState.error) {
        errEl.textContent = '⚠ ' + runState.error;
        errEl.style.display = '';
      } else {
        errEl.style.display = 'none';
      }

      // --- signals + timeline ---
      document.getElementById('signals').innerHTML = signals.length
        ? [renderSignal(signals[0], true)].concat(signals.slice(1).map(s => renderSignal(s))).join('')
        : '<div class="card">No signals yet.</div>';
      document.getElementById('events').innerHTML = events.map(e => `<div class="event"><b>${esc(e.agent)}</b> · ${esc(e.event_type)} · ${esc(e.message)}</div>`).join('') || '<div class="card">No events yet.</div>';
      document.getElementById('tools').innerHTML = tools.map(t => `<div class="event"><b>${esc(t.agent)}</b> called ${esc(t.tool)} · ${esc(t.status)} · confidence ${esc(t.confidence)}</div>`).join('') || '<div class="card">No tool calls yet.</div>';
      document.getElementById('memory').innerHTML = memory.map(m => `<div class="event"><b>${esc(m.topic)}</b><br>${esc(m.title)}</div>`).join('') || '<div class="event">No memory yet.</div>';
    }

    async function runAgent() {
      const btn = document.getElementById('run-btn');
      btn.textContent = '⏳ Starting…';
      btn.disabled = true;
      try {
        const result = await post('/api/run', {});
        if (result.status === 'already_running') {
          btn.textContent = '⏳ Running…';
        }
      } catch(e) {
        btn.textContent = '▶ Run Agent';
        btn.disabled = false;
        document.getElementById('run-error').textContent = '⚠ Could not start run: ' + e;
        document.getElementById('run-error').style.display = '';
      }
      // Auto-refresh picks up the new status on the next tick.
      setTimeout(load, 800);
    }
    async function loadSettings() {
      const brain = await get('/api/settings');
      const b = brain.behavior || {}, p = brain.prompts || {}, s = brain.scoring || {};
      for (const key of ['scout_mode','analyst_mode','relevance_policy','repeat_penalty_strength','summary_mode','visuals_mode','entity_extraction']) {
        if (document.getElementById(key)) document.getElementById(key).value = b[key] || document.getElementById(key).value;
      }
      document.getElementById('scout_note_enabled').value = String(b.scout_note_enabled !== false);
      document.getElementById('model_score_adjustment_limit').value = b.model_score_adjustment_limit || 20;
      document.getElementById('analyst_review_limit').value = b.analyst_review_limit || 8;
      document.getElementById('analyst_full_review').value = String(b.analyst_full_review === true);
      // Critic-loop knobs (default off / 1 round / threshold 70 if missing).
      document.getElementById('enable_critic').value = String(b.enable_critic === true);
      document.getElementById('max_critic_rounds').value = b.max_critic_rounds != null ? b.max_critic_rounds : 1;
      document.getElementById('critic_score_threshold').value = b.critic_score_threshold != null ? b.critic_score_threshold : 70;
      document.getElementById('prompt_orchestrator').value = p.orchestrator || '';
      document.getElementById('prompt_scout').value = p.scout || '';
      document.getElementById('prompt_analyst').value = p.analyst || '';
      document.getElementById('prompt_critic').value = p.critic || '';
      const max = s.max_points || {};
      for (const key of ['priority_match','major_player','corroboration','repeat_penalty','low_value_penalty']) {
        document.getElementById('score_' + key).value = max[key] || 0;
      }
      document.getElementById('low_value_phrases').value = (s.low_value_phrases || []).join('\\n');
      document.getElementById('raw_brain').value = brain.raw || '';
    }
    function collectSettings() {
      return {
        behavior: {
          scout_mode: document.getElementById('scout_mode').value,
          analyst_mode: document.getElementById('analyst_mode').value,
          relevance_policy: document.getElementById('relevance_policy').value,
          scout_note_enabled: document.getElementById('scout_note_enabled').value === 'true',
          model_score_adjustment_limit: Number(document.getElementById('model_score_adjustment_limit').value || 20),
          analyst_review_limit: Number(document.getElementById('analyst_review_limit').value || 8),
          analyst_full_review: document.getElementById('analyst_full_review').value === 'true',
          summary_mode: document.getElementById('summary_mode').value,
          visuals_mode: document.getElementById('visuals_mode').value,
          repeat_penalty_strength: document.getElementById('repeat_penalty_strength').value,
          entity_extraction: document.getElementById('entity_extraction').value,
          // Critic-loop knobs — persisted alongside the rest of behavior.
          enable_critic: document.getElementById('enable_critic').value === 'true',
          max_critic_rounds: Number(document.getElementById('max_critic_rounds').value || 1),
          critic_score_threshold: Number(document.getElementById('critic_score_threshold').value || 70)
        },
        prompts: {
          orchestrator: document.getElementById('prompt_orchestrator').value,
          scout: document.getElementById('prompt_scout').value,
          analyst: document.getElementById('prompt_analyst').value,
          critic: document.getElementById('prompt_critic').value
        },
        scoring: {
          max_points: {
            priority_match: Number(document.getElementById('score_priority_match').value || 0),
            major_player: Number(document.getElementById('score_major_player').value || 0),
            corroboration: Number(document.getElementById('score_corroboration').value || 0),
            repeat_penalty: Number(document.getElementById('score_repeat_penalty').value || 0),
            low_value_penalty: Number(document.getElementById('score_low_value_penalty').value || 0)
          },
          low_value_phrases: document.getElementById('low_value_phrases').value.split(/\\n|,/).map(s => s.trim()).filter(Boolean)
        }
      };
    }
    async function saveSettings() {
      const result = await post('/api/settings', collectSettings());
      document.getElementById('settings-status').textContent = result.status === 'ok' ? 'Saved friendly settings.' : `Save failed: ${result.error || 'unknown error'}`;
      await loadSettings();
    }
    async function saveRawBrain() {
      const result = await post('/api/brain', {raw: document.getElementById('raw_brain').value});
      document.getElementById('settings-status').textContent = result.status === 'ok' ? 'Saved advanced TOML.' : `Save failed: ${result.error || 'unknown error'}`;
      if (result.status === 'ok') await loadSettings();
    }
    load();
    setInterval(load, 5000);
  </script>
</body>
</html>"""
