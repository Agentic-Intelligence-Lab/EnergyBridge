"""Local web dashboard for EnergyBridge benchmark results.

Run:
    python experiments/benchmark/web_dashboard.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "benchmark_results"
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
JOBS: dict[str, dict] = {}
JOB_PROCS: dict[str, subprocess.Popen] = {}
JOBS_LOCK = threading.Lock()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _append_job_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.setdefault("logs", []).append(line.rstrip("\n"))


def _set_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)


def _infer_human_input_context(logs: list[str], requested_kind: str = "") -> tuple[str, str]:
    """Infer which blocking human prompt the web input is answering."""
    tail = "\n".join(logs[-100:])
    event_matches = re.findall(r"(?:VPP event|VPP事件)\s*(\d+)", tail)
    selected_matches = re.findall(r"\[Strategy Selected\s+\|\s+event=(\d+)\]", tail)
    score_matches = re.findall(r"\[Human Score Selected\s+\|\s+event=(\d+)\]", tail)
    event_id = (event_matches or selected_matches or score_matches or [""])[-1]

    strategy_idx = max(tail.rfind("[请选择策略"), tail.rfind("输入 A / B / C"))
    selected_idx = tail.rfind("[Strategy Selected")
    score_prompt_idx = tail.rfind("请对本次VPP处理结果评分")
    score_done_idx = tail.rfind("[Human Score Selected")
    comment_idx = tail.rfind("可选：留下简短反馈")

    if comment_idx > max(score_prompt_idx, strategy_idx) and comment_idx > score_done_idx:
        return "score_comment", event_id
    if score_prompt_idx > max(strategy_idx, selected_idx) and score_prompt_idx > score_done_idx:
        return "score", event_id
    if strategy_idx > selected_idx:
        return "strategy_choice", event_id
    return requested_kind or "unknown", event_id


def _attach_finished_summary(job_id: str) -> None:
    with JOBS_LOCK:
        logs = list(JOBS.get(job_id, {}).get("logs", []))
    summary_path = None
    for line in reversed(logs):
        if "run_summary.txt" in line:
            tail = line.split("run_summary.txt", 1)[-1].strip()
            if "→" in line:
                tail = line.rsplit("→", 1)[-1].strip()
            summary_path = tail if tail.endswith("run_summary.txt") else None
            if summary_path:
                break
    if not summary_path:
        for line in reversed(logs):
            if line.strip().startswith("OUTPUT"):
                output_dir = line.split(":", 1)[-1].strip()
                candidate = Path(output_dir) / "run_summary.txt"
                if candidate.exists():
                    summary_path = str(candidate)
                    break
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            _set_job(
                job_id,
                run_summary_path=str(path),
                run_summary_text=path.read_text(encoding="utf-8", errors="replace"),
            )


def _run_command_job(job_id: str, argv: list[str]) -> None:
    _set_job(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
    try:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            argv,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=0,
            env=env,
        )
        with JOBS_LOCK:
            JOB_PROCS[job_id] = proc
            if job_id in JOBS:
                JOBS[job_id]["pid"] = proc.pid
        assert proc.stdout is not None
        chunk = []
        while True:
            char = proc.stdout.read(1)
            if char == "" and proc.poll() is not None:
                break
            if not char:
                continue
            chunk.append(char)
            if char == "\n" or "".join(chunk).endswith("  > "):
                _append_job_log(job_id, "".join(chunk))
                chunk = []
        if chunk:
            _append_job_log(job_id, "".join(chunk))
        code = proc.wait()
        _attach_finished_summary(job_id)
        _set_job(
            job_id,
            status="succeeded" if code == 0 else "failed",
            exit_code=code,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _append_job_log(job_id, f"[dashboard error] {exc}")
        _set_job(
            job_id,
            status="failed",
            exit_code=-1,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        with JOBS_LOCK:
            JOB_PROCS.pop(job_id, None)


def _validate_run_command(command: str) -> list[str]:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("Command is empty")
    allowed_prefixes = [
        ["python", "experiments/benchmark/run_persona_json.py"],
        [sys.executable, "experiments/benchmark/run_persona_json.py"],
        ["conda", "run", "--no-capture-output", "-n", "energybridge", "python", "experiments/benchmark/run_persona_json.py"],
        ["conda", "run", "-n", "energybridge", "python", "experiments/benchmark/run_persona_json.py"],
    ]
    if not any(argv[: len(prefix)] == prefix for prefix in allowed_prefixes):
        raise ValueError(
            "Only run_persona_json commands are allowed from the dashboard."
        )
    forbidden = {";", "&&", "||", "|", ">", "<"}
    if any(token in forbidden for token in argv):
        raise ValueError("Shell operators are not allowed")
    if argv[:2] == ["conda", "run"] and "--no-capture-output" not in argv[:6]:
        argv = ["conda", "run", "--no-capture-output"] + argv[2:]
    return argv


def _list_personas() -> list[dict]:
    personas = []
    for path in sorted(PERSONA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        persona_id = data.get("id") or path.stem
        label = persona_id
        if persona_id.startswith("basic_role_"):
            parts = persona_id.split("_")
            label = f"role_{parts[2]}" if len(parts) > 2 else persona_id
        personas.append({
            "id": persona_id,
            "label": label,
            "name": data.get("name", persona_id),
            "path": str(path),
        })
    return personas


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EnergyBridge Agent Console</title>
  <style>
    :root {
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8dee7;
      --paper: #f7f3ea;
      --panel: #ffffff;
      --leaf: #25705a;
      --clay: #b5533c;
      --sky: #2e6f9e;
      --gold: #be8a22;
      --shadow: 0 18px 45px rgba(31, 41, 51, .12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(37,112,90,.09), transparent 34%),
        linear-gradient(315deg, rgba(190,138,34,.13), transparent 40%),
        var(--paper);
      font-family: ui-sans-serif, "Avenir Next", "Segoe UI", "Noto Sans SC", sans-serif;
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 320px 1fr;
      transition: grid-template-columns .2s ease;
    }
    .shell.sidebar-collapsed { grid-template-columns: 0 1fr; }
    aside {
      border-right: 1px solid var(--line);
      background: rgba(255,255,255,.72);
      backdrop-filter: blur(14px);
      padding: 24px 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      transition: opacity .15s ease, padding .2s ease;
      z-index: 3;
    }
    .shell.sidebar-collapsed aside {
      opacity: 0;
      pointer-events: none;
      padding-left: 0;
      padding-right: 0;
    }
    main { padding: 26px; overflow: hidden; }
    h1 { font-size: 26px; line-height: 1.1; margin: 0 0 8px; letter-spacing: 0; }
    h2 { font-size: 18px; margin: 0 0 12px; }
    h3 { font-size: 15px; margin: 0 0 10px; color: var(--muted); font-weight: 700; }
    .sub { color: var(--muted); font-size: 13px; line-height: 1.5; margin-bottom: 20px; }
    select, textarea, input, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    button {
      cursor: pointer;
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
      font-weight: 700;
    }
    button.secondary { background: #fff; color: var(--ink); }
    .sidebar-toggle {
      position: fixed;
      left: 14px;
      bottom: 14px;
      z-index: 5;
      width: auto;
      border-radius: 999px;
      padding: 10px 14px;
      box-shadow: var(--shadow);
    }
    .run-list { display: grid; gap: 8px; margin-top: 14px; }
    .run-btn {
      text-align: left;
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
      font-weight: 650;
    }
    .run-btn.active { border-color: var(--leaf); box-shadow: inset 4px 0 0 var(--leaf); }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }
    .title-block { max-width: 860px; }
    .pill-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
    .pill {
      border: 1px solid var(--line);
      background: rgba(255,255,255,.82);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 13px;
      color: var(--muted);
    }
    .grid { display: grid; gap: 16px; }
    .metrics { grid-template-columns: repeat(4, minmax(140px, 1fr)); }
    .panel {
      background: rgba(255,255,255,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .metric .label { color: var(--muted); font-size: 12px; }
    .metric .value { font-size: 26px; font-weight: 800; margin-top: 6px; }
    .event-layout { grid-template-columns: 1fr; align-items: start; }
    .events { display: grid; gap: 14px; }
    .event {
      border-left: 5px solid var(--sky);
      transition: transform .15s ease, box-shadow .15s ease;
    }
    .event:hover { transform: translateY(-1px); }
    .event-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .score { font-size: 28px; font-weight: 850; color: var(--leaf); }
    .kv {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .kv div { padding: 10px; background: #f8faf9; border: 1px solid #edf0f2; border-radius: 7px; }
    .kv span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .timeline { display: grid; gap: 8px; margin-top: 10px; }
    .decision {
      display: grid;
      grid-template-columns: 72px 86px 1fr;
      gap: 10px;
      align-items: start;
      padding: 9px 0;
      border-top: 1px solid #eef1f3;
      font-size: 13px;
    }
    .actions { color: var(--muted); overflow-wrap: anywhere; }
    .command-row { display: grid; grid-template-columns: 1fr 120px; gap: 10px; }
    .preset-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0 0 10px; }
    .preset-grid button { padding: 9px; }
    .field-row { margin: 0 0 12px; }
    .field-row label { display: block; color: var(--muted); font-size: 12px; font-weight: 800; margin-bottom: 6px; }
    .live-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }
    .live-card { background: #f8faf9; border: 1px solid #edf0f2; border-radius: 7px; padding: 9px; }
    .live-card span { display: block; color: var(--muted); font-size: 11px; margin-bottom: 3px; }
    .live-card strong { font-size: 16px; }
    .dialogue-feed {
      max-height: 220px;
      overflow: auto;
      display: grid;
      gap: 8px;
      background: #f8faf9;
      border: 1px solid #edf0f2;
      border-radius: 7px;
      padding: 10px;
      margin-top: 10px;
    }
    .dialogue-item { padding: 8px 10px; border-radius: 7px; background: #fff; border-left: 4px solid var(--sky); }
    .dialogue-item.user { border-left-color: var(--clay); }
    .dialogue-item.grid { border-left-color: var(--gold); }
    .dialogue-item.result { border-left-color: var(--leaf); }
    .dialogue-item small { display: block; color: var(--muted); margin-bottom: 3px; font-weight: 700; }
    .progress-viz {
      display: grid;
      grid-template-columns: repeat(3, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 10px;
    }
    .progress-card {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      border-top: 4px solid var(--sky);
      min-height: 170px;
    }
    .progress-card.active { border-top-color: var(--gold); box-shadow: 0 10px 24px rgba(190, 138, 34, .15); }
    .progress-card.done { border-top-color: var(--leaf); }
    .progress-card h4 { margin: 0 0 8px; font-size: 15px; }
    .progress-row { display: grid; grid-template-columns: 88px 1fr; gap: 8px; padding: 5px 0; border-top: 1px solid #eef1f3; font-size: 12px; }
    .progress-row span { color: var(--muted); }
    .progress-log { margin-top: 8px; display: grid; gap: 5px; }
    .progress-chip { font-size: 12px; border-radius: 999px; padding: 5px 8px; background: #f8faf9; border: 1px solid #edf0f2; overflow-wrap: anywhere; }
    .progress-section { margin-top: 10px; }
    .progress-section strong { display: block; font-size: 12px; margin-bottom: 5px; color: var(--muted); }
    .appliance-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
    .appliance-pill { font-size: 12px; border-radius: 7px; padding: 6px 8px; background: #fffdf8; border: 1px solid #eee2c9; overflow-wrap: anywhere; }
    .terminal {
      height: 260px;
      overflow: auto;
      background: #101820;
      color: #d9f0e3;
      border-radius: 7px;
      padding: 12px;
      font: 12px/1.45 ui-monospace, "SFMono-Regular", Consolas, monospace;
      white-space: pre-wrap;
    }
    .summary-log {
      max-height: 520px;
      overflow: auto;
      background: #fffdf8;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 12px;
      margin-top: 10px;
      white-space: pre-wrap;
      font: 12px/1.55 ui-monospace, "SFMono-Regular", Consolas, monospace;
    }
    .human-dialogue {
      background: #fffdf8;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
    }
    .human-custom { display: grid; grid-template-columns: 1fr 110px; gap: 10px; }
    .empty { padding: 40px; text-align: center; color: var(--muted); }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .shell.sidebar-collapsed { grid-template-columns: 1fr; }
      aside { position: relative; height: auto; }
      .metrics, .event-layout, .progress-viz { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <button id="sidebarToggle" class="sidebar-toggle secondary">隐藏历史</button>
  <div class="shell" id="shell">
    <aside id="sidebar">
      <h1>EnergyBridge</h1>
      <div class="sub">Agent 决策与 VPP 容量量化控制台</div>
      <button id="newRunButton">新运行</button>
      <div style="height:14px"></div>
      <label for="runSelect">选择结果</label>
      <select id="runSelect"></select>
      <div class="run-list" id="runList"></div>
    </aside>
    <main>
      <div id="app" class="empty">正在加载 benchmark 结果...</div>
    </main>
  </div>
  <script>
    const state = {
      runs: [],
      personas: [],
      active: null,
      data: null,
      view: 'run',
      sidebarOpen: true,
      eventId: 'vpp1',
      jobId: null,
      jobPoll: null,
      selectedMethod: 'agent',
      selectedPersona: 'basic_role_a_commuter_price_cooperative',
      userMode: 'roleplay',
      humanName: 'human_user'
    };
    const $ = (id) => document.getElementById(id);
    const fmt = (n, d=2) => Number.isFinite(Number(n)) ? Number(n).toFixed(d) : 'N/A';
    const pct = (n) => Number.isFinite(Number(n)) ? `${(Number(n)*100).toFixed(0)}%` : 'N/A';

    async function api(path, opts) {
      const res = await fetch(path, opts);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function loadRuns() {
      state.runs = await api('/api/runs');
      const select = $('runSelect');
      select.innerHTML = '<option value="">选择历史结果...</option>' + state.runs.map(r => `<option value="${r.id}">${r.label}</option>`).join('');
      select.onchange = () => loadRun(select.value);
      renderRunButtons();
      render();
    }

    async function loadPersonas() {
      state.personas = await api('/api/personas');
      if (!state.personas.some(p => p.id === state.selectedPersona) && state.personas[0]) {
        state.selectedPersona = state.personas[0].id;
      }
    }

    function renderRunButtons() {
      $('runList').innerHTML = state.runs.map(r => `
        <button class="run-btn ${state.active === r.id ? 'active' : ''}" data-id="${r.id}">
          ${r.method || 'unknown'} · ${r.persona || r.label}<br>
          <small>${r.date || ''} ${r.city || ''}</small>
        </button>`).join('');
      document.querySelectorAll('.run-btn').forEach(btn => btn.onclick = () => loadRun(btn.dataset.id));
    }

    async function loadRun(id) {
      if (!id) return;
      state.view = 'history';
      state.active = id;
      state.data = await api(`/api/run?id=${encodeURIComponent(id)}`);
      state.eventId = (state.data.vpp_event_log?.[0]?.id) || 'vpp1';
      $('runSelect').value = id;
      renderRunButtons();
      render();
    }

    function eventTitle(ev, index) {
      const day = index + 1;
      return `${ev.id || `vpp${day}`} · Day ${day} 18:00-19:00`;
    }

    function actionsLine(actions) {
      const entries = Object.entries(actions || {}).filter(([, v]) => v !== null && v !== undefined);
      if (!entries.length) return '保持当前策略';
      return entries.map(([k, v]) => `${k}=${v}`).join(' · ');
    }

    function renderEvent(ev, index) {
      const tq = ev.total_quantification_90 || {};
      const demandKw = ev.demand_target_kw ?? tq.vpp_target_capacity_120_kw;
      const actualShed = ev.actual_shed_kwh;
      const targetShed = ev.demand_target_shed_kwh ?? tq.vpp_target_capacity_energy_kwh;
      const demandOk = Number(actualShed) >= Number(targetShed || Infinity);
      const decisions = ev.day_decisions || [];
      return `
        <section class="panel event" data-event="${ev.id}">
          <div class="event-head">
            <div>
              <h2>${eventTitle(ev, index)}</h2>
              <div class="sub">${ev.reason || '无 Agent 理由'}</div>
            </div>
            <div class="score">${ev.score ?? 'N/A'}/5</div>
          </div>
          <div class="kv">
            <div><span>VPP需求目标</span>${fmt(demandKw, 3)} kW</div>
            <div><span>实际削减</span>${fmt(actualShed, 3)} / ${fmt(targetShed, 3)} kWh ${demandOk ? '✓' : '!'}</div>
            <div><span>等价用电上限</span>${fmt(ev.demand_target_kwh, 3)} kWh</div>
            <div><span>VPP实际用电</span>${fmt(ev.actual_kwh, 3)} kWh</div>
            <div><span>90%可信容量</span>${fmt(tq.avg_reported_capacity_90_kw, 3)} kW</div>
            <div><span>1.2倍目标容量</span>${fmt(tq.vpp_target_capacity_120_kw, 3)} kW</div>
          </div>
          <h3>全天决策轨迹</h3>
          <div class="timeline">
            ${decisions.map(d => `
              <div class="decision">
                <strong>${fmt(d.h % 24, 1)}h</strong>
                <span>${fmt(d.sp, 1)}°C</span>
                <div>
                  <div>${d.reason || '无说明'}</div>
                  <div class="actions">${actionsLine(d.actions || d.raw_appliance_actions)}</div>
                </div>
              </div>`).join('')}
          </div>
        </section>`;
    }

    function defaultCommand() {
      return commandPreset(state.selectedMethod, state.selectedPersona, state.userMode, state.humanName);
    }

    function shellQuote(value) {
      return `'${String(value || '').replaceAll("'", "'\"'\"'")}'`;
    }

    function commandPreset(method, persona, userMode, humanName) {
      const base = `conda run --no-capture-output -n energybridge python experiments/benchmark/run_persona_json.py ${persona || state.selectedPersona}`;
      const controllerMethod = method || 'agent';
      const horizon = controllerMethod === 'mpc_dynamic' || controllerMethod === 'mpc_ep' ? ' --mpc-horizon 6' : '';
      const human = userMode === 'human'
        ? ` --user-mode human --human-name ${shellQuote(humanName || 'human')}`
        : ' --user-mode roleplay';
      return `${base} --method ${controllerMethod}${horizon}${human}`;
    }

    function methodLabel(method) {
      return {
        agent: 'Agent',
        mpc_dynamic: 'MPC Dynamic',
        mpc_ep: 'MPC EP'
      }[method] || method;
    }

    function syncCommandFromSelections() {
      const input = $('commandInput');
      if (input) input.value = defaultCommand();
    }

    function ensureProgressEvent(events, key) {
      if (!events[key]) {
        events[key] = {
          key,
          label: `Day ${key}`,
          status: 'active',
          day: '—',
          target: 'waiting',
          setpoint: 'waiting',
          score: 'waiting',
          energy: 'waiting',
          strategies: [],
          selectedStrategy: 'waiting',
          appliances: {},
          notes: []
        };
      }
      return events[key];
    }

    function parseLive(logs) {
      const text = (logs || []).join('\n');
      const latest = (pattern) => {
        const matches = [...text.matchAll(pattern)];
        return matches.length ? matches[matches.length - 1] : null;
      };
      const day = latest(/\[(?:AC|MPC) Agent[^\]]*Day(\d+)/g)?.[1] || latest(/--- Day\s+(\d+)\s+start/g)?.[1] || '—';
      const event = latest(/VPP Demand-Response Event\s+(\d+)\/3/g)?.[1] || '—';
      const energy = latest(/\[family\/[^\]]+\]\s+exit=\d+\s+energy=([0-9.]+)kWh\s+vpp_window=([0-9.]+)kWh/g);
      const score = latest(/User score:\s*([0-9.]+)\/5\s+\(([^)]+)\)/g);
      const sp = latest(/\[(?:AC|MPC) Agent[^\]]*\].*?setpoint(?:→|->)([0-9.]+)/g);
      const demand = latest(/shed_target=([0-9.]+)kW\s+\(cap=([0-9.]+)kWh\)/g);
      const dialogue = [];
      const progressEvents = {};
      let currentKey = null;
      let currentDay = '—';
      let candidateEventKey = null;
      for (const raw of logs || []) {
        const line = raw.trim();
        if (!line) continue;
        const dayMatch = line.match(/--- Day\s+(\d+)\s+start/);
        if (dayMatch) {
          currentDay = dayMatch[1];
        }
        const acDayMatch = line.match(/\[(?:AC|MPC) Agent[^\]]*Day(\d+)/);
        if (acDayMatch) {
          currentDay = acDayMatch[1];
          currentKey = currentDay;
          const dayEv = ensureProgressEvent(progressEvents, currentKey);
          dayEv.day = currentDay;
          dayEv.status = 'active';
        }
        const eventMatch = line.match(/VPP Demand-Response Event\s+(\d+)\/3/);
        if (eventMatch) {
          currentKey = currentDay !== '—' ? currentDay : eventMatch[1];
          const ev = ensureProgressEvent(progressEvents, currentKey);
          ev.day = currentDay;
          ev.status = 'active';
          ev.notes.push('VPP event started');
        }
        const candidateHeader = line.match(/\[Strategy Candidates \| VPP event\s+(\d+)\]/);
        if (candidateHeader) {
          candidateEventKey = candidateHeader[1];
          currentKey = candidateEventKey;
          const ev = ensureProgressEvent(progressEvents, currentKey);
          ev.day = currentKey;
        }
        const candidateMatch = line.match(/│\s+\[([ABC])\]\s+(.+?)\s+—\s+(.+)/);
        if (candidateMatch && candidateEventKey) {
          const ev = ensureProgressEvent(progressEvents, candidateEventKey);
          ev.strategies.push(`[${candidateMatch[1]}] ${candidateMatch[2].trim()} — ${candidateMatch[3].trim()}`);
        }
        const candidateApplianceMatch = line.match(/│\s+电器控制:\s+(.+)/);
        if (candidateApplianceMatch && candidateEventKey) {
          const ev = ensureProgressEvent(progressEvents, candidateEventKey);
          if (ev.strategies.length) {
            ev.strategies[ev.strategies.length - 1] += ` | 电器控制: ${candidateApplianceMatch[1].trim()}`;
          } else {
            ev.strategies.push(`电器控制: ${candidateApplianceMatch[1].trim()}`);
          }
        }
        const resultMatch = line.match(/\[VPP Result \| Event\s+(\d+)\/3/);
        if (resultMatch) {
          currentKey = resultMatch[1];
          const ev = ensureProgressEvent(progressEvents, currentKey);
          ev.day = currentKey;
        }
        const humanScoreMatch = line.match(/\[Human Score Selected\s+\|\s+event=(\d+)\]\s+→\s+([0-9.]+)\/5\s+\|\s*(.*)/);
        if (humanScoreMatch) {
          currentKey = humanScoreMatch[1];
          const scoreEv = ensureProgressEvent(progressEvents, currentKey);
          scoreEv.day = currentKey;
          scoreEv.score = `${humanScoreMatch[2]}/5 human`;
          scoreEv.status = 'done';
          const comment = humanScoreMatch[3].trim();
          if (comment && comment !== '—') scoreEv.notes.push(`真人反馈: ${comment}`);
        }
        const webInputMatch = line.match(/\[web human input(?:\s+\|\s+kind=([^ ]+)\s+event=([^\]]+))?\]\s*(.*)/);
        if (webInputMatch) {
          const kind = webInputMatch[1] || 'unknown';
          const inputEvent = webInputMatch[2] || '';
          const value = (webInputMatch[3] || '').trim() || '回车默认';
          const targetKey = inputEvent && inputEvent !== '?' ? inputEvent : currentKey;
          if (targetKey) {
            const inputEv = ensureProgressEvent(progressEvents, targetKey);
            inputEv.day = targetKey;
            if (kind === 'strategy_choice') {
              inputEv.notes.push(`真人策略输入: ${value}`);
            } else if (kind === 'score') {
              inputEv.score = `${value}/5 human`;
              inputEv.notes.push(`真人评分输入: ${value}`);
            } else if (kind === 'score_comment') {
              inputEv.notes.push(`真人评分反馈: ${value}`);
            }
          }
          dialogue.push({type: 'user', label: 'Human Input', text: line});
          continue;
        }
        if (!currentKey) continue;
        const ev = ensureProgressEvent(progressEvents, currentKey);
        const selectedMatch = line.match(/\[Strategy Selected\s+\|\s+event=(\d+)\]\s+→\s+(.+)/);
        if (selectedMatch) {
          const selectedEv = ensureProgressEvent(progressEvents, selectedMatch[1]);
          selectedEv.day = selectedMatch[1];
          selectedEv.selectedStrategy = selectedMatch[2].trim();
        }
        const targetMatch = line.match(/shed_target=([0-9.]+)kW\s+\(cap=([0-9.]+)kWh\)/);
        if (targetMatch) {
          ev.target = `${targetMatch[1]} kW / ${targetMatch[2]} kWh`;
        }
        const acMatch = line.match(/\[(?:AC|MPC) Agent[^\]]*\].*?setpoint(?:→|->)([0-9.]+)/);
        if (acMatch) {
          ev.setpoint = `${acMatch[1]}°C`;
        }
        const scoreMatch = line.match(/User score:\s*([0-9.]+)\/5\s+\(([^)]+)\)/);
        if (scoreMatch) {
          ev.score = `${scoreMatch[1]}/5 ${scoreMatch[2]}`;
          ev.status = 'done';
        }
        const energyMatch = line.match(/\[family\/[^\]]+\]\s+exit=\d+\s+energy=([0-9.]+)kWh\s+vpp_window=([0-9.]+)kWh/);
        if (energyMatch) {
          ev.energy = `${energyMatch[1]} kWh total / ${energyMatch[2]} kWh VPP`;
        }
        const shiftMatch = line.match(/\[Appliance\]\s+shift\s+(\w+)\s+day=(\d+)\s+hod=([0-9.]+)\s+->\s+(\w+)/);
        if (shiftMatch) {
          const dayKey = String(Number(shiftMatch[2]) + 1);
          const applianceEv = ensureProgressEvent(progressEvents, dayKey);
          applianceEv.day = dayKey;
          applianceEv.appliances[shiftMatch[1]] = `排程@${Number(shiftMatch[3]).toFixed(1)} (${shiftMatch[4]})`;
        }
        const skipMatch = line.match(/\[Appliance\]\s+skip\s+(\w+)\s+day=(\d+)\s+->\s+(\w+)/);
        if (skipMatch) {
          const dayKey = String(Number(skipMatch[2]) + 1);
          const applianceEv = ensureProgressEvent(progressEvents, dayKey);
          applianceEv.day = dayKey;
          applianceEv.appliances[skipMatch[1]] = `跳过 (${skipMatch[3]})`;
        }
        const whMatch = line.match(/\[Appliance\]\s+water_heater preheat schedule:\s+start=([^ ]+)\s+end=([^ ]+)\s+temp=([^ ]+)\s+->\s+(\w+)/);
        if (whMatch) {
          const whEv = ensureProgressEvent(progressEvents, currentKey);
          whEv.appliances.water_heater = `预热 ${whMatch[1]}-${whMatch[2]} @ ${whMatch[3]}°C (${whMatch[4]})`;
        }
        const evModeMatch = line.match(/\[Appliance\]\s+ev mode=([^ ]+)\s+->\s+(\w+)/);
        if (evModeMatch) {
          ev.appliances.ev = `模式 ${evModeMatch[1]} (${evModeMatch[2]})`;
        }
        const evWindowMatch = line.match(/\[Appliance\]\s+ev charge_window=([^ ]+)\s+->\s+(\w+)/);
        if (evWindowMatch) {
          ev.appliances.ev = `充电窗口 ${evWindowMatch[1]} (${evWindowMatch[2]})`;
        }
        if (line.includes('[VPP Grid Agent]')) {
          dialogue.push({type: 'grid', label: 'Grid VPP', text: line});
          ev.notes.push(line.replace('[VPP Grid Agent]', '').trim());
        } else if (line.includes('[Strategy Selected')) {
          dialogue.push({type: 'user', label: 'User Strategy', text: line});
          ev.notes.push(line);
        } else if (line.includes('[Human Score Selected')) {
          dialogue.push({type: 'result', label: 'Human Score', text: line});
          ev.notes.push(line);
        } else if (line.includes('[AC Agent') || line.includes('[MPC Agent')) {
          dialogue.push({type: 'agent', label: 'Agent', text: line});
          ev.notes.push(line);
        } else if (line.includes('[VPP Result')) {
          dialogue.push({type: 'result', label: 'Role-play Score', text: line});
          ev.notes.push(line);
        } else if (line.includes('[LLM stats')) {
          dialogue.push({type: 'result', label: 'LLM Stats', text: line});
        }
        if (ev.notes.length > 5) ev.notes = ev.notes.slice(-5);
      }
      const progress = Object.values(progressEvents)
        .sort((a, b) => Number(a.key) - Number(b.key));
      return {
        day,
        event,
        energy: energy ? `${energy[1]} kWh` : 'running',
        vpp: energy ? `${energy[2]} kWh` : 'running',
        score: score ? `${score[1]}/5 ${score[2]}` : 'waiting',
        sp: sp ? `${sp[1]}°C` : 'waiting',
        demand: demand ? `${demand[1]} kW` : 'waiting',
        cap: demand ? `${demand[2]} kWh` : 'waiting',
        dialogue,
        progress
      };
    }

    function renderOps(events) {
      const methods = ['agent', 'mpc_dynamic', 'mpc_ep'];
      const personaOptions = state.personas.map(p => `
        <option value="${p.id}" ${state.selectedPersona === p.id ? 'selected' : ''}>
          ${p.label} · ${p.name}
        </option>`).join('');
      const userTypeField = state.userMode === 'human' ? `
        <div class="field-row">
          <label for="humanNameInput">2. 用户类型</label>
          <input id="humanNameInput" value="${state.humanName}" placeholder="输入真人用户名称，例如 alice">
        </div>` : `
        <div class="field-row">
          <label for="personaSelect">2. 用户类型</label>
          <select id="personaSelect">${personaOptions}</select>
        </div>`;
      const humanInputPanel = state.userMode === 'human' ? `
        <div class="human-dialogue">
          <h3>Human-in-loop 输入</h3>
          <div class="sub">根据终端当前提示输入：策略阶段填 A/B/C，评分阶段填 1-5，反馈阶段可填一句话；直接发送空内容等于回车默认。</div>
          <div class="human-custom">
            <input id="humanCustomInput" placeholder="输入 A/B/C、1-5、反馈文字，或留空回车">
            <button id="sendHumanCustom">回车</button>
          </div>
        </div>` : '';
      return `
        <div class="grid">
          <section class="panel">
            <h2>实时运行</h2>
            <div class="sub">先选用户类别，再选用户类型，最后选控制方法。命令行输入保留作 fallback。</div>
            <h3>1. 用户类别</h3>
            <div class="preset-grid">
              <button class="${state.userMode === 'roleplay' ? '' : 'secondary'}" data-user-mode="roleplay">Role-play LLM</button>
              <button class="${state.userMode === 'human' ? '' : 'secondary'}" data-user-mode="human">Human</button>
            </div>
            ${userTypeField}
            <h3>3. 方法</h3>
            <div class="preset-grid">
              ${methods.map(m => `<button class="${state.selectedMethod === m ? '' : 'secondary'}" data-method="${m}">${methodLabel(m)}</button>`).join('')}
            </div>
            <div class="command-row">
              <input id="commandInput" value="${defaultCommand()}">
              <button id="startRun">运行${state.userMode === 'human' ? 'Human' : 'Role-play LLM'}</button>
            </div>
            <div class="live-grid" id="liveMetrics">
              <div class="live-card"><span>Day/Event</span><strong>—</strong></div>
              <div class="live-card"><span>Setpoint</span><strong>waiting</strong></div>
              <div class="live-card"><span>VPP target</span><strong>waiting</strong></div>
              <div class="live-card"><span>User score</span><strong>waiting</strong></div>
            </div>
            <div style="height:10px"></div>
            <h3>逐步可视化</h3>
            <div class="progress-viz" id="progressViz"><div class="sub">运行时会按事件逐步生成可视化卡片。</div></div>
            <div style="height:10px"></div>
            <h3>实时日志摘要 / 对话结果</h3>
            <div class="dialogue-feed" id="dialogueFeed"><div class="sub">运行后会实时显示 Grid、Agent、用户选择、role-play 评分和 LLM stats。</div></div>
            <div id="finishedSummary"></div>
            <div style="height:10px"></div>
            <div class="terminal" id="runLog">等待运行命令...</div>
            ${humanInputPanel}
          </section>
        </div>`;
    }

    function renderRunPage() {
      $('app').innerHTML = `
        <div class="topbar">
          <div class="title-block">
            <h1>新运行控制台</h1>
            <div class="sub">运行新 benchmark 时，这里只显示当前 job 的实时日志和结果，不混入左侧历史结果。</div>
          </div>
        </div>
        ${renderOps()}`;
      bindRunControls();
    }

    function renderHistoryPage() {
      const d = state.data;
      const events = d.vpp_event_log || [];
      $('app').innerHTML = `
        <div class="topbar">
          <div class="title-block">
            <h1>${d.scenario || 'EnergyBridge Run'}</h1>
            <div class="pill-row">
              <span class="pill">${d.method || 'unknown'}</span>
              <span class="pill">${d.weather || 'unknown city'}</span>
              <span class="pill">exit ${d.exit_code}</span>
              <span class="pill">${d.output_dir || ''}</span>
            </div>
          </div>
        </div>
        <div class="grid metrics">
          <div class="panel metric"><div class="label">用户评分</div><div class="value">${fmt(d.user_pref_score, 1)}/5</div></div>
          <div class="panel metric"><div class="label">总能耗</div><div class="value">${fmt(d.energy_kwh_total, 1)} kWh</div></div>
          <div class="panel metric"><div class="label">电器错峰</div><div class="value">${pct(d.appliance_shift_success_rate)}</div></div>
          <div class="panel metric"><div class="label">VPP削减达成</div><div class="value">${fmt(d.vpp_demand_achievement_ratio, 2)}x</div></div>
        </div>
        <div class="grid event-layout">
          <div class="events">${events.map(renderEvent).join('')}</div>
        </div>`;
    }

    function bindRunControls() {
      $('startRun').onclick = startRun;
      document.querySelectorAll('[data-method]').forEach(btn => {
        btn.onclick = () => {
          state.selectedMethod = btn.dataset.method;
          render();
        };
      });
      const personaSelect = $('personaSelect');
      if (personaSelect) {
        personaSelect.onchange = (e) => {
          state.selectedPersona = e.target.value;
          syncCommandFromSelections();
        };
      }
      const humanNameInput = $('humanNameInput');
      if (humanNameInput) {
        humanNameInput.oninput = (e) => {
          state.humanName = e.target.value;
          syncCommandFromSelections();
        };
      }
      document.querySelectorAll('[data-user-mode]').forEach(btn => {
        btn.onclick = () => {
          state.userMode = btn.dataset.userMode;
          render();
        };
      });
      document.querySelectorAll('[data-human-input]').forEach(btn => {
        btn.onclick = () => sendHumanInput(btn.dataset.humanInput, btn.dataset.humanKind || '');
      });
      const humanCustom = $('sendHumanCustom');
      if (humanCustom) {
        humanCustom.onclick = () => {
          const input = $('humanCustomInput');
          sendHumanInput(input.value.trim(), 'custom');
          input.value = '';
        };
        const input = $('humanCustomInput');
        if (input) {
          input.onkeydown = (event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              sendHumanInput(input.value.trim(), 'custom');
              input.value = '';
            }
          };
        }
      }
    }

    function render() {
      const shell = $('shell');
      shell.classList.toggle('sidebar-collapsed', !state.sidebarOpen);
      $('sidebarToggle').textContent = state.sidebarOpen ? '隐藏历史' : '打开历史';
      $('newRunButton').onclick = () => {
        state.view = 'run';
        state.active = null;
        state.data = null;
        $('runSelect').value = '';
        renderRunButtons();
        render();
      };
      $('sidebarToggle').onclick = () => {
        state.sidebarOpen = !state.sidebarOpen;
        render();
      };
      if (state.view === 'history' && state.data) renderHistoryPage();
      else renderRunPage();
    }

    async function startRun() {
      const command = $('commandInput').value.trim();
      const res = await api('/api/start_run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command})
      });
      state.jobId = res.job_id;
      $('runLog').textContent = `job ${state.jobId} queued...\n`;
      if (state.jobPoll) clearInterval(state.jobPoll);
      state.jobPoll = setInterval(pollJob, 1500);
      pollJob();
    }

    async function pollJob() {
      if (!state.jobId) return;
      const job = await api(`/api/job?id=${encodeURIComponent(state.jobId)}`);
      const log = $('runLog');
      if (log) {
        log.textContent = `[${job.status}] ${job.command}\n\n${(job.logs || []).join('\n')}`;
        log.scrollTop = log.scrollHeight;
      }
      renderLiveJob(job);
      if (job.status === 'succeeded' || job.status === 'failed') {
        clearInterval(state.jobPoll);
        state.jobPoll = null;
        await loadRuns();
        const refreshedLog = $('runLog');
        if (refreshedLog) {
          refreshedLog.textContent = `[${job.status}] ${job.command}\n\n${(job.logs || []).join('\n')}`;
          refreshedLog.scrollTop = refreshedLog.scrollHeight;
        }
        renderLiveJob(job);
      }
    }

    function renderLiveJob(job) {
      const live = parseLive(job.logs || []);
      const metrics = $('liveMetrics');
      if (metrics) {
        metrics.innerHTML = `
          <div class="live-card"><span>Day/Event</span><strong>D${live.day} / E${live.event}</strong></div>
          <div class="live-card"><span>Setpoint</span><strong>${live.sp}</strong></div>
          <div class="live-card"><span>VPP target</span><strong>${live.demand}</strong></div>
          <div class="live-card"><span>User score</span><strong>${live.score}</strong></div>
          <div class="live-card"><span>Total energy</span><strong>${live.energy}</strong></div>
          <div class="live-card"><span>VPP energy</span><strong>${live.vpp}</strong></div>
          <div class="live-card"><span>Energy cap</span><strong>${live.cap}</strong></div>
          <div class="live-card"><span>Status</span><strong>${job.status}</strong></div>`;
      }
      const feed = $('dialogueFeed');
      if (feed) {
        feed.innerHTML = live.dialogue.length
          ? live.dialogue.map(item => `<div class="dialogue-item ${item.type}"><small>${item.label}</small>${item.text}</div>`).join('')
          : '<div class="sub">运行后会显示 Grid、Agent、用户选择和 role-play 评分。</div>';
        feed.scrollTop = feed.scrollHeight;
      }
      const progressViz = $('progressViz');
      if (progressViz) {
        progressViz.innerHTML = live.progress.length
          ? live.progress.map(ev => `
            <div class="progress-card ${ev.status}">
              <h4>${ev.label}</h4>
              <div class="progress-row"><span>Day</span><strong>${ev.day}</strong></div>
              <div class="progress-row"><span>VPP目标</span><strong>${ev.target}</strong></div>
              <div class="progress-row"><span>空调设定</span><strong>${ev.setpoint}</strong></div>
              <div class="progress-row"><span>用户评分</span><strong>${ev.score}</strong></div>
              <div class="progress-row"><span>能耗</span><strong>${ev.energy}</strong></div>
              <div class="progress-row"><span>选中策略</span><strong>${ev.selectedStrategy}</strong></div>
              <div class="progress-section">
                <strong>候选策略</strong>
                <div class="progress-log">
                  ${ev.strategies.length ? ev.strategies.map(item => `<div class="progress-chip">${item}</div>`).join('') : '<div class="progress-chip">waiting</div>'}
                </div>
              </div>
              <div class="progress-section">
                <strong>电器排程</strong>
                <div class="appliance-grid">
                  ${Object.keys(ev.appliances).length
                    ? Object.entries(ev.appliances).map(([name, text]) => `<div class="appliance-pill">${name}: ${text}</div>`).join('')
                    : '<div class="appliance-pill">waiting</div>'}
                </div>
              </div>
              <div class="progress-log">
                ${ev.notes.map(note => `<div class="progress-chip">${note}</div>`).join('')}
              </div>
            </div>`).join('')
          : '<div class="sub">运行时会按事件逐步生成可视化卡片。</div>';
      }
      const summary = $('finishedSummary');
      if (summary) {
        if (job.run_summary_text) {
          summary.innerHTML = `<h3>完整用户日志 / run_summary.txt</h3><div class="summary-log">${job.run_summary_text}</div>`;
        } else {
          summary.innerHTML = '';
        }
      }
    }

    async function sendHumanInput(text, kind = '') {
      const log = $('runLog');
      if (!state.jobId) {
        if (log) log.textContent += '\n[web] 还没有正在运行的 human job。';
        return;
      }
      await api('/api/job_input', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({job_id: state.jobId, text, kind})
      });
      if (log) {
        log.textContent += `\n[web] sent human input${kind ? ` (${kind})` : ''}: ${text || '(default enter)'}`;
        log.scrollTop = log.scrollHeight;
      }
    }

    (async function boot() {
      await loadPersonas();
      await loadRuns();
    })().catch(err => $('app').textContent = `加载失败：${err.message}`);
  </script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    results_dir: Path = DEFAULT_RESULTS_DIR

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/api/runs", "/api/run"}:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(INDEX_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/runs":
            self._send_json(self._list_runs())
            return
        if parsed.path == "/api/personas":
            self._send_json(_list_personas())
            return
        if parsed.path == "/api/run":
            run_id = parse_qs(parsed.query).get("id", [""])[0]
            run_path = self._resolve_run(run_id)
            if run_path is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown run")
                return
            self._send_json(json.loads((run_path / "benchmark_result.json").read_text(encoding="utf-8")))
            return
        if parsed.path == "/api/job":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown job")
                return
            self._send_json(job)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/start_run":
            self._handle_start_run()
            return
        if parsed.path == "/api/job_input":
            self._handle_job_input()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_start_run(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            command = str(payload.get("command", "")).strip()
            argv = _validate_run_command(command)
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "command": command,
                    "argv": argv,
                    "logs": [],
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            thread = threading.Thread(target=_run_command_job, args=(job_id, argv), daemon=True)
            thread.start()
            self._send_json({"ok": True, "job_id": job_id})
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_job_input(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            job_id = str(payload.get("job_id", ""))
            text = str(payload.get("text", ""))
            requested_kind = str(payload.get("kind", ""))
            if "\n" in text or "\r" in text:
                raise ValueError("Input must be a single line")
            with JOBS_LOCK:
                proc = JOB_PROCS.get(job_id)
                logs = list(JOBS.get(job_id, {}).get("logs", []))
            if proc is None or proc.stdin is None or proc.poll() is not None:
                self._send_error(HTTPStatus.NOT_FOUND, "Job is not accepting input")
                return
            kind, event_id = _infer_human_input_context(logs, requested_kind=requested_kind)
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            _append_job_log(job_id, f"[web human input | kind={kind} event={event_id or '?'}] {text}")
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, fmt: str, *args) -> None:
        print(f"[dashboard] {self.address_string()} - {fmt % args}")

    def _list_runs(self) -> list[dict]:
        runs = []
        for path in sorted(self.results_dir.glob("*/*/benchmark_result.json"), reverse=True):
            run_dir = path.parent
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rel = run_dir.relative_to(self.results_dir).as_posix()
            runs.append({
                "id": rel,
                "label": rel,
                "date": rel.split("/", 1)[0] if "/" in rel else "",
                "persona": run_dir.name.split("_", 2)[0],
                "method": data.get("method", ""),
                "city": data.get("weather", ""),
                "score": data.get("user_pref_score"),
            })
        return runs

    def _resolve_run(self, run_id: str) -> Path | None:
        if not run_id or ".." in Path(run_id).parts:
            return None
        run_path = (self.results_dir / run_id).resolve()
        try:
            run_path.relative_to(self.results_dir.resolve())
        except ValueError:
            return None
        if not (run_path / "benchmark_result.json").is_file():
            return None
        return run_path

    def _send_json(self, data: object) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, content_type: str) -> None:
        raw = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        raw = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve EnergyBridge benchmark dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args()

    DashboardHandler.results_dir = args.results_dir.resolve()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"EnergyBridge dashboard: http://{args.host}:{args.port}")
    print(f"Reading results from: {DashboardHandler.results_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
