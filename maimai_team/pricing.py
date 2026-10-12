"""Verify whole-cart quotes, filter against budget, and allocate integer fen."""
from copy import deepcopy

SHARING_RULE = "按各人餐品估算成本比例分摊整单实付（含配送等费用），最大余数法分配尾差；不是单品实时价格。"


def allocate_shares(assignments, total_fen):
    """Allocate a whole-cart total; zero-cost assignments share equally."""
    if type(total_fen) is not int or total_fen < 0 or not assignments:
        raise ValueError("分摊需要非负整数分及每人的餐品归属。")
    ids = [a.get("person_id") for a in assignments]
    if any(not isinstance(pid, str) or not pid for pid in ids) or len(set(ids)) != len(ids):
        raise ValueError("餐品归属的人员标识必须存在且唯一。")
    weights = [a.get("estimated_cost_fen") for a in assignments]
    if any(type(weight) is not int or weight < 0 for weight in weights):
        raise ValueError("每人估算成本必须为非负整数分。")
    if not sum(weights):
        weights = [1] * len(assignments)
    denominator = sum(weights)
    divided = [divmod(total_fen * weight, denominator) for weight in weights]
    shares = [q for q, remainder in divided]
    # Stable participant order is the tie-break for equally large remainders.
    remainder_order = sorted(range(len(weights)), key=lambda i: (-divided[i][1], i))
    for i in remainder_order[:total_fen - sum(shares)]:
        shares[i] += 1
    return [{"person_id": a["person_id"], "person_name": a.get("person_name", a["person_id"]),
             "share_fen": share} for a, share in zip(assignments, shares)]


def _valid_quote(quote):
    if not isinstance(quote, dict) or type(quote.get("total_fen")) is not int or quote["total_fen"] < 0:
        return False
    evidence = quote.get("evidence")
    return (isinstance(evidence, dict) and evidence.get("tool") == "calculate-price"
            and evidence.get("source") == "mcp"
            and evidence.get("total_includes_fees") is True
            and isinstance(evidence.get("store_id"), str) and bool(evidence["store_id"].strip())
            and isinstance(evidence.get("verified_at"), str) and bool(evidence["verified_at"].strip()))


def verify_candidates(result, request, quote_callback):
    """Quote each candidate once; only successful budget-compliant quotes remain.

    quote_callback is the application's trusted MCP adapter, not user-supplied
    browser data. It must return evidence for the actual calculate-price call
    and a whole-cart total that includes all mandatory fees.
    """
    if not isinstance(result, dict) or not isinstance(request, dict):
        raise ValueError("规划结果及请求必须为对象。")
    budget = request.get("budget_fen")
    if type(budget) is not int or budget < 0:
        raise ValueError("budget_fen 必须是非负整数分。")
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidates 必须为列表。")
    output = deepcopy(result)
    output.update({"source": "mcp", "verified": False, "candidates": [],
                   "budget_fen": budget, "failed_candidate_ids": [],
                   "over_budget_candidate_ids": [], "quote_status": "pending"})
    warnings = list(output.get("warnings", []))
    passed = []
    for candidate in candidates:
        cid = candidate.get("id") if isinstance(candidate, dict) else None
        try:
            quote = quote_callback(deepcopy(candidate))
            if not _valid_quote(quote):
                raise ValueError("核价证据不完整。")
            verified = deepcopy(candidate)
            verified.update({"total_fen": quote["total_fen"], "verified": True,
                             "evidence": deepcopy(quote["evidence"]),
                             "shares": allocate_shares(candidate["assignments"], quote["total_fen"]),
                             "sharing_rule": SHARING_RULE})
            if quote["total_fen"] > budget:
                output["over_budget_candidate_ids"].append(cid)
            else:
                passed.append(verified)
        except Exception:
            # Callback exceptions can contain raw remote errors or credentials.
            # Keep only the local candidate id and a fixed, non-sensitive notice.
            output["failed_candidate_ids"].append(cid)
    if request.get("strategy", "balanced") == "budget":
        passed.sort(key=lambda c: (c["total_fen"], -c.get("score", 0), c.get("id", "")))
    else:
        passed.sort(key=lambda c: (-c.get("score", 0), c["total_fen"], c.get("id", "")))
    output["candidates"] = passed
    output["verified"] = bool(passed)
    if passed:
        output["status"] = "ok"
        output["quote_status"] = "verified"
        output['price_source'] = 'mcp_calculate_price'
        warnings = [w for w in warnings if w != 'Catalog prices are estimates; availability, discounts and final payable price require MCP verification.'
                    and w != 'All returned estimates exceed budget. Check MCP quotes and coupons before concluding budget is insufficient.']
        warnings.append('价格已通过整单核价确认；门店、就餐方式、规格、券或需求变化后须重新核价。')
    elif output["over_budget_candidate_ids"]:
        output["status"] = "over_budget"
        output["quote_status"] = "no_verified_candidate_within_budget"
        warnings.append("已成功核价的方案均超过预算；未推荐未核价或核价失败的方案。")
    elif candidates:
        output["status"] = "quote_failed"
        output["quote_status"] = "all_quotes_failed"
        warnings.append("全部候选核价失败，无法确认预算内方案。请检查门店、供应和核价模板。")
    else:
        output["status"] = "no_solution"
        output["quote_status"] = "no_candidates"
    if output["failed_candidate_ids"] and passed:
        warnings.append("部分候选核价失败，已从推荐中移除。")
    output["warnings"] = warnings
    return output
