"""Check publishable files and original declaration; never print matched secrets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
DECLARATION_SHA256 = 'cd28101bcb29cc8fb24b372fe91ce6b42c4a1487a54e01f7ade0c8c6a84241b7'
REQUIRED = ['README.md', 'SKILL.md', 'CONTEST_DECLARATION.md', 'MCP_INTEGRATION.md',
            'mcp-config.example.json', 'maimai_team/planner.py', 'maimai_team/live.py']


def check(require_live=False):
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f'缺少必需文件：{name}')
    declaration = ROOT / 'CONTEST_DECLARATION.md'
    if declaration.exists() and hashlib.sha256(declaration.read_bytes()).hexdigest() != DECLARATION_SHA256:
        errors.append('官方参赛声明与保存的官方原文哈希不一致。')
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    files = sorted(set(names.decode('utf-8').split('\0')) - {''})
    patterns = [re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
                re.compile(r'\bBearer\s+[A-Za-z0-9._-]{25,}'),
                re.compile(r'(?m)^\s*MCD_MCP_TOKEN\s*=\s*[\"\x27]?[A-Za-z0-9][^\n]+')]
    for name in files:
        path = Path(name)
        if '.private' in path.parts or '.env' == path.name or (path.name.startswith('.env.') and path.name != '.env.example'):
            errors.append(f'私有文件在发布集合中：{name}')
            continue
        try:
            text = (ROOT / path).read_text(encoding='utf-8')
        except UnicodeError:
            continue
        if any(pattern.search(text) for pattern in patterns):
            errors.append(f'需人工检查疑似凭证：{name}（未显示内容）')
    config_path = ROOT / 'mcp-config.example.json'
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if config != {'mcpServers': {'mcd-mcp': {'type': 'streamablehttp', 'url': 'https://mcp.mcd.cn',
                                               'headers': {'Authorization': 'Bearer ${MCD_MCP_TOKEN}'}}}}:
            errors.append('公开MCP配置必须是官方地址和环境变量占位符。')
    if require_live:
        try:
            live = json.loads((ROOT / '.private/live-plan.json').read_text(encoding='utf-8'))
            if live.get('source') != 'mcp' or live.get('verified') is not True or not live.get('candidates'):
                errors.append('还没有真实MCP预算内核价结果。')
            if not (ROOT / 'artifacts/live-verification.md').is_file():
                errors.append('缺少脱敏真实联调验收记录。')
        except (OSError, ValueError):
            errors.append('还没有真实MCP核价结果文件。')
    return errors, files


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-live', action='store_true')
    args = parser.parse_args()
    errors, files = check(args.require_live)
    print(json.dumps({'ok': not errors, 'publishable_file_count': len(files), 'errors': errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
