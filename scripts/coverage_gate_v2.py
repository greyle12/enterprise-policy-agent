"""Experimental clause-scoped veto; no labels, case IDs, model or production wiring."""

import re


def decide(query, rule_id):
    clauses = [
        c.strip() for c in re.split(r"[，,。！？!?；;：:]|(?:但是|不过|但)", query) if c.strip()
    ]
    trace = []
    for c in clauses:
        if rule_id == "city_category_for_lodging_table":
            monetary = bool(re.search(r"限额|上限|多少钱|多少住宿|住宿标准|每晚|[0-9]+元", c))
            category_question = bool(re.search(r"算不算|属于哪|哪类|哪些|怎么.*分类", c))
            known = (
                bool(re.search(r"(已确认|已确定|已经确认|已知|属于)[一二三]类城市", c))
                and not category_question
            )
            excluded = bool(re.search(r"(不问|不用|无需|不需要).*(同住|合住)", c))
            need = (monetary and not known) or category_question
            veto = known or (bool(re.search(r"同住|合住", c)) and not need and not excluded)
        elif rule_id == "finance_review_for_overdue_expense":
            double_negative = bool(re.search(r"不是不|并非不|不能不", c))
            excluded = (
                bool(
                    re.search(
                        r"(不需要|不用|不问|无需|不要展开).*(审批|流程|复核)|其他环节.*不讲", c
                    )
                )
                and not double_negative
            )
            need = not excluded and bool(
                re.search(r"审批|复核|分别.*做什么|哪些人|怎么处理|直接.*报|继续.*办", c)
            )
            veto = excluded
        else:
            raise ValueError("unknown rule")
        trace.append({"clause": c, "need": need, "veto": veto})
    # Positive demand in another clause takes precedence over a local exclusion.
    allowed = any(t["need"] for t in trace) or not any(t["veto"] for t in trace)
    return allowed, trace


def select_v2(query, base, frozen, events):
    for e in events:
        if e["action"] == "supplemented":
            allowed, trace = decide(query, e["rule_id"])
            return list(frozen if allowed else base), trace
    return list(frozen), []
