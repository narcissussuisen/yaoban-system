"""TDX 恢复监测（2026-09-10 事故后新增）。

背景: 9/10 09:15 起 TDX 中继行情整体停供——协议层正常应答(get_security_count 0.05s)但
数据载荷全空(39 节点/mootdx/pytdx/bestip 全测); 同日生产四路降级腾讯备胎(a-stock-data
备用源速查)。本脚本工作日每 30 分钟探测, 用于:
  1) 恢复即飞书通知(event_key=tdx-recovered:<date>, 每日至多一条);
  2) 状态留痕 outputs/validation/tdx_state.json + 当日采样序列 tdx_probe_<date>.json。

判据(双层, 任一失败即视为不可用):
  - 协议层: get_security_count(深市) 返回正整数;
  - 数据层: get_security_bars(5 分钟, 600000) 返回 >=2 根且 close>0(真实取数验活)。
用法: python scripts/tdx_recovery_probe.py
"""
from __future__ import annotations
import json
import pathlib
import sys
import time
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'validation'
OUT.mkdir(parents=True, exist_ok=True)
SERVERS = [('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]


def probe_server(api_factory, host, port):
    """单服务器双层探测 -> dict(protocol_ok, data_ok, bars, last, err)"""
    api = None
    res = {'server': host + ':' + str(port), 'protocol_ok': False, 'data_ok': False,
           'bars': 0, 'last': '', 'err': ''}
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        try:
            s.connect((host, port))
        finally:
            s.close()
        api = api_factory(heartbeat=False)
        if not api.connect(host, port, time_out=5):
            res['err'] = 'connect_false'
            return res
        try:
            cnt = api.get_security_count(1)
            res['protocol_ok'] = bool(cnt and int(cnt) > 0)
        except Exception as exc:
            res['err'] = 'count:' + type(exc).__name__
            return res
        for _ in range(2):
            try:
                bars = api.get_security_bars(0, 1, '600000', 0, 5)
            except Exception:
                bars = None
            if bars and len(bars) >= 2 and float(bars[-1].get('close', 0)) > 0:
                res['data_ok'] = True
                res['bars'] = len(bars)
                res['last'] = str(bars[-1].get('datetime', ''))
                return res
            time.sleep(1.0)
        res['err'] = res['err'] or 'empty_bars'
    except Exception as exc:
        res['err'] = type(exc).__name__
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:
                pass
    return res


def load_state():
    try:
        return json.loads((OUT / 'tdx_state.json').read_text(encoding='utf-8'))
    except Exception:
        return {}


def main() -> int:
    from pytdx.hq import TdxHq_API
    now = datetime.now()
    day = now.strftime('%Y-%m-%d')
    results = []
    winner = None
    for host, port in SERVERS:
        r = probe_server(TdxHq_API, host, port)
        results.append(r)
        if r['data_ok']:
            winner = r
            break
    state = 'up' if winner else 'down'
    prev = load_state()
    prev_state = prev.get('state')
    row = {'ts': now.strftime('%Y-%m-%d %H:%M:%S'), 'state': state,
           'server': (winner or {}).get('server', ''),
           'bars': (winner or {}).get('bars', 0), 'last': (winner or {}).get('last', ''),
           'probed': len(results),
           'protocol_any': any(x['protocol_ok'] for x in results)}
    seq_fp = OUT / f'tdx_probe_{day}.json'
    try:
        seq = json.loads(seq_fp.read_text(encoding='utf-8'))
        if not isinstance(seq, list):
            seq = []
    except Exception:
        seq = []
    seq.append(row)
    seq_fp.write_text(json.dumps(seq[-200:], ensure_ascii=False, indent=1), encoding='utf-8')
    (OUT / 'tdx_state.json').write_text(
        json.dumps({'date': day, 'state': state, 'since': (prev.get('since') if prev_state == state else row['ts']),
                    'last_check': row['ts'], 'server': row['server'], 'detail': row},
                   ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(row, ensure_ascii=False))
    if state == 'up' and prev_state == 'down':
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            from feishu_notify import send_text
            send_text(f"[TDX恢复] {row['ts']} 通达信行情恢复可用\n节点: {row['server']} bars={row['bars']} last={row['last']}\n"
                      f"说明: 生产自 TDX 停供起降级腾讯备胎, tick/scan/monitor/preflight 已自动优先 TDX(无需人工切换)。",
                      event_key=f'tdx-recovered:{day}', kind='alert')
        except Exception as exc:
            print('recovery notify failed: ' + type(exc).__name__, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
