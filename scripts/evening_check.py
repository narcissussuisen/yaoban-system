"""EvoAlpha 晚间核验 v1（2026-09-10 合并两个 WorkBuddy 定时任务）。

合并来源：A) 16:00《P0 观察窗口每日核对与推送》 B) 18:00《盘后链完成核验》。
三处增强（均由 9/10 事故暴露）：
  1. 时间挪到 19:30（链常 17:15-18:30 完成，原 18:00 常只能报仍在运行）；
  2. 检查从日期/存在性升级为日期+数据合理性断言——9/10 事故形态是产物齐全但数据错
     （daily_rebuilt 5291 只历史被截成 1 行 -> 情绪表 zt=1654 而日期正确），纯日期检查会全 PASS 漏报；
  3. 成交合规改为当日全部 confirm 快照并集 + ledger 反查——原实现只看最新一份快照，
     9/10 实测最新快照 fill_ids 为空导致当日两笔成交未被审计。
窗口计数机制已失效（9/2-9/8 五日全非绿），本脚本不再计数，改为每日只读核验。
只读保证：仅读项目产物；唯一写入是核验报告 outputs/validation/evening_check_<date>.json
与飞书推送审计（delivery_*.jsonl），不改动账本/计划/数据。
用法：python scripts/evening_check.py [--date 2026-09-10] [--no-push]
"""
from __future__ import annotations
import argparse
import datetime as _dt
import glob
import json
import pathlib
import random
import sys
from datetime import datetime

import pandas as pd

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源
OUT = BASE / "outputs"
REBUILT = pathlib.Path("F:/WorkBuddyItem/a股level2/daily_rebuilt")


def _json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def latest_task_log(day, mode):
    files = sorted(glob.glob(str(OUT / "task_logs" / day / ("*_" + mode + ".json"))))
    if not files:
        return None, None
    return files[-1], _json(files[-1])


def _hhmmss(v):
    return str(v)[11:19]


def check_close(day):
    fp, d = latest_task_log(day, "close")
    if not d:
        return {"ok": False, "exit": None, "text": "未找到当日 close 运行记录"}
    rc = d.get("exit_code")
    return {"ok": rc == 0, "exit": rc, "file": pathlib.Path(fp).name,
            "text": "exit=" + str(rc) + " (" + _hhmmss(d.get("finished_at")) + ")"}


def check_chain(day):
    fp, d = latest_task_log(day, "post-close")
    if not d:
        return {"state": "running", "text": "未完成（json 未落盘：链仍在运行或未启动）"}
    rc = d.get("exit_code")
    fin = _hhmmss(d.get("finished_at"))
    data_bad = 0
    acc_code = None
    mf = OUT / "acceptance" / ("chain_manifest_" + day + ".jsonl")
    if mf.exists():
        seen = {}
        for ln in mf.read_text(encoding="utf-8-sig").splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            seen[r.get("stage")] = r
        data_bad = sum(1 for s, r in seen.items() if s != "acceptance" and r.get("status") == "failed")
        if "acceptance" in seen:
            acc_code = seen["acceptance"].get("exit_code")
    return {"state": "done", "exit": rc, "finished": fin, "data_bad": data_bad, "acc_code": acc_code,
            "text": "exit=" + str(rc) + " 完成 " + fin + "；数据段失败=" + str(data_bad) + " 验收段=" + str(acc_code)}


def check_sentiment(day):
    try:
        df = pd.read_csv(OUT / "sentiment_full_2026.csv")
        df["date"] = df["date"].astype(str).str[:10]
        last = df.iloc[-1]
        last_date = str(last["date"])[:10]
        zt = int(last.get("zt", -1))
        up = int(last.get("up_count", -1))
        down = int(last.get("down_count", -1))
        issues = []
        if last_date != day:
            issues.append("停在 " + last_date)
        if not (0 <= zt <= 400):
            issues.append("zt=" + str(zt) + " 越界(合理 0-400)")
        if up > 0 and down > 0 and not (3000 <= up + down <= 7000):
            issues.append("涨跌家数合计=" + str(up + down) + " 异常")
        txt = "更新至 " + last_date + "（zt=" + str(zt) + " 涨=" + str(up) + " 跌=" + str(down) + "）"
        if issues:
            txt += " ⚠ " + "；".join(issues)
        return {"ok": not issues, "date": last_date, "zt": zt, "text": txt, "issues": issues}
    except Exception as exc:
        return {"ok": False, "text": "读取失败 " + type(exc).__name__, "issues": ["read_fail"]}


def check_candidates(day):
    try:
        df = pd.read_csv(OUT / "r6p_candidates_2026.csv", usecols=["date"])
        mx = str(df["date"].max())[:10]
        txt = "更新至 " + mx + "（" + str(len(df)) + " 行）"
        if mx != day:
            txt += " ⚠ 滞后于当日"
        return {"ok": mx == day and len(df) > 1000, "date": mx, "rows": len(df), "text": txt}
    except Exception as exc:
        return {"ok": False, "text": "读取失败 " + type(exc).__name__}


def check_next_plan(day):
    nxt = (_dt.date.fromisoformat(day) + _dt.timedelta(days=1)).isoformat()
    p = _json(OUT / "plans" / (nxt + "_plan.json"))
    if not p:
        return {"ok": False, "next": nxt, "text": "缺失 " + nxt + "_plan.json"}
    mode = str(p.get("mode", ""))
    picks = p.get("picks") or []
    emo = p.get("emotion") or {}
    issues = []
    if day not in mode:
        issues.append("mode 未含输入≤" + day + "收盘")
    if not picks:
        issues.append("picks 为空")
    try:
        ptemp = float(emo.get("temp"))
        if not (0 <= ptemp <= 100):
            issues.append("计划情绪温度=" + str(ptemp) + " 越界")
    except (TypeError, ValueError):
        issues.append("计划缺 emotion.temp 或非数值")
    txt = "就绪 " + nxt + "（" + mode + "，picks=" + str(len(picks)) + "）"
    if issues:
        txt += " ⚠ " + "；".join(issues)
    return {"ok": not issues, "next": nxt, "picks": len(picks), "text": txt, "issues": issues}


def check_rebuilt(day, sample=200):
    try:
        led = _json(LEDGER) or {}
        holds = list((led.get("account", {}).get("positions") or {}).keys())
        files = list(REBUILT.glob("*.parquet"))
        if not files:
            return {"ok": False, "text": "daily_rebuilt 目录为空"}
        random.seed(11)
        pick = random.sample(files, min(sample, len(files)))
        for h in holds:
            fp = REBUILT / (h + ".parquet")
            if fp.exists() and fp not in pick:
                pick.append(fp)
        ok_days = 0
        min_rows = 10 ** 9
        thin = []
        for fp in pick:
            try:
                d = pd.read_parquet(fp, columns=["date"])
                d["date"] = d["date"].astype(str).str[:10]
                if str(d["date"].iloc[-1])[:10] == day:
                    ok_days += 1
                n = len(d)
                if n < min_rows:
                    min_rows = n
                if n < 60:
                    thin.append(fp.stem + "(" + str(n) + ")")
            except Exception:
                pass
        cov = ok_days / max(len(pick), 1)
        issues = []
        if cov < 0.9:
            issues.append("当日覆盖率 " + format(cov, ".1%") + " < 90%")
        if len(thin) > len(pick) * 0.05:
            issues.append("行数过薄 " + str(len(thin)) + "/" + str(len(pick)) + " 只(<60 行)，疑历史被截断")
        txt = "抽样 " + str(len(pick)) + " 只：含今日 " + format(cov, ".1%") + "，最小行数 " + str(min_rows)
        if issues:
            txt += " ⚠ " + "；".join(issues)
        return {"ok": not issues, "coverage": round(cov, 4), "min_rows": min_rows, "thin": thin[:5], "text": txt, "issues": issues}
    except Exception as exc:
        return {"ok": False, "text": "检查失败 " + type(exc).__name__}


def check_acceptance(day):
    a = _json(OUT / "acceptance" / ("acceptance_" + day + ".json"))
    if not a:
        return {"ok": None, "text": "final 报告缺失（链未完成？）"}
    failed = [k for k, v in (a.get("checks") or {}).items() if v is False]
    txt = "final=" + str(a.get("status"))
    if failed:
        txt += "（" + str(len(failed)) + " 项未过：" + ",".join(failed) + "）"
    return {"ok": a.get("status") == "pass", "status": a.get("status"), "failed": failed, "text": txt}


def _ts(x):
    s = str(x)
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S") if len(s) > 16 else datetime.strptime(s[:16], "%Y-%m-%d %H:%M")


def check_fills(day):
    led = _json(LEDGER) or {}
    fills = [f for f in (led.get("account", {}).get("fills") or []) if f.get("date") == day]
    decs = led.get("autonomous_decisions") or {}
    snaps = sorted(glob.glob(str(OUT / "intraday" / ("confirm_" + day.replace("-", "") + "*.json"))))
    snap_fills = set()
    for s in snaps:
        d = _json(s) or {}
        for fid in (d.get("fill_ids") or []):
            snap_fills.add(str(fid))
    issues, boundary = [], []
    for f in fills:
        sym = str(f.get("sym"))
        did = str(f.get("decision_id") or "")
        if not did:
            issues.append(sym + " decision_id 为空")
            continue
        if did not in decs:
            issues.append(sym + " decision_id 未登记")
        try:
            lag = (_ts(f.get("signal_ts")) - _ts(f.get("decision_ts"))).total_seconds()
            if _ts(f.get("decision_ts")) > _ts(f.get("recorded_at")):
                issues.append(sym + " decision_ts > recorded_at")
            if lag > 60:
                issues.append(sym + " signal_ts 早于 decision_ts " + str(int(lag)) + "s > 60s 容差")
            elif lag > 50:
                boundary.append(sym + " 临界 " + str(int(lag)) + "s/60s")
        except Exception:
            boundary.append(sym + " 时间字段未解析")
    txt = str(len(fills)) + " 笔（快照并集命中 " + str(len(snap_fills)) + "）"
    if issues:
        txt += " ⚠ " + "；".join(issues)
    if boundary:
        txt += " [临界：" + "；".join(boundary) + "]"
    return {"ok": not issues, "fills": len(fills), "snapshots": len(snaps), "snap_fills": len(snap_fills),
            "issues": issues, "boundary": boundary, "text": txt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    day = a.date or datetime.now().strftime("%Y-%m-%d")
    res = {"date": day, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    res["chain"] = check_chain(day)
    res["close"] = check_close(day)
    res["sentiment"] = check_sentiment(day)
    res["candidates"] = check_candidates(day)
    res["plan"] = check_next_plan(day)
    res["rebuilt"] = check_rebuilt(day)
    res["acceptance"] = check_acceptance(day)
    res["fills"] = check_fills(day)
    blockers = []
    if res["sentiment"]["issues"] or res["candidates"].get("ok") is False or res["plan"].get("ok") is False or res["rebuilt"].get("ok") is False:
        blockers.append("数据就绪/合理性断言未过，明日 gate 可能拦截")
    if res["fills"]["issues"]:
        blockers.append("成交合规有违规项")
    if res["chain"]["state"] == "running":
        blockers.append("盘后链仍在运行（本次核验未覆盖链产物）")
    res["verdict"] = "明日早盘可正常放行" if not blockers else "需处理：" + "；".join(blockers)
    rep = OUT / "validation" / ("evening_check_" + day + ".json")
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["【EvoAlpha 晚间核验 · " + day + "】"]
    L.append("· 盘后链(16:30): " + res["chain"]["text"])
    L.append("· 收盘链(15:10): " + res["close"]["text"])
    L.append("· 情绪表: " + res["sentiment"]["text"])
    L.append("· 候选表: " + res["candidates"]["text"])
    L.append("· 明日计划: " + res["plan"]["text"])
    L.append("· daily_rebuilt: " + res["rebuilt"]["text"])
    L.append("· 当日验收: " + res["acceptance"]["text"])
    L.append("· 成交合规: " + res["fills"]["text"])
    L.append("· 结论: " + res["verdict"])
    if blockers:
        L.append("今晚有约 13 小时运维缓冲，请对助手说「例行」发起修复")
    msg = chr(10).join(L)
    print(msg)
    if not a.no_push:
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from feishu_notify import send_text
            kind = "failure" if blockers else "alert"
            ok = send_text(msg, event_key="evening-check:" + day, kind=kind)
            print("push:", "OK" if ok else "FAIL")
        except Exception as exc:
            print("push error: " + type(exc).__name__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
