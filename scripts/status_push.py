"""EvoAlpha 关键节点状态卡推送 v1（2026-09-11，用户裁定）。

在每个时间关键节点，把当日链路的进展与健康度渲染成一张飞书交互卡片推到 EvoAlpha 群。
本模块是**只读观测者**：只消费既有产物，不写任何生产文件、不改门禁判定、不碰账本与计划。

边界（与 selfheal.py 同一约定）:
- 只读: task_logs / outputs 下的既有产物 / portfolio/ledger.json / trading_calendar 缓存
- 只写: outputs/notifications/status_push_<date>.json(观测状态)
        outputs/notifications/delivery_<YYYYMMDD>.jsonl(推送审计, kind=status)
        outputs/intraday/status_push.latest.log(由 run_status_push.ps1 重定向)
        失败升级时追加 outputs/selfcheck/anomalies.log
- 恒 exit 0: 与 evening_check / tdx-probe 同惯例，状态由卡片结论承载，避免与自身告警重复推送。

去重与补推:
- 状态文件按 (date, node) 去重，同日同节点同状态只推一次；
- 节点到点后来源未就绪则记 pending，由计划任务的 5 分钟重复窗自动补推（不在同一轮内重试）；
- 连续 PENDING_ALERT_ROUNDS 轮来源缺失才升级为 1 条 failure 告警（每节点每日至多 1 条）。

自证（避免手动/重复触发产生假卡）:
- 查询本任务 schtasks 的「下次运行时间」；若不在当日，说明本次就是该时刻的计划实例，
  记为一次 tick 后直接退出（不推导、不推送）。

用法:
  python -X utf8 scripts/status_push.py --node run                 # 生产: 本轮到点的全部节点
  python -X utf8 scripts/status_push.py --node preflight --dry-run # 本地演练: 只渲染不发
  python -X utf8 scripts/status_push.py --node preflight --force   # 人工补发单节点(须已到点)
  python -X utf8 scripts/status_push.py --date 2026-09-10 --node preflight --dry-run --allow-early
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源
OUT = BASE / "outputs"
NOTIF = OUT / "notifications"
STATE_DIR = OUT / "selfcheck"
SCRIPTS = BASE / "scripts"
TZ = ZoneInfo("Asia/Shanghai")

TASK_NAME = "EvoAlphaStatusPush"
PENDING_ALERT_ROUNDS = 3  # 连续 3 轮(计划任务 5 分钟重复窗)来源缺失 → 升级 1 条告警
DEADLINE_GRACE = datetime.timedelta(minutes=1)  # 触发时刻 + 宽限, 吸收计划任务启动抖动

WEBHOOK_FILE = pathlib.Path(os.environ.get(
    "YAOBAN_FEISHU_SECRET_FILE", r"C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt"))

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"
MARK = {PASS: "🟢", WARN: "🟡", FAIL: "🔴", INFO: "⚪"}

_warnings: list[str] = []
_audits: list[dict] = []


# ---------------------------------------------------------------- 基础工具

def _now() -> datetime.datetime:
    return datetime.datetime.now(TZ).replace(tzinfo=None)


def _read_text(p: pathlib.Path) -> str:
    """utf-8-sig -> gbk -> latin-1 逐级回退（仓库内编码混杂: 任务日志带 BOM、anomalies.log 是 GBK）。"""
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _json(p: pathlib.Path, default=None):
    try:
        return json.loads(_read_text(p))
    except Exception:
        return default


def _jsonl(p: pathlib.Path) -> list:
    rows = []
    if not p.exists():
        return rows
    for line in _read_text(p).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _mtime_day(p: pathlib.Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _start_cash(state: dict) -> float | None:
    """从账本取起始本金；**不得回落到历史口径**（2026-09-15 修复）。

    旧实现为 `float(ledger.get("start_cash", 100000) or 100000)` —— R0.2 主账本迁移到 50 万后，
    一旦账本缺该键（损坏/半写），卡片会**静默按 10 万**算累计收益：数字看着正常、实则错，
    而且没有任何告警。现改为显式降级：取不到返回 None，调用方改为「不显示」，并记一条 warning。
    """
    raw = state.get("start_cash")
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    _warnings.append(f"账本 start_cash 不可用({raw!r})，累计收益改为不显示（不回落到历史本金）")
    return None


def _short(text, n: int) -> str:
    s = str(text)
    return s if len(s) <= n else s[: n - 1] + "…"


def _pid_alive(pid: int) -> bool:
    """尽力而为地判断 PID 是否存活; 判断不了返回 True(不掩盖在跑的事实)。"""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return True


# ---------------------------------------------------------------- 交易日历

def _calendar_check(day: str) -> int:
    """0=交易日 / 3=非交易日 / 4=未知。调用失败按 4(未知)处理 → 调用方 fail-open。"""
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "trading_calendar.py"),
                            "check", "--date", day], capture_output=True, timeout=180)
        return int(r.returncode)
    except Exception:
        return 4


def _next_trading_day(day: str) -> str:
    """缓存内第一个晚于 day 的交易日; 无缓存退化为自然日 +1（仅用于找次日计划文件名）。"""
    try:
        d = datetime.datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return day
    for year in (d.year, d.year + 1):
        p = OUT / "calendar" / f"trade_dates_{year}.json"
        data = _json(p)
        if not isinstance(data, dict):
            continue
        days = data.get("days") or {}
        later = [k for k, v in days.items() if str(v) == "1" and k > day]
        if later:
            return min(later)
    return (d + datetime.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- 任务自证

_ALIASES = {
    "next": ("Next Run Time", "下次运行时间"),
    "last": ("Last Run Time", "上次运行时间"),
}


def _task_field(text: str, key: str) -> str:
    for alias in _ALIASES[key]:
        for line in text.splitlines():
            if line.strip().startswith(alias):
                return line.split(":", 1)[1].strip() if ":" in line else ""
    return ""


def _norm_day(raw: str) -> str:
    """'2026/9/14 8:35:00' -> '2026-09-14'（zh-CN schtasks 地区格式规范化）。"""
    s = str(raw or "").strip().split(" ")[0]
    parts = s.split("/")
    if len(parts) == 3:
        try:
            return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except ValueError:
            return s
    return s


def _self_next_run_day() -> str:
    """本任务 schtasks 的「下次运行时间」日期; 查询失败或无法解析返回 'unknown'。"""
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", "\\" + TASK_NAME, "/FO", "LIST", "/V"],
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            return "unknown"
        raw = r.stdout
        for enc in ("gbk", "utf-8-sig", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return "unknown"
        norm = _norm_day(_task_field(text, "next"))
        return norm if len(norm) == 10 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- 状态文件

def _state_path(day: str) -> pathlib.Path:
    return NOTIF / f"status_push_{day}.json"


def _load_state(day: str) -> dict:
    st = _json(_state_path(day))
    if not isinstance(st, dict) or st.get("date") != day:
        st = {"date": day, "nodes": {}, "ticks": [], "warned": []}
    st.setdefault("nodes", {})
    st.setdefault("ticks", [])
    if not isinstance(st.get("warned"), list):
        st["warned"] = []
    return st


def _atomic_json(p: pathlib.Path, value) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _save_state(day: str, st: dict) -> None:
    _atomic_json(_state_path(day), st)


def _anomaly(text: str) -> None:
    """把观测器自身的异常写入既有异常日志, 保证「失败可见」而不另造一套日志。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _now().strftime("%Y-%m-%d %H:%M:%S")
        with (STATE_DIR / "anomalies.log").open("a", encoding="gbk", errors="replace") as fh:
            fh.write(f"{stamp} [status-push] {text}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 推送

def _webhook() -> str:
    value = os.environ.get("YAOBAN_FEISHU_WEBHOOK", "").strip()
    if not value and WEBHOOK_FILE.exists():
        value = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
    if not value.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
        raise RuntimeError("Feishu webhook is missing or invalid")
    return value


def _audit_row(kind: str, event_key: str, text: str, ok: bool, status, code, error=None) -> dict:
    return {"time": _now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "event_key": event_key,
            "message_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "http_status": status, "business_code": code, "ok": ok, "error_type": error}


def _audit_append(rows: list, day: str = "") -> None:
    """推送审计落盘（`delivery_<YYYYMMDD>.jsonl`）。

    ⚠️ 2026-09-12 修（真缺陷，非测试问题）：原文件名用 `_now():%Y%m%d`（**运行当天**），
    而**读取侧**（`:398` / `:574` / `:760`）用的是 `day` —— 属**双源命名**。
    生产不传 `--date` 时两者恰好相等所以一直看不出来；但**补发 / 指定日期回放**时，
    会写入 `delivery_<今天>` 而读取 `delivery_<指定日>` → **刚写的审计行立刻读不到**，
    补发对账会静默漏行。
    现统一口径：**显式 `day` 优先 → 否则从 `rows[*].event_key` 里的 `YYYY-MM-DD` 推 → 最后才回落 `_now()`**。
    """
    if not rows:
        return
    NOTIF.mkdir(parents=True, exist_ok=True)
    stamp = (day or _day_from_event_keys(rows) or _now().strftime("%Y-%m-%d")).replace("-", "")
    p = NOTIF / f"delivery_{stamp}.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _day_from_event_keys(rows: list) -> str:
    """从审计行的 `event_key` 里取第一个 `YYYY-MM-DD`（`_push_card` 未带 day 时的兜底）。"""
    import re as _re
    for r in rows:
        m = _re.search(r"\d{4}-\d{2}-\d{2}", str((r or {}).get("event_key", "")))
        if m:
            return m.group(0)
    return ""


def _post(card: dict, timeout: float = 15.0):
    body = json.dumps(card, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_webhook(), data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    status = code = None
    err = None
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            payload = json.loads(resp.read(16384).decode("utf-8"))
        code = payload.get("code")
        ok = status == 200 and code == 0
    except Exception as exc:
        err = type(exc).__name__
    return ok, status, code, err


def _push_card(card: dict, event_key: str, summary_text: str, dry_run: bool = False):
    """推交互卡片 + 同键文本审计行。返回 (ok, attempted)。dry_run 时只渲染不发送。"""
    if dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=1))
        return False, False
    ok, status, code, err = _post(card)
    row = _audit_row("status", event_key, summary_text, ok, status, code, err)
    _audit_append([row])
    if not ok:
        return False, True
    # 同步写入全局去重表 delivery_state.json, 与本仓其它推送口径一致（失败不影响卡片已送达的事实）
    try:
        st_path = NOTIF / "delivery_state.json"
        state = _json(st_path, {"sent": {}}) or {"sent": {}}
        state.setdefault("sent", {})[event_key] = {
            "time": row["time"], "kind": "status", "message_sha256": row["message_sha256"]}
        _atomic_json(st_path, state)
    except Exception as exc:
        _warnings.append(f"delivery_state 写入失败: {type(exc).__name__}")
    return True, True


def _escalate(day: str, node: str, detail: str, st: dict, dry_run: bool) -> None:
    """来源连续缺失升级: 每节点每日至多 1 条 failure 告警（复用全局 failure 推送路径）。

    幂等依据是 state["warned"]（节点名列表，由 _load_state 规范化为 list）——
    必须与实际写入的容器类型一致，否则守卫失效会每轮重发。
    """
    warned = st.setdefault("warned", [])
    if not isinstance(warned, list):
        warned = st["warned"] = []
    if node in warned:
        return
    text = "\n".join([
        f"EvoAlpha｜状态推送链路异常 {day}",
        f"节点：{node}",
        f"详情：{_short(detail, 200)}",
        "影响：仅观测卡片缺失，不影响交易链路（生产脚本未参与本推送）。",
        f"证据：outputs/notifications/status_push_{day}.json",
    ])
    if dry_run:
        print("[dry-run escalate]\n" + text)
        warned.append(node)
        return
    ok, status, code, err = _post({"msg_type": "text", "content": {"text": text}})
    _audits.append(_audit_row("failure", f"failure:{day}:status-push:{node}", text,
                              ok, status, code, err))
    warned.append(node)
    _anomaly(f"{node} 来源连续 {PENDING_ALERT_ROUNDS} 轮未就绪: {_short(detail, 160)}")


# ---------------------------------------------------------------- 卡片构建

def _card(title: str, tone: str, header_rows: list, rows: list,
          footers: list = None, tail_note: str = "") -> dict:
    md = lambda t: {"tag": "lark_md", "content": t}  # noqa: E731
    elements = []
    for row in header_rows:
        elements.append({"tag": "div", "text": md(row)})
    if rows:
        elements.append({"tag": "hr"})
    for row in rows:
        elements.append({"tag": "div", "text": md(row)})
    for extra in (footers or []):
        elements.append({"tag": "hr"})
        for row in extra:
            elements.append({"tag": "div", "text": md(row)})
    if tail_note:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": tail_note}]})
    return {"msg_type": "interactive",
            "card": {"config": {"wide_screen_mode": True},
                     "header": {"template": tone, "title": {"tag": "plain_text", "content": title}},
                     "elements": elements}}


def _row(name: str, state: str, detail: str, limit: int = 140) -> str:
    return f"{MARK.get(state, '⚪')} {name}：{_short(detail, limit)}"


def _tone(states: list) -> str:
    if FAIL in states:
        return "red"
    if WARN in states:
        return "yellow"
    return "green"


def _plan_picks(plan: dict) -> str:
    picks = plan.get("picks") or []
    return "、".join(str(p.get("sym", "")) for p in picks[:8]) or "无"


def _count_deliveries(day: str) -> dict:
    rows = _jsonl(NOTIF / f"delivery_{day.replace('-', '')}.jsonl")
    out = {"total": 0, "alert": 0, "failure": 0, "fill": 0}
    for r in rows:
        if not r.get("ok"):
            continue
        out["total"] += 1
        kind = r.get("kind")
        if kind in out:
            out[kind] += 1
    return out


# ---------------------------------------------------------------- 节点实现

def _n_preflight(day: str, now: datetime.datetime):
    rep = _json(OUT / f"preflight_{day}_infra.json")
    if not isinstance(rep, dict) or rep.get("date") != day:
        return None, "体检报告缺失或非当日"
    s = rep.get("summary", {})
    selfheal = _json(OUT / "selfheal" / f"{day}.json")
    heal = selfheal[0] if isinstance(selfheal, list) and selfheal else {}
    morning = _json(OUT / "validation" / f"morning_check_{day}.json", {}) or {}
    warns = [r for r in rep.get("results", []) if not r.get("ok") and not r.get("critical")]
    fails = [r for r in rep.get("results", []) if not r.get("ok") and r.get("critical")]
    state = FAIL if fails else (WARN if warns else PASS)
    rows = [
        _row("基础设施体检", state, f"{s.get('pass', 0)} 项通过 / {s.get('fail', 0)} 项失败 / "
                                    f"{s.get('warn', 0)} 项告警（{rep.get('time', '')}）", 200),
        _row("盘前自愈", PASS if not heal.get("attempts") else WARN,
             (heal.get("verdict") or "无自愈记录") + (
                 f"；修复 {len(heal.get('attempts') or [])} 项" if heal.get("attempts") else ""), 200),
    ]
    for r in warns[:6]:
        rows.append(_row(f"⚠️ {r.get('name')}", WARN, r.get("detail", ""), 160))
    for r in fails[:6]:
        rows.append(_row(f"❌ {r.get('name')}", FAIL, r.get("detail", ""), 160))
    if morning.get("summary") is not None:
        ms = morning.get("summary") or {}
        rows.append(_row("08:58 晨检", PASS if ms.get("pass") else FAIL,
                         f"chain_ok={ms.get('chain_ok')} preflight_ok={ms.get('preflight_ok')} "
                         f"no_failure_push={ms.get('no_failure_push')}", 200))
    head = [f"**运行状态：{MARK[state]} {'异常' if state == FAIL else ('注意' if state == WARN else '正常')}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            f"阶段：基础设施体检（08:35）· 共 {len(rep.get('results', []))} 项"]
    card = _card("EvoAlpha 盘前体检 · 08:35", _tone([state]), head, rows,
                 footers=[["**📄 证据**",
                           f"`outputs/preflight_{day}_infra.json`",
                           f"`outputs/selfheal/{day}.json`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测 · 不影响门禁判定")
    tone = _tone([state])
    return (card, state, tone), None


def _n_premarket(day: str, now: datetime.datetime):
    plan = _json(OUT / "plans" / f"{day}_plan.json")
    if not isinstance(plan, dict) or plan.get("date") != day:
        return None, "当日计划缺失（08:50 盘前刷新无输入）"
    refreshed = str(plan.get("premarket_refreshed_at") or "")
    if refreshed.startswith(day):
        rstate, rdetail = PASS, f"已刷新 {refreshed[11:]}"
    else:
        rstate, rdetail = WARN, "未刷新（计划尚未注入外盘快照）"
    emotion = plan.get("emotion", {}) or {}
    glob = plan.get("global", {}) or {}
    gtext = "，".join(f"{v.get('name', k)} {v.get('chg_pct')}%" for k, v in list(glob.items())[:3]) or "未获取"
    state = PASS if rstate == PASS else WARN
    rows = [
        _row("计划就绪", PASS, f"{len(plan.get('picks') or [])} 只 · {plan.get('mode', '未标注')}", 200),
        _row("外盘快照刷新", rstate, rdetail, 200),
        _row("候选观察", INFO, _plan_picks(plan), 200),
        _row("市场温度", INFO, f"{emotion.get('temp', '未获取')} · 阶段 {emotion.get('stage', '未获取')}", 200),
        _row("隔夜外盘", INFO, gtext, 200),
    ]
    head = [f"**运行状态：{MARK[state]} {'注意' if state == WARN else '正常'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            f"阶段：盘前就绪（08:50）· 计划发布于 {plan.get('published_at', '未标注')}"]
    card = _card("EvoAlpha 盘前就绪 · 08:50", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/plans/{day}_plan.json`",
                           f"`outputs/premarket_{day}.log`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state])), None


def _n_plan_gate(day: str, now: datetime.datetime):
    rep = _json(OUT / f"preflight_{day}_post_plan.json")
    if not isinstance(rep, dict) or rep.get("date") != day:
        return None, "计划验收报告缺失（08:55 门禁未产出）"
    s = rep.get("summary", {})
    fails = [r for r in rep.get("results", []) if not r.get("ok") and r.get("critical")]
    warns = [r for r in rep.get("results", []) if not r.get("ok") and not r.get("critical")]
    state = FAIL if fails else (WARN if warns else PASS)
    rows = [_row("计划验收", state, f"{s.get('pass', 0)} 项通过 / {s.get('fail', 0)} 项失败 / "
                                    f"{s.get('warn', 0)} 项告警（{rep.get('time', '')}）", 200)]
    for r in rep.get("results", []):
        if r.get("name") in ("当日计划", "外盘新鲜度"):
            rows.append(_row(r.get("name"), PASS if r.get("ok") else FAIL, r.get("detail", ""), 200))
    for r in fails[:6]:
        rows.append(_row(f"❌ {r.get('name')}", FAIL, r.get("detail", ""), 160))
    conclusion = ("买入类链放行" if state != FAIL else "买入类链停摆（tick 秒级风控不受影响）")
    head = [f"**运行状态：{MARK[state]} {'异常' if state == FAIL else ('注意' if state == WARN else '正常')}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            f"结论：{conclusion}"]
    card = _card("EvoAlpha 计划验收 · 08:55", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/preflight_{day}_post_plan.json`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测 · 门禁判定以报告为准")
    return (card, state, _tone([state])), None


def _n_open(day: str, now: datetime.datetime):
    morning = _json(OUT / "validation" / f"morning_check_{day}.json")
    if not isinstance(morning, dict):
        return None, "晨检报告缺失（08:58 未产出）"
    ms = morning.get("summary") or {}
    ledger = _json(LEDGER, {}) or {}
    acct = ledger.get("account", {}) or {}
    curve = acct.get("equity_curve", []) or []
    last_eq = curve[-1].get("equity") if curve else None
    pos = acct.get("positions", {}) or {}
    guard = _json(OUT / "intraday" / "tick_guard_state.json", {}) or {}
    state = PASS if ms.get("pass") else FAIL
    # 2026-09-11: 守护必须报**双信号** —— 旧卡只报 state=restart_exhausted, 读者会以为
    # "守护全瘫"; 而 9/11 的真实形态是"进程活着、数据路径卡住"(两者处置完全不同)。
    _alive, _fresh = guard.get("daemon_alive"), guard.get("tick_fresh")
    gtext = f"state={guard.get('state', '未知')}"
    if _alive is not None or _fresh is not None:
        gtext += f" · 进程{'存活' if _alive else '已死'} · 数据{'新鲜' if _fresh else '陈旧'}"
    if _alive is False:
        gstate = FAIL
    elif guard.get("degraded") or _fresh is False or str(guard.get("state", "")).lower() not in (
            "", "running", "ok", "armed", "closed_ok", "recovered"):
        gstate = WARN
    else:
        gstate = PASS
    rows = [
        _row("08:58 晨检", state, f"chain_ok={ms.get('chain_ok')} preflight_ok={ms.get('preflight_ok')} "
                                  f"no_failure_push={ms.get('no_failure_push')}", 200),
        _row("持仓与净值", INFO, f"净值 {last_eq if last_eq is not None else '未获取'} · 持仓 {len(pos)} 只", 200),
        _row("tick 秒级风控", gstate, gtext + "（门禁解耦，独立守护）", 200),
        _row("开盘动作", INFO, "09:30 扫描+监控+事件推送按 08:55 验收结论决定是否放行", 200),
    ]
    head = [f"**运行状态：{MARK[state]} {'异常' if state == FAIL else '正常'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            "阶段：开盘就绪（09:30）"]
    card = _card("EvoAlpha 开盘就绪 · 09:30", _tone([state, gstate]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/validation/morning_check_{day}.json`",
                           "`portfolio/ledger.json`", "`outputs/intraday/tick_guard_state.json`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state, gstate])), None


def _n_midday(day: str, now: datetime.datetime):
    log_dir = OUT / "task_logs" / day
    scans = []
    if log_dir.exists():
        for p in log_dir.glob("*_scan.json"):
            row = _json(p, {}) or {}
            if row.get("date") == day and int(row.get("exit_code", -1)) == 0:
                scans.append(str(row.get("finished_at", "")))
    pos_live = _json(OUT / "intraday" / "pos_live.json", {}) or {}
    tick_ok = pos_live.get("date") == day
    tick_detail = f"{pos_live.get('time', '无')} · {len(pos_live.get('positions') or [])} 只" if tick_ok \
        else "本日无 tick 快照"
    d = _count_deliveries(day)
    state = PASS if (scans or tick_ok) else WARN
    rows = [
        _row("全市场扫描", PASS if scans else WARN,
             (f"最近成功 {max(scans)[11:19]} · 当日累计成功 {len(scans)} 轮" if scans else "当日至此无成功轮次"), 200),
        _row("持仓 tick 快照", PASS if tick_ok else WARN, tick_detail, 200),
        _row("上午推送", INFO, f"告警 {d['alert']} · 失败 {d['failure']} · 成交 {d['fill']}（合计 {d['total']}）", 200),
    ]
    head = [f"**运行状态：{MARK[state]} {'注意' if state == WARN else '正常'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            "阶段：上午小结（11:30）· 午休 11:30–13:00"]
    card = _card("EvoAlpha 上午小结 · 11:30", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/task_logs/{day}/*_scan.json`",
                           "`outputs/intraday/pos_live.json`",
                           f"`outputs/notifications/delivery_{day.replace('-', '')}.jsonl`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state])), None


def _n_afternoon(day: str, now: datetime.datetime):
    board = OUT / "intraday" / "board_refresh.latest.log"
    if not board.exists():
        return None, "看板刷新日志缺失"
    bday = _mtime_day(board)
    live_dir = OUT / "validation" / "live-ticks"
    live_files = [p for p in live_dir.glob("*") if day.replace("-", "") in p.name] if live_dir.exists() else []
    guard = _json(OUT / "intraday" / "tick_guard_state.json", {}) or {}
    bstate = PASS if bday == day else WARN
    lstate = PASS if live_files else WARN
    # 2026-09-11: 同开盘卡, 守护报双信号(进程存活 / 数据新鲜), 避免只看到 state 就误判"全瘫"。
    _alive2, _fresh2 = guard.get("daemon_alive"), guard.get("tick_fresh")
    _gtext = f"state={guard.get('state', '未知')}"
    if _alive2 is not None or _fresh2 is not None:
        _gtext += f" · 进程{'存活' if _alive2 else '已死'} · 数据{'新鲜' if _fresh2 else '陈旧'}"
    gstate = FAIL if _alive2 is False else (WARN if guard.get("degraded") or _fresh2 is False else PASS)
    rows = [
        _row("看板快照重建", bstate, f"最近写入 {bday or '未知'}"
                                     f"（{'当日' if bday == day else '非当日'}）", 200),
        _row("tick 数据校验", lstate, f"当日 live-tick 产物 {len(live_files)} 个" if live_files else "当日无产物", 200),
        _row("tick 守护", gstate, _gtext, 200),
        _row("午后续跑", INFO, "13:00 起扫描/监控/事件推送按 08:55 验收结论续跑", 200),
    ]
    state = FAIL if FAIL in (bstate, lstate, gstate) else (WARN if WARN in (bstate, lstate, gstate) else PASS)
    head = [f"**运行状态：{MARK[state]} {'注意' if state == WARN else '正常'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            "阶段：午后就绪（13:05）"]
    card = _card("EvoAlpha 午后就绪 · 13:05", _tone([bstate, lstate, gstate]), head, rows,
                 footers=[["**📄 证据**", "`outputs/intraday/board_refresh.latest.log`",
                           "`outputs/validation/live-ticks/`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state])), None


def _n_close(day: str, now: datetime.datetime):
    cd = _json(OUT / "intraday" / f"close_decision_{day}.json")
    if not isinstance(cd, dict) or cd.get("date") != day:
        return None, "收盘决策文件缺失（15:10 收盘链未产出）"
    ledger = _json(LEDGER, {}) or {}
    acct = ledger.get("account", {}) or {}
    curve = acct.get("equity_curve", []) or []
    row = next((x for x in reversed(curve) if x.get("date") == day), None)
    equity = (row or {}).get("equity", cd.get("equity"))
    start_cash = _start_cash(ledger)
    ret = (float(equity) / start_cash - 1) * 100 if (equity is not None and start_cash) else None
    # 2026-09-11 修复(口径): close_decision 是"规则全天会怎么打"的**审计反事实**产物, 文件自带
    # kind=audit_counterfactual / executed=false / authority=account.fills 三键 —— 它不是成交。
    # 原实现直接把它的 buys/sells 当"当日成交"展示, 9/11 实录: 账本真实为 2 笔卖出
    # (300468/300394 止损), 卡片却报"买入 2 笔 · 卖出 0 笔", 与账本完全相反(持仓数也取自审计)。
    # 成交口径唯一权威 = 账本 account.fills; 审计另起一行并显式标注"仅审计, 非成交"。
    audit_buys = cd.get("buys") or []
    audit_sells = cd.get("sells") or []
    fills = [x for x in (acct.get("fills") or []) if x.get("date") == day]
    n_buy = sum(1 for x in fills if x.get("side") == "buy")
    n_sell = sum(1 for x in fills if x.get("side") == "sell")
    n_pos = len(acct.get("positions") or {})
    # 2026-09-11 补齐: 风控状态必须上卡。当日账本 risk_state 已进入
    # portfolio_drawdown_pause（position_multiplier=0）——持仓/新仓被硬停，
    # 但此前卡片只报净值与成交笔数, 结论仍是"🟢 正常", 读者会误以为系统照常开仓。
    risk = ledger.get("risk_state", {}) or {}
    paused = bool(risk.get("paused"))
    terminated = bool(risk.get("terminated"))
    dd = risk.get("drawdown_pct")
    peak = risk.get("peak_equity")
    mult = risk.get("position_multiplier")
    if terminated:
        risk_state, risk_label = FAIL, "已终止"
    elif paused or (mult is not None and float(mult) <= 0):
        risk_state, risk_label = WARN, "新仓暂停"
    else:
        risk_state, risk_label = PASS, "正常"
    risk_detail = f"{risk_label} · 回撤 {dd}% / 峰值 {peak} · 仓位系数 {mult}"
    if risk.get("reason"):
        risk_detail += f" · {risk['reason']}"
    state = FAIL if terminated else (WARN if risk_state != PASS else PASS)
    rows = [
        _row("收盘估值", PASS, f"净值 {equity if equity is not None else '未落账'}"
                               + (f" · 累计 {ret:+.2f}%" if ret is not None else ""), 200),
        _row("风控状态", risk_state, risk_detail, 240),
        _row("当日成交", PASS if fills else INFO,
             f"买入 {n_buy} 笔 · 卖出 {n_sell} 笔 · 持仓 {n_pos} 只"
             + ("（空仓）" if n_pos == 0 else ""), 200),
        _row("规则审计", INFO, f"反事实买点 {len(audit_buys)} 个 · 卖点 {len(audit_sells)} 个"
                               f" · 仅审计未执行", 200),
        _row("账本修订", INFO, f"ledger_revision={cd.get('ledger_revision')} · "
                               f"run_id={cd.get('run_id', '未标注')}", 200),
    ]
    ts = cd.get("generated_at", "")
    head = [f"**运行状态：{MARK[state]} {'异常' if state == FAIL else ('注意' if state == WARN else '正常')}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            f"阶段：收盘落账（15:10）· 决策生成于 {ts}"]
    if state == WARN:
        head.append("⚠️ 回撤闸口已触发：**不会开新仓**，需人工复核风控后才恢复（详见 17:45 晚间核验卡）")
    elif state == FAIL:
        head.append("❌ 账户已终止，请立即人工介入")
    card = _card("EvoAlpha 收盘落账 · 15:10", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/intraday/close_decision_{day}.json`",
                           "`portfolio/ledger.json`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 模拟盘记录，不涉及真实资金")
    return (card, state, _tone([state])), None


def _n_post_close(day: str, now: datetime.datetime):
    manifest = OUT / "acceptance" / f"chain_manifest_{day}.jsonl"
    rows_raw = _jsonl(manifest)
    latest = {}
    for r in rows_raw:
        stage = r.get("stage")
        if not stage:
            continue
        if stage not in latest or int(r.get("attempt_no", 0)) >= int(latest[stage].get("attempt_no", 0)):
            latest[stage] = r
    if not latest:
        return None, "盘后链 manifest 缺失（15:35 链尚未开始）"
    acceptance = _json(OUT / "acceptance" / f"acceptance_{day}.json", {}) or {}
    next_day = _next_trading_day(day)
    plan = _json(OUT / "plans" / f"{next_day}_plan.json", {}) or {}
    bad = [s for s, r in latest.items() if int(r.get("exit_code", -1)) != 0]
    state = FAIL if bad else PASS
    rows = []
    for stage, r in latest.items():
        code = int(r.get("exit_code", -1))
        detail = (f"rc={code} · {str(r.get('started_at', ''))[11:]} → {str(r.get('finished_at', ''))[11:]}"
                  f" · attempt={r.get('attempt_no')}")
        rows.append(_row(stage, PASS if code == 0 else FAIL, detail, 200))
    astatus = str(acceptance.get("status", "未产出"))
    rows.append(_row("日终验收", PASS if astatus == "pass" else (WARN if astatus == "未产出" else FAIL),
                     f"status={astatus}", 200))
    if str(plan.get("date")) == next_day:
        rows.append(_row(f"次日计划（{next_day}）", PASS,
                         f"{len(plan.get('picks') or [])} 只 · {plan.get('mode', '未标注')}", 200))
    else:
        rows.append(_row(f"次日计划（{next_day}）", FAIL, "尚未生成", 200))
        state = FAIL
    head = [f"**运行状态：{MARK[state]} {'异常' if state == FAIL else '正常'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            f"阶段：盘后数据链与次日计划（15:35 起跑，共 {len(latest)} 个 stage）"]
    card = _card("EvoAlpha 盘后链 · 次日计划", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/acceptance/chain_manifest_{day}.jsonl`",
                           f"`outputs/acceptance/acceptance_{day}.json`",
                           f"`outputs/plans/{next_day}_plan.json`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state])), None


def _n_evening(day: str, now: datetime.datetime):
    ev = _json(OUT / "validation" / f"evening_check_{day}.json")
    if not isinstance(ev, dict):
        return None, "晚间核验报告缺失（17:30 未产出）"
    blockers = ev.get("blockers") or ev.get("blocking") or []
    if isinstance(blockers, dict):
        blockers = list(blockers.items())
    state = WARN if blockers else PASS
    rep = _json(OUT / f"preflight_{day}_infra.json", {}) or {}
    s = rep.get("summary", {}) or {}
    d = _count_deliveries(day)
    acceptance = _json(OUT / "acceptance" / f"acceptance_{day}.json", {}) or {}
    rows = [
        _row("八项合并核验", state, f"阻断项 {len(blockers)} 项"
                                   f"（{ev.get('checked_at', ev.get('time', '未标注'))}）", 200),
    ]
    for b in blockers[:8]:
        rows.append(_row("🚧 阻断", FAIL, b if isinstance(b, str) else json.dumps(b, ensure_ascii=False), 200))
    rows += [
        _row("全天体检", PASS if not s.get("fail") else FAIL,
             f"通过 {s.get('pass', 0)} · 失败 {s.get('fail', 0)} · 告警 {s.get('warn', 0)}", 200),
        _row("全天推送", INFO, f"告警 {d['alert']} · 失败 {d['failure']} · 成交 {d['fill']}（合计 {d['total']}）", 200),
        _row("盘后链", INFO, f"验收 status={acceptance.get('status', '未产出')}"
                             + ("（盘后链进行中，终局见 18:30 卡）"
                                if not (OUT / "acceptance" / f"acceptance_{day}.json").exists()
                                or datetime.datetime.fromtimestamp(
                                    (OUT / "acceptance" / f"acceptance_{day}.json").stat().st_mtime
                                ).strftime("%Y-%m-%d") != day else ""), 220),
    ]
    head = [f"**运行状态：{MARK[state]} {'有阻断项' if blockers else '无阻断'}**"
            f"　　　时间：{now.strftime('%m-%d %H:%M')}",
            "阶段：晚间核验 · 全天总结（17:30 核验跑完）"]
    if blockers:
        head.append(f"⚠️ 夜间修复窗口 ≥15 小时，请优先处置上述阻断项")
    card = _card("EvoAlpha 晚间核验 · 全天总结", _tone([state]), head, rows,
                 footers=[["**📄 证据**", f"`outputs/validation/evening_check_{day}.json`",
                           f"`outputs/notifications/delivery_{day.replace('-', '')}.jsonl`"]],
                 tail_note="EvoAlpha 关键节点状态卡 · 只读观测")
    return (card, state, _tone([state])), None


NODES = [
    {"name": "preflight", "at": "08:36", "fn": _n_preflight},
    {"name": "premarket", "at": "08:50", "fn": _n_premarket},
    {"name": "plan-gate", "at": "08:55", "fn": _n_plan_gate},
    {"name": "open", "at": "09:30", "fn": _n_open},
    {"name": "midday", "at": "11:30", "fn": _n_midday},
    {"name": "afternoon", "at": "13:05", "fn": _n_afternoon},
    {"name": "close", "at": "15:05", "fn": _n_close},
    {"name": "evening", "at": "17:45", "fn": _n_evening},
    {"name": "post-close-chain", "at": "18:30", "fn": _n_post_close},
]
NODE_BY_NAME = {n["name"]: n for n in NODES}


def _deadline(day: str, at: str) -> datetime.datetime:
    return datetime.datetime.strptime(f"{day} {at}", "%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 主流程

def _tick(day: str, st: dict, now: datetime.datetime, dry_run: bool) -> None:
    ticks = [t for t in st.get("ticks", []) if str(t).startswith(day)]
    ticks.append(now.strftime("%Y-%m-%d %H:%M:%S"))
    st["ticks"] = ticks[-80:]
    if not dry_run:
        _save_state(day, st)


def _run_node(day: str, node: dict, st: dict, now: datetime.datetime, args) -> dict:
    """处理单个节点。返回 {'action': ..., 'state': ...}。"""
    name = node["name"]
    record = st["nodes"].get(name, {})
    if record.get("pushed") and not args.force:
        return {"action": "already", "state": record.get("state")}
    deadline = _deadline(day, node["at"]) + DEADLINE_GRACE
    # --force 只解除「已推送」锁定用于补发; 未到点(含 --force)一律不推, 避免把未来时刻的卡片提前发出去。
    early = now < deadline
    if early and not args.allow_early:
        return {"action": "waiting", "state": None}
    try:
        # 节点契约: 返回 (rendered, missing_reason)。
        # rendered 为 None → 来源未就绪(缺什么写在 missing_reason); 否则为 (card, state, tone)。
        rendered, missing = node["fn"](day, now)
    except Exception as exc:  # 观测器自身异常不得静默, 也不能中断其它节点
        missing = f"节点渲染异常 {type(exc).__name__}: {exc}"
        rendered = None
        _anomaly(f"{name} 渲染异常: {type(exc).__name__}: {_short(exc, 200)}")
    if rendered is None:
        rounds = int(record.get("pending_rounds", 0)) + 1
        st["nodes"][name] = {**record, "pending_rounds": rounds,
                             "last_attempt": now.strftime("%Y-%m-%d %H:%M:%S"),
                             "detail": _short(missing, 200)}
        if not args.no_push and not early and rounds >= PENDING_ALERT_ROUNDS:
            _escalate(day, name, str(missing), st, args.dry_run)
        return {"action": "pending", "state": None, "detail": missing, "rounds": rounds}
    card, state, tone = rendered
    event_key = f"status:{day}:{name}"
    summary = f"EvoAlpha 状态卡 {day} 节点={name} state={state}"
    if args.no_push:
        print(json.dumps(card, ensure_ascii=False, indent=1))
        ok = False
    else:
        ok, _ = _push_card(card, event_key, summary, dry_run=args.dry_run)
    if args.dry_run or args.no_push:
        st["nodes"][name] = {"pushed": True, "state": state, "tone": tone,
                             "pushed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                             "event_key": event_key, "pending_rounds": 0}
        return {"action": "dry" if args.dry_run else "rendered", "state": state}
    if ok:
        st["nodes"][name] = {"pushed": True, "state": state, "tone": tone,
                             "pushed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                             "event_key": event_key, "pending_rounds": 0}
        return {"action": "pushed", "state": state}
    st["nodes"][name] = {**record, "pushed": False, "state": state,
                         "last_attempt": now.strftime("%Y-%m-%d %H:%M:%S"),
                         "detail": "推送失败，等待下一轮重试"}
    return {"action": "push-failed", "state": state}


def _select_nodes(args) -> list:
    if args.node == "run":
        return list(NODES)
    node = NODE_BY_NAME.get(args.node)
    if node is None:
        raise SystemExit(f"unknown node: {args.node} (可选: run / {', '.join(NODE_BY_NAME)})")
    return [node]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", default="run",
                    help="run=本轮到点的全部节点 / 单节点名（preflight, premarket, ...）")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true", help="只渲染卡片并打印，不发送、不改状态")
    ap.add_argument("--no-push", action="store_true", help="渲染并打印，标记为已处理但不发送")
    ap.add_argument("--force", action="store_true", help="忽略「已推送」标记，把已到点的节点重发（补发用）")
    ap.add_argument("--allow-early", action="store_true",
                    help="允许在节点到点前推送（仅用于本地演练；生产与补发都不得使用）")
    args = ap.parse_args()

    now = _now()
    day = args.date or now.strftime("%Y-%m-%d")
    production = args.node == "run" and not args.date

    # 交易日守卫: 非交易日全线静默(零推送、零状态写入); 日历不可用 fail-open 但只记异常不推送
    rc = _calendar_check(day)
    if rc == 3:
        print(json.dumps({"date": day, "silent": "non_trading"}, ensure_ascii=False))
        return 0
    if rc == 4:
        _anomaly("交易日历不可用，status_push 按交易日继续（fail-open）；日历告警由既有路径负责")

    st = _load_state(day)

    # 自证: 生产触发时, 本任务的「下次运行时间」必为当日; 否则本次不是计划实例 → 只记 tick
    if production and not args.force:
        nxt = _self_next_run_day()
        if nxt not in (day, "unknown"):
            _tick(day, st, now, args.dry_run)
            print(json.dumps({"date": day, "self_check": "not scheduled instance", "next_run_day": nxt},
                             ensure_ascii=False))
            return 0
        if nxt == "unknown":
            _anomaly("status_push 无法读取本任务下次运行时间，按计划实例继续（避免漏推）")

    results = {}
    for node in _select_nodes(args):
        results[node["name"]] = _run_node(day, node, st, now, args)

    if not args.dry_run:
        st["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        _save_state(day, st)
    _audit_append(_audits, day)

    print(json.dumps({"date": day, "node": args.node, "at": now.strftime("%H:%M:%S"),
                      "results": results, "warnings": _warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
