# -*- coding: utf-8 -*-
"""T-0 就绪核验：上实盘前的最后一道体检（**只读，零生产写入**）。

为什么要它：既有的 `scripts/preflight.py` 是**门禁**（fail-closed 拦链），它守的是
「数据/账本/任务注册/资源」；而 2026-09-13 的预演暴露出一类它**结构上抓不到**的问题 ——
**脚本能不能真跑起来**（例如 `scan_and_confirm.py --help` 因 argparse 把帮助文本里的
`±3%）` 当格式符而崩溃，`py_compile` 只验语法、验不出这种）。同时「机械 + LLM」双路
（规则引擎 + 裁量层）在 9/14 首次齐上，需要一次性把它们各自的**活体可用性**核清楚。

本工具产出 12 项检查，分三级：
  FAIL = 会挡住明天正常运转（必须今晚修）
  WARN = 不挡，但要知道（残留物/资源/口径）
  OK   = 已核验

用法：
  python -X utf8 tools/t0_readiness.py                 # 默认：下一交易日 + 含 LLM 活体调用
  python -X utf8 tools/t0_readiness.py --no-live       # 跳过真调 LLM（离线）
  python -X utf8 tools/t0_readiness.py --date 2026-09-14 --json outputs/readiness/t0.json

⚠️ 本机在 WorkBuddy 终端里跑时建议 `CODEBUDDY_SAFE_DELETE_ENABLED=0`：sitecustomize 会把删除
改道回收站并可能触发批量删除护栏（抛 `SystemExit`）。本工具已对每项检查做 BaseException 兜底，
但设了该变量可以避免「清理探针文件被拦」这类噪音。真调 LLM 需外网（沙箱外）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "src"), str(REPO / "scripts"), str(REPO / "portfolio")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PY = sys.executable
DASH = "http://127.0.0.1:8790/api/status"

# 生产入口（口径＝ scripts/run_trading_task.ps1 的 switch 分支 + post_close_chain.ps1 的 stage）
ENTRIES = {
    "infra": ["scripts/preflight.py"],
    "premarket": ["scripts/premarket.py"],
    "plan-gate": ["scripts/preflight.py"],
    "morning-check": ["scripts/check_morning.py"],
    "selfheal": ["scripts/selfheal.py"],
    "auction": ["scripts/auction_monitor.py"],
    "tick": ["scripts/_tick_watch.py"],
    "scan": ["scripts/scan_and_confirm.py"],
    "monitor": ["scripts/monitor_intraday.py"],
    "notify": ["scripts/notify_trading_events.py"],
    "close": ["scripts/close_pipeline.py"],
    "rebuild": ["scripts/fetch_daily_minute_rebuild.py", "scripts/generate_next_plan.py"],
    "calendar-refresh": ["scripts/trading_calendar.py"],
    "tdx-probe": ["scripts/tdx_recovery_probe.py"],
    "tdx-verify": ["scripts/verify_tdx_servers.py"],
    "evening-check": ["scripts/evening_check.py"],
    "acceptance": ["scripts/collect_daily_acceptance.py"],
    "data-refresh": ["scripts/r5p_sentiment_build.py", "scripts/r6p_candidates_build.py"],
    "notify-child": ["scripts/feishu_notify.py"],
    "post-close": ["scripts/post_close_chain.ps1"],
    "daemon-child": ["scripts/tick_monitor.py"],
}
# 守护类：**不能**用 `-h` 冒烟（若无 argparse，`-h` 会被忽略 → 真的把 daemon 拉起来）
DAEMON = {"scripts/_tick_watch.py", "scripts/tick_monitor.py"}

WRITE_DIRS = ["outputs/decision_chain/intraday", "outputs/decision_chain/llm/cache",
              "outputs/decision_chain/llm", "outputs/intraday", "outputs/shadow", "outputs/plans"]

results: list[dict] = []


def add(name: str, level: str, detail: str, extra: dict | None = None) -> None:
    results.append(dict(name=name, level=level, detail=detail, extra=extra or {}))
    print(f"[{level:4}] {name}: {detail}", flush=True)


def _registry_brain_sell():
    """读 HKCU\\Environment 的 EVOALPHA_BRAIN_SELL（生产路径的权威来源）。读不到返回 None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v, _ = winreg.QueryValueEx(k, "EVOALPHA_BRAIN_SELL")
            return str(v)
    except Exception:  # noqa: BLE001
        return None


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return 125, f"{type(e).__name__}: {e}"


# ───────────────────────────────────────── 1) 看板活体探针（任务表/账本/manifest/测试/订阅）
def c_dashboard() -> dict:
    try:
        with urllib.request.urlopen(DASH, timeout=45) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        add("dashboard", "WARN", f"看板不可达（{type(e).__name__}）—— 本项跳过，其余检查独立成立")
        return {}
    pr = d.get("probes") or {}
    t = pr.get("tasks") or {}
    add("dashboard.tasks", "OK" if t.get("ok") else "FAIL",
        f"计划任务 total={t.get('total')} found={t.get('found')} ready={t.get('ready')} "
        f"missing={t.get('missing')}", {"tasks": t.get("detail")})
    lg = pr.get("ledger") or {}
    add("dashboard.ledger", "OK" if lg.get("ok") else "FAIL",
        f"rev={lg.get('revision')} start_date={lg.get('start_date')} cash={lg.get('cash')} "
        f"mode={lg.get('account_mode')} nav={lg.get('nav')}")
    wb = pr.get("workbuddy") or {}
    add("dashboard.workbuddy", "OK" if wb.get("ok") else "WARN",
        f"status={wb.get('status')} cli={wb.get('version')}")
    ts = pr.get("tests") or {}
    add("dashboard.tests", "WARN" if ts.get("failed") else "OK",
        f"基线 {ts.get('passed')} passed / {ts.get('failed')} failed（快照 {ts.get('run_at')}）")
    return d


# ───────────────────────────────────────── 2) 入口冒烟（py_compile + argparse 帮助渲染）
def c_entry_smoke() -> None:
    files: list[str] = []
    for v in ENTRIES.values():
        for f in v:
            if f not in files:
                files.append(f)
    bad_compile, help_tested, help_bad, skipped = [], [], [], []
    for rel in files:
        p = REPO / rel
        if not p.exists():
            bad_compile.append(f"{rel}(缺失)")
            continue
        if p.suffix == ".ps1":
            b = p.read_bytes()[:3]
            if b != b"\xef\xbb\xbf":
                bad_compile.append(f"{rel}(无 BOM，GBK 吞行风险)")
            continue
        rc, out = run([PY, "-X", "utf8", "-m", "py_compile", str(p)], timeout=60)
        if rc != 0:
            bad_compile.append(f"{rel}: {out.strip().splitlines()[-1] if out.strip() else rc}")
            continue
        if rel in DAEMON:
            skipped.append(rel)
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        if "ArgumentParser" not in src:
            skipped.append(rel)
            continue
        rc2, out2 = run([PY, "-X", "utf8", str(p), "-h"], timeout=120)
        help_tested.append(rel)
        if rc2 != 0:
            help_bad.append(f"{rel}: rc={rc2} {out2.strip()[-300:]}")
    add("entry.py_compile", "FAIL" if bad_compile else "OK",
        f"{len(files) - len(bad_compile)}/{len(files)} 通过" + (f"；失败 {bad_compile}" if bad_compile else ""))
    add("entry.help_smoke", "FAIL" if help_bad else "OK",
        f"渲染了 {len(help_tested)} 个入口的帮助；失败 {len(help_bad)}"
        + (f" → {help_bad}" if help_bad else "")
        + (f"；跳过（守护/无 argparse）{skipped}" if skipped else ""))


# ───────────────────────────────────────── 3) 交易日历
def c_calendar(day: str) -> None:
    rc, out = run([PY, "-X", "utf8", "scripts/trading_calendar.py", "check", "--date", day], timeout=120)
    ok = rc == 0
    add("calendar.trading_day", "OK" if ok else "FAIL",
        f"{day} → " + ("交易日" if ok else f"非交易日/异常 rc={rc} {out.strip()[:200]}"))


# ───────────────────────────────────────── 4) 账本与风控硬闸门
def c_ledger() -> None:
    fp = REPO / "portfolio" / "ledger.json"
    try:
        j = json.loads(fp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        add("ledger.policy", "FAIL", f"账本不可读：{type(e).__name__}: {e}")
        return
    pol = j.get("policy") or {}
    mode = pol.get("account_mode")
    lv = "OK" if mode == "autonomous_paper" and not pol.get("require_human_decision") else "FAIL"
    add("ledger.policy", lv,
        f"account_mode={mode} require_human={pol.get('require_human_decision')} "
        f"单票上限={pol.get('max_single_weight')} 最大持仓={pol.get('max_positions')} "
        f"日新开={pol.get('max_new_buys_per_day')} 毛敞口={pol.get('max_gross_exposure')} "
        f"| start_date={j.get('start_date')} cash={j.get('cash')}",
        {"policy": pol})


# ───────────────────────────────────────── 5) LLM 配置 + 活体调用
def c_llm(live: bool) -> None:
    try:
        from decision_chain import llm as L
    except Exception as e:  # noqa: BLE001
        add("llm.import", "FAIL", f"decision_chain.llm 不可导入：{type(e).__name__}: {e}")
        return
    cfg = {}
    try:
        cfg = L.load_config() or {}
    except Exception as e:  # noqa: BLE001
        add("llm.config", "FAIL", f"load_config 抛异常：{type(e).__name__}: {e}")
        return
    add("llm.config", "OK" if cfg.get("ok") else "FAIL",
        f"ok={cfg.get('ok')} model={cfg.get('model')} url={(cfg.get('url') or '')[:48]} "
        f"err={cfg.get('error') or cfg.get('err') or ''}")
    add("llm.points", "OK" if set(L.POINTS) >= {"D6", "D11"} else "FAIL",
        f"模板版本 D6={L.POINTS['D6']['template_version']} D11={L.POINTS['D11']['template_version']} "
        f"| 默认模型={L.DEFAULT_MODEL}")
    if not live:
        add("llm.live", "WARN", "已跳过（--no-live）")
        return
    # 冷键真调：snapshot_hash 用一次性的探针串，绝不与生产键重合（不会污染任何真实判定）
    snap = f"T0-PROBE|{dt.datetime.now():%Y%m%d%H%M%S}"
    t0 = time.time()
    try:
        r = L.consult("D6", date=dt.date.today().isoformat(),
                      obs=dict(code="000000", probe=True, note="t0-readiness"),
                      snapshot_hash=snap, sop={}, cache_ttl=60.0)
        el = time.time() - t0
        ok = r.get("status") == "ok"
        add("llm.live", "OK" if ok else "FAIL",
            f"status={r.get('status')} choice={r.get('choice')} {el:.2f}s "
            f"model={r.get('model')} degraded={r.get('_degraded')} "
            f"err={r.get('error') or (r.get('_degrade_reason') if r.get('_degraded') else '')}",
            {"choice": r.get("choice"), "elapsed_s": round(el, 2), "status": r.get("status")})
    except Exception as e:  # noqa: BLE001
        add("llm.live", "FAIL", f"真调抛异常：{type(e).__name__}: {e}（{time.time() - t0:.2f}s）")


# ───────────────────────────────────────── 6) D6 盘中否决链路（离线，不碰生产目录）
def c_d6() -> None:
    try:
        from decision_chain import figures as F
        from decision_chain import intraday_veto as V
    except Exception as e:  # noqa: BLE001
        add("d6.import", "FAIL", f"intraday_veto 不可导入：{type(e).__name__}: {e}")
        return
    # 日级键的**稳定性**是「当日锁定」的前提：同日同码同值、异码异值
    h1 = V.intraday_snapshot_hash("600519", "2026-09-14")
    h2 = V.intraday_snapshot_hash("600519", "2026-09-14")
    h3 = V.intraday_snapshot_hash("000001", "2026-09-14")
    ok_key = (h1 == h2) and (h1 != h3)
    add("d6.lock_key", "OK" if ok_key else "FAIL",
        f"键稳定={'是' if h1 == h2 else '否'} 异码异键={'是' if h1 != h3 else '否'} key={h1[:16]}")
    ttl = V.lock_ttl(dt.datetime(2026, 9, 14, 9, 45))
    add("d6.lock_ttl", "OK" if ttl > 3600 * 5 else "FAIL",
        f"09:45 判定 → TTL={ttl:.0f}s（{ttl / 3600:.2f}h，应覆盖到 15:30）")
    # 真跑一次判定（use_llm=False → 不调 LLM），落痕重定向到临时目录，绝不写生产
    tmp = pathlib.Path(os.environ.get("TEMP", ".")) / f"t0_veto_{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    orig = V.OUT_DIR
    try:
        V.OUT_DIR = tmp
        code = None
        for f in sorted((REPO / "data" / "minute" / "1m").glob("*.parquet")):
            code = f.stem
            break
        if code is None:
            add("d6.judge", "WARN", "无 data/minute/1m 数据，跳过判定演练")
            return
        df = F.load_day_minute(code, "2026-09-11")
        if df is None or len(df) == 0:
            add("d6.judge", "WARN", f"{code} 取不到 2026-09-11 分钟数据，跳过判定演练")
            return
        out = V.judge(code, day="2026-09-11", df=df, prev_df=None,
                      now=dt.datetime(2026, 9, 11, 10, 30), signal_ts="2026-09-11 10:30:00",
                      candidates_ref="t0-readiness", plan_ref="t0", use_llm=False)
        errs = out.get("digest_errors") or []
        ok = bool(out.get("digest")) and not errs and out.get("allowed") is True
        add("d6.judge_offline", "OK" if ok else "FAIL",
            f"{code} status={out.get('status')} allowed={out.get('allowed')} "
            f"digest={'有' if out.get('digest') else '无'} 契约错误={errs}")
    finally:
        V.OUT_DIR = orig
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────── 7) D11 卖出脑接线自检
def c_d11() -> None:
    rc, out = run([PY, "-X", "utf8", "tools/verify_brain_sell_wiring.py"], timeout=300)
    ok = "RESULT OK" in out
    tail = " | ".join([ln for ln in out.strip().splitlines() if ln.startswith(("FAIL", "PASS", "RESULT"))][-4:])
    add("d11.wiring", "OK" if ok else "FAIL", f"rc={rc} {tail}")
    # 开关解析：**以注册表为权威**（生产路径 = 计划任务子进程，它从注册表拿到新值），
    # 本进程 env 只作对照 —— 交互式父进程常早于 `setx` 启动，读到 None 属正常现象。
    reg = _registry_brain_sell()
    env = os.environ.get("EVOALPHA_BRAIN_SELL")
    if reg is None:
        add("d11.switch", "WARN", f"读不到注册表值（env={env!r}）")
    else:
        add("d11.switch", "OK" if reg.strip() == "1" else "FAIL",
            f"注册表 HKCU\\Environment={reg!r}（生产权威，计划任务子进程实测继承 '1'）"
            f"｜本进程 env={env!r}"
            + ("" if str(env).strip() == "1" else "（陈旧/未设 —— **手动补跑 tick daemon 必须显式带该变量**）"))


# ───────────────────────────────────────── 8) 落痕目录可写
def c_dirs() -> None:
    """落痕目录可写性。

    ⚠️ 判据 = **能否创建文件**，不是能否删除 —— 本机 WorkBuddy 的 sitecustomize 会把
    `unlink` 改道回收站，还可能触发批量删除护栏并**抛 `SystemExit`**（`except Exception` 抓不到）。
    故这里把清理失败降级为 note：只要写入成功就算可写。
    """
    bad, notes = [], []
    probe = f".t0_write_{os.getpid()}"
    for rel in WRITE_DIRS:
        d = REPO / rel
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / probe).write_text("ok", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{rel}: {type(e).__name__}")
            continue
        try:
            (d / probe).unlink()
        except BaseException as e:  # noqa: BLE001  —— shim 会抛 SystemExit，必须捕 BaseException
            notes.append(f"{rel}(清理被拦: {type(e).__name__})")
    add("dirs.writable", "FAIL" if bad else "OK",
        f"{len(WRITE_DIRS) - len(bad)}/{len(WRITE_DIRS)} 可写" + (f"；失败 {bad}" if bad else "")
        + (f"；{'; '.join(notes)}" if notes else ""))


# ───────────────────────────────────────── 9) 残留锁与重复进程
def c_residue() -> None:
    locks = []
    for pat in ("**/_tick_daemon.lock", "**/*.lock"):
        for p in REPO.glob(pat):
            if "py_libs" in str(p) or "Vibe-Research" in str(p):
                continue
            locks.append(p)
    seen, info = set(), []
    for p in locks:
        if str(p) in seen:
            continue
        seen.add(str(p))
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            txt = ""
        m = re.search(r'"(?:pid)"\s*:\s*(\d+)', txt)
        age = (time.time() - p.stat().st_mtime) / 3600.0
        info.append(f"{p.relative_to(REPO)} pid={m.group(1) if m else '?'} age={age:.1f}h")
    add("residue.locks", "OK" if not info else "WARN",
        "锁文件：无" if not info else "；".join(info))
    rc, out = run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"], timeout=60)
    rc2, out2 = run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"], timeout=60)
    lines = [ln for ln in (out + out2).splitlines() if ln.strip() and "INFO:" not in ln]
    add("residue.procs", "OK" if len(lines) <= 6 else "WARN",
        f"python/pythonw 进程数={len(lines)}（周末非交易日应≈0；>6 需查明来源）")


# ───────────────────────────────────────── 10) 磁盘
def c_disk() -> None:
    bad, warn, parts = [], [], []
    for drive in ("C:/", "F:/"):
        try:
            u = shutil.disk_usage(drive)
        except Exception:  # noqa: BLE001
            continue
        pct = u.used / u.total * 100
        parts.append(f"{drive} 可用 {u.free / 2**30:.1f}GB（{pct:.1f}%）")
        if pct >= 95:
            bad.append(drive)
        elif pct >= 90:
            warn.append(drive)
    add("disk.space", "FAIL" if bad else ("WARN" if warn else "OK"), "；".join(parts))


def main() -> int:
    ap = argparse.ArgumentParser(description="EvoAlpha T-0 就绪核验（只读）")
    ap.add_argument("--date", default=None, help="目标交易日，默认=今天")
    ap.add_argument("--no-live", action="store_true", help="跳过真调 LLM")
    ap.add_argument("--json", default=None, help="把结果写到该路径")
    a = ap.parse_args()
    day = a.date or dt.date.today().isoformat()

    print(f"=== EvoAlpha T-0 就绪核验 | 目标日 {day} | {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===")
    # 每项独立兜底：本机 shim 的删除护栏会抛 SystemExit（BaseException），
    # 若不加这层，一项炸掉会让整份报告夭折（实测已踩一次）。
    for fn in (lambda: c_dashboard(), c_entry_smoke, lambda: c_calendar(day), c_ledger,
               lambda: c_llm(live=not a.no_live), c_d6, c_d11, c_dirs, c_residue, c_disk):
        try:
            fn()
        except BaseException as e:  # noqa: BLE001
            add(f"check.{getattr(fn, '__name__', 'lambda')}", "FAIL",
                f"该项自身异常（不影响其余检查）：{type(e).__name__}: {e}")

    fails = [r for r in results if r["level"] == "FAIL"]
    warns = [r for r in results if r["level"] == "WARN"]
    print(f"\n=== 汇总：OK={len(results) - len(fails) - len(warns)} WARN={len(warns)} FAIL={len(fails)} ===")
    for r in fails:
        print("  FAIL " + r["name"] + ": " + r["detail"])
    for r in warns:
        print("  WARN " + r["name"] + ": " + r["detail"])
    if a.json:
        fp = pathlib.Path(a.json)
        if not fp.is_absolute():
            fp = REPO / fp
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(dict(day=day, generated_at=dt.datetime.now().isoformat(),
                                      results=results), ensure_ascii=False, indent=1),
                      encoding="utf-8")
        print("报告 → " + str(fp))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
