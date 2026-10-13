"""Adapt observed official pickup responses without guessing product semantics.

This module performs no network calls and reads no credentials or files.
The host supplies confirmed category/tags for every selected component.
It preserves the observed default customizations verbatim in cart arguments.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .live import payload


def _data(response, label):
    if not isinstance(response, dict):
        raise ValueError(f"{label} 必须为官方响应对象。")
    if "success" not in response:
        response = payload(response)
    if response.get("success") is not True or response.get("code") != 200 or not isinstance(response.get("data"), dict):
        raise ValueError(f"{label} 业务状态未成功。")
    return response["data"]


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须为非空字符串。")
    return value


def _quantity(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} 必须为至少 {minimum} 的整数。")
    return value


def _yuan(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        raise ValueError(f"{label} 必须为可精确转换的元价格字符串。")
    try:
        amount = Decimal(value.strip()) * 100
    except InvalidOperation:
        raise ValueError(f"{label} 金额无效。") from None
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise ValueError(f"{label} 不能精确转换为整数分。")
    return int(amount)


def _diff(value):
    if not isinstance(value, str):
        raise ValueError("选配差价缺失，无法确认估算价格。")
    match = re.fullmatch(r"\s*([+-]?)\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        raise ValueError("选配差价格式未知，无法确认估算价格。")
    amount = _yuan(match.group(2), "选配差价")
    return -amount if match.group(1) == "-" else amount


def _modification(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ValueError("商品特制详情格式未知。")
    values = []
    for group in raw["items"]:
        if not isinstance(group, dict) or not isinstance(group.get("values"), list):
            raise ValueError("商品特制分组格式未知。")
        for value in group["values"]:
            if not isinstance(value, dict):
                raise ValueError("商品特制选项格式未知。")
            quantity = _quantity(value.get("selectedQuantity", 0), "特制选中数量")
            key = value.get("selectedKey") if quantity else value.get("unselectedKey")
            if quantity or key:
                values.append({"code": _text(value.get("code"), "特制 code"),
                               "key": _text(key, "特制 key"), "quantity": quantity})
    # Duplicate code values are intentionally retained: keys encode distinct
    # choices, e.g. coffee add-shot and no-shot share the same product code.
    return {"values": values}


def _component(choice, quantity, definitions):
    code = _text(choice.get("code"), "组件 code")
    definition = definitions.get(code)
    if not isinstance(definition, dict) or definition.get("category") not in {"main", "drink", "side"}:
        raise ValueError("已选商品缺少宿主核对的 category；请完善 profile.components。")
    tags = definition.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("已选商品缺少宿主核对的 tags 列表。")
    if len(tags) != len(set(tags)):
        raise ValueError("组件 tags 不得重复。")
    return {"name": _text(choice.get("name"), "组件名称"), "category": definition["category"],
            "tags": list(tags), "quantity": quantity}


def _round_selection(round_detail, override):
    choices = round_detail.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("套餐 round 缺少商品列表。")
    by_code = {}
    for choice in choices:
        code = _text(choice.get("code"), "round 商品 code")
        if code in by_code:
            raise ValueError("同一 round 出现重复商品 code，无法唯一确定规格。")
        by_code[code] = choice
    target = _quantity(round_detail.get("quantity"), "round quantity")
    minimum = _quantity(round_detail.get("minQuantity"), "round minQuantity")
    maximum = _quantity(round_detail.get("maxQuantity"), "round maxQuantity")
    if not minimum <= target <= maximum:
        raise ValueError("round 当前数量不在官方上下限内。")
    positive = [(c, _quantity(c.get("quantity", 0), "round 商品当前数量"))
                for c in choices if c.get("quantity", 0) != 0]
    if positive and sum(q for c, q in positive) != target:
        raise ValueError("round 当前默认数量不守恒，需重新查询详情。")
    defaults = [c for c in choices if c.get("isDefault") == 1]
    baseline = positive
    if not baseline and target:
        if len(defaults) == 1:
            baseline = [(defaults[0], target)]
        elif override is None:
            raise ValueError("round 默认选择不唯一；请在 profile.round_selections 中明确选择。")
        else:
            # No current positive quantity: only a zero-difference default base
            # proves the menu price is a usable reference for explicit choices.
            if not defaults or any(_diff(c.get("diffPrice")) != 0 for c in defaults):
                raise ValueError("默认选择与基础差价无法确定，需重新查询详情。")
    if override is None:
        selected = baseline
    else:
        if not isinstance(override, list):
            raise ValueError("round_selections 每个 round 必须为商品数量列表。")
        selected, seen = [], set()
        for item in override:
            if not isinstance(item, dict) or set(item) != {"code", "quantity"}:
                raise ValueError("round 选择只接受 code 和 quantity，不支持私自修改特制。")
            code = _text(item["code"], "round 选择 code")
            quantity = _quantity(item["quantity"], "round 选择数量", 1)
            if code not in by_code or code in seen:
                raise ValueError("round 选择商品不存在或重复。")
            seen.add(code)
            selected.append((by_code[code], quantity))
    count = sum(q for c, q in selected)
    if not minimum <= count <= maximum or count != target:
        raise ValueError("round 选择数量必须符合上下限并保持官方当前套餐份数。")
    # Only read diffPrice if changing the official default selection. An
    # unchanged default uses the actual currentPrice and needs no price guess.
    signature = lambda selection: sorted((c["code"], q) for c, q in selection)
    adjustment = 0
    if signature(selected) != signature(baseline):
        adjustment = sum(_diff(c.get("diffPrice")) * q for c, q in selected)
        adjustment -= sum(_diff(c.get("diffPrice")) * q for c, q in baseline)
    return selected, adjustment


def build_snapshot(menu_payload, details_by_code, profile, store):
    """Return planner catalog and schema-compatible pickup quote template.

    profile: {offers: [{code, id?, round_selections?: {"1": [{code,
    quantity}]}}], components: {productCode: {category, tags}},
    meal_mode?: "到店取餐"}. The host must verify that store id/name came from
    query-nearby-stores and confirm the quote total semantics with a live call.
    """
    menu = _data(menu_payload, "菜单")
    meals = menu.get("meals")
    if not isinstance(meals, dict) or not isinstance(details_by_code, dict):
        raise ValueError("菜单 meals 与 details_by_code 必须为对象。")
    if not isinstance(profile, dict) or not isinstance(profile.get("offers"), list) or not profile["offers"]:
        raise ValueError("profile.offers 必须明确指定至少一个官方商品。")
    if set(profile) - {"offers", "components", "meal_mode"}:
        raise ValueError("profile 包含不支持的配置；仅适配已观察的到店取餐。")
    if profile.get("meal_mode", "到店取餐") != "到店取餐":
        raise ValueError("当前适配器仅支持到店取餐。")
    definitions = profile.get("components")
    if not isinstance(definitions, dict):
        raise ValueError("profile.components 必须显式提供已核对的分类和 tags。")
    if not isinstance(store, dict):
        raise ValueError("store 必须来自实际门店查询。")
    store_info = {"id": _text(store.get("id"), "门店 id"), "name": _text(store.get("name"), "门店名称")}
    offers, item_templates, seen = [], {}, set()
    for entry in profile["offers"]:
        if not isinstance(entry, dict) or set(entry) - {"code", "id", "round_selections"}:
            raise ValueError("profile.offer 配置格式未知。")
        code = _text(entry.get("code"), "offer code")
        if code not in meals or code not in details_by_code:
            raise ValueError("指定商品缺少当前门店菜单或详情。")
        meal = meals[code]
        detail = _data(details_by_code[code], "商品详情")
        if detail.get("code") != code:
            raise ValueError("商品详情 code 与菜单商品不一致。")
        price = _yuan(meal.get("currentPrice"), "currentPrice")
        name = _text(meal.get("name"), "菜单商品名称")
        rounds = detail.get("rounds")
        if not isinstance(rounds, list):
            raise ValueError("商品详情 rounds 格式未知。")
        selections = entry.get("round_selections", {})
        if not isinstance(selections, dict):
            raise ValueError("round_selections 必须为以 round id 为键的对象。")
        round_ids = [str(r.get("id")) for r in rounds]
        if len(set(round_ids)) != len(round_ids) or set(selections) - set(round_ids):
            raise ValueError("round_selections 出现重复或未知 round。")
        argument = {"productCode": code, "quantity": 1}
        components, round_list = [], []
        for round_detail in rounds:
            rid = str(round_detail["id"])
            _quantity(round_detail["id"], "round id", 1)
            selected, adjustment = _round_selection(round_detail, selections.get(rid))
            price += adjustment
            combo_items = []
            for choice, quantity in selected:
                components.append(_component(choice, quantity, definitions))
                item = {"code": choice["code"], "quantity": quantity}
                modification = _modification(choice.get("modification"))
                if modification is not None:
                    item["modification"] = modification
                combo_items.append(item)
            round_list.append({"round": rid, "comboItemList": combo_items})
        if rounds:
            argument["roundList"] = round_list
        else:
            components.append(_component(detail, 1, definitions))
        modification = _modification(detail.get("modification"))
        if modification is not None:
            argument["modification"] = modification
        if price < 0:
            raise ValueError("选配后估算价格为负，无法确认价格。")
        fingerprint = hashlib.sha256(json.dumps(argument, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:12]
        local_id = _text(entry.get("id", code), "本地 offer id") + "-" + fingerprint
        if local_id in seen:
            raise ValueError("profile 中同规格的本地 offer id 重复。")
        seen.add(local_id)
        offers.append({"id": local_id, "name": name, "price_fen": price,
                       "components": components, "items": [{"id": local_id, "quantity": 1}]})
        item_templates[local_id] = {"argument": argument, "quantity_path": "/quantity"}
    catalog = {"store": store_info, "offers": offers, "source": {
        "mode": "mcp", "tools": ["query-meals", "query-meal-detail"],
        "captured_at": datetime.now(timezone.utc).isoformat(), "meal_mode": "到店取餐"}}
    template = {"tool": "calculate-price", "arguments": {
        "storeCode": {"$ref": "store_id"}, "orderType": 1, "beType": 1,
        "items": {"$ref": "cart"}, "needTableware": False},
        "item_templates": item_templates, "success": {"path": "/success", "equals": True},
        "total_path": "/data/price", "total_unit": "fen", "total_includes_fees": True,
        "meal_mode": "到店取餐"}
    return catalog, template
