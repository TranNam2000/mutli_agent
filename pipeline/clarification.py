"""
Clarification flow — input clarity check (PM + BA gates) and post-produce
MISSING_INFO resolution (batch clarify across peer agents).

Extracted from orchestrator so the clarification logic is testable in
isolation with stub agents.

Public API
----------
    pm_clarify_with_user(orch, decision, product_idea) -> RouteDecision
    clarification_gate  (orch, product_idea) -> enriched_idea
    batch_clarify       (orch, outputs, peer_agents, original_task) -> dict
    resolve_info_needs  (orch, step_key, agent, produce_fn, ...) -> str
"""
from __future__ import annotations

from core.logging import tprint
from agents.pm_agent import ALL_KINDS as PM_ALL_KINDS, RouteDecision


def pm_clarify_with_user(orch, decision: RouteDecision, product_idea: str) -> RouteDecision:
    """Interactive fallback when PM confidence < 0.6."""
    tprint(f"\n  ⚠️  PM confidence {decision.confidence:.2f} is low — please confirm.")
    tprint(f"     PM suggestion: {decision.kind}")
    tprint(f"     Reason: {decision.reason}")
    tprint(f"\n     Kinds:")
    for i, k in enumerate(PM_ALL_KINDS, 1):
        marker = " ← PM pick" if k == decision.kind else ""
        tprint(f"       {i}. {k}{marker}")

    while True:
        raw = input("\n     Pick kind [1-5] or Enter to accept PM pick: ").strip()
        if not raw:
            return decision
        if raw.isdigit() and 1 <= int(raw) <= len(PM_ALL_KINDS):
            chosen = PM_ALL_KINDS[int(raw) - 1]
            return RouteDecision(
                kind=chosen,
                confidence=1.0,
                reason=f"User confirmed after low-confidence PM pick ({decision.kind}).",
                source="user",
            )
        if raw in PM_ALL_KINDS:
            return RouteDecision(
                kind=raw, confidence=1.0,
                reason="User confirmed kind by name.",
                source="user",
            )
        tprint("     Invalid choice. Enter 1-5 or a kind name.")


def clarification_gate(orch, product_idea: str) -> str:
    """BA checks if input is clear. if not, ask user clarifying questions."""
    if orch._step_done("ba"):
        return product_idea  # already done, skip gate

    tprint(f"\n  {'─'*60}")
    tprint(f"  🎯 CLARIFICATION GATE — BA evaluating request...")
    tprint(f"  {'─'*60}")

    ba = orch.agents["ba"]
    result = ba.check_clarity(product_idea)

    if result["is_clear"]:
        tprint(f"  ✅ Request is clear — proceeding.\n")
        return product_idea

    tprint(f"  ⚠️  Request is ambiguous. BA needs more info:\n")
    qa_pairs = []
    for i, q in enumerate(result["questions"], 1):
        tprint(f"  {i}. {q}")
        answer = input(f"     Answer: ").strip()
        if answer:
            qa_pairs.append({"q": q, "a": answer})

    if qa_pairs:
        enriched = ba.enrich_input(product_idea, qa_pairs)
        tprint(f"\n  ✅ Added {len(qa_pairs)} clarification(s) — proceeding with pipeline.\n")
        return enriched

    tprint(f"\n  ⚠️  No answer — continuing with current info.\n")
    return product_idea


def batch_clarify(orch,
    outputs: dict[str, str],          # {agent_key: output_text}
    peer_agents: dict[str, object],
    original_task: str = "",
) -> dict[str, str]:
    """
    Hướng 3: Post-produce batch clarification.
    1. Scan all outputs for MISSING_INFO blocks
    2. Try to resolve each via peer agents first
    3. Collect whatever's still unresolved → ask user ONCE in batch
    4. Revise only agents that had unresolved MISSING_INFO
    Returns {agent_key: revised_output} for agents that were revised.
    """
    # Gather all missing info across all outputs
    all_items: list[dict] = []
    for key, text in outputs.items():
        for item in orch._extract_missing_info(text):
            all_items.append({**item, "agent_key": key})

    if not all_items:
        return {}

    tprint(f"\n  {'═'*60}")
    tprint(f"  📋 POST-PRODUCE CLARIFICATION — {len(all_items)} info needed xác nhận")
    tprint(f"  {'═'*60}")

    # Step 1: Try peer agents first (no input() needed)
    source_map = {
        "BA": "ba", "TECHLEAD": "techlead", "TECH LEAD": "techlead",
        "DESIGN": "design",
    }
    resolved: dict[str, str] = {}

    for item in all_items:
        src_key = source_map.get(item["source"].upper())
        if src_key and src_key in peer_agents:
            peer = peer_agents[src_key]
            tprint(f"\n  ❓ [{item['agent_key']}] {item['info']}")
            tprint(f"     → Hỏi {peer.ROLE}...")
            answer = peer.respond_to("Orchestrator", item["info"])
            if answer and len(answer.strip()) > 10:
                resolved[item["info"]] = answer
                tprint(f"     ✅ {peer.ROLE}: {answer[:80]}...")
                continue
        # Mark as unresolved for user batch
        resolved.setdefault(item["info"], None)

    # Step 2: Batch ask user for still-unresolved items (dedup by info text)
    seen_info: set[str] = set()
    unresolved = []
    for item in all_items:
        if resolved.get(item["info"]) is None and item["info"] not in seen_info:
            seen_info.add(item["info"])
            unresolved.append(item)
    if unresolved:
        tprint(f"\n  {'─'*60}")
        tprint(f"  💬 {len(unresolved)} question need user answer (1 time duy nhất):")
        tprint(f"  {'─'*60}")
        for i, item in enumerate(unresolved, 1):
            tprint(f"\n  {i}. [{item['agent_key'].upper()}] {item['info']}")
            tprint(f"     Nguồn đề xuất: {item['source']}")
            answer = input("     Answer: ").strip()
            if answer:
                resolved[item["info"]] = answer
            else:
                tprint("     ⚠️  Skip — agent will giữ placeholder.")

    # Step 3: Revise agents that had missing info
    actually_resolved = {k: v for k, v in resolved.items() if v}
    if not actually_resolved:
        return {}

    revised: dict[str, str] = {}
    for key, text in outputs.items():
        agent_items = [i for i in all_items if i["agent_key"] == key]
        applicable = {i["info"]: actually_resolved[i["info"]]
                     for i in agent_items if i["info"] in actually_resolved}
        if not applicable:
            continue
        agent = orch.agents[key]
        tprint(f"\n  🔄 Revise {agent.ROLE} with {len(applicable)} thông tin  xác nhận...")
        # BA use revise_with_answers to tổng hợp sạch, phân cấp rõ
        if key == "ba" and hasattr(agent, "revise_with_answers"):
            revised[key] = agent.revise_with_answers(text, applicable, original_task)
        else:
            guide = [f'Tor thế placeholder "{info}" bằng: {ans}'
                     for info, ans in applicable.items()]
            revised[key] = agent.revise(text, guide, original_task)

    if revised:
        tprint(f"\n  ✅ Done revise: {', '.join(revised.keys())}")
    return revised


def resolve_info_needs(orch,
    agent,
    task_description: str,
    available_context: str,
    peer_agents: dict[str, object],
    *,
    _called_from_thread: bool = False,
) -> dict:
    """
    Pre-produce check (Phương án 2):
    1. Ask agent what it needs
    2. Route each NEED to peer agent (message bus) or user
    3. Return resolved {need: answer} dict to inject into produce call
    """
    needs = agent.plan_needed_info(task_description, available_context)
    if not needs:
        return {}

    tprint(f"\n  {'─'*60}")
    tprint(f"  📋 PRE-PRODUCE CHECK — {agent.ROLE} need {len(needs)} thông tin")
    tprint(f"  {'─'*60}")

    resolved: dict[str, str] = {}
    source_map = {
        "BA": "ba", "TECHLEAD": "techlead", "TECH LEAD": "techlead",
        "DESIGN": "design",
    }

    for item in needs:
        need = item["need"]
        source_key = source_map.get(item["source"], None)
        tprint(f"\n  ❓ Need biết: {need}")
        tprint(f"     Nguồn: {item['source']}")

        answer = None

        # Try peer agent first
        if source_key and source_key in peer_agents:
            peer = peer_agents[source_key]
            tprint(f"     → Hỏi {peer.ROLE}...")
            answer = agent.ask(peer, need)
            if answer and len(answer.strip()) > 10:
                resolved[need] = answer
                tprint(f"     ✅ Done có answer from {peer.ROLE}")
                continue

        # Fallback: ask user — only safe outside parallel threads
        if _called_from_thread:
            tprint(f"     ⚠️  Cannot hỏi user from parallel thread — agent will flag MISSING_INFO.")
            continue
        tprint(f"     → Peer agent no enough thông tin. Hỏi user:")
        tprint(f"     {need}")
        user_ans = input(f"     Answer: ").strip()
        if user_ans:
            resolved[need] = user_ans
            tprint(f"     ✅ Done ghi nhận answer from user.")
        else:
            tprint(f"     ⚠️  không có answer — agent will flag MISSING_INFO in output.")

    tprint(f"\n  ✅ Resolved {len(resolved)}/{len(needs)} — proceed produce.\n")
    return resolved
