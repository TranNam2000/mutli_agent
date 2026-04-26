"""
Rule lifecycle helpers — legacy classifier-gated apply, regression rollback,
criteria upgrade.

Split from `learning/rule_runner.py` to keep each file ≤ 500 lines.

Public API
----------
    run_legacy_rule_optimizer(orch, suggestions, history) -> None
    rollback_regressed_rules (orch, history) -> None
    maybe_upgrade_criteria   (orch, history) -> list[tuple[str, int, int]]
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime

from core.logging import tprint
from core.config import get_bool
from core.paths import RULES_DIR

from core.paths import rule_path_for


def run_legacy_rule_optimizer(orch, suggestions, history) -> None:
    """Legacy classifier-gated apply flow. Kept behind
    MULTI_AGENT_LEGACY_RULE_OPTIMIZER=1 as an escape hatch."""
    from analyzer import (
        classifier_should_apply, snapshot_features,
        train_regression_classifier, load_outcome_entries,
    )
    from core.config import get_learning_mode

    if not suggestions:
        tprint("  không tìm thấy pattern bug rõ ràng để cải thiện.")
        return

    auto_applied: list[tuple] = []
    pending: list[tuple] = []

    for s in suggestions:
        if history.is_blacklisted(s["agent_key"], s["reason"], s["target_type"]):
            tprint(f"  🚫 [{s['agent_key'].upper()}] Skip — blacklisted (auto-unblock sau 90d).")
            continue
        conflict = history.conflicts_with_pass_patterns(s["agent_key"], s["addition"])
        if conflict:
            tprint(f"  🛡️  [{s['agent_key'].upper()}] Skip — conflicts PASS pattern:")
            tprint(f"       \"{conflict[:80]}\"")
            continue
        count = history.record(s["agent_key"], s["reason"], s["addition"], s["target_type"])
        pending.append((s, count))

    mode    = get_learning_mode()
    dry_run = get_bool("MULTI_AGENT_LEARNING_DRY_RUN")
    interactive = sys.stdin.isatty()

    if mode == "off":
        if pending:
            tprint(f"\n  ⏸️  Learning mode=off — {len(pending)} suggestion(s) recorded, not applied.")
        return

    if not pending:
        return

    try:
        meta = train_regression_classifier(orch.profile, history)
        if meta.get("fitted"):
            tprint(f"  🤖 Classifier retrained: n={meta['n']}, "
                   f"train_acc={meta['train_accuracy']:.2f}")
    except Exception as e:
        tprint(f"  ⚠️  Classifier training skipped: {type(e).__name__}: {e}")

    outcome_entries = load_outcome_entries(orch.profile)

    tprint(f"\n  {'═'*60}")
    label = {"auto": "auto (classifier)", "propose": "propose (user review)"}.get(mode, mode)
    tprint(f"  🤖 RULE CHANGES ({label}) — {len(pending)} suggestion(s)"
           + (" [DRY-RUN]" if dry_run else ""))
    tprint(f"  {'═'*60}")

    for s, count in pending:
        icon = "📋" if s["target_type"] == "criteria" else "📜"
        gate = classifier_should_apply(
            profile=orch.profile, agent_key=s["agent_key"],
            suggestion=s, history=history,
            outcome_entries=outcome_entries,
        )
        proba_txt = (f" | P(regress)={gate['proba']:.2f}"
                     if gate["proba"] is not None else "")
        tprint(f"  {icon} [{s['agent_key'].upper()}] ({count}x)"
               f" {gate['decision']} via {gate['via']}{proba_txt}")
        tprint(f"     reason: {gate['reason']}")
        tprint(f"     + {s['reason'][:80]}")
        tprint(f"       {s['addition'][:120]}")

        if dry_run:
            continue

        will_apply = False
        if mode == "auto":
            will_apply = (gate["decision"] == "apply")
            if gate["decision"] == "shadow":
                tprint(f"     (shadow: pattern needs more data before promote)")
            elif gate["decision"] in ("skip", "hold"):
                tprint(f"     (skipped)")
        elif mode == "propose":
            if not interactive:
                tprint(f"     (non-interactive — skipped; set MULTI_AGENT_LEARNING_MODE=auto for CI)")
            else:
                try:
                    ans = input("     Apply? [y/N]: ").strip().lower()
                    will_apply = ans in ("y", "yes")
                except EOFError:
                    will_apply = False

        if will_apply:
            try:
                snapshot_features(
                    profile=orch.profile,
                    apply_id=f"{orch.session_id}:{s['agent_key']}:{count}",
                    session_id=orch.session_id,
                    agent_key=s["agent_key"],
                    features=gate["features"],
                )
            except Exception as e:
                tprint(f"     ⚠️  feature snapshot failed: {type(e).__name__}: {e}")

            backup_path = orch.rule_optimizer.apply(s)
            if backup_path:
                history.mark_applied(
                    s["agent_key"], s["reason"], s["target_type"],
                    backup_path=backup_path, apply_session_id=orch.session_id,
                )
                auto_applied.append((s, count))
                tprint(f"     ✅ Applied (backup: rules/backups/)")

    if auto_applied:
        tprint(f"\n  ✅ Total applied: {len(auto_applied)} rule change(s).")


def rollback_regressed_rules(orch, history) -> None:
    """Scan applied rules; rollback any that caused score regression."""
    regressed: list[tuple] = []
    for entry in history.get_applied_entries():
        apply_sid   = entry.get("apply_session_id", "")
        backup_path = entry.get("backup_path", "")
        if not apply_sid or not backup_path:
            continue
        if history.detect_regression(entry["agent_key"], apply_sid):
            regressed.append((entry, backup_path))

    if not regressed:
        return

    tprint(f"\n  {'═'*60}")
    tprint(f"  🔄 REGRESSION DETECTED — tự động rollback {len(regressed)} rule(s)")
    tprint(f"  {'═'*60}")
    for entry, backup_path in regressed:
        agent_key   = entry["agent_key"]
        target_type = entry["target_type"]
        rule_path   = rule_path_for(orch.profile, agent_key, target_type)
        ok = orch.rule_optimizer.rollback(backup_path, rule_path)
        if ok:
            history.mark_failed(agent_key, entry["reason_sample"], target_type)
            history.mark_rolled_back(entry["_key"])
            tprint(f"  ↩️  [{agent_key.upper()}] Rule rolled back + pattern blacklisted.")
        else:
            tprint(f"  ❌ [{agent_key.upper()}] Rollback thất bại — kiểm tra: {backup_path}")


def maybe_upgrade_criteria(orch, history) -> list[tuple[str, int, int]]:
    """If any agent has PASSed high for N consecutive sessions, bump its
    criteria PASS_THRESHOLD by 1. Returns [(agent_key, old, new), ...]."""
    upgraded: list[tuple[str, int, int]] = []
    for key in set(r["agent_key"] for r in orch.critic_reviews):
        if not history.should_upgrade_criteria(key):
            continue
        crit_path = RULES_DIR / orch.profile / "criteria" / f"{key}.md"
        if not crit_path.exists():
            crit_path = RULES_DIR / "default" / "criteria" / f"{key}.md"
        if not crit_path.exists():
            continue
        content = crit_path.read_text(encoding="utf-8")
        m = re.search(r"PASS_THRESHOLD:\s*(\d+)", content)
        if not m:
            continue
        old_threshold = int(m.group(1))
        new_threshold = min(old_threshold + 1, 10)
        if new_threshold == old_threshold:
            continue
        backup_dir = RULES_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy(crit_path, backup_dir / f"criteria_{key}_upgrade_{ts}.md")
        new_content = re.sub(
            r"PASS_THRESHOLD:\s*\d+",
            f"PASS_THRESHOLD: {new_threshold}",
            content,
        )
        active_path = RULES_DIR / orch.profile / "criteria" / f"{key}.md"
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text(new_content, encoding="utf-8")
        history.mark_upgraded(key)
        upgraded.append((key, old_threshold, new_threshold))
    return upgraded
