# 麦桌搭子 MCP 集成说明

## Server 和 Tools

接入官方远程 Server `https://mcp.mcd.cn`，采用 Streamable HTTP，Authorization 使用本机 `MCD_MCP_TOKEN`。项目标准库客户端支持 JSON/SSE、initialize与initialized通知、协议版本协商、Session ID、工具分页和参数校验。

| Tool | 在多人协调中的作用 |
|---|---|
| `query-nearby-stores` | 按用户提供位置查询门店，随后明确门店与就餐方式 |
| `query-meals` | 获取所选门店当前可售菜单，生成真实候选范围 |
| `query-meal-detail` | 确认套餐完整组成、规格和可替换项，保持实购参数与分配一致 |
| `query-store-coupons` | 获取该账号在当前门店可用的优惠条件，用于核价模板 |
| `calculate-price` | 对每个完整候选核算整单实付，含适用优惠与费用 |
| `now-time-info` | 需要时确认业务当前时间；核价证据也记录本机UTC时间 |

精确参数和金额单位依据实际工具 `inputSchema` 与响应确认，没有预置猜测的业务参数。Python 客户端允许的工具是上表的只读集合，不提供下单、取消、兑换、领券、抽奖或支付。

## 调用流程与业务价值

用户报人数、每人需求和总预算 → 确认门店/就餐方式 → 查询菜单、套餐详情和券 → 整理标准菜单 → 规划完整人员分配与最多8个整单购物车候选 → 每个候选调用 calculate-price → 移除核价失败和实际超预算候选 → 输出按人餐品、整单实付、预算余量、套餐额外组成与分摊建议。

门店、规格、券、就餐方式或任一个人的需求变化后，旧方案和价格失效。更新必要查询并重新规划/核价。价格反馈直接决定候选能否推荐，而非只把 MCP 查到的数据附在结果中。

宿主智能体可直接调用它已连接的官方工具；独立客户端入口是 `python -m maimai_team discover/call/live-plan`，用从真实 schema 建立的本机核价模板。见 [详细工作流](references/live-workflow.md)。

## 数据和验证状态

`examples/` 与本机浏览器演示全部使用模拟数据，不代表真实门店菜单或价格。2026-10-13已完成真实官方服务的工具发现、公开门店查询、菜单查询、10个餐品详情查询、门店可用券查询、两轮各8个候选整单核价和改单重算。详情见[脱敏真实验收记录](artifacts/live-verification.md)。

实际使用的业务工具包括 `query-nearby-stores`、`query-meals`、`query-meal-detail`、`query-store-coupons`、`calculate-price`；`now-time-info`为可选支持工具，本次没有调用。首轮方案菜单估算28元而整单核价46元，验证了核价会纠正促销展示价。改为咖啡后重新核价44.50元，分摊金额合计与整单一致。

`official.py`依据本次发现的schema与真实响应整理套餐和参数，优先使用正的已选数量而非所有`isDefault=1`标记，保留完整套餐与默认特制，区分不同选配的购物车身份。`prepare-live`输出的模板与真实`calculate-price`调用一致；未知结构、数量冲突或未核对类别会拒绝。

原始账号数据、菜单/券响应、人员需求及核价结果仅在 `.private/` 保存，真实 Token 仅在环境或 `.env`，两者均被 `.gitignore` 排除。公开材料使用匿名人物与模拟数据，不公开地址、凭证或券码。
