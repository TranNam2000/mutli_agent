"""
Trend report — aggregates metrics across all sessions for a project.

Usage:
  from reporting.trend_report import build_trend_report
  build_trend_report(sessions_dir=Path(".multi_agent/sessions"),
                     output_path=Path("trend.html"))

Reads all sessions/<id>/99_SUMMARY.md + REPORT.html fragments to extract:
  - Agent score over time (sparkline per agent)
  - Token usage trend
  - Pass rate trend
  - Skill usage heatmap across sessions
  - Feedback-report severity over time
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SessionSummary:
    session_id:    str
    timestamp:     datetime
    agent_scores:  dict[str, float] = field(default_factory=dict)
    skill_usage:   list[tuple[str, str]] = field(default_factory=list)  # (agent, skill)
    tokens_used:   int = 0
    token_budget:  int = 0
    blockers:      int = 0
    test_pass:     int = 0
    test_fail:     int = 0


def _parse_session_folder(folder: Path) -> SessionSummary | None:
    """Extract metrics from a single session folder."""
    sid = folder.name
    try:
        ts = datetime.strptime(sid.split("_")[0] + "_" + sid.split("_")[1],
                               "%Y%m%d_%H%M%S")
    except (ValueError, IndexError):
        ts = datetime.now()

    summary = SessionSummary(session_id=sid, timestamp=ts)

    # Try to find the SUMMARY.md for agent scores
    summary_md = folder / "99_SUMMARY.md"
    if not summary_md.exists():
        # Legacy naming
        for candidate in folder.glob("*SUMMARY*"):
            summary_md = candidate
            break

    # Try the report JSON (if we add one later) else parse from text
    for md_file in folder.glob("*.md"):
        content = md_file.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"score[:\s]+(\d+(?:\.\d+)?)/10.*?([\w ]+)", content, re.IGNORECASE):
            # weak fallback — not reliable
            pass

    # Parse critic reviews from conversations if present
    conv = folder / "conversations.md"
    for md in [summary_md, conv]:
        if not md or not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="ignore")
        # Generic score extraction
        for m in re.finditer(
            r"\[(BA|Design|TechLead|Dev|QA|Test)\].*?score[:\s]*(\d+(?:\.\d+)?)",
            text, re.IGNORECASE,
        ):
            agent = m.group(1).lower()
            score = float(m.group(2))
            # keep latest score per agent
            summary.agent_scores[agent] = score

    # Parse tokens used from text if present
    for md in folder.glob("*.md"):
        text = md.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"Used\s*:?\s*([\d,]+)\s*tokens", text)
        if m:
            try:
                summary.tokens_used = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
            break

    return summary


def scan_all_sessions(sessions_dir: Path) -> list[SessionSummary]:
    """Walk sessions/ folder, parse each."""
    if not sessions_dir.exists():
        return []
    summaries: list[SessionSummary] = []
    for folder in sorted(sessions_dir.iterdir()):
        if not folder.is_dir():
            continue
        s = _parse_session_folder(folder)
        if s:
            summaries.append(s)
    return summaries


def _sparkline_svg(values: list[float], width: int = 200, height: int = 40,
                    color: str = "#3b82f6") -> str:
    if not values or len(values) < 2:
        return '<span class="muted">—</span>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / max(len(values) - 1, 1)
    pts = [(i * step, height - ((v - lo) / span) * (height - 4) - 2)
           for i, v in enumerate(values)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{poly}"/>'
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" fill="{color}"/>'
        f'</svg>'
    )


def _bar_svg(values: list[float], labels: list[str],
              max_val: float | None = None, width: int = 500) -> str:
    if not values:
        return ""
    mx = max_val or max(values) or 1
    bar_h = 24
    total_h = bar_h * len(values) + 10
    rows = []
    for i, (v, lbl) in enumerate(zip(values, labels)):
        w = (v / mx) * (width - 120)
        y = i * bar_h + 5
        rows.append(
            f'<text x="0" y="{y + 16}" font-size="11" fill="#475569">{lbl[:14]}</text>'
            f'<rect x="120" y="{y + 4}" width="{w:.1f}" height="16" fill="#3b82f6" rx="2"/>'
            f'<text x="{120 + w + 4:.1f}" y="{y + 16}" font-size="10" fill="#64748b">{v:.1f}</text>'
        )
    return (
        f'<svg width="{width}" height="{total_h}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(rows) + '</svg>'
    )


def build_trend_report(sessions_dir: Path, output_path: Path,
                       project_name: str = "") -> Path:
    """Generate trend HTML report."""
    sessions = scan_all_sessions(sessions_dir)
    if not sessions:
        html = f"<p>No sessions found in {sessions_dir}</p>"
        output_path.write_text(html, encoding="utf-8")
        return output_path

    # Build trends per agent
    agent_trend: dict[str, list[float]] = {}
    for s in sessions:
        for agent, score in s.agent_scores.items():
            agent_trend.setdefault(agent, []).append(score)

    token_trend = [s.tokens_used for s in sessions if s.tokens_used > 0]

    # Latest session stats
    latest = sessions[-1]
    n_sessions = len(sessions)

    # ── Compose HTML ─────────────────────────────────────────────────────────
    trend_rows = ""
    for agent in sorted(agent_trend.keys()):
        scores = agent_trend[agent]
        spark = _sparkline_svg(scores, width=200, height=32)
        trend = scores[-1] - scores[0] if len(scores) > 1 else 0
        icon = "↑" if trend > 0 else ("↓" if trend < 0 else "→")
        color = "#22c55e" if trend >= 0 else "#ef4444"
        trend_rows += (
            f'<tr><td><b>{agent.upper()}</b></td>'
            f'<td>{spark}</td>'
            f'<td>{scores[-1]:.1f}/10</td>'
            f'<td style="color:{color}">{icon} {trend:+.1f}</td>'
            f'<td>{len(scores)} runs</td></tr>'
        )

    token_chart = _sparkline_svg(
        [float(t) for t in token_trend], width=400, height=60,
        color="#a855f7",
    ) if token_trend else "<span class='muted'>—</span>"

    sessions_table = ""
    for s in reversed(sessions[-10:]):
        scores_summary = " · ".join(f"{k[:2].upper()}:{v:.0f}"
                                      for k, v in sorted(s.agent_scores.items()))
        sessions_table += (
            f'<tr><td><code>{s.session_id}</code></td>'
            f'<td>{s.timestamp.strftime("%Y-%m-%d %H:%M")}</td>'
            f'<td>{scores_summary or "—"}</td>'
            f'<td>{s.tokens_used:,}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Multi-Agent Trend Report — {project_name or sessions_dir.parent.name}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 1100px; margin: 0 auto;
         padding: 24px; color: #0f172a; background: #f8fafc; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 8px; }}
  h2 {{ font-size: 1.15rem; margin: 28px 0 10px; border-bottom: 1px solid #e2e8f0;
        padding-bottom: 6px; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
           padding: 16px 20px; margin-bottom: 16px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .stat {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
           padding: 14px; text-align: center; }}
  .stat .num {{ font-size: 1.5rem; font-weight: 700; }}
  .stat .lbl {{ color: #64748b; font-size: 0.8rem; text-transform: uppercase; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
  .muted {{ color: #94a3b8; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; }}
</style>
</head><body>
<h1>📈 Trend Report — {project_name or sessions_dir.parent.name}</h1>
<p class="muted">{n_sessions} sessions · Latest: {latest.session_id}
 · Generated {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>

<div class="grid">
  <div class="stat"><div class="num">{n_sessions}</div><div class="lbl">Sessions</div></div>
  <div class="stat"><div class="num">{len(agent_trend)}</div><div class="lbl">Agents tracked</div></div>
  <div class="stat"><div class="num">{sum(token_trend):,}</div><div class="lbl">Total tokens</div></div>
  <div class="stat"><div class="num">{sum(token_trend) // max(n_sessions, 1):,}</div><div class="lbl">Avg / session</div></div>
</div>

<h2>🧠 Agent Score Trends</h2>
<div class="card">
  <table>
    <thead><tr><th>Agent</th><th>Trend</th><th>Latest</th><th>Δ</th><th>Data</th></tr></thead>
    <tbody>{trend_rows or '<tr><td colspan="5" class="muted">No scores tracked</td></tr>'}</tbody>
  </table>
</div>

<h2>💰 Token Usage Over Time</h2>
<div class="card">{token_chart}</div>

<h2>🗂️ Recent Sessions (last 10)</h2>
<div class="card">
  <table>
    <thead><tr><th>Session</th><th>Date</th><th>Scores</th><th>Tokens</th></tr></thead>
    <tbody>{sessions_table}</tbody>
  </table>
</div>

</body></html>"""
    output_path.write_text(html, encoding="utf-8")
    return output_path
