# -*- coding: utf-8 -*-
"""R0.2 验证：账本路径参数化 + mv 字段兼容归并。

分两部分：
A. 结构校验（不执行生产脚本，避免副作用）：8 个改码文件的 import 块位置 + LEDGER 引用
B. 行为校验（真跑）：EVOALPHA_LEDGER 解析、LOCK 跟随、mv 归并、configure() 切换

用法：python tools/verify_r02_ledger_path.py
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

CODEMOD_FILES = [
    "feishu_notify.py", "evening_check.py", "day_timeline.py",
    "notify_trading_events.py",
    # 2026-09-14: collect_static_readiness.py 已归档到 scripts/_legacy/（一次性脚本，
    # 期望值全面过期）；它仍带 R0.2 的 LEDGER 改动，但不再参与本结构校验。
    "collect_daily_acceptance.py", "status_push.py", "preflight.py",
]

failures: list[str] = []
oks: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    (oks if cond else failures).append(label + (f" | {detail}" if detail and not cond else ""))


# ---------- A. 结构校验 ----------
def structural() -> None:
    for name in CODEMOD_FILES:
        p = SCRIPTS / name
        src = p.read_text(encoding="utf-8-sig")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            check(False, f"A/{name} 语法", str(exc))
            continue

        # import 块必须存在
        has_block = "from ledger import LEDGER" in src
        check(has_block, f"A/{name} 有 from ledger import LEDGER")

        # 该 import 必须是 module 级（不是函数内）
        module_level = any(
            isinstance(n, ast.ImportFrom) and n.module == "ledger"
            and any(a.name == "LEDGER" for a in n.names)
            for n in tree.body
        )
        check(module_level, f"A/{name} import 在模块级")

        # 必须在 sys.path.insert(portfolio) 之后
        i_path = src.find('sys.path.insert(0, str(BASE / "portfolio"))')
        i_imp = src.find("from ledger import LEDGER")
        check(0 <= i_path < i_imp, f"A/{name} sys.path 先于 import", f"path@{i_path} imp@{i_imp}")

        # 不应再有硬编码账本路径（排除注释与文档字符串之外的代码）
        hard = [k for k in ('BASE / "portfolio" / "ledger.json"', "BASE/'portfolio'/'ledger.json'",
                            'BASE / "portfolio" / "ledger.json", {}') if k in src]
        check(not hard, f"A/{name} 无残留硬编码", str(hard))

    # _valuation_audit 单独查（已重写为 BASE 相对）
    v = (SCRIPTS / "_valuation_audit.py").read_text(encoding="utf-8-sig")
    check("EvoAlpha" in v or "pathlib.Path(__file__)" in v, "A/_valuation_audit 用 __file__ 相对路径")
    check("ledger.json" not in v, "A/_valuation_audit 无残留硬编码账本路径")


# ---------- B. 行为校验 ----------
CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])                 # portfolio
import ledger

out = {}
out["ledger"] = str(ledger.LEDGER)
out["lock"] = str(ledger.LOCK)
out["lock_follows"] = (ledger.LOCK.parent == ledger.LEDGER.parent
                       and ledger.LOCK.name == ledger.LEDGER.stem + ".lock")
out["env_seen"] = os.environ.get("EVOALPHA_LEDGER")
# configure() 进程内切换
ledger.configure(sys.argv[2])
out["after_configure"] = str(ledger.LEDGER)
out["lock_after_configure"] = str(ledger.LOCK)
# mv 归并
st = {"account": {"equity_curve": [
    {"date": "2026-08-31", "equity": 100.0, "cash": 100.0, "market_value": 0.0, "baseline": True},
    {"date": "2026-09-01", "equity": 101.0, "cash": 50.0, "mv": 51.0},
]}}
ledger._normalize(st)
rows = st["account"]["equity_curve"]
out["mv_backfilled"] = rows[0].get("mv")
out["mv_untouched"] = rows[1].get("mv")
out["curve_len"] = len(rows)
out["baseline_flag_kept"] = rows[0].get("baseline")
print(json.dumps(out))
"""


def run_child(env_ledger, alt_path, alt2) -> dict:
    env = dict(os.environ)
    env.pop("EVOALPHA_LEDGER", None)
    if env_ledger is not None:
        env["EVOALPHA_LEDGER"] = env_ledger
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-c", CHILD, str(REPO / "portfolio"), str(alt2)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60)
    if r.returncode != 0:
        raise AssertionError((r.stderr or "")[-500:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def behavioral() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="r02_verify_"))
    try:
        alt = tmp / "ledger_alt.json"
        alt.write_text(json.dumps({"_revision": 1, "start_cash": 123.0}), encoding="utf-8")
        alt2 = tmp / "ledger_bench.json"

        canon = (REPO / "portfolio" / "ledger.json")

        # 轮 1：不设 env → 必须回退 canonical（生产零改动）
        a = run_child(None, alt, alt2)
        check(Path(a["ledger"]) == canon, "B1/无 env 回退 canonical", a["ledger"])
        check(Path(a["lock"]) == REPO / "portfolio" / "ledger.lock", "B1/LOCK=ledger.lock", a["lock"])
        check(a["lock_follows"], "B1/LOCK 跟随 LEDGER")

        # 轮 2：设 env（绝对）→ 必须跟随
        b = run_child(str(alt), alt, alt2)
        check(Path(b["ledger"]) == alt, "B2/env 被读取（绝对路径）", b["ledger"])
        check(Path(b["lock"]) == alt.parent / (alt.stem + ".lock"),
              "B2/LOCK 随 env 走（不与他人共锁）", b["lock"])
        check(b["lock_follows"], "B2/LOCK 跟随 LEDGER")

        # 轮 3：设 env（相对路径）→ 相对 portfolio/ 解析
        c = run_child("ledger_bench_rel.json", alt, alt2)
        check(Path(c["ledger"]) == REPO / "portfolio" / "ledger_bench_rel.json",
              "B3/相对路径相对 portfolio/ 解析", c["ledger"])

        # configure() 三处一致
        for tag, d in (("B1", a), ("B2", b), ("B3", c)):
            check(Path(d["after_configure"]) == alt2, f"{tag}/configure() 切换生效", d["after_configure"])
            check(Path(d["lock_after_configure"]) == alt2.parent / (alt2.stem + ".lock"),
                  f"{tag}/configure() 后 LOCK 跟随", d["lock_after_configure"])

        # mv 归并（三处一致，取一次断言即可）
        check(a["mv_backfilled"] == 0.0, "B/market_value -> mv 归并", str(a["mv_backfilled"]))
        check(a["mv_untouched"] == 51.0, "B/已有 mv 不被改写", str(a["mv_untouched"]))
        check(a["curve_len"] == 2, "B/归并不增删曲线点", str(a["curve_len"]))
        check(a["baseline_flag_kept"] is True, "B/baseline 布尔标志不被破坏")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sizing() -> None:
    """R0.9：仓位定档必须用动态权益（cost_equity），不得用初始本金 start_cash。"""
    code = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import ledger\n"
        "st = {'start_cash': 500000.0, 'account': {'cash': 300000.0,\n"
        "      'positions': {'600000': {'qty': 1000, 'cost': 10.0}}}}\n"
        "print(json.dumps({'cost_equity': ledger.cost_equity(st)}))\n"
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", code, str(REPO / "portfolio")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=60)
    if r.returncode != 0:
        check(False, "C/cost_equity 子进程", (r.stderr or "")[-300:])
    else:
        got = json.loads(r.stdout.strip().splitlines()[-1])["cost_equity"]
        # cash 300000 + 1000×10 = 310000（**不等于** start_cash 500000 —— 这正是要点）
        check(got == 310000.0, "C/cost_equity = cash + Σ(cost×qty)", str(got))

    src = (SCRIPTS / "scan_and_confirm.py").read_text(encoding="utf-8-sig")
    check("equity_budget = cost_equity(state)" in src, "C/scan 以 cost_equity(state) 定档")
    # 只在**非注释行**上断言：R0.9 的说明性注释里会合法地提到 start_cash
    code_only = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    check("start_cash" not in code_only, "C/scan 代码行已无 start_cash 引用")
    imp = src.split("from ledger import", 1)[1].split("\n", 1)[0] if "from ledger import" in src else ""
    check("cost_equity" in imp, "C/scan 已导入 cost_equity", imp)


def main() -> int:
    structural()
    behavioral()
    sizing()
    print(f"PASS {len(oks)}")
    for f in failures:
        print(f"FAIL {f}")
    print("RESULT", "OK" if not failures else "FAILED")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
