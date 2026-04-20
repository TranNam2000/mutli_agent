"""
Detect Claude subscription plan and map to token budget for pipeline sessions.

Priority:
  1. `claude auth status` → subscriptionType (if exposed in future CLI versions)
  2. Cached forice in ~/.claude_pipeline.json
  3. Interactive plan selection → saved to cache
"""
from __future__ import annotations
import json
import subprocess
import os
from pathlib import Path

_CACHE_FILE = Path.home() / ".claude_pipeline.json"

# Estimated tokens per pipeline session per plan.
# Based on typical pipeline size (~6 agents × ~3 calls each, avg 8k tokens/call).
# Users can override with --budget flag.
PLAN_BUDGETS: dict[str, int] = {
    "free":       100_000,   # ~12 agent calls before slowing down
    "pro":        500_000,   # comfortable for 1-2 full pipeline runs
    "max":      2_000_000,   # multiple runs, heavy revision loops
    "team":     2_000_000,
    "enterprise": 5_000_000,
    "api":        300_000,   # API users set their own cost ceiling
}

PLAN_LABELS = {
    "free":       "Free",
    "pro":        "Pro ($20/mo)",
    "max":        "Max ($100/mo)",
    "team":       "Team",
    "enterprise": "Enterprise",
    "api":        "API key (pay-per-use)",
}


def _try_cli_plan() -> str | None:
    """Try to read subscriptionType from `claude auth status`."""
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            sub = data.get("subscriptionType")
            if sub:
                return sub.lower().strip()
    except Exception:
        pass
    return None


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        existing = _load_cache()
        existing.update(data)
        _CACHE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception:
        pass


def _ask_user_plan() -> tuple[str, int]:
    """Interactive plan selection. Returns (plan_key, budget)."""
    print("\n  ┌─────────────────────────────────────────────────┐")
    print("  │  Choose gói Claude to ước tính token budget       │")
    print("  ├─────────────────────────────────────────────────┤")
    options = list(PLAN_BUDGETS.keys())
    for i, key in enumerate(options, 1):
        label  = PLAN_LABELS[key]
        budget = PLAN_BUDGETS[key]
        print(f"  │  [{i}] {label:<25} ~{budget:>9,} tok/session  │")
    print("  │  [7] Custom (enter tay)                          │")
    print("  └─────────────────────────────────────────────────┘")

    while True:
        raw = input("  Choose [1-7]: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                plan = options[idx - 1]
                budget = PLAN_BUDGETS[plan]
                return plan, budget
            elif idx == 7:
                custom = input("  Enter token budget (for example 800000): ").strip()
                if custom.isdigit() and int(custom) > 0:
                    return "custom", int(custom)
        print("  Vui lòng enter number from 1-7.")


def detect_budget(force_reselect: bool = False) -> tuple[str, int]:
    """
    Return (plan_name, token_budget) using the best available source.
    Caches user's forice — won't ask again on subsequent runs.
    """
    # 1. Try CLI auth status
    cli_plan = _try_cli_plan()
    if cli_plan and cli_plan in PLAN_BUDGETS and not force_reselect:
        budget = PLAN_BUDGETS[cli_plan]
        print(f"\n  ✅ Phát current gói Claude: {PLAN_LABELS.get(cli_plan, cli_plan)}  →  budget {budget:,} tokens")
        return cli_plan, budget

    # 2. Load from cache
    cache = _load_cache()
    if "plan" in cache and not force_reselect:
        plan   = cache["plan"]
        budget = cache.get("budget", PLAN_BUDGETS.get(plan, 500_000))
        print(f"\n  📋 Gói saved: {PLAN_LABELS.get(plan, plan)}  →  budget {budget:,} tokens")
        print(f"     (Use --reselect-plan to choose again)")
        return plan, budget

    # 3. Ask user once, save to cache
    plan, budget = _ask_user_plan()
    _save_cache({"plan": plan, "budget": budget})
    print(f"\n  ✅ Done lưu: {PLAN_LABELS.get(plan, plan)}  →  {budget:,} tokens/session")
    print(f"     (Lưu tại {_CACHE_FILE}  |  --reselect-plan to tor đổi)")
    return plan, budget
