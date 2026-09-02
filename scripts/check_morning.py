"""P0 开盘前晨检脚本（每个交易日 08:58 运行）。
检查 08:45-08:55 盘前任务链、preflight 报告和失败推送，输出 PASS/FAIL 报告。
2026-09-02 修复: 推送 tpoint 式交互卡片到 EvoAlpha webhook(此前仅 stdout/落盘,
用户无任何可见通知——9/2 gate 全灭事件暴露的报告盲区)。
用法: python scripts/check_morning.py [--date 2026-09-01] [--no-push]
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs'
# 2026-09-01 重构(用户评审): 系统检验必须在 09:00 前完成(09:15 集合竞价/09:30 开盘)。
# 晨检 08:58 只检查"开盘前就绪"链; 盘中连续性(scan/monitor/notify/auction)由收盘后 acceptance 覆盖。
CHAIN = [
    ('YaobanPreflight', '08:45'), ('YaobanPremarket', '08:50'), ('YaobanPlanGate', '08:55'),
]
NEXT_DAY = [('YaobanPostCloseChain', '16:30'), ('YaobanClosePipeline', '15:10')]
# 与 feishu_notify.py 同源的生产 webhook(用户 2026-08-30 23:14 设置, 2026-09-02 确认为 EvoAlpha 专用)
WEBHOOK_FILE = pathlib.Path(os.environ.get('YAOBAN_FEISHU_SECRET_FILE',
                                           r'C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt'))


def norm_date(s: str) -> str:
    """'2026/9/1 8:45:45' / '2026/9/1' -> '2026-09-01'（schtasks 地区格式规范化为 ISO）"""
    s = s.split(' ')[0]
    parts = s.split('/')
    if len(parts) == 3:
        try:
            return f'{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}'
        except ValueError:
            pass
    return s


# P0-7修复(2026-09-01): zh-CN 系统 schtasks 输出中文键（上次运行时间/上次结果等），
# 与英文键并存解析；此前仅解析英文键导致 chain 恒判失败（假阴性晨检）。
_KEY_ALIASES = {
    'Last Run Time': ('Last Run Time', '上次运行时间'),
    'Last Result': ('Last Result', '上次结果'),
    'Next Run Time': ('Next Run Time', '下次运行时间'),
    'Scheduled Task State': ('Scheduled Task State', '计划任务状态'),
}


def task_info(name: str) -> dict:
    # A1(2026-09-02, P0 计划 v2.1 批次 A): zh-CN 系统 schtasks 输出 GBK 字节——
    # text=True 无 encoding 时按 locale(或 -X utf8 下按 UTF-8)解码, 中文键成替换符,
    # 解析恒空(9/2 晨检假阴性根因, E1 实测)。显式 encoding='gbk' 使解码确定化,
    # 与 -X utf8 运行标志解耦; errors='replace' 容错未知字节。
    r = subprocess.run(['schtasks', '/Query', '/TN', chr(92) + name, '/FO', 'LIST', '/V'],
                       capture_output=True, text=True, encoding='gbk', errors='replace', timeout=45)
    info = {}
    for line in r.stdout.splitlines():
        for key, aliases in _KEY_ALIASES.items():
            if key in info:
                continue
            for alias in aliases:
                if line.startswith(alias + ':'):
                    info[key] = line.split(':', 1)[1].strip()
                    break
    if 'Last Run Time' in info:
        info['_lr_date'] = norm_date(info['Last Run Time'])
    return info


def result_zero(v) -> bool:
    """'0' / '0x0' / 0 -> True（兼容 zh-CN 十进制与十六进制结果格式）"""
    try:
        return int(str(v).strip(), 0) == 0
    except Exception:
        return False


def build_card(report: dict) -> dict:
    """tpoint 式盘前自检交互卡片(结构对齐 selfcheck_daily._build_status_text)。"""
    ok_all = report['summary']['pass']
    tpl = 'green' if ok_all else 'red'
    icon = '✅' if ok_all else '❌'
    label = '正常' if ok_all else '异常'
    md = lambda t: {'tag': 'lark_md', 'content': t}
    elements = [
        {'tag': 'div', 'text': md(f"**运行状态：{icon} {label}**　　　时间：{report['date']} 08:58")},
        {'tag': 'hr'},
        {'tag': 'div', 'text': md('**🔧 盘前任务链（08:45-08:55）**')},
    ]
    for name, at in CHAIN:
        c = report['chain'][name]
        mark = '🟢' if c['ok'] else '🔴'
        elements.append({'tag': 'div', 'text': md(
            f"{mark} {name}（{at}）：last={c['last_run'] or '无'} result={c['result'] or '无'}")})
    elements.append({'tag': 'hr'})
    elements.append({'tag': 'div', 'text': md('**📋 Gate 检查报告**')})
    for stage, title in (('infra', '基础设施'), ('post_plan', '盘前计划')):
        c = report['checks'].get(stage, {})
        if c.get('missing'):
            elements.append({'tag': 'div', 'text': md(f"🔴 {title}：报告缺失（gate 未放行，盘中任务将被拦截）")})
        elif c.get('status') == 'pass':
            elements.append({'tag': 'div', 'text': md(f"🟢 {title}：PASS（pass={c.get('pass')} fail={c.get('fail')}）")})
        else:
            elements.append({'tag': 'div', 'text': md(f"🔴 {title}：{c.get('status', '?')}（pass={c.get('pass')} fail={c.get('fail')}）")})
    fails = report.get('delivery_failures', [])
    if fails:
        elements.append({'tag': 'hr'})
        elements.append({'tag': 'div', 'text': md(f"**❌ 当日失败告警（{len(fails)} 条）**")})
        for f in fails[:6]:
            elements.append({'tag': 'div', 'text': md(f"🔴 {f['event']}（{str(f['time'])[11:19]}）")})
    if not ok_all:
        elements.append({'tag': 'hr'})
        elements.append({'tag': 'div', 'text': md(
            "**处理建议**：盘前链/gate 异常 → 盘中任务 fail-closed 拦截（无错误交易风险）；"
            "请对 WorkBuddy 助手说「例行」发起根因分析与修复。")})
    elements.append({'tag': 'hr'})
    elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content':
        f"完整报告 | outputs/validation/morning_check_{report['date']}.json  |  EvoAlpha 盘前自检 · 仅供内部运维参考"}]})
    return {'msg_type': 'interactive', 'card': {
        'header': {'template': tpl,
                   'title': {'tag': 'plain_text', 'content': f'EvoAlpha 盘前自检 · {icon} {label}'}},
        'elements': elements}}


def push_card(card: dict) -> str:
    """优先 requests(继承系统代理, tpoint 2026-08-12 同款修复); 不可用回退 urllib。"""
    try:
        hook = WEBHOOK_FILE.read_text(encoding='utf-8').strip()
        if not hook.startswith('https://open.feishu.cn/open-apis/bot/v2/hook/'):
            return 'PUSH_FAIL: invalid webhook'
        body = json.dumps(card, ensure_ascii=False).encode('utf-8')
        try:
            import requests
            resp = requests.post(hook, data=body, headers={'Content-Type': 'application/json'}, timeout=15)
            return resp.text[:60]
        except ImportError:
            req = urllib.request.Request(hook, data=body, headers={'Content-Type': 'application/json'})
            resp = urllib.request.urlopen(req, timeout=15)
            return resp.read().decode('utf-8')[:60]
    except Exception as e:
        return f'PUSH_FAIL: {e}'


def today_shanghai() -> str:
    """当前上海时区日期(ISO)。A1b(2026-09-02, P0 计划 v2.1): E12——显式固定
    Asia/Shanghai, 不依赖本机时区设置; 默认日期与 --date 参数同一取值路径。"""
    return datetime.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    ap.add_argument('--no-push', action='store_true', help='不推送飞书卡片（测试用）')
    args = ap.parse_args()
    day = args.date or today_shanghai()
    report = {'date': day, 'checks': {}, 'chain': {}, 'next_day': {}}
    # 1) 早晨链: 最后运行日期必须是 day 且结果 0
    for name, at in CHAIN:
        info = task_info(name)
        lr = info.get('Last Run Time', '')
        res = info.get('Last Result', '')
        ok = info.get('_lr_date') == day and result_zero(res)
        report['chain'][name] = {'expected_at': at, 'last_run': lr, 'result': res, 'ok': ok}
    # 2) preflight 报告
    for stage in ('infra', 'post_plan'):
        fp = OUT / f'preflight_{day}_{stage}.json'
        if fp.exists():
            try:
                d = json.loads(fp.read_text(encoding='utf-8'))
                report['checks'][stage] = {'status': d.get('status'), 'pass': d.get('summary', {}).get('pass'),
                                           'fail': d.get('summary', {}).get('fail'), 'time': d.get('time')}
            except Exception as e:
                report['checks'][stage] = {'error': str(e)}
        else:
            report['checks'][stage] = {'missing': True}
    # 3) 失败推送检查（当日 delivery 中的 failure 事件；仅统计当日 08:00 之后，
    #    忽略凌晨演练/历史遗留的 failure 推送，避免假阴性晨检）
    dl = OUT / 'notifications' / ('delivery_' + day.replace('-', '') + '.jsonl')
    failures = []
    cutoff = day + ' 08:00:00'
    if dl.exists():
        for line in dl.read_text(encoding='utf-8').splitlines():
            try:
                ev = json.loads(line)
                if ev.get('kind') == 'failure' and str(ev.get('time', '')) >= cutoff:
                    failures.append({'event': ev.get('event_key'), 'time': ev.get('time')})
            except Exception:
                pass
    report['delivery_failures'] = failures
    # 4) 明日任务就绪
    for name, at in NEXT_DAY:
        info = task_info(name)
        report['next_day'][name] = {'expected_at': at, 'state': info.get('Scheduled Task State'),
                                    'next_run': info.get('Next Run Time')}
    # 汇总
    chain_ok = all(v['ok'] for v in report['chain'].values())
    gate_ok = all(v.get('status') == 'pass' for v in report['checks'].values())
    fail_ok = len(failures) == 0
    report['summary'] = {'chain_ok': chain_ok, 'preflight_ok': gate_ok, 'no_failure_push': fail_ok,
                         'pass': chain_ok and gate_ok and fail_ok}
    # 落盘（供 08:58 定时任务自动留痕 + 跨日验证归档）
    vdir = OUT / 'validation'
    vdir.mkdir(parents=True, exist_ok=True)
    vfp = vdir / f'morning_check_{day}.json'
    vfp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    # 2026-09-02: 推送 tpoint 式卡片(此前仅落盘+stdout, 用户零可见通知)
    if not args.no_push:
        print('card_push:', push_card(build_card(report)))
    return 0 if report['summary']['pass'] else 2


if __name__ == '__main__':
    sys.exit(main())
