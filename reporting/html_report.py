"""
HTML dashboard generator — single-file report with:
  - Agent scores + sparkline trends
  - Skill usage heatmap
  - Maestro / Patrol test results with embedded screenshots
  - Token usage breakdown
  - Auto-feedback items + suggested fixes

Output: outputs/<project>/<session>_REPORT.html — open in browser.
"""
from __future__ import annotations
import base64
import json
from datetime import datetime
from pathlib import Path


def _b64_img(path: str | Path) -> str:
    """Embed image as base64 data URI so the report is self-contained."""
    p = Path(path)
    if not p.exists():
        return ""
    ext = p.suffix.lstrip(".").lower() or "png"
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/{ext};base64,{data}"


def _score_color(score: float) -> str:
    if score >= 8: return "#22c55e"   # green
    if score >= 7: return "#84cc16"   # lime
    if score >= 5: return "#eab308"   # yellow
    return "#ef4444"                   # red


def _sparkline_svg(values: list[float], width: int = 160, height: int = 30) -> str:
    """Tiny inline SVG sparkline."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1
    step = width / max(len(values) - 1, 1)
    points = [
        (i * step, height - ((v - lo) / span) * (height - 4) - 2)
        for i, v in enumerate(values)
    ]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<polyline fill="none" stroke="#3b82f6" stroke-width="2" points="{poly}"/>'
            f'<circle cx="{points[-1][0]:.1f}" cy="{points[-1][1]:.1f}" r="3" fill="#3b82f6"/>'
            f'</svg>')


def _severity_badge(sev: str) -> str:
    colors = {
        "BLOCKER":  "#dc2626", "CRITICAL": "#ea580c",
        "MAJOR":    "#eab308", "MINOR":    "#3b82f6",
    }
    bg = colors.get(sev, "#6b7280")
    return f'<span class="badge" style="background:{bg}">{sev}</span>'


def build_report(
    *,
    session_id: str,
    project_name: str,
    profile: str,
    critic_reviews: list[dict],
    skill_usage: list[dict],
    token_summary: dict,
    patrol_result=None,
    maestro_result=None,
    feedback_report=None,
    pipeline_steps: list[str],
    out_dir: Path,
) -> Path:
    # ── Compute aggregates ────────────────────────────────────────────────────
    agent_trends: dict[str, list[float]] = {}
    for r in critic_reviews:
        agent_trends.setdefault(r["agent_key"], []).append(r.get("score", 0))

    skill_rows = ""
    skill_counts: dict[tuple, int] = {}
    for u in skill_usage:
        key = (u.get("step", "?"), u.get("skill", "?"), u.get("scope", "?"))
        skill_counts[key] = skill_counts.get(key, 0) + 1
    for (step, skill, scope), count in sorted(skill_counts.items()):
        skill_rows += (f'<tr><td>{step}</td><td><code>{skill}</code></td>'
                       f'<td>{scope}</td><td>{count}</td></tr>')

    agent_rows = ""
    for key, scores in agent_trends.items():
        avg = sum(scores) / len(scores)
        color = _score_color(avg)
        spark = _sparkline_svg(scores)
        agent_rows += (
            f'<tr>'
            f'<td><b>{key.upper()}</b></td>'
            f'<td>{spark}</td>'
            f'<td style="color:{color};font-weight:600">{avg:.1f}</td>'
            f'<td>{len(scores)}</td>'
            f'<td>{scores[-1]:.0f}/10</td>'
            f'</tr>'
        )

    # ── Token breakdown ──────────────────────────────────────────────────────
    token_rows = ""
    for agent, tok in sorted(token_summary.get("by_agent", {}).items(), key=lambda x: -x[1]):
        pct = tok / max(token_summary.get("used", 1), 1) * 100
        token_rows += (
            f'<tr><td>{agent}</td>'
            f'<td style="text-align:right">{tok:,}</td>'
            f'<td><div class="bar"><div style="width:{pct:.1f}%"></div></div>{pct:.1f}%</td></tr>'
        )

    # ── Patrol block ─────────────────────────────────────────────────────────
    patrol_block = "<p class='muted'>No Patrol run</p>"
    if patrol_result is not None:
        ps = []
        for r in [getattr(patrol_result, "android", None), getattr(patrol_result, "ios", None)]:
            if r:
                icon = "✅" if r.success else "❌"
                ps.append(f"<div><b>{icon} {r.platform.upper()}</b> — {r.passed} passed, {r.failed} failed ({r.duration_s:.1f}s)</div>")
                for f in r.failures[:5]:
                    ps.append(f'<div class="muted">✗ {f}</div>')
        patrol_block = "\n".join(ps)

    # ── Maestro block ────────────────────────────────────────────────────────
    maestro_block = "<p class='muted'>No Maestro run</p>"
    if maestro_result is not None and getattr(maestro_result, "flows", None):
        rows = []
        for f in maestro_result.flows:
            icon = "✅" if f.passed else "❌"
            shots = ""
            for s in f.screenshots[:3]:
                uri = _b64_img(s)
                if uri:
                    shots += f'<img src="{uri}" class="thumb"/>'
            rows.append(
                f'<tr><td>{icon}</td><td>{f.flow_name}</td>'
                f'<td>{f.steps_total - f.steps_failed}/{f.steps_total}</td>'
                f'<td>{f.duration_s:.1f}s</td><td>{shots}</td></tr>'
            )
        maestro_block = (
            '<table class="data"><thead><tr><th></th><th>Flow</th><th>Steps</th><th>Duration</th><th>Screenshots</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )

    # ── Feedback block ───────────────────────────────────────────────────────
    feedback_block = "<p class='muted'>No auto-feedback collected</p>"
    if feedback_report is not None and getattr(feedback_report, "items", None):
        rows = []
        for i in feedback_report.items[:20]:
            shot = ""
            if i.screenshot:
                uri = _b64_img(i.screenshot)
                if uri:
                    shot = f'<img src="{uri}" class="thumb"/>'
            rows.append(
                f'<tr><td>{_severity_badge(i.severity)}</td>'
                f'<td>{i.type}</td>'
                f'<td>{i.description[:200]}</td>'
                f'<td>{shot}</td></tr>'
            )
        feedback_block = (
            '<table class="data"><thead><tr><th>Severity</th><th>Type</th><th>Description</th><th>Evidence</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            f'<p class="muted">Build: {"✅ OK" if feedback_report.build_succeeded else "❌ FAILED"}  |  '
            f'Pass rate est.: {feedback_report.pass_rate*100:.1f}%</p>'
        )

    # ── Pipeline flow ────────────────────────────────────────────────────────
    pipeline_html = " → ".join(
        f'<span class="step done">{s}</span>' if s in agent_trends
        else f'<span class="step">{s}</span>'
        for s in pipeline_steps
    )

    # ── Assemble HTML ────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pipeline Report — {project_name} / {session_id}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 1180px; margin: 0 auto; padding: 24px;
         color: #0f172a; background: #f8fafc; }}
  h1, h2 {{ margin: 0 0 8px; }}
  h1 {{ font-size: 1.6rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 28px; color: #1e293b; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }}
  .meta {{ color: #64748b; font-size: 0.9rem; margin-bottom: 20px; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
           padding: 18px 20px; margin-bottom: 18px; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .grid4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .stat {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
           padding: 14px; text-align: center; }}
  .stat .num {{ font-size: 1.6rem; font-weight: 700; color: #1e293b; }}
  .stat .lbl {{ color: #64748b; font-size: 0.8rem; text-transform: uppercase; letter-spacing: .06em; }}
  table.data {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  table.data th, table.data td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }}
  table.data th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
  code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
  .bar {{ display: inline-block; width: 120px; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; vertical-align: middle; margin-right: 6px; }}
  .bar > div {{ height: 100%; background: #3b82f6; }}
  .thumb {{ width: 80px; height: 80px; object-fit: cover; border-radius: 6px; margin: 2px; border: 1px solid #e2e8f0; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; color: #fff; font-size: 0.75rem; font-weight: 600; }}
  .step {{ display: inline-block; padding: 4px 10px; border-radius: 6px; background: #e2e8f0; color: #64748b; font-size: 0.85rem; margin: 2px; }}
  .step.done {{ background: #dcfce7; color: #166534; }}
  .muted {{ color: #94a3b8; font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>🤖 Multi-Agent Pipeline Report</h1>
<div class="meta">
  Project: <b>{project_name}</b> · Session: <code>{session_id}</code> ·
  Profile: <code>{profile}</code> · Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
</div>

<div class="card">
  <b>Pipeline:</b> {pipeline_html}
</div>

<div class="grid4">
  <div class="stat"><div class="num">{len(agent_trends)}</div><div class="lbl">Agents run</div></div>
  <div class="stat"><div class="num">{len(skill_usage)}</div><div class="lbl">Skill activations</div></div>
  <div class="stat"><div class="num">{token_summary.get("used", 0):,}</div><div class="lbl">Tokens used</div></div>
  <div class="stat"><div class="num">{(feedback_report.pass_rate*100 if feedback_report else 0):.0f}%</div><div class="lbl">Product pass rate</div></div>
</div>

<h2>🧠 Agent Scores</h2>
<div class="card">
  <table class="data">
    <thead><tr><th>Agent</th><th>Trend</th><th>Avg</th><th>Runs</th><th>Latest</th></tr></thead>
    <tbody>{agent_rows or '<tr><td colspan="5" class="muted">No critic reviews recorded</td></tr>'}</tbody>
  </table>
</div>

<h2>🎯 Skill Usage</h2>
<div class="card">
  <table class="data">
    <thead><tr><th>Step</th><th>Skill</th><th>Scope</th><th>Count</th></tr></thead>
    <tbody>{skill_rows or '<tr><td colspan="4" class="muted">No skill usage logged</td></tr>'}</tbody>
  </table>
</div>

<div class="grid2">
  <div>
    <h2>🧪 Patrol Tests</h2>
    <div class="card">{patrol_block}</div>
  </div>
  <div>
    <h2>🌊 Maestro E2E</h2>
    <div class="card">{maestro_block}</div>
  </div>
</div>

<h2>💬 Auto Feedback</h2>
<div class="card">{feedback_block}</div>

<h2>💰 Token Usage</h2>
<div class="card">
  <table class="data">
    <thead><tr><th>Agent</th><th>Tokens</th><th>Share</th></tr></thead>
    <tbody>{token_rows or '<tr><td colspan="3" class="muted">No token usage</td></tr>'}</tbody>
  </table>
  <p class="muted">Budget: {token_summary.get("budget", 0):,} · Used: {token_summary.get("used", 0):,} ({token_summary.get("pct", 0):.1f}%)</p>
</div>

</body></html>
"""

    out_path = out_dir / f"{session_id}_REPORT.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
