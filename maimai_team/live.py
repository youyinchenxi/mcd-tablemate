"""Explicit schema-driven cart mapping. Never guesses McDonald's argument fields."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json


def pointer_get(value, pointer):
    if pointer == '':
        return value
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('路径必须为 JSON Pointer，例如 /data/total。')
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def pointer_set(value, pointer, replacement):
    if not isinstance(pointer, str) or not pointer.startswith('/') or pointer == '/':
        raise ValueError('请指定非空 JSON Pointer。')
    prefix, _, tail = pointer.rpartition('/')
    parent = pointer_get(value, prefix)
    key = tail.replace('~1', '/').replace('~0', '~')
    if isinstance(parent, list):
        parent[int(key)] = replacement
    elif isinstance(parent, dict) and key in parent:
        parent[key] = replacement
    else:
        raise ValueError('模板中缺少待替换路径。')


def replace_refs(value, cart, store_id):
    if isinstance(value, dict):
        if set(value) == {'$ref'}:
            refs = {'cart': cart, 'store_id': store_id}
            if value['$ref'] not in refs:
                raise ValueError('未知模板引用。仅支持 cart / store_id。')
            return deepcopy(refs[value['$ref']])
        return {k: replace_refs(v, cart, store_id) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_refs(v, cart, store_id) for v in value]
    return value


def payload(result):
    import json
    if 'structuredContent' in result:
        return result['structuredContent']
    texts = [c.get('text', '') for c in result.get('content', []) if c.get('type') == 'text']
    if len(texts) != 1:
        raise ValueError('核价响应无法确定为唯一 JSON 对象。')
    return json.loads(texts[0])


def count_refs(value):
    counts = {'cart': 0, 'store_id': 0}
    def visit(node):
        if isinstance(node, dict):
            if set(node) == {'$ref'}:
                if node['$ref'] not in counts:
                    raise ValueError('未知模板引用。')
                counts[node['$ref']] += 1
            else:
                for child in node.values():
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return counts


def quote_callback(client, catalog, template):
    if template.get('tool') != 'calculate-price':
        raise ValueError('核价模板只允许 calculate-price。')
    if template.get('total_unit') not in ('fen', 'yuan'):
        raise ValueError('核价模板必须明确 total_unit: fen 或 yuan。')
    if not isinstance(template.get('arguments'), dict):
        raise ValueError('核价模板缺少 arguments 对象。')
    if count_refs(template['arguments']) != {'cart': 1, 'store_id': 1}:
        raise ValueError('核价 arguments 必须且只能引用一次完整 cart 和一次 store_id。')
    if not template.get('total_path') or not isinstance(template.get('item_templates'), dict):
        raise ValueError('核价模板缺少总实付路径或餐品参数映射。')
    if template.get('total_includes_fees') is not True:
        raise ValueError('必须确认总实付字段已包含配送费等费用。')
    if not isinstance(template.get('meal_mode'), str) or not template['meal_mode'].strip():
        raise ValueError('核价模板必须明确本次就餐方式。')
    if catalog.get('source', {}).get('mode') != 'mcp':
        raise ValueError('真实核价仅接受来自实际 MCP 菜单整理的 catalog。')
    success = template.get('success')
    if (not isinstance(success, dict) or 'path' not in success or 'equals' not in success
            or type(success['equals']) not in (str, int, bool)):
        raise ValueError('必须明确业务成功字段与标量成功值。')

    def quote(candidate):
        cart = []
        if not isinstance(candidate.get('items'), list) or not candidate['items']:
            raise ValueError('核价购物车不能为空。')
        seen = set()
        for item in candidate['items']:
            if item.get('id') in seen or type(item.get('quantity')) is not int or item['quantity'] < 1:
                raise ValueError('核价购物车必须使用唯一餐品标识和正整数数量。')
            seen.add(item['id'])
            entry = template['item_templates'].get(item['id'])
            if not isinstance(entry, dict) or not isinstance(entry.get('argument'), dict):
                raise ValueError('候选餐品缺少经过实际 schema 确认的参数映射。')
            argument = deepcopy(entry['argument'])
            pointer_set(argument, entry['quantity_path'], item['quantity'])
            cart.append(argument)
        args = replace_refs(template['arguments'], cart, catalog['store']['id'])
        raw = client.call_tool('calculate-price', args)
        data = payload(raw)
        # A business failure must never turn into a zero-price recommendation.
        actual = pointer_get(data, success['path'])
        if type(actual) is not type(success['equals']) or actual != success['equals']:
            raise ValueError('门店核价业务状态未成功。')
        amount = pointer_get(data, template['total_path'])
        if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
            raise ValueError('总实付金额格式无效。')
        try:
            exact = Decimal(str(amount)) * (100 if template['total_unit'] == 'yuan' else 1)
        except InvalidOperation as exc:
            raise ValueError('总实付金额无效。') from exc
        if not exact.is_finite() or exact < 0 or exact != exact.to_integral_value():
            raise ValueError('总实付必须能精确转换为非负整数分。')
        return {'total_fen': int(exact), 'evidence': {
            'tool': 'calculate-price', 'source': 'mcp', 'store_id': catalog['store']['id'],
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'total_includes_fees': True,
            'candidate_id': candidate['id'],
            'meal_mode': template['meal_mode'],
            'cart_digest': hashlib.sha256(json.dumps(args, ensure_ascii=False, sort_keys=True,
                                                      separators=(',', ':')).encode('utf-8')).hexdigest(),
        }}
    return quote
