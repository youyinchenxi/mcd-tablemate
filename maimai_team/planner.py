"""Bounded, deterministic group meal planning using integer fen estimates.

Offers are indivisible: a whole offer and every included component belong to one
person. Prices here are catalog estimates; a MCP quote must verify checkout.
"""

from collections import Counter

CATEGORIES = ("main", "drink", "side")
MAX_NODES_PER_PERSON = 12000
MAX_OPTIONS_PER_PERSON = 64
MAX_BEAM = 256


def _integer(value, label, minimum=0, maximum=1_000_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _string(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{label} must be a nonempty string (max 200 characters)")
    return value.strip()


def _tags(value, label):
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError(f"{label} must be a list of at most 30 tags")
    tags = [_string(tag, label) for tag in value]
    if len(tags) != len(set(tags)):
        raise ValueError(f"{label} contains duplicate tags")
    return frozenset(tags)


def _validate(catalog, request):
    if not isinstance(catalog, dict) or not isinstance(request, dict):
        raise ValueError("catalog and request must be objects")
    store = catalog.get("store")
    if not isinstance(store, dict):
        raise ValueError("catalog.store must be an object")
    _string(store.get("id"), "store.id")
    _string(store.get("name"), "store.name")
    raw_offers = catalog.get("offers")
    if not isinstance(raw_offers, list) or len(raw_offers) > 80:
        raise ValueError("catalog.offers must be a list of at most 80 offers")
    offers, offer_ids = [], set()
    for raw in raw_offers:
        if not isinstance(raw, dict):
            raise ValueError("each offer must be an object")
        oid = _string(raw.get("id"), "offer.id")
        if oid in offer_ids:
            raise ValueError(f"duplicate offer.id: {oid}")
        offer_ids.add(oid)
        name = _string(raw.get("name"), "offer.name")
        price = _integer(raw.get("price_fen"), "offer.price_fen")
        maximum = _integer(raw.get("max_quantity", 60), "offer.max_quantity", 0, 60)
        raw_components = raw.get("components")
        if not isinstance(raw_components, list) or not 1 <= len(raw_components) <= 20:
            raise ValueError("offer.components must contain 1–20 components")
        components, counts, all_tags = [], Counter(), set()
        for component in raw_components:
            if not isinstance(component, dict):
                raise ValueError("each component must be an object")
            category = component.get("category")
            if category not in CATEGORIES:
                raise ValueError("component.category must be main, drink or side")
            tags = _tags(component.get("tags", []), "component.tags")
            quantity = _integer(component.get("quantity", 1), "component.quantity", 1, 20)
            components.append({"name": _string(component.get("name"), "component.name"),
                               "category": category, "tags": sorted(tags), "quantity": quantity})
            counts[category] += quantity
            all_tags.update(tags)
        raw_items = raw.get("items")
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 20:
            raise ValueError("offer.items must contain 1–20 purchase items")
        items, item_ids = [], set()
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("each item must be an object")
            iid = _string(item.get("id"), "item.id")
            if iid in item_ids:
                raise ValueError(f"duplicate item.id in offer {oid}: {iid}")
            item_ids.add(iid)
            items.append({"id": iid, "quantity": _integer(item.get("quantity"), "item.quantity", 1, 20)})
        offers.append({"id": oid, "name": name, "price_fen": price, "components": components,
                       "items": items, "max_quantity": maximum, "counts": counts,
                       "tags": frozenset(all_tags)})
    raw_people = request.get("people")
    if not isinstance(raw_people, list) or not 2 <= len(raw_people) <= 6:
        raise ValueError("request.people must contain 2–6 people")
    people, person_ids = [], set()
    for raw in raw_people:
        if not isinstance(raw, dict):
            raise ValueError("each person must be an object")
        pid = _string(raw.get("id"), "person.id")
        if pid in person_ids:
            raise ValueError(f"duplicate person.id: {pid}")
        person_ids.add(pid)
        needs = raw.get("needs", {"main": 1, "drink": 1, "side": 0})
        if not isinstance(needs, dict) or set(needs) - set(CATEGORIES):
            raise ValueError("person.needs may only contain main, drink and side")
        needs = {cat: _integer(needs.get(cat, 0), f"needs.{cat}", 0, 3) for cat in CATEGORIES}
        if not 1 <= sum(needs.values()) <= 6:
            raise ValueError("each person must need 1–6 components")
        must = _tags(raw.get("must_tags", []), "person.must_tags")
        if len(must) > 4:
            raise ValueError("person.must_tags may contain at most 4 tags")
        people.append({"id": pid, "name": _string(raw.get("name"), "person.name"), "needs": needs,
                       "exclude": _tags(raw.get("exclude_tags", []), "person.exclude_tags"),
                       "must": must, "prefer": _tags(raw.get("prefer_tags", []), "person.prefer_tags")})
    budget = _integer(request.get("budget_fen"), "request.budget_fen")
    strategy = request.get("strategy", "balanced")
    if strategy not in ("balanced", "budget"):
        raise ValueError("request.strategy must be balanced or budget")
    limit = _integer(request.get("quote_limit", 8), "request.quote_limit", 1, 8)
    return offers, people, budget, strategy, limit


def _option(choices, person, offers):
    counts, tags, usage = Counter(), set(), Counter()
    cost = 0
    for index in choices:
        offer = offers[index]
        usage[index] += 1
        counts.update(offer["counts"])
        tags.update(offer["tags"])
        cost += offer["price_fen"]
    extras = sum(max(0, counts[cat] - person["needs"][cat]) for cat in CATEGORIES)
    hits = person["prefer"] & tags
    return {"choices": tuple(choices), "usage": usage, "cost": cost,
            "score": len(hits) * 100 - extras * 5, "hits": sorted(hits)}


def _individual_options(person, offers, strategy):
    # Full offer tags are checked, including unwanted sides/drinks in a package.
    eligible = [i for i, offer in enumerate(offers)
                if offer["max_quantity"] and not offer["tags"] & person["exclude"]]
    eligible.sort(key=lambda i: (offers[i]["price_fen"], offers[i]["id"]))
    results, nodes, truncated = [], 0, False
    maximum_depth = sum(person["needs"].values()) + len(person["must"])

    def visit(start, choices, counts, tags, usage):
        nonlocal nodes, truncated
        if nodes >= MAX_NODES_PER_PERSON:
            truncated = True
            return
        nodes += 1
        deficits = {cat: max(0, person["needs"][cat] - counts[cat]) for cat in CATEGORIES}
        missing = person["must"] - tags
        if not any(deficits.values()) and not missing:
            results.append(_option(choices, person, offers))
            return
        if len(choices) >= maximum_depth:
            return
        for position in range(start, len(eligible)):
            index = eligible[position]
            offer = offers[index]
            if usage[index] >= offer["max_quantity"]:
                continue
            if not (any(deficits[cat] and offer["counts"][cat] for cat in CATEGORIES)
                    or missing & offer["tags"]):
                continue
            next_usage = usage.copy()
            next_usage[index] += 1
            visit(position, choices + [index], counts + offer["counts"], tags | offer["tags"], next_usage)
            if nodes >= MAX_NODES_PER_PERSON:
                truncated = True
                break

    visit(0, [], Counter(), frozenset(), Counter())
    key = ((lambda option: (option["cost"], -option["score"], option["choices"])) if strategy == "budget"
           else (lambda option: (-option["score"], option["cost"], option["choices"])))
    results.sort(key=key)
    if len(results) > MAX_OPTIONS_PER_PERSON:
        # Keep cheap alternatives as well as preferred meals for shared budgets.
        preferred = results[:48]
        cheapest = sorted(results, key=lambda option: (option["cost"], -option["score"], option["choices"]))
        seen = {option["choices"] for option in preferred}
        for option in cheapest:
            if option["choices"] not in seen:
                preferred.append(option)
                seen.add(option["choices"])
                if len(preferred) == MAX_OPTIONS_PER_PERSON:
                    break
        results = sorted(preferred, key=key)
        truncated = True
    return results, nodes, truncated


def _render_assignment(person, option, offers):
    components = []
    remaining = dict(person["needs"])
    extras = []
    for index in option["choices"]:
        for component in offers[index]["components"]:
            copied = {**component, "tags": list(component["tags"])}
            components.append(copied)
            used = min(remaining[copied["category"]], copied["quantity"])
            remaining[copied["category"]] -= used
            if used < copied["quantity"]:
                extras.append({**copied, "quantity": copied["quantity"] - used})
    return {"person_id": person["id"], "person_name": person["name"],
            "estimated_cost_fen": option["cost"],
            "offers": [{"id": offers[i]["id"], "name": offers[i]["name"], "quantity": quantity}
                       for i, quantity in sorted(option["usage"].items())],
            "components": components, "extras": extras, "preference_hits": option["hits"]}


def plan(catalog, request):
    """Return <=8 complete group candidates; never relax hard requirements.

    ``ok`` means at least one catalog estimate is within budget, not a verified
    store quote. Some over-estimate candidates are retained for coupon quoting.
    A bounded search cannot guarantee a global optimum or exhaustive infeasibility.
    """
    offers, people, budget, strategy, limit = _validate(catalog, request)
    warnings, option_sets, unmet = [], [], []
    search = {"individual_nodes": 0, "group_expansions": 0, "truncated": False}
    for person in people:
        options, nodes, truncated = _individual_options(person, offers, strategy)
        search["individual_nodes"] += nodes
        search["truncated"] |= truncated
        option_sets.append(options)
        if not options:
            unmet.append({"person_id": person["id"], "reason": "No complete meal found satisfying needs and hard tags."})
    base = {"budget_fen": budget, "strategy": strategy, "price_source": "catalog_estimate",
            "warnings": warnings, "search": search, "unmet": unmet}
    if unmet:
        if search["truncated"]:
            warnings.append("Search limit reached; no exhaustive feasibility claim is made.")
        return {**base, "status": "no_solution", "candidates": []}

    beam = [{"options": [None] * len(people), "usage": Counter(), "cost": 0, "score": 0}]
    # Assign people with fewer feasible meals first to protect scarce offers.
    order = sorted(range(len(people)), key=lambda index: (len(option_sets[index]), index))
    for person_index in order:
        options = option_sets[person_index]
        expanded = []
        for current in beam:
            for option in options:
                usage = current["usage"] + option["usage"]
                if any(quantity > offers[index]["max_quantity"] for index, quantity in usage.items()):
                    continue
                chosen_options = list(current["options"])
                chosen_options[person_index] = option
                expanded.append({"options": chosen_options, "usage": usage,
                                 "cost": current["cost"] + option["cost"],
                                 "score": current["score"] + option["score"]})
                search["group_expansions"] += 1
        if not expanded:
            unmet.append({"reason": "No group assignment found respecting shared offer quantity limits."})
            if search["truncated"]:
                warnings.append("Search limit reached; no exhaustive feasibility claim is made.")
            return {**base, "status": "no_solution", "candidates": []}
        rank = (lambda row: (row["cost"], -row["score"])) if strategy == "budget" else (lambda row: (-row["score"], row["cost"]))
        expanded.sort(key=rank)
        if len(expanded) > MAX_BEAM:
            # Use both price and preference order; preserve affordable fallbacks.
            chosen = expanded[:MAX_BEAM // 2]
            signatures = {tuple(option["choices"] if option else None for option in row["options"]) for row in chosen}
            for row in sorted(expanded, key=lambda row: (row["cost"], -row["score"])):
                signature = tuple(option["choices"] if option else None for option in row["options"])
                if signature not in signatures:
                    chosen.append(row)
                    signatures.add(signature)
                    if len(chosen) == MAX_BEAM:
                        break
            expanded = sorted(chosen, key=rank)
            search["truncated"] = True
        beam = expanded

    in_budget = [row for row in beam if row["cost"] <= budget]
    over_budget = [row for row in beam if row["cost"] > budget]
    # At most two quote slots for near-budget estimates, because coupons may help.
    reserve = min(2, limit - 1, len(over_budget)) if in_budget else 0
    inside_count = min(len(in_budget), limit - reserve)
    selected = in_budget[:inside_count]
    over_budget.sort(key=lambda row: (row["cost"], -row["score"]))
    selected += over_budget[:limit - len(selected)]
    if len(selected) < limit:
        selected += in_budget[inside_count:inside_count + limit - len(selected)]
    candidates = []
    for number, row in enumerate(selected, 1):
        assignments = [_render_assignment(person, option, offers) for person, option in zip(people, row["options"])]
        items = Counter()
        for index, quantity in row["usage"].items():
            for item in offers[index]["items"]:
                items[item["id"]] += item["quantity"] * quantity
        candidates.append({"id": f"candidate-{number}", "assignments": assignments,
                           "items": [{"id": iid, "quantity": quantity} for iid, quantity in sorted(items.items())],
                           "estimated_total_fen": row["cost"], "estimated_within_budget": row["cost"] <= budget,
                           "score": row["score"], "unmet": [],
                           "extras": [{"person_id": assignment["person_id"], **extra}
                                      for assignment in assignments for extra in assignment["extras"]]})
    if search["truncated"]:
        warnings.append("Candidate search was bounded; results are feasible suggestions, not a proven global optimum.")
    if not in_budget:
        warnings.append("All returned estimates exceed budget. Check MCP quotes and coupons before concluding budget is insufficient.")
    warnings.append("Catalog prices are estimates; availability, discounts and final payable price require MCP verification.")
    return {**base, "status": "ok" if in_budget else "estimated_over_budget", "candidates": candidates}
