"""Human-readable checklist; no inferred per-product actual prices."""


def money(fen):
    return f'¥{fen / 100:.2f}'


def markdown(result):
    mode = 'MCP 实价核验' if result.get('verified') else '模拟/估算 · 非实时门店价格'
    lines = ['# 麦桌搭子 · 多人点餐清单', '', mode, '',
             f"总预算：{money(result['budget_fen'])}", '']
    for n, candidate in enumerate(result.get('candidates', [])[:2], 1):
        verified = candidate.get('verified') is True
        amount = candidate.get('total_fen') if verified else candidate['estimated_total_fen']
        label = '整单核算实付' if verified else '整单估算价'
        lines += [f'## 方案 {n}', '', f'{label}：{money(amount)}',
                  f"预算余量：{money(result['budget_fen'] - amount)}", '']
        if verified:
            evidence = candidate.get('evidence', {})
            lines += [f"门店标识：{evidence.get('store_id', '未提供')}；核价时间：{evidence.get('verified_at', '未提供')}",
                      f"就餐方式：{evidence.get('meal_mode', '请核对本次核价所选方式')}", '']
        for person in candidate['assignments']:
            contents = '、'.join(f"{c['name']} ×{c['quantity']}" for c in person['components'])
            name = person['person_name'].replace('\n', ' ')
            lines.append(f'- {name}：{contents}')
        extras = candidate.get('extras', [])
        if extras:
            lines += ['', '套餐完整包含的额外餐品：' + '、'.join(
                f"{x['name']} ×{x['quantity']}" for x in extras)]
        if candidate.get('shares'):
            lines += ['', '分摊建议（按各人餐品估算成本比例分摊整单实付，尾差按最大余数分配）：']
            for share in candidate['shares']:
                lines.append(f"- {share.get('person_name', share.get('person_id'))}：{money(share['share_fen'])}")
        lines += ['', '实际购买数量：' + '、'.join(
            f"{x['id']} ×{x['quantity']}" for x in candidate['items']), '']
    if not result.get('candidates'):
        lines += ['暂无满足全部条件且预算内的已核验方案。', '']
    translations = {
        'Candidate search was bounded; results are feasible suggestions, not a proven global optimum.': '本次搜索有数量上限，结果为可行建议，不保证全局最低价。',
        'Catalog prices are estimates; availability, discounts and final payable price require MCP verification.': '菜单价格为估算，供应、优惠和整单实付须由 MCP 核验。',
        'All returned estimates exceed budget. Check MCP quotes and coupons before concluding budget is insufficient.': '当前估算方案均超预算，需确认实际核价和优惠后再判断。',
        'Search limit reached; no exhaustive feasibility claim is made.': '搜索已达上限，尚不能穷尽判断是否存在可行方案。',
    }
    lines += [translations.get(str(x), str(x)) for x in result.get('warnings', [])]
    lines += ['', '菜单、价格与供应以麦当劳官方实时结果为准；本清单未创建订单。', '']
    return '\n'.join(lines)
