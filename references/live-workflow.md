# 真实 MCP 工作流

真实接入需要用户自己的 MCP Token 和所选门店。官方工具文档：<https://github.com/M-China/mcd-mcp-server>。工具名称已公开，但精确参数、输出路径、金额单位需要在实际 `tools/list` 和响应中确认，项目没有硬编码猜测的业务 schema。

## 方式A：已连接MCP的宿主智能体

1. 使用客户端支持的远程 MCP 配置接入 `https://mcp.mcd.cn`，Authorization 从环境变量读取。某些客户端不会展开 `${MCD_MCP_TOKEN}`，此时使用客户端的凭证字段或环境变量功能，不能把含真实 Token 的配置上传。
2. 发现工具 schema，按真实参数查询 `query-nearby-stores`，确定门店和就餐方式；查询 `query-meals`、必要的 `query-meal-detail`、`query-store-coupons`。
3. 按 schema.md 整理标准化菜单、人员需求和完整购物车参数映射，临时文件保存 `.private/`；对未确认的排除标签保守过滤。
4. 运行 `plan` 得到最多8个候选，宿主用 `calculate-price` 分别核算整单。按实际业务成功字段与总实付字段判定成功，确认费用已包含；只输出预算内已成功核验方案。
5. 用户改需求后重复步骤3–4；变更门店或就餐方式则重新查询菜单和券。不要复用旧价格。

这种方式不要求宿主把它自己的 MCP Token 交给 Python 脚本。规划输出和真实核价都由宿主保存证据；纯离线 demo 不算该方式成功。

## 方式B：标准库客户端 + 明确核价模板

在仓库根目录将 `.env.example` 复制为 `.env`，填 `MCD_MCP_TOKEN`。然后：

```sh
python -m maimai_team discover
python -m maimai_team call --tool query-meals --args .private/menu-args.json --out .private/menu-response.json
python -m maimai_team live-plan --catalog .private/catalog.json --request .private/request.json --quote-template .private/quote-template.json
python -m maimai_team report --input .private/live-plan.json --out .private/meal-checklist.md
```

`menu-args.json` 由真实发现的 schema 填写，不是项目预置字段。所有原始工具响应默认只保存到 `.private/`，不输出到控制台。`live-plan` 需要下面的模板，其业务字段和路径由实测工具响应确定：

- `tool`: 必须是 `calculate-price`。
- `arguments`: 精确工具 arguments；用唯一对象 `{"$ref":"cart"}` 替代实际购物车数组，用 `{"$ref":"store_id"}` 替代门店 id。二者必须各出现一次，避免重复计数或对不相关购物车/门店核价。其余字段如就餐方式、优惠券、配置规格按实际 schema 明确填写；不要在其中放凭证。
- `item_templates`: 以本地 item id 为键，每项 `{argument: 精确餐品参数对象, quantity_path: JSON Pointer}`。quantity_path 必须指向原模板中已有的数量字段；不用猜字段名。不同规格/券绑定条件必须有独立 item id。
- `success`: `{path: JSON Pointer, equals: 实际业务成功值}`。成功值为字符串、整数或布尔值，类型和值都必须相等，不能把false当0。路径相对 structuredContent 或唯一文本JSON解析结果。
- `total_path`: 总实付（含所有费用）的 JSON Pointer。
- `total_unit`: `fen` 或 `yuan`；转换必须精确到整数分。
- `total_includes_fees`: 实测确认后才写 `true`。
- `meal_mode`: 与本次 arguments 所选就餐方式一致的可读说明，如到店取餐；保留在核价证据与清单中。

服务端业务失败、schema不匹配、券不可用、字段缺失或金额/费用不明确都会使核价失败；不会回退为估算成功。仅会话404时重新初始化并重试当前只读请求一次，其他错误不自动重放。持续会话失败由用户/宿主重新发现并运行只读流程。

## 最小真实验收记录

在实际凭证就绪后，至少完成一次门店/菜单/详情查询和两个候选的核价，再修改一个人的需求重新生成并核价。验证总预算、人员需求、套餐完整性及分摊合计。可以在 `artifacts/live-verification.md` 发布不含个人信息的摘要：日期、工具名称、成功/失败状态、用匿名人员的预算和价格结果、调用次数。不要发布门店位置、账号、券码、原始响应、Token或精确地址。未运行前不创建通过记录、不称已完成联调。
