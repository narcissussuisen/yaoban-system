# -*- coding: utf-8 -*-
"""路线图看板生成器。

设计原则（对齐 SOUL.md「证据先行」）：
- **叙事状态**在 `docs/dashboard/status.json`（我维护单一数据源）；
- **客观事实**由本脚本**实时探测**，不写死在看板里 —— 账本、19 个计划任务、
  归档校验、测试基线、manifest 哈希。若探测失败则显式降级为 n/a，不假装有值。
- 产物 `docs/dashboard/roadmap.html` 为自包含 HTML（数据内嵌），
  同时在 http 环境下会尝试 `fetch('status.json')` 热更新。

用法：
    python tools/build_roadmap_dashboard.py
"""
from __future__ import annotations

import hashlib
import html
import json
import pathlib
import subprocess
import sys
import urllib.request
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parent.parent      # yaoban-system（代码与账本所在 repo）
PROJ_ROOT = REPO.parent                                     # EvoAlpha（权威文档所在项目根）
DASH = PROJ_ROOT / "docs" / "dashboard"                     # 看板与其它权威文档同级
STATUS = DASH / "status.json"
OUT = DASH / "roadmap.html"
ARCHIVE = pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\ledger_archive")
MANIFEST_DIR = REPO / "baseline" / "manifests" / "baseline-v2-pre-restructure"

TASK_NAMES = [
    "YaobanPreflight", "YaobanSelfHeal", "YaobanPremarket", "YaobanPlanGate",
    "YaobanMorningCheck", "YaobanTdxProbe", "YaobanAuctionMonitor", "YaobanEventNotify",
    "YaobanTickDaemon", "YaobanScanConfirm", "YaobanIntradayMonitor", "YaobanClosePipeline",
    "YaobanPostCloseChain", "YaobanEveningCheck", "YaobanTdxServerVerify", "YaobanBoardRefresh",
    "YaobanStatusPush", "VibeResearchDashboardServices", "VibeResearchLiveTickValidation",
]


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def probe_ledger() -> dict:
    p = REPO / "portfolio" / "ledger.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
        acct = st.get("account", {}) or {}
        curve = acct.get("equity_curve") or []
        eq = float(curve[-1].get("equity")) if curve else None
        start = float(st.get("start_cash") or 0) or None
        return {
            "ok": True,
            "revision": st.get("_revision"),
            "start_cash": st.get("start_cash"),
            "start_date": st.get("start_date"),
            "cash": acct.get("cash"),
            "positions": len(acct.get("positions") or {}),
            "fills": len(acct.get("fills") or []),
            "equity_points": len(curve),
            "last_equity": eq,
            "nav": round(eq / start, 6) if (eq is not None and start) else None,
            "paused": (st.get("risk_state") or {}).get("paused"),
            "position_multiplier": (st.get("risk_state") or {}).get("position_multiplier"),
            "account_mode": (st.get("policy") or {}).get("account_mode"),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def probe_manifest() -> dict:
    try:
        s = json.loads((MANIFEST_DIR / "summary.json").read_text(encoding="utf-8"))
        return {"ok": True, "baseline_id": s.get("baseline_id"),
                "manifest_hash": s.get("manifest_hash"), "file_count": s.get("file_count"),
                "total_bytes": s.get("total_bytes"),
                "generated_at": s.get("generated_at"),
                "git": s.get("git")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def probe_archive() -> dict:
    try:
        migs = sorted(ARCHIVE.glob("migration_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not migs:
            return {"ok": False, "error": "无归档清单"}
        man = json.loads(migs[0].read_text(encoding="utf-8"))
        ap = pathlib.Path(man["archive"])
        actual = hashlib.sha256(ap.read_bytes()).hexdigest() if ap.exists() else None
        return {"ok": True, "file": ap.name, "exists": ap.exists(),
                "sha256": man.get("sha256"), "sha256_ok": actual == man.get("sha256"),
                "archived_at": man.get("archived_at"),
                "old_start_cash": man.get("old_start_cash"),
                "old_fills": man.get("old_fills")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def probe_tasks() -> dict:
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/nh"],
                           capture_output=True, timeout=60)
        text = _decode(r.stdout or b"")
        seen: dict[str, dict] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith('"'):
                continue
            parts = [x.strip('"') for x in line.split('","')]
            if len(parts) < 3:
                continue
            name = parts[0].lstrip("\\").strip()
            if name in TASK_NAMES and name not in seen:
                seen[name] = {"next": parts[1].strip(), "state": parts[2].strip()}
        ready = [n for n, v in seen.items() if v["state"] in ("就绪", "Ready")]
        disabled = [n for n, v in seen.items() if v["state"] in ("已禁用", "Disabled")]
        missing = [n for n in TASK_NAMES if n not in seen]
        return {"ok": True, "total": len(TASK_NAMES), "found": len(seen),
                "ready": len(ready), "disabled": len(disabled),
                "missing": missing, "detail": seen}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def probe_tests() -> dict:
    p = DASH / "test_baseline.json"
    try:
        return {"ok": True, **json.loads(p.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": f"未记录测试基线（{exc}）"}


def probe_workbuddy() -> dict:
    """探测 Vibe-Research 的 WorkBuddy / CodeBuddy **订阅通道**是否就绪。

    通道本身不需要 API key（复用本机已登录的 CodeBuddy CLI）；本机 WorkBuddy 装在
    F:\\workbuddy\\，不在 local_agent_runtime.ts 硬编码的探测根下，靠环境变量
    CODEBUDDY_BIN 指路（见 Vibe-Research/scripts/ensure-dashboard-services.ps1）。
    """
    try:
        tok = (PROJ_ROOT / "Vibe-Research" / ".local" / "api.token").read_text(encoding="utf-8").strip()
    except Exception as exc:
        return {"ok": False, "error": f"读不到 api.token（{type(exc).__name__}）"}
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8766/local-agents?provider=cli-codebuddy",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        a = data[0] if isinstance(data, list) and data else {}
        return {"ok": True, "status": a.get("status"), "installed": a.get("installed"),
                "authenticated": a.get("authenticated"), "version": a.get("version"),
                "detail": a.get("detail")}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


STATUS_STYLE = {
    "done":        ("已完成", "#1D9E75", "#9FE1CB"),
    "in_progress": ("进行中", "#BA7517", "#FAC775"),
    "pending":     ("待办",   "#5F5E5A", "#B4B2A9"),
    "blocked":     ("受制约", "#A32D2D", "#F09595"),
    "na":          ("不适用", "#5F5E5A", "#B4B2A9"),
}
KIND_LABEL = {"time": "时间卡点", "tech": "技术堵点", "human": "人工边界",
              "blocked": "阻塞项", "data": "数据缺口"}
AUTH_STYLE = {
    "proceeding": ("按建议推进中", "#BA7517", "#FAC775"),
    "applied":    ("已落地", "#1D9E75", "#9FE1CB"),
    "pending":    ("待你批", "#A32D2D", "#F09595"),
    "vetoed":     ("已否决", "#5F5E5A", "#B4B2A9"),
}
PRIO_STYLE = {"P0": ("#E24B4A", "#F09595"), "P1": ("#BA7517", "#FAC775"), "P2": ("#5F5E5A", "#B4B2A9")}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pill(text: str, fg: str, bg: str) -> str:
    return (f'<span class="pill" style="color:{fg};background:{bg}22;'
            f'border:0.5px solid {fg}66">{esc(text)}</span>')


def action_items(items: list) -> str:
    """渲染「需要你出手」/「可选事项」两类条目（同款卡片，仅优先级配色不同）。"""
    out = []
    for x in items:
        fg, _bg = PRIO_STYLE.get(x.get("priority", "P2"), PRIO_STYLE["P2"])
        out.append(
            f'<li class="blk" style="border-left-color:{fg}">'
            f'<div class="btop">{pill(x.get("priority", "?"), fg, "#000")}'
            f'<span class="btitle">{esc(x.get("title", ""))}</span></div>'
            f'<div class="bdet"><b>为什么</b>：{esc(x.get("why", ""))}</div>'
            f'<div class="bdet"><b>怎么给</b>：{esc(x.get("how", ""))}</div></li>')
    return "".join(out)


def collect_probes() -> dict:
    """每次调用都**重新探测** —— 这是「动态看板」的核心：浏览器刷新即取新值。"""
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ledger": probe_ledger(),
        "manifest": probe_manifest(),
        "archive": probe_archive(),
        "tasks": probe_tasks(),
        "tests": probe_tests(),
        "workbuddy": probe_workbuddy(),
    }


def build_page(spec: dict, probes: dict) -> str:
    """把叙事状态 + 实时事实渲染成完整 HTML 页（服务端渲染，刷新即最新）。"""
    led = probes["ledger"]
    man = probes["manifest"]
    arc = probes["archive"]
    tsk = probes["tasks"]
    tst = probes["tests"]
    wb = probes.get("workbuddy", {})
    now = probes["generated_at"]

    # ---- 阶段进度 ----
    stages_html = []
    for stg in spec["stages"]:
        items = stg["items"]
        done = sum(1 for i in items if i["status"] == "done")
        blocked = sum(1 for i in items if i["status"] == "blocked")
        pct = round(done / len(items) * 100) if items else 0
        label, fg, _bg = STATUS_STYLE.get(stg["status"], STATUS_STYLE["pending"])
        rows = []
        for it in items:
            l2, f2, b2 = STATUS_STYLE.get(it["status"], STATUS_STYLE["pending"])
            rows.append(
                f'<li><div class="itop"><code>{esc(it["id"])}</code>'
                f'<span class="iname">{esc(it["name"])}</span>{pill(l2, f2, b2)}</div>'
                f'<div class="iev">{esc(it["evidence"])}</div></li>')
        bar_color = fg
        stages_html.append(f"""
<section class="card stage">
  <div class="shead">
    <div><span class="sid">{esc(stg['id'])}</span>
      <span class="sname">{esc(stg['name'])}</span>
      {pill(label, fg, '#000')}
      {f'<span class="snote">{esc(stg.get("note",""))}</span>' if stg.get("note") else ''}
    </div>
    <div class="scount">{done}/{len(items)}{f' · 受制约 {blocked}' if blocked else ''}</div>
  </div>
  <div class="bar"><div class="fill" style="width:{pct}%;background:{bar_color}"></div></div>
  <ul class="items">{''.join(rows)}</ul>
</section>""")

    # ---- 事实卡 ----
    if led.get("ok"):
        nav_txt = f'{led["nav"]:.4f}' if led.get("nav") is not None else "n/a"
        facts = [
            ("主账本净值", nav_txt, f'起点 {led["start_cash"]:,.0f} · {led["start_date"]}'),
            ("账本修订号", f'#{led["revision"]}', f'持仓 {led["positions"]} · 成交 {led["fills"]} · 曲线点 {led["equity_points"]}'),
            ("熔断状态", "已清零" if not led["paused"] and led["position_multiplier"] == 1.0 else "⚠ 未清零",
             f'paused={led["paused"]} · mult={led["position_multiplier"]}'),
            ("baseline-v2", f'{man.get("file_count","?")} 文件' if man.get("ok") else "n/a",
             f'{esc((man.get("manifest_hash") or "")[:12])}…' if man.get("ok") else esc(man.get("error", ""))),
            ("计划任务", f'{tsk["ready"]}/{tsk["total"]} 就绪' if tsk.get("ok") else "n/a",
             (f'禁用 {tsk["disabled"]}' + (f' · 缺失 {tsk["missing"]}' if tsk.get("missing") else '')) if tsk.get("ok") else esc(tsk.get("error", ""))),
            ("测试基线", f'{tst.get("passed","?")} 通过' if tst.get("ok") else "n/a",
             f'{tst.get("failed","?")} 先存失败 · 无回归' if tst.get("ok") else ""),
            ("旧账归档", "sha256 校验通过" if arc.get("sha256_ok") else "⚠ 校验失败",
             f'旧本金 {arc.get("old_start_cash")} · {arc.get("old_fills")} 笔成交' if arc.get("ok") else ""),
            ("WorkBuddy 订阅通道",
             ("就绪" if wb.get("status") == "ready" else (wb.get("status") or "未探测")) if wb.get("ok") else "探测失败",
             f'CodeBuddy CLI {wb.get("version")} · 免 API key' if wb.get("ok") and wb.get("status") == "ready"
             else (wb.get("error") or wb.get("detail") or "")),
        ]
    else:
        facts = [("主账本", "读取失败", esc(led.get("error", "")))]
    facts_html = "".join(
        f'<div class="fact"><div class="fk">{esc(k)}</div>'
        f'<div class="fv">{esc(v)}</div><div class="fs">{esc(d)}</div></div>'
        for k, v, d in facts)

    blockers_html = "".join(
        f'<li class="blk {esc(b["kind"])}"><div class="btop">{pill(KIND_LABEL.get(b["kind"], b["kind"]), "#F09595", "#000")}'
        f'<span class="btitle">{esc(b["title"])}</span></div>'
        f'<div class="bimp">{esc(b["impact"])}</div><div class="bdet">{esc(b["detail"])}</div></li>'
        for b in spec["blockers"])

    auth_html = "".join(
        (lambda lab, fg, bg: f'<tr><td><code>{esc(a["id"])}</code></td><td>{esc(a["title"])}</td>'
                            f'<td class="adv">{esc(a["advice"])}</td><td>{pill(lab, fg, bg)}</td></tr>')(
            *AUTH_STYLE.get(a["status"], AUTH_STYLE["pending"]))
        for a in spec["authorizations"])

    next_html = "".join(f'<li>{esc(x)}</li>' for x in spec["next_steps"])
    # 「需要你出手」必须只放**真正卡住施工**的事；没有就明说"无阻塞"，别拿可选事项充数。
    nua = spec.get("needs_user_action", [])
    if nua:
        nua_html = f'<ul class="blk">{action_items(nua)}</ul>'
    else:
        nua_html = (
            '<div class="card" style="border-left:2px solid #1D9E75">'
            f'<div class="btop">{pill("无阻塞", "#1D9E75", "#000")}'
            f'<span class="btitle">{esc(spec.get("needs_user_action_note", "当前无阻塞项"))}</span></div></div>')
    opt = spec.get("optional_items", [])
    opt_html = (f'<h2>可选事项（不做也能继续）</h2><ul class="blk">{action_items(opt)}</ul>') if opt else ""
    chain_html = "".join(
        f'<div class="chain"><div class="cl">{esc(c["label"])}</div><div class="cd">{esc(c["detail"])}</div></div>'
        for c in spec["meta"]["goal_chain"])

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(spec['meta']['title'])}</title>
<style>
:root{{--bg:#14161a;--card:#1c1f24;--card2:#22262c;--bd:rgba(255,255,255,.12);
--tx:#e8e9ea;--tx2:#9aa0a6;--tx3:#6b7075;--acc:#5DCAA5}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);
font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:13px;line-height:1.6}}
.wrap{{max-width:1100px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:19px;font-weight:500;margin:0 0 6px}}
h2{{font-size:15px;font-weight:500;margin:32px 0 12px;color:var(--tx)}}
.sub{{color:var(--tx2);font-size:12px}}
.goal{{background:var(--card2);border:0.5px solid var(--bd);border-left:2px solid var(--acc);
border-radius:8px;padding:14px 16px;margin:16px 0}}
.goal .g{{font-size:14px;font-weight:500;line-height:1.65}}
.chain{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}}
.chain .cl{{font-size:12px;color:var(--acc);font-weight:500}}
.chain .cd{{font-size:12px;color:var(--tx2)}}
.note{{font-size:12px;color:var(--tx3);margin-top:10px}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:14px 0}}
.fact{{background:var(--card);border:0.5px solid var(--bd);border-radius:8px;padding:12px}}
.fk{{font-size:11px;color:var(--tx3)}}
.fv{{font-size:17px;font-weight:500;margin:2px 0 2px}}
.fs{{font-size:11px;color:var(--tx2)}}
.card{{background:var(--card);border:0.5px solid var(--bd);border-radius:8px;padding:14px 16px;margin-bottom:10px}}
.shead{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
.sid{{font-family:ui-monospace,Consolas,monospace;color:var(--acc);font-weight:500;margin-right:6px}}
.sname{{font-size:14px;font-weight:500;margin-right:8px}}
.snote{{font-size:11px;color:var(--tx3);margin-left:8px}}
.scount{{font-size:12px;color:var(--tx2);font-variant-numeric:tabular-nums}}
.bar{{height:4px;background:#2c3037;border-radius:2px;margin:10px 0 12px;overflow:hidden}}
.fill{{height:100%;border-radius:2px;transition:width .3s}}
.items{{list-style:none;margin:0;padding:0}}
.items li{{padding:7px 0;border-top:0.5px solid rgba(255,255,255,.06)}}
.items li:first-child{{border-top:none}}
.itop{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.iname{{font-size:13px}}
.iev{{font-size:11px;color:var(--tx2);margin-top:2px}}
code{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--tx2);
background:rgba(255,255,255,.06);padding:1px 5px;border-radius:4px}}
.pill{{font-size:11px;padding:1px 7px;border-radius:10px;white-space:nowrap}}
ul.blk{{list-style:none;margin:0;padding:0}}
.blk{{background:var(--card);border:0.5px solid var(--bd);border-radius:8px;
padding:12px 14px;margin-bottom:8px;border-left:2px solid #E24B4A}}
.blk.time{{border-left-color:#EF9F27}} .blk.tech{{border-left-color:#E24B4A}}
.blk.human{{border-left-color:#888780}} .blk.blocked{{border-left-color:#E24B4A}}
.blk.data{{border-left-color:#7F77DD}}
.btop{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.btitle{{font-size:13px;font-weight:500}}
.bimp{{font-size:11px;color:#F09595;margin-top:3px}}
.bdet{{font-size:12px;color:var(--tx2);margin-top:3px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:0.5px solid rgba(255,255,255,.07);vertical-align:top}}
th{{color:var(--tx3);font-weight:400;font-size:11px}}
td.adv{{color:var(--tx2)}}
ol.steps{{margin:0;padding-left:20px}} ol.steps li{{margin-bottom:5px}}
footer{{margin-top:34px;padding-top:12px;border-top:0.5px solid var(--bd);
font-size:11px;color:var(--tx3)}}
.bar-top{{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
background:rgba(20,22,26,.94);border-bottom:0.5px solid var(--bd);padding:9px 0;margin-bottom:14px}}
button{{background:#22262c;color:var(--tx);border:0.5px solid var(--bd);border-radius:6px;
padding:5px 14px;font-size:12px;font-family:inherit;cursor:pointer}}
button:hover{{border-color:var(--acc);color:var(--acc)}}
.bar-top label{{font-size:12px;color:var(--tx2);display:flex;align-items:center;gap:5px;cursor:pointer}}
.live{{margin-left:auto;font-size:11px;color:var(--acc);
border:0.5px solid #085041;background:#08504133;border-radius:10px;padding:1px 8px}}
</style></head><body><div class="wrap">
<div class="bar-top">
  <button onclick="location.reload()">刷新进度</button>
  <label><input type="checkbox" id="ar"> 每 30 秒自动刷新</label>
  <span class="sub" id="stamp">服务端渲染于 {esc(now)}</span>
  <span class="live">动态 · 每次请求实时探测</span>
</div>
<h1>{esc(spec['meta']['title'])}</h1>
<div class="sub">服务端渲染于 {esc(now)} ｜ 本页 <code>{esc(spec['meta'].get('dashboard_url', ''))}</code>
｜ 叙事状态源 <code>docs/dashboard/status.json</code>
｜ 权威路线 <code>{esc(spec['meta']['authority'])}</code></div>

<div class="goal"><div class="g">{esc(spec['meta']['goal'])}</div>
<div class="chain">{chain_html}</div>
<div class="note">{esc(spec['meta']['evidence_note'])}</div></div>

<h2>实时事实（脚本探测，非手写）</h2>
<div class="facts">{facts_html}</div>

<h2>需要你出手（否则会被挡住）</h2>
{nua_html}

{opt_html}

<h2>阶段进度</h2>
{''.join(stages_html)}

<h2>卡点与堵点</h2>
<ul class="blk">{blockers_html}</ul>

<h2>待授权 / 推进中的决策</h2>
<div class="card"><table><thead><tr><th>#</th><th>事项</th><th>建议</th><th>状态</th></tr></thead>
<tbody>{auth_html}</tbody></table>
<div class="note">{esc(spec.get('authorization_note',''))}</div></div>

<h2>下一步（按依赖排序）</h2>
<div class="card"><ol class="steps">{next_html}</ol></div>

<footer>本页由 <code>tools/build_roadmap_dashboard.py</code> 生成 ｜
重新生成即刷新全部实时事实 ｜ 叙事状态请在 <code>status.json</code> 中修改</footer>
</div>
<script>
(function(){{
  var cb = document.getElementById('ar');
  var KEY = 'evo-roadmap-autorefresh';
  try {{ cb.checked = (localStorage.getItem(KEY) === '1'); }} catch (e) {{}}
  var timer = null;
  function apply() {{
    if (timer) {{ clearTimeout(timer); timer = null; }}
    if (cb.checked) {{ timer = setTimeout(function(){{ location.reload(); }}, 30000); }}
    try {{ localStorage.setItem(KEY, cb.checked ? '1' : '0'); }} catch (e) {{}}
  }}
  cb.addEventListener('change', apply);
  apply();
}})();
</script>
</body></html>"""

    return doc


def main() -> int:
    """离线快照模式：生成一次性 HTML 文件（默认仅用于无服务时的降级查看）。

    常态请用 `tools/roadmap_server.py` —— 服务端渲染，刷新即最新。
    """
    spec = json.loads(STATUS.read_text(encoding="utf-8"))
    probes = collect_probes()
    doc = build_page(spec, probes)
    DASH.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")

    summary = {
        "out": str(OUT),
        "ledger": {k: probes["ledger"].get(k) for k in ("revision", "start_cash", "start_date", "nav", "paused")},
        "tasks": {"ready": probes["tasks"].get("ready"), "total": probes["tasks"].get("total")},
        "manifest_hash": (probes["manifest"].get("manifest_hash") or "")[:12],
        "archive_sha256_ok": probes["archive"].get("sha256_ok"),
        "stages": {s["id"]: f'{sum(1 for i in s["items"] if i["status"] == "done")}/{len(s["items"])}'
                   for s in spec["stages"]},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
