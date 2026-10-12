# 标准输入和输出

价格单位统一为整数分。完整示例见 `examples/demo_catalog.json` 和 `examples/demo_request.json`，示例全为模拟数据。

## catalog

- `store`: `{id: string, name: string}`。真实数据的 id 来自查询工具。
- `source`: 真实菜单写 `{mode: "mcp", captured_at: ISO时间, tool: "query-meals"}`；仅是来源说明，不能代替实际调用记录。演示写 `mode: "demo"`。
- `offers`: 最多 80 个可购买选项，各含唯一 `id`、`name`、`price_fen`、`max_quantity`（可选，默认60）、完整 `components` 和实购 `items`。
- `components`: `[{name,category,tags,quantity}]`，category 为 `main`、`drink` 或 `side`，tags 如 `牛肉`、`鸡肉`、`咖啡`、`辣`。每项数量为 1–20。标签只是已核对的信息，不构成过敏原判断。
- `items`: `[{id,quantity}]`，id 为映射至实际 MCP 购物车规格的本地唯一标识；quantity 为完整 offer 所需购买数量。套餐通常对应一个实购套餐项和多个 components，不能同时购买套餐及其组成商品。

同个本地 item id 必须代表同商品、同规格、同特制、同券绑定条件。不同选择使用不同 id。门店可售状态来自当前查询；`max_quantity` 如无服务端库存数据，不得称为实库存。

## request

```json
{
  "budget_fen": 8000,
  "strategy": "balanced",
  "quote_limit": 8,
  "people": [
    {"id":"a","name":"小林","needs":{"main":1,"drink":1,"side":0},"exclude_tags":["牛肉","辣"],"must_tags":[],"prefer_tags":["鸡肉"]},
    {"id":"b","name":"小陈","needs":{"main":1,"drink":1,"side":0},"exclude_tags":[],"must_tags":["咖啡"],"prefer_tags":[]}
  ]
}
```

2–6 人，person id 唯一。每个 needs 值为 0–3，每人总需求为 1–6。`exclude_tags` 和 `must_tags` 为硬条件；`prefer_tags` 是可以未命中的软条件。`balanced` 优先偏好命中，再考虑额外餐品和价格；`budget` 优先价格，均保留便宜备选和少量估算超预算候选供核价。可比较的是有限搜索结果。

## result

`candidates` 最多 8 个。每份含 `assignments`（按 people 原顺序）、整单汇总 `items`、`estimated_total_fen`、`estimated_within_budget`、`score`、`extras`。assignment 含 `person_id`、`person_name`、`estimated_cost_fen`、完整 `components`、`offers`、`preference_hits` 和已包含在 components 中的 `extras`。不要再次添加 extras 的数量或费用。

估算状态 `ok` 只表示估算预算内；`estimated_over_budget` 不能直接解释为真实预算无解；`no_solution` 是本次搜索没有找到满足硬条件的完整组合。`search.truncated` 为真时尤其不能作穷尽判断。

核价后仅保留预算内已核验候选，增加 `total_fen`、`verified`、`evidence` 和 `shares`。shares 为 `[{person_id,person_name,share_fen}]`；合计必须等于 `total_fen`。
