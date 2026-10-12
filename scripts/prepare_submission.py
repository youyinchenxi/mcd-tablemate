"""Prepare the public Issue body, without submitting it."""
import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent


def body(owner):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        raise ValueError('请填写有效GitHub用户名。')
    return (f'〖参赛申请〗\n'
            f'项目名称：麦桌搭子 — 多人点餐协调员\n'
            f'项目地址：https://github.com/{owner}/mcd-tablemate\n'
            f'项目简介：面向2–6人的办公室午餐、宿舍聚餐和朋友点餐，协调每个人的主食、饮料、配餐、口味限制与整桌预算。'
            f'基于麦当劳MCP查询真实门店菜单、套餐详情及可用优惠券，用calculate-price逐候选核验整单实付，输出按人餐品、套餐额外组成、预算余量和分摊建议。'
            f'需求变化后旧清单失效并重新规划核价；完整套餐不漏算或重复分配，预算不足及核价失败明确反馈。'
            f'项目包含可安装Skill、Python标准库规划与MCP客户端、模拟交互演示和自动化测试。\n')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    target = ROOT / '.private/issue-body.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body(args.owner), encoding='utf-8')
    print('报名正文已准备到 .private/issue-body.md；尚未提交。')


if __name__ == '__main__':
    main()
