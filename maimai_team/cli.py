import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / '.private'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def private_output(path):
    target = Path(path).resolve()
    if not target.is_relative_to(PRIVATE.resolve()):
        raise ValueError('原始 MCP 响应只能写入项目 .private/，不会打印到控制台或提交到 GitHub。')
    return target


def make_parser():
    parser = argparse.ArgumentParser(prog='python -m maimai_team', description='麦桌搭子 · 多人点餐协调员')
    commands = parser.add_subparsers(dest='command', required=True)
    web = commands.add_parser('serve', help='启动本机模拟演示')
    web.add_argument('--port', type=int, default=8765)
    demo = commands.add_parser('demo', help='运行三人80元模拟示例')
    demo.add_argument('--out')
    planning = commands.add_parser('plan', help='从标准化菜单生成有限候选，价格尚未核验')
    planning.add_argument('--catalog', required=True)
    planning.add_argument('--request', required=True)
    planning.add_argument('--out', required=True)
    live = commands.add_parser('live-plan', help='生成候选并通过官方 MCP 核算整单价格')
    live.add_argument('--catalog', required=True)
    live.add_argument('--request', required=True)
    live.add_argument('--quote-template', required=True)
    live.add_argument('--out', default=str(PRIVATE / 'live-plan.json'))
    normalize = commands.add_parser('prepare-live', help='按实测官方结构整理菜单、完整套餐与核价模板')
    normalize.add_argument('--menu', required=True)
    normalize.add_argument('--details', required=True, help='code到已解析详情payload的JSON映射')
    normalize.add_argument('--profile', required=True, help='宿主已核对的组件分类与选配清单')
    normalize.add_argument('--store', required=True, help='实际门店的id/name对象')
    normalize.add_argument('--out-dir', default=str(PRIVATE))
    discover = commands.add_parser('discover', help='发现真实工具schema，保存到 .private/')
    discover.add_argument('--out', default=str(PRIVATE / 'tools.json'))
    call = commands.add_parser('call', help='按真实schema调用只读工具，保存原始响应')
    call.add_argument('--tool', required=True)
    call.add_argument('--args', required=True, help='包含精确 arguments 的 JSON 文件')
    call.add_argument('--out', required=True)
    report = commands.add_parser('report', help='把规划结果转为 Markdown 点餐清单')
    report.add_argument('--input', required=True)
    report.add_argument('--out', required=True)
    commands.add_parser('doctor', help='检查接入准备情况，不输出凭证')
    return parser


def main(argv=None):
    # Windows redirected consoles may default to a legacy codepage.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = make_parser().parse_args(argv)
    try:
        if args.command == 'serve':
            from .server import serve
            if not 1024 <= args.port <= 65535:
                raise ValueError('端口应在1024–65535之间。')
            serve(args.port)
        elif args.command == 'demo':
            from .server import demo_data, demo_plan
            from .report import markdown
            result = demo_plan(demo_data()['request'])
            if args.out:
                write_json(args.out, result)
            print(markdown(result))
        elif args.command == 'plan':
            from .planner import plan
            result = plan(read_json(args.catalog), read_json(args.request))
            result.update(source='estimate', verified=False)
            write_json(args.out, result)
            print(f"候选 {len(result['candidates'])} 个；仅估算，未真实核价。已保存到 {args.out}")
        elif args.command == 'discover':
            from .mcp import MCPClient
            out = private_output(args.out)
            with MCPClient() as client:
                tools = client.list_tools()
            write_json(out, {'source': 'https://mcp.mcd.cn',
                             'captured_at': datetime.now(timezone.utc).isoformat(), 'tools': tools})
            print(f'已发现 {len(tools)} 个工具，schema已写入 .private/；未打印原始响应。')
        elif args.command == 'call':
            from .mcp import MCPClient
            out = private_output(args.out)
            with MCPClient() as client:
                result = client.call_tool(args.tool, read_json(args.args))
            write_json(out, {'tool': args.tool, 'captured_at': datetime.now(timezone.utc).isoformat(),
                             'result': result})
            print('只读工具响应已写入 .private/；未打印账号、门店或券信息。')
        elif args.command == 'live-plan':
            from .mcp import MCPClient
            from .planner import plan
            from .pricing import verify_candidates
            from .live import quote_callback
            catalog, request, template = read_json(args.catalog), read_json(args.request), read_json(args.quote_template)
            result = plan(catalog, request)
            out = private_output(args.out)
            with MCPClient() as client:
                result = verify_candidates(result, request, quote_callback(client, catalog, template))
            write_json(out, result)
            print(f"核价结果：{result.get('status')}；预算内已核验方案 {len(result['candidates'])} 个。结果仅保存在 .private/。")
            if not result['candidates']:
                return 2
        elif args.command == 'prepare-live':
            from .official import build_snapshot
            from .live import payload
            out = private_output(Path(args.out_dir) / 'catalog.json')
            menu = read_json(args.menu)
            if 'result' in menu:
                menu = payload(menu['result'])
            elif 'content' in menu or 'structuredContent' in menu:
                menu = payload(menu)
            catalog, template = build_snapshot(menu, read_json(args.details), read_json(args.profile), read_json(args.store))
            write_json(out, catalog)
            write_json(out.parent / 'quote-template.json', template)
            print(f"已整理 {len(catalog['offers'])} 个完整选项与核价模板，保存于 .private/。价格仍需真实核价。")
        elif args.command == 'report':
            from .report import markdown
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown(read_json(args.input)), encoding='utf-8')
            print(f'清单已保存到 {target}')
        elif args.command == 'doctor':
            from .mcp import load_token
            configured = bool(load_token(ROOT / '.env'))
            print(json.dumps({'python': sys.version.split()[0], 'token_configured': configured,
                              'demo_catalog_present': (ROOT / 'examples/demo_catalog.json').exists(),
                              'web_present': (ROOT / 'web/index.html').exists(),
                              'live_validation_record_present': (ROOT / 'artifacts/live-verification.md').exists()},
                             ensure_ascii=False, indent=2))
    except Exception as exc:
        from .mcp import MCPError
        if isinstance(exc, MCPError):
            print(f'接入失败：{exc}', file=sys.stderr)
        elif isinstance(exc, (ValueError, OSError, KeyError, TypeError)):
            # Never echo raw input or private file contents in error messages.
            print(f'未完成：{type(exc).__name__}。请检查输入文件和接入配置。', file=sys.stderr)
        else:
            print(f'未完成：{type(exc).__name__}。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
