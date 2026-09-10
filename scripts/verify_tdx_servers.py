"""TDX 候选池全量验活 + 清单建议（2026-09-10 根因更正后新增）。

背景: 9/10 误判"TDX 服务端停供"的真因是——硬编码清单全是"TCP 通但 bars 返空"的坏节点,
而 pytdx 内置候选池 104 条里真正可用的 8 台位于索引 70-77, 被 `[:30]` 截断逻辑全部排除。
本工具: 枚举 pytdx hq_hosts(104) + mootdx config HQ(38) 去重后全量验活, 输出报告与建议清单。
  验活判据(双层): get_security_count>0(协议) + get_security_bars 有效(数据); 仅 TCP 通不算可用。
输出: outputs/validation/tdx_servers_<date>.json + 打印可直接粘贴的清单字面量。
建议频率: 每周一次(节点会轮换/失效); 可与 YaobanTdxProbe 共用清单。
用法: python scripts/verify_tdx_servers.py [--limit N]
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import json
import pathlib
import socket
import sys
import time
from datetime import datetime

from pytdx.hq import TdxHq_API

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs" / "validation"
OUT.mkdir(parents=True, exist_ok=True)


def candidates():
    hosts = []
    try:
        from pytdx.config.hosts import hq_hosts
        for item in hq_hosts:
            hosts.append((item[1], item[2] if len(item) > 2 else 7709, item[0]))
    except Exception:
        pass
    try:
        cfg = pathlib.Path(r"C:/Users/YZP/.mootdx/config.json")
        d = json.loads(cfg.read_text(encoding="utf-8"))
        for item in d["SERVER"]["HQ"]:
            hosts.append((item[1], item[2], item[0]))
    except Exception:
        pass
    seen, uniq = set(), []
    for ip, port, name in hosts:
        if (ip, port) in seen:
            continue
        seen.add((ip, port))
        uniq.append((ip, port, name))
    return uniq


def tcp_ok(ip, port, timeout=1.5):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def validate(ip, port):
    """真实验活; 返回 (ok, count, bars, last, elapsed, err)"""
    api = TdxHq_API(heartbeat=False)
    t0 = time.time()
    try:
        if not api.connect(ip, port, time_out=5):
            return (False, 0, 0, "", time.time() - t0, "connect_false")
        try:
            cnt = api.get_security_count(1)
        except Exception:
            cnt = 0
        bars = None
        for _ in range(2):
            try:
                bars = api.get_security_bars(0, 1, "600000", 0, 5)
            except Exception:
                bars = None
            if bars and len(bars) >= 1 and float(bars[-1].get("close", 0)) > 0:
                break
            time.sleep(0.5)
        ok = bool(cnt and cnt > 0 and bars and len(bars) >= 1)
        last = "" if not bars else str(bars[-1].get("datetime", ""))
        return (ok, int(cnt or 0), 0 if not bars else len(bars), last, time.time() - t0, "" if ok else "empty_bars")
    except Exception as exc:
        return (False, 0, 0, "", time.time() - t0, type(exc).__name__)
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cands = candidates()
    if a.limit:
        cands = cands[:a.limit]
    print(f"候选池 {len(cands)} 台, TCP 预筛中...", flush=True)
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        alive_flags = list(ex.map(lambda c: tcp_ok(c[0], c[1]), cands))
    alive = [c for c, ok in zip(cands, alive_flags) if ok]
    print(f"TCP 可达 {len(alive)} 台, 真实验活中...", flush=True)
    results = []
    for i, (ip, port, name) in enumerate(alive):
        ok, cnt, bars, last, el, err = validate(ip, port)
        results.append({"ip": ip, "port": port, "name": name, "ok": ok, "count": cnt,
                        "bars": bars, "last": last, "elapsed": round(el, 2), "err": err})
        if ok:
            print(f"  DATA-OK {ip}:{port} ({name}) count={cnt} bars={bars} last={last} {el:.2f}s", flush=True)
        if (i + 1) % 20 == 0:
            print(f"  ...{i+1}/{len(alive)}", flush=True)
    good = [r for r in results if r["ok"]]
    good.sort(key=lambda r: r["elapsed"])
    day = datetime.now().strftime("%Y-%m-%d")
    report = {"date": day, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "candidates": len(cands), "tcp_alive": len(alive), "usable": len(good),
              "servers": good, "all": results}
    fp = OUT / f"tdx_servers_{day}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n可用 {len(good)} 台 (报告: {fp}):")
    literal = "[" + ",".join(f"('{r['ip']}',{r['port']})" for r in good) + "]"
    print("建议清单字面量:")
    print(literal)
    return 0 if good else 3


if __name__ == "__main__":
    sys.exit(main())
