from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import pathlib
from urllib.parse import urlparse

from .models import SignalConfig
from .prompt_loader import load_brain_file, save_brain_file, save_raw_brain_file
from .storage import SignalStorage

# Allowed extensions when serving files from web/dist/assets/.
# Kept tight so the handler can't accidentally serve .py or .toml files.
_ALLOWED_EXTENSIONS = {".js", ".css", ".svg", ".png", ".ico", ".woff", ".woff2", ".map", ".txt"}


def _static_dist() -> pathlib.Path:
    """Return the path to web/dist relative to the repo root.

    Plain English: walk up from dashboard.py (signal_stream/) to the repo root,
    then append web/dist.  This works however the package is installed because
    __file__ is always the real file on disk.
    """
    # __file__ is  <repo>/signal_stream/dashboard.py
    # parents[1]  is  <repo>/
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


def serve_dashboard(config: SignalConfig, host: str = "127.0.0.1", port: int | None = None) -> None:
    """Start the local dashboard.

    Plain English: this is a tiny local website for watching what the agents did.
    It reads SQLite and does not send your data anywhere.
    """

    storage = SignalStorage(config.storage_path)
    storage.init()
    server = ThreadingHTTPServer((host, port or config.agent.dashboard_port), _handler(storage, config))
    print(f"Signal Stream dashboard: http://{host}:{server.server_port}")
    server.serve_forever()


def _handler(storage: SignalStorage, config: SignalConfig) -> type[BaseHTTPRequestHandler]:
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
            if path == "/api/signals":
                self._json(storage.list_signals(limit=25))
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
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name.
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/settings":
                    self._json({"status": "ok", "settings": save_dashboard_settings(config, payload)})
                    return
                if path == "/api/brain":
                    save_raw_brain_file(config.agent.brain_file, str(payload.get("raw", "")))
                    self._json({"status": "ok", "brain": load_brain_file(config.agent.brain_file)})
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
    </nav>
  </header>
  <main>
    <aside>
      <h2>Run</h2>
      <div class="metric">Status <b id="status">-</b></div>
      <div class="metric">Started <b id="started">-</b></div>
      <div class="metric">Signals <b id="signal-count">-</b></div>
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
          <label>Scout mode</label>
          <select id="scout_mode"><option>code</option><option>hybrid</option><option>model</option></select>
          <label>Analyst mode</label>
          <select id="analyst_mode"><option>code</option><option>hybrid</option><option>model</option></select>
          <label>Relevance policy</label>
          <select id="relevance_policy"><option value="soft_keep">soft keep borderline items</option><option value="hard_drop">hard drop model-labeled drop items</option></select>
          <label>Model score adjustment limit</label>
          <input id="model_score_adjustment_limit" type="number" min="0" max="100">
          <label>Repeat penalty strength</label>
          <select id="repeat_penalty_strength"><option>light</option><option>medium</option><option>strong</option></select>
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
          <h3>Prompts</h3>
          <label>Orchestrator prompt</label><textarea id="prompt_orchestrator"></textarea>
          <label>Scout prompt</label><textarea id="prompt_scout"></textarea>
          <label>Analyst prompt</label><textarea id="prompt_analyst"></textarea>
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
      const [run, events, tools, signals, memory] = await Promise.all([
        get('/api/run/latest'), get('/api/events'), get('/api/tool-calls'), get('/api/signals'), get('/api/memory')
      ]);
      document.getElementById('status').textContent = run.status || 'none';
      document.getElementById('started').textContent = run.started_at || '-';
      document.getElementById('signal-count').textContent = signals.length;
      document.getElementById('signals').innerHTML = signals.length
        ? [renderSignal(signals[0], true)].concat(signals.slice(1).map(s => renderSignal(s))).join('')
        : '<div class="card">No signals yet.</div>';
      document.getElementById('events').innerHTML = events.map(e => `<div class="event"><b>${esc(e.agent)}</b> · ${esc(e.event_type)} · ${esc(e.message)}</div>`).join('') || '<div class="card">No events yet.</div>';
      document.getElementById('tools').innerHTML = tools.map(t => `<div class="event"><b>${esc(t.agent)}</b> called ${esc(t.tool)} · ${esc(t.status)} · confidence ${esc(t.confidence)}</div>`).join('') || '<div class="card">No tool calls yet.</div>';
      document.getElementById('memory').innerHTML = memory.map(m => `<div class="event"><b>${esc(m.topic)}</b><br>${esc(m.title)}</div>`).join('') || '<div class="event">No memory yet.</div>';
    }
    async function loadSettings() {
      const brain = await get('/api/settings');
      const b = brain.behavior || {}, p = brain.prompts || {}, s = brain.scoring || {};
      for (const key of ['scout_mode','analyst_mode','relevance_policy','repeat_penalty_strength','summary_mode','visuals_mode','entity_extraction']) {
        if (document.getElementById(key)) document.getElementById(key).value = b[key] || document.getElementById(key).value;
      }
      document.getElementById('scout_note_enabled').value = String(b.scout_note_enabled !== false);
      document.getElementById('model_score_adjustment_limit').value = b.model_score_adjustment_limit || 20;
      document.getElementById('prompt_orchestrator').value = p.orchestrator || '';
      document.getElementById('prompt_scout').value = p.scout || '';
      document.getElementById('prompt_analyst').value = p.analyst || '';
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
          summary_mode: document.getElementById('summary_mode').value,
          visuals_mode: document.getElementById('visuals_mode').value,
          repeat_penalty_strength: document.getElementById('repeat_penalty_strength').value,
          entity_extraction: document.getElementById('entity_extraction').value
        },
        prompts: {
          orchestrator: document.getElementById('prompt_orchestrator').value,
          scout: document.getElementById('prompt_scout').value,
          analyst: document.getElementById('prompt_analyst').value
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
