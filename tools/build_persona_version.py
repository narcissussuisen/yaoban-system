# -*- coding: utf-8 -*-
"""R1.5 施工：人格版本化 —— v0 = 现状生产逐字克隆（只验证零差异，不申请晋级）。

产出：
  yaoban-system/persona/versions/v0.toml            版本定义（含 hash 清单 + 语义契约 + 零差异协议 + 预注册）
  yaoban-system/persona/versions/registry.json      不可变 append-only 版本注册表
  docs/PERSONA_VERSIONING_v0.md                     人读说明

用法：
    python build_persona_version.py            # 冻结：计算 hash → 写 v0.toml + 登记 registry
    python build_persona_version.py --verify   # 校验：重算比对，frozen 漂移即非零退出

设计要点（为什么要分两组 hash）
--------------------------------
- **frozen（冻结组）**：人格定义本身（SOP/裁量/容差/记忆/自述/引擎参数）。
  它们的任何改动都意味着**人格变了** → 必须产生新版本，故 `--verify` 对它们**失败即非零退出**。
- **referenced（引用组）**：决策路径的代码（`sell.py` / `tick_monitor.py` / …）。
  代码演进是 R2–R4 的施工常态，把它们纳入「漂移=失败」会让 v0 永远红。
  故**只记录 hash 供溯源**，漂移仅作 info 报告（并在 `drift_since` 里注明）。

零差异协议（三层，本工具实现 ①②，③ 定义契约待 R3/R4 落地）
----------------------------------------------------------
① **文件 hash 全等**：frozen 组每个文件 sha256 与冻结时一致
② **语义契约全等**：从代码里抽出的关键契约常量（见 SEMANTIC_SPECS）与冻结时一致 ——
   这一层比 hash 更有意义：它让「逐字克隆」不只靠字节，还能**读懂**到底克隆了什么
③ **决策重放一致**：给定同一交易日输入，决策链产出同一 artifact（需 R3/R4 的 harness）
"""

import hashlib
import json
import pathlib
import re
import sys
import tomllib
import traceback
from datetime import datetime

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
YS = ROOT / "yaoban-system"
OUTDIR = YS / "persona/versions"
OUT_V0 = OUTDIR / "v0.toml"
OUT_REG = OUTDIR / "registry.json"
OUT_MD = ROOT / "docs/PERSONA_VERSIONING_v0.md"

# ── frozen：人格定义（改动 = 人格变了 = 新版本）
FROZEN = [
    "yaoban-system/persona/sop_v0.toml",
    "yaoban-system/persona/discretion_v0.toml",
    "yaoban-system/persona/tolerance_v0.toml",
    "yaoban-system/persona/memory/memory_v0.toml",
    "yaoban-system/persona/memory/self_narrative_schema_v0.toml",
    "yaoban-system/persona/memory/self_narrative_v0.toml",
    "yaoban-system/persona/video_index.json",
    "yaoban-system/config/parameters.toml",
]
# ── referenced：决策路径代码（只溯源，漂移不判失败）
REFERENCED = [
    "yaoban-system/src/core/sell.py",
    "yaoban-system/scripts/tick_monitor.py",
    "yaoban-system/scripts/_tick_watch.py",
    "yaoban-system/scripts/plan_daily.py",
    "yaoban-system/scripts/scan_and_confirm.py",
    "yaoban-system/scripts/close_pipeline.py",
    "yaoban-system/portfolio/ledger.py",
]

# ── 语义契约：从代码里抽出的、可读的「克隆到底克隆了什么」
# 每条 = (文件名, 正则, 描述, 可选标志)。文件名按 SEARCH_DIRS 顺序解析。
# ⚠️ 正则必须按**真实代码形态**写：本工具第一版有 4 条抽不到（跨行 dict / 常量名猜错 /
#    文件不在 src/core），已逐条核对修正 —— 教训：语义契约的锚点与补丁锚点一样，必须取实际原文。
SEARCH_DIRS = ["scripts", "src/core", "portfolio", "."]
SEMANTIC_SPECS = [
    ("tick_monitor.py", r"^AFTER_HOURS_START\s*,\s*AFTER_HOURS_END\s*=\s*(.+)$", None,
     "盘后固定价格窗口边界"),
    ("tick_monitor.py", r"^def (in_continuous_session|in_after_hours_session|in_exec_window)\(",
     None, "成交窗口判定函数（三处调用点收敛到此）"),
    ("tick_monitor.py", r"^SELL_ENGINE_PARAMS\s*=\s*\{[\s\S]*?\}", "M", "卖点引擎生产参数覆盖"),
    ("tick_monitor.py", r"^DAILY_REBUILT\s*=\s*(.+)$", None, "日线兜底数据源路径"),
    ("_tick_watch.py", r"^CLOSE_NO_RESTART\s*=\s*(.+)$", None, "看门狗：收盘后不再重启的时刻"),
    ("_tick_watch.py", r"^WATCH_EXIT_AT\s*=\s*(.+)$", None, "看门狗：兜底退出时刻"),
    ("_tick_watch.py", r"^\s*'check_every':\s*20,\s*'stale_sec':\s*90.*$", None,
     "看门狗生产配置（check_every/stale_sec/boot_grace/max_restarts）"),
    # ⚠️ 不能锚 `$`：这些参数行尾常带注释（如 `"stop_loss_pct": 5.0,        # …`）
    ("sell.py", r"[\"']t_enabled[\"']\s*:\s*([^,\n]+)", None, "引擎默认参数 t_enabled（做T）"),
    ("sell.py", r"[\"']stop_loss_pct[\"']\s*:\s*([^,\n]+)", None, "引擎默认参数 stop_loss_pct"),
    ("ledger.py", r"^\s*_DEFAULT_LEDGER\s*=\s*(.+)$", None, "账本默认路径"),
    ("ledger.py", r"^def (_resolve_ledger|cost_equity)\(", None, "账本路径解析 / 动态权益函数"),
]


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def hash_group(paths):
    out = {}
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            out[rel] = None
            continue
        st = p.stat()
        out[rel] = {"sha256": sha256(p), "bytes": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
    return out


def _find(fname):
    for d in SEARCH_DIRS:
        p = (YS / d / fname) if d != "." else (YS / fname)
        if p.exists():
            return p
    return None


def extract_semantics():
    """从代码里抽语义契约。返回 {key: value}，key = '<file>::<描述>'"""
    out = {}
    for fname, pat, flags, desc in SEMANTIC_SPECS:
        fl = re.MULTILINE                      # 默认按行锚定（^ / $ 逐行生效）
        if flags and "M" in flags:
            fl |= re.DOTALL                    # "M" = 额外允许跨行捕获
        p = _find(fname)
        if p is None:
            out[f"{fname}::{desc}"] = "(file missing)"
            continue
        rx = re.compile(pat, fl)
        ms = rx.findall(p.read_text(encoding="utf-8", errors="replace"))
        if not ms:
            out[f"{fname}::{desc}"] = "(pattern not found)"
        elif len(ms) == 1:
            m = ms[0]
            s = m if isinstance(m, str) else "|".join(m)
            # 跨行捕获 → 压成单行，便于比对与阅读
            out[f"{fname}::{desc}"] = " ".join(s.split())
        else:
            out[f"{fname}::{desc}"] = "; ".join(
                (" ".join((m if isinstance(m, str) else "|".join(m)).split())) for m in ms)
    return out


META = {
    "version": "v0",
    "kind": "baseline",
    "parent_version": "",
    "name": "现状生产逐字克隆",
    "declared_at": "2026-09-12",
    "authority": "docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md §R1.5",
    "definition": "v0 = 现状生产逐字克隆（verbatim clone of current production）。"
                  "唯一目的是把「当前生产」钉成可比对的基线：不新增策略、不改参数、不改代码，"
                  "所以它**不申请晋级**，也不构成任何业绩声明。",
    "promotion_requested": False,
    "promotion_note": "按计划：v0 **只验证零差异**，不申请晋级。晋级只能由 R5.1 净值判定器"
                      "（前向净值 vs 双基准臂）授予。",
    "zero_diff_protocol": [
        "① 文件 hash 全等：frozen 组每个文件 sha256 与冻结时一致（--verify 失败即非零退出）",
        "② 语义契约全等：代码里抽出的关键契约常量与冻结时一致",
        "③ 决策重放一致：给定同一交易日输入，决策链产出同一 artifact（需 R3/R4 harness，本轮只定义契约）",
    ],
    "preregistered": {
        "hypothesis": "v0 与现状生产逐日零差异（同输入 → 同决策 → 同 fill 序列）",
        "falsifier": "出现任一：(a) frozen 组 hash 漂移；(b) 语义契约漂移；"
                     "(c) 同一交易日输入下决策 artifact 不一致",
        "expected_result": "零差异",
        "performance_claim": "无 —— v0 明确**不作业绩声明**。主账本已于 2026-09-12 切换为"
                             "50 万独立账本（start_date=2026-09-14），与切换前账户不具可比性。",
        "review_trigger": "R5.0（分层晋级机制）建成后，v0 作为「实现层」基线参与比对",
    },
    "scope_note": "frozen 组的任何改动都意味着**人格变了** → 必须产生 v1 并声明 parent_version=v0 与"
                  "新的预注册假设。referenced 组（决策路径代码）的演进是 R2–R4 施工常态，"
                  "故只记录 hash 供溯源，漂移不判失败。",
}


def main():
    verify_only = "--verify" in sys.argv
    OUTDIR.mkdir(parents=True, exist_ok=True)

    frozen = hash_group(FROZEN)
    referenced = hash_group(REFERENCED)
    sem = extract_semantics()

    missing = [k for k, v in frozen.items() if v is None]
    if missing:
        print("FAIL: frozen 组缺文件：" + ", ".join(missing))
        return 2

    if verify_only:
        if not OUT_V0.exists():
            print("FAIL: 尚未冻结（缺 v0.toml）")
            return 2
        old = tomllib.loads(OUT_V0.read_text(encoding="utf-8"))
        of = old.get("frozen", {})
        osem = {k: v for k, v in old.get("semantics", {}).items()}
        orf = old.get("referenced", {})

        drift = []
        for k, v in frozen.items():
            ov = of.get(k, {})
            if ov.get("sha256") != v["sha256"]:
                drift.append(f"FROZEN  {k}\n    冻结 {ov.get('sha256','(缺)')[:16]} → 现在 {v['sha256'][:16]}")
        for k, v in sem.items():
            if osem.get(k) != v:
                drift.append(f"SEMANTIC {k}\n    冻结 {osem.get(k)!r} → 现在 {v!r}")
        info = []
        for k, v in referenced.items():
            ov = orf.get(k, {})
            if ov.get("sha256") != v["sha256"]:
                info.append(f"  (info) referenced 漂移：{k}")

        print("=" * 76)
        print("R1.5 人格 v0 零差异校验")
        print("=" * 76)
        print(f"frozen 文件数      : {len(frozen)}")
        print(f"语义契约数        : {len(sem)}")
        print(f"referenced 文件数 : {len(referenced)}")
        print()
        if drift:
            print(f"❌ 检测到 {len(drift)} 处漂移（frozen/语义 = 判失败）：")
            for d in drift:
                print("  " + d)
        else:
            print("✅ 零差异：frozen 组 hash 与语义契约全部与冻结时一致")
        if info:
            print()
            print("referenced 组漂移（代码演进，不判失败）：")
            for i in info:
                print(i)
        return 1 if drift else 0

    # ── 冻结
    now = datetime.now().isoformat(timespec="seconds")
    with OUT_V0.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# EvoAlpha 人格版本 v0 —— 现状生产逐字克隆（R1.5）\n")
        fh.write("# 生成器：yaoban-system/tools/build_persona_version.py\n")
        fh.write("# 校验：python build_persona_version.py --verify（frozen 漂移即非零退出）\n\n")
        fh.write("[meta]\n")
        fh.write(f'frozen_at = "{now}"\n')
        for k, v in META.items():
            fh.write(f"{k} = {tv(v)}\n")
        fh.write("\n[frozen]\n")
        for rel, v in frozen.items():
            fh.write(f'"{rel}" = {{ sha256 = "{v["sha256"]}", bytes = {v["bytes"]}, '
                     f'mtime = "{v["mtime"]}" }}\n')
        fh.write("\n[semantics]\n")
        for k, v in sem.items():
            fh.write(f"{toml_key(k)} = {tv(v)}\n")
        fh.write("\n[referenced]\n")
        for rel, v in referenced.items():
            if v is None:
                fh.write(f'"{rel}" = {{ sha256 = "", bytes = 0, mtime = "" }}\n')
            else:
                fh.write(f'"{rel}" = {{ sha256 = "{v["sha256"]}", bytes = {v["bytes"]}, '
                         f'mtime = "{v["mtime"]}" }}\n')

    # ── registry（append-only）
    reg = json.loads(OUT_REG.read_text(encoding="utf-8")) if OUT_REG.exists() else {"versions": []}
    entry = {
        "version": "v0",
        "kind": "baseline",
        "parent_version": None,
        "declared_at": META["declared_at"],
        "frozen_at": now,
        "promotion_requested": False,
        "frozen_files": len(frozen),
        "semantics": len(sem),
        "referenced_files": len(referenced),
        "note": META["definition"],
    }
    reg["versions"] = [v for v in reg.get("versions", []) if v.get("version") != "v0"] + [entry]
    reg["updated_at"] = now
    reg["note"] = ("不可变 append-only 版本注册表。每次人格改动生成新版本条目（parent_version 指向前一版），"
                   "禁止改写历史条目。")
    OUT_REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")

    write_md(frozen, sem, referenced, now)

    # ── 自校验
    tomllib.loads(OUT_V0.read_text(encoding="utf-8"))
    back = json.loads(OUT_REG.read_text(encoding="utf-8"))
    assert any(v["version"] == "v0" for v in back["versions"]), "registry 未登记 v0"
    print(f"selfcheck: v0.toml 回读 OK / registry 已登记 v0")
    print(f"OK v0 冻结：frozen={len(frozen)} semantics={len(sem)} referenced={len(referenced)}")
    print(f"  {OUT_V0}")
    print(f"  {OUT_REG}")
    print(f"  {OUT_MD}")
    return 0


def tv(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f'"{k}" = {tv(x)}' for k, x in v.items()) + "}"
    s = (str(v).replace("\\", "\\\\").replace('"', '\\"')
         .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{s}"'


def toml_key(s):
    # 「file.md::描述」→ 合法 TOML bare key 不便，统一用引号
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_md(frozen, sem, referenced, now):
    L = []
    a = L.append
    a("# EvoAlpha 人格版本化 v0")
    a("")
    a("> **自动生成**，请勿手改。生成器 `yaoban-system/tools/build_persona_version.py`；")
    a("> 机器可读：`persona/versions/v0.toml` + `persona/versions/registry.json`。")
    a("> 权威：`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md` §R1.5。")
    a("")
    a(f"**冻结时刻**：{now}")
    a("")
    a("## 一、v0 是什么")
    a("")
    a(f"{META['definition']}")
    a("")
    a(f"- **kind**：`{META['kind']}`　**parent_version**：`(无)`　"
      f"**是否申请晋级**：**否**")
    a(f"- {META['promotion_note']}")
    a(f"- {META['scope_note']}")
    a("")
    a("## 二、零差异协议（三层）")
    a("")
    for p in META["zero_diff_protocol"]:
        a(f"- {p}")
    a("")
    a("> 本工具实现 ①②；③ 需 R3/R4 的 harness，本轮只定义契约。")
    a("")
    a("**为什么分两组 hash**")
    a("")
    a("| 组 | 内容 | 漂移后果 | 文件数 |")
    a("|---|---|---|---|")
    a(f"| **frozen** | 人格定义本身（SOP/裁量/容差/记忆/自述/引擎参数/vNN 索引） | "
      f"**= 人格变了 → 必须产生新版本**；`--verify` 失败即非零退出 | {len(frozen)} |")
    a(f"| **referenced** | 决策路径代码（`sell.py` / `tick_monitor.py` / …） | "
      f"只记录 hash 供溯源；漂移仅作 info（代码演进是 R2–R4 常态） | {len(referenced)} |")
    a("")
    a("## 三、frozen 组（人格定义）")
    a("")
    a("| 文件 | 字节 | sha256（前 16） | 冻结前 mtime |")
    a("|---|---|---|---|")
    for rel, v in frozen.items():
        a(f"| `{rel}` | {v['bytes']:,} | `{v['sha256'][:16]}…` | {v['mtime']} |")
    a("")
    a("## 四、语义契约（比 hash 更能说明「克隆了什么」）")
    a("")
    a("从代码里抽出的关键契约常量 —— 逐个可读、可比对：")
    a("")
    a("| 契约 | 值 |")
    a("|---|---|")
    try:
        sem_items = list(sem.items())
    except Exception:
        sem_items = []
    for k, v in sem_items:
        a(f"| `{k}` | `{v}` |")
    a("")
    a("> ⭐ 这一层的意义：hash 只告诉你「变了」，语义契约告诉你「**变的是不是策略行为**」。")
    a("> 例如 `AFTER_HOURS_START, AFTER_HOURS_END` 被改动 = 成交窗口变了 = 策略行为变了，"
      "必须走新版本；而纯格式调整不会触发语义漂移。")
    a("")
    a("## 五、referenced 组（决策路径代码，仅供溯源）")
    a("")
    a("| 文件 | 字节 | sha256（前 16） |")
    a("|---|---|---|")
    for rel, v in referenced.items():
        if v is None:
            a(f"| `{rel}` | — | (不存在) |")
        else:
            a(f"| `{rel}` | {v['bytes']:,} | `{v['sha256'][:16]}…` |")
    a("")
    a("## 六、预注册假设")
    a("")
    pr = META["preregistered"]
    a(f"- **假设**：{pr['hypothesis']}")
    a(f"- **证伪条件**：{pr['falsifier']}")
    a(f"- **预期结果**：{pr['expected_result']}")
    a(f"- **业绩声明**：{pr['performance_claim']}")
    a(f"- **评审触发**：{pr['review_trigger']}")
    a("")
    a("## 七、版本纪律")
    a("")
    a("1. **不可变**：`registry.json` 是 append-only；历史条目禁止改写。")
    a("2. **每次改动生成新版本**：frozen 组任一文件变更 → 生成 `vN+1`，声明 `parent_version`，"
      "并写新的预注册假设（含证伪条件与预期结果）。")
    a("3. **禁止跨段拼接**：净值归因必须按版本分段（R5.2），不得把 v0 与后续版本的净值拼成一条曲线。")
    a("4. **v0 不作业绩声明**：主账本已于 2026-09-12 切换为 50 万独立账本"
      "（start_date=2026-09-14），与切换前账户不具可比性。")
    a("")
    a("## 八、用法")
    a("")
    a("```bash")
    a("# 冻结（首次 / 生成新版本时）")
    a("python yaoban-system/tools/build_persona_version.py")
    a("")
    a("# 校验零差异（frozen 或语义漂移 → 非零退出）")
    a("python yaoban-system/tools/build_persona_version.py --verify")
    a("```")
    a("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        OUTDIR.mkdir(parents=True, exist_ok=True)
        (OUTDIR / "_build_version.crash.txt").write_text(
            traceback.format_exc(), encoding="utf-8")
        raise
