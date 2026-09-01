"""P0 开盘前晨检脚本（每个交易日 08:58 运行）。
检查 08:45-08:55 盘前任务链、preflight 报告和失败推送，输出 PASS/FAIL 报告。
用法: python scripts/check_morning.py [--date 2026-09-01]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import subprocess
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs'
# 2026-09-01 重构(用户评审): 系统检验必须在 09:00 前完成(09:15 集合竞价/09:30 开盘)。
# 晨检 08:58 只检查"开盘前就绪"链; 盘中连续性(scan/monitor/notify/auction)由收盘后 acceptance 覆盖。
CHAIN = [
    ('YaobanPreflight', '08:45'), ('YaobanPremarket', '08:50'), ('YaobanPlanGate', '08:55'),
]
NEXT_DAY = [('YaobanPostCloseChain', '16:30'), ('YaobanClosePipeline', '15:10')]


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
    r = subprocess.run(['schtasks', '/Query', '/TN', chr(92) + name, '/FO', 'LIST', '/V'],
                       capture_output=True, text=True, errors='replace', timeout=45)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    args = ap.parse_args()
    import datetime
    day = args.date or datetime.date.today().isoformat()
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
    return 0 if report['summary']['pass'] else 2


if __name__ == '__main__':
    sys.exit(main())
