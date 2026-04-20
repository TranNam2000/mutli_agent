"""Pipeline reports: HTML dashboard, markdown summaries, trend analysis."""
from .html_report import build_report
from .trend_report import build_trend_report, scan_all_sessions, SessionSummary

__all__ = ["build_report", "build_trend_report", "scan_all_sessions", "SessionSummary"]
