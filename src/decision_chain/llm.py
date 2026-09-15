# -*- coding: utf-8 -*-
"""R3.2 · LLM 裁量层（输出 schema + prompt 模板 + 调用/落盘/缓存/降级 + 回放校验）。

## 权威约束（`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md §4.2`，逐条落地）
| 协议项 | 本文件落地 |
|---|---|
| 职权：只在**非结构化判定**出力；输出限定 schema（**枚举 + 0-1 评分 + ≤N 字理由**） | `validate_output()` 严格校验：required / enum / 0-1 / 理由长度 / **禁止额外键** |
| 禁止：自由文本下单、覆盖硬规则、绕过硬风控、生成新标的理由 | prompt 显式写禁令；调用方**只接受 schema 字段**，不解析自由文本 |
| 落盘：记 **prompt 模板版本 / 模型名 / 输入快照 hash / 输出 / 耗时 / tokens** | `_persist()` 全量落 `outputs/decision_chain/llm/<date>/` |
| 可重放：**temperature=0** + 模板/模型版本冻结 → 同快照必须复现；**不可重放则判 `unreproducible` 并弃用该次裁量** | `replay_check()` 重发比对；不一致 → `status=unreproducible` 且**降级到保守默认档** |
| 限流：候选级（≤50 只/日），不做全市场；同标的同状态 **5 分钟内复用缓存** | `--no-cache` 之外的默认走 `_cache_path()`，TTL=300s |
| 降级：LLM 不可用时回退为**硬规则 + 保守默认档** | 任一失败路径 → `conservative_default(point)`，**绝不自由发挥** |

## ⚠️ key 来源（实测 2026-09-12）
**环境变量 `DEEPSEEK_API_KEY` 是失效的**（`/models` 返回 401）；
有效配置在 **`~/.workbuddy/models.json`**（含 `url` / `apiKey` / `id`）。本模块**优先读 models.json**，
环境变量仅作兜底。实测 `POST /chat/completions` → **HTTP 200**，`temperature=0` 被接受。

## 用法（供 engine 调用）
    from decision_chain.llm import consult, POINTS
    r = consult("D6", day="2026-09-11", obs={...}, snapshot_hash="...")
    # r["choice"] ∈ D6 的枚举；r["status"] ∈ ok / degraded / unreproducible
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request

BASE = pathlib.Path(__file__).resolve().parents[2]
if str(BASE / "src") not in sys.path:
    sys.path.insert(0, str(BASE / "src"))

OUT_DIR = BASE / "outputs" / "decision_chain" / "llm"
CACHE_TTL_SEC = 300.0
REASON_MAX = 120          # ≤N 字理由（协议：≤N 字）

# ── 模型选择（2026-09-13 实测后确立）
# ⭐ DeepSeek 侧实测（`GET /models`）：**可用模型只有两个** —— `deepseek-flash`（现用）与 `deepseek-v4-pro`。
#    而 `deepseek-chat` 与 `deepseek-reasoner` **返回的 `model` 都是 `deepseek-flash`** ——
#    它们**不是两个模型，而是同一底座的两种「应答模式」**：
#      · `deepseek-chat`     ：不产生 reasoning，completion **30** tok（实测）
#      · `deepseek-reasoner` ：产生 reasoning（756 字符），completion **211** tok（**7 倍**）
#    → reasoner 多出的 reasoning 会**吃掉 token 预算**，是 `schema_failed 12.5%` 的直接根因
#      （`content` 被截断/为空）。而本裁量层的任务**不需要推理链**（输出是严格 JSON 枚举 + ≤120 字理由）
#      → **默认改用 `deepseek-chat`**（模式），底座仍是同一个 flash。
#    ⚠️ 仍可用环境变量覆盖：`EVOALPHA_LLM_MODEL=deepseek-reasoner` / `deepseek-v4-pro`。
DEFAULT_MODEL = os.environ.get("EVOALPHA_LLM_MODEL", "").strip() or "deepseek-chat"
# 落盘用：本裁量层的"实现版本"（R1.6 `discretions[].cli_version` 的来源）
CLI_VERSION = "decision-chain/llm@2026-09-13"

# ── 裁量点注册表（枚举即 schema 的取值域；定义源＝persona/discretion_v0.toml）
POINTS: dict[str, dict] = {
    "D6": dict(
        seg="ENTRY", name="分时质量三档分级 与「止跌企稳」判定",
        # v3：2026-09-13 加「键名逐字照抄」硬约束 + choice/score 自洽 + reason 单行 ≤N 字
        #     （v2→v3：实测 9/22 失败源于模型把 discretion_id 写成驼峰 discretionId）。
        # v2：2026-09-12 修判据语义（区分「第一波回落」时序 vs 「全日最低」极值）。
        # ⚠️ 版本号必须随模板内容变更而升 —— 缓存键含它，否则旧模板的错误裁量会被继续复用。
        template_version="d6-v3",
        choices=["A_optimum", "B_medium", "C_reject"],
        # ⭐ 保守默认档：不是"弃"，而是**可观察不优先** ——
        #    因为 GEN-ENTRY-04 在**收益**上已被证伪，退化时不该用被证伪的判据去否决。
        conservative="B_medium",
        # 该点被上游 ④ 段消费的方式：C_reject → 弃（否决权归本点）
        veto_choice="C_reject",
    ),
    # ⭐ 2026-09-13 新增（架构共识：**机械算位置、LLM 判买卖**）。
    #   背景：卖出侧此前是**纯机械**（`sell.manage_day` 固定动作），但 SOP 里
    #   「走不走 / 减半还是全走」本质是 **brain 判断**（`GEN-HOLD-05/06`；
    #   见 `docs/RULE_ROLE_SPLIT.md` 的 86 条分流表）。
    "D11": dict(
        seg="HOLD", name="卖出决策：走不走 / 减半还是全走",
        # v2：2026-09-13 —— 开始**喂 SOP 原语**（`brain._load_sop`）。模板内容变了必须升版，
        #     否则缓存键不变 → 会命中「未喂 SOP」的旧判定。
        template_version="d11-v2",
        choices=["clear_all", "halve", "hold"],
        # 保守档 = **维持现状（不卖）**：卖出不可逆，降级时不得擅自清仓。
        #   与 D6 的「降级=放行」同族 —— 都是「退回本次改动之前的行为」。
        conservative="hold",
        # 卖出侧不存在「否决候选」语义，故为 None。
        veto_choice=None,
    ),
    "D8": dict(
        seg="SIZE", name="仓位档位选择与弹性调节",
        # v2：2026-09-13 —— `build_prompt` 是 D6/D8 共用的，故模板改动必须**两处同步升版**。
        template_version="d8-v2",
        choices=["floor", "mid", "ceiling"],
        conservative="floor",           # 保守默认＝区间下沿（少下注）
        veto_choice=None,
    ),
}

BAN_LINES = [
    "禁止输出任何自由文本的下单指令（不得出现买卖价量、标的代码作为建议）。",
    "禁止覆盖或改写硬规则判定；硬规则结果由代码给出，你只能在其**区间内**裁量。",
    "禁止绕过风控 veto。",
    "禁止生成新的标的理由（不得引入 prompt 未提供的标的）。",
    "只输出一个 JSON 对象，不要任何解释性前后缀、不要 markdown 代码块。",
    "⚠️ `choice` 与 `score` 必须**自洽**：选最差档（如 C_reject）时 `score` 必须低（建议 ≤0.3）；"
    "高 `score`（≥0.6）却选最差档属自相矛盾输出。",
]

# 各裁量点的**判据语义澄清**（防止把「极值」当「时序」等误用；由实测事故驱动，见各条注释）
POINT_GUARD = {
    "D6": [
        "⭐ **「第一波回落跌破均价线」是*时序*判据，不是*极值*判据**：它指**开盘后第一波上涨之后的回落**"
        "是否跌破均价线（SOP 冻结口径：09:35–10:00 窗口内、连续 5 分钟收盘低于均价线）。",
        "**权威字段**：`first_break`（布尔）/ `first_break_ts`（跌破时刻）/ `first_break_below_minutes`"
        "（窗口内低于均价线的分钟数）/ `first_break_reason`（口径说明）—— 判「弃」**必须以它为准**。",
        "⚠️ `intraday_low_vs_vwap_pct` 是**全日最低点**相对均价线的跌幅（**极值**）。"
        "任何振幅较大的票盘中都可能瞬时刺破均价线，**这不能用来推断「第一波回落跌破」**；"
        "若 `first_break=false`，**不得**因为该字段为负就判「弃」。",
        "若 `first_break=false` 且 `px_vs_vwap_pct≥0`（现价在均价线上）→ 属「未被均价线否决」，"
        "应优先在 A_optimum / B_medium 之间按量能与斜率定档。",
        "**尾盘是否收回**只在 `first_break=true` 时作为分档参考，**不能**把 `first_break=false` 翻成「弃」。",
    ],
}


# ══════════════════════════════════════════ 配置（key/url/model）
def load_config(model: str | None = None) -> dict:
    """凭证/端点取 `~/.workbuddy/models.json`（实测有效），**模型由本层自选**。

    ⚠️ 关键：**不要盲从 models.json 的 `id`** —— 那是 WorkBuddy 自己的偏好（`deepseek-reasoner`，
    即推理模式）。本裁量层的任务不需要推理链，故默认用 `DEFAULT_MODEL`（`deepseek-chat`）。
    凭证与端点仍复用该文件（它是本机唯一有效的 DeepSeek 配置）。
    """
    want = (model or DEFAULT_MODEL).strip()
    m = pathlib.Path.home() / ".workbuddy" / "models.json"
    if m.exists():
        try:
            doc = json.loads(m.read_text(encoding="utf-8"))
            items = doc if isinstance(doc, list) else (doc.get("models") or doc.get("items") or [])
            for it in items:
                if not isinstance(it, dict):
                    continue
                if "deepseek" in json.dumps(it, ensure_ascii=False).lower() and it.get("apiKey"):
                    return dict(ok=True,
                                url=it.get("url") or "https://api.deepseek.com/chat/completions",
                                key=it["apiKey"], model=want,
                                vendor=it.get("vendor"),
                                config_source="~/.workbuddy/models.json（凭证/端点）+ 本层自选模型",
                                wb_preferred_id=it.get("id"))
        except Exception:
            pass
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return dict(ok=True, url="https://api.deepseek.com/chat/completions", key=k,
                    model=want, config_source="env DEEPSEEK_API_KEY（⚠️ 实测曾失效）")
    return dict(ok=False, reason="未找到可用的 DeepSeek 配置")


# ══════════════════════════════════════════ prompt 模板
def build_prompt(point_id: str, obs: dict, sop: dict | None = None) -> str:
    p = POINTS[point_id]
    lines = [
        f"你是一个 A 股短线交易人格的**裁量模块**。本次只做一个判定：{p['name']}（裁量点 {point_id}）。",
        "",
        "【你的职权】只在非结构化判定上给出**枚举选择 + 0-1 评分 + 简short理由**；",
        "【硬约束】",
    ]
    lines += ["- " + x for x in BAN_LINES]
    lines += [
        "",
        f"【可选答案】只能取其一：{', '.join(p['choices'])}",
        f"【理由长度】不超过 {REASON_MAX} 个字符。",
    ]
    if POINT_GUARD.get(point_id):
        lines += ["", "【⭐ 判据语义澄清（务必先读，误用会导致系统性误判）】"]
        lines += ["- " + x for x in POINT_GUARD[point_id]]
    if sop:
        lines += ["", "【SOP 依据（选手原语，逐字）】"]
        if sop.get("stmt"):
            lines.append("- 规则: " + str(sop["stmt"]))
        if sop.get("quote"):
            lines.append("- 选手原话: " + str(sop["quote"]))
        if sop.get("outputs"):
            lines.append("- 三档定义: " + " / ".join(map(str, sop["outputs"])))
    lines += ["", "【本题要判的观测数据（只有这些，不得引入外部信息）】",
              json.dumps(obs, ensure_ascii=False, indent=1)]
    lines += [
        "",
        "【输出格式】严格输出这个 JSON（不要多余键、不要代码块围栏）：",
        json.dumps({"discretion_id": point_id, "choice": p["choices"][0],
                    "score": 0.0, "reason": "≤%d 字" % REASON_MAX,
                    "confidence": 0.0}, ensure_ascii=False),
        "",
        "⭐【键名必须逐字照抄】全部使用**小写 + 下划线**，且**只有下列 5 个键**："
        "`discretion_id` / `choice` / `score` / `reason` / `confidence`。",
        "- ✗ 错误示例：`discretionId`（驼峰）、`discretion-id`、`DiscretionId` —— 一律不接受。",
        "- ✗ 不要输出任何额外键（如 `sym` / `rationale` / `rationale_text` / `note` / `model`）。",
        "⭐【choice 与 score 必须自洽】若你给 `score` ≥0.6（认可该判定），就不能同时选语义上最保守或最差的那一档；反之亦然。",
        f"⭐【reason】必须是 ≤{REASON_MAX} 个字符的**单行**字符串，不要换行、不要引号嵌套。",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════ schema 校验
def validate_output(point_id: str, out: dict) -> list[str]:
    """严格校验（R3 验收：LLM 输出 **100%** 通过 schema 校验）。返回错误列表（空=通过）。"""
    errs: list[str] = []
    p = POINTS.get(point_id)
    if p is None:
        return [f"未知裁量点 {point_id}"]
    if not isinstance(out, dict):
        return ["输出不是 JSON 对象"]
    allow = {"discretion_id", "choice", "score", "reason", "confidence"}
    extra = set(out) - allow
    if extra:
        errs.append(f"出现未允许的键: {sorted(extra)}")
    for k in ("discretion_id", "choice", "score", "reason"):
        if k not in out:
            errs.append(f"缺必填键 {k}")
    if out.get("discretion_id") not in (None, point_id):
        errs.append(f"discretion_id 不符: {out.get('discretion_id')}")
    if "choice" in out and out["choice"] not in p["choices"]:
        errs.append(f"choice 越出枚举: {out['choice']} ∉ {p['choices']}")
    for k in ("score", "confidence"):
        if k in out:
            v = out[k]
            if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
                errs.append(f"{k} 必须是 0~1 的数：{v!r}")
    r = out.get("reason")
    if r is not None and (not isinstance(r, str) or len(r) > REASON_MAX):
        errs.append(f"reason 必须是 ≤{REASON_MAX} 字的字符串（实得 {len(str(r))}）")
    return errs


def conservative_default(point_id: str, why: str) -> dict:
    """协议第 6 条：降级回**硬规则 + 保守默认档**（不是自由发挥）。"""
    p = POINTS[point_id]
    return dict(discretion_id=point_id, choice=p["conservative"], score=0.0,
                reason=f"降级({why})"[:REASON_MAX], confidence=0.0,
                _degraded=True, _degrade_reason=why)


# ══════════════════════════════════════════ 缓存
def _key(point_id: str, snapshot_hash: str, template_version: str, model: str) -> str:
    return hashlib.sha256(
        f"{point_id}|{template_version}|{model}|{snapshot_hash}".encode()).hexdigest()[:24]


def _cache_path(k: str) -> pathlib.Path:
    return OUT_DIR / "cache" / f"{k}.json"


def _cache_get(k: str, ttl: float = CACHE_TTL_SEC):
    fp = _cache_path(k)
    if not fp.exists():
        return None
    try:
        if ttl and (time.time() - fp.stat().st_mtime) > ttl:
            return None
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════ 调用
def _raw_chat(cfg: dict, prompt: str, temperature: float, max_tokens: int) -> dict:
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(
        cfg["url"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['key']}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode("utf-8"))
    ch = (d.get("choices") or [{}])[0]
    return dict(payload=d, elapsed_ms=int((time.time() - t0) * 1000),
                served_model=d.get("model"), usage=d.get("usage") or {},
                finish_reason=ch.get("finish_reason"),
                has_reasoning=bool(ch.get("message", {}).get("reasoning_content")))


def _parse_content(payload: dict) -> dict:
    """只从 `choices[0].message.content` 取 JSON。

    ⚠️ **不从 `reasoning_content` 里捞** —— 协议要求输出限定 schema，
    解析模型的思考过程属"解析自由文本"，越界。
    """
    msg = (payload.get("choices") or [{}])[0].get("message", {})
    txt = str(msg.get("content") or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.split("\n", 1)[1] if "\n" in txt else txt
    if not txt:
        raise ValueError("empty_content"
                         f"(finish_reason={(payload.get('choices') or [{}])[0].get('finish_reason')}, "
                         f"reasoning={'yes' if msg.get('reasoning_content') else 'no'})")
    return json.loads(txt)


def _persist(rec: dict) -> pathlib.Path:
    d = OUT_DIR / rec["date"]
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{rec['point']}__{rec['cache_key'][:8]}__{int(time.time()*1000)%100000}.json"
    fp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return fp


def _put_cache(key: str, cfg: dict, out: dict, snapshot_hash: str, status: str = "ok",
               prompt_hash: str = "", cli_version: str = "") -> None:
    """写缓存。**所有终态都写**（含 degraded / schema_failed 的保守档），并记住**原始 status 与可重放元信息**。

    ⚠️ 三个必须（2026-09-12/13 各踩一次）：
    1. **降级路径也要缓存**：最初只缓存 `ok` → degraded 的标的每轮重新调用 → 输出漂移 →
       决策链 `chain_hash` 不一致、不可重放率 2/6，把 R3 验收核心项打退化。
    2. **缓存必须记住原始 status**：修完①后仍漂移 1/6 —— 因为缓存命中时一律返回
       `status="ok"`，把原本的 `degraded` 抹掉，导致 `llm_stats` 的 ok/degraded 计数两轮不同。
    3. **缓存必须记住 `prompt_hash` / `cli_version`**（2026-09-13 加 `discretions` 后暴露）：
       缓存命中分支若不返回这两个字段，`discretions[].prompt_sha256` 会在第二轮变空
       → payload 变化 → **`replay_hash` 漂移**（即使 LLM 输出完全一致）。
       而 `decision.discretions` **在 `IMPLEMENTATION_HASH_FIELDS` 内**，因此任何字段缺失都会污染可重放性。
    """
    fp = _cache_path(key)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(dict(model=cfg.get("model"), output=out, status=status,
                                  prompt_hash=prompt_hash, cli_version=cli_version,
                                  ts=time.time(), snapshot_hash=snapshot_hash),
                             ensure_ascii=False), encoding="utf-8")


def consult(point_id: str, *, date: str, obs: dict, snapshot_hash: str,
            sop: dict | None = None, no_cache: bool = False,
            temperature: float = 0.0, max_tokens: int = 2400,
            replay_of: dict | None = None, cache_ttl: float | None = None) -> dict:
    """对一个裁量点发起一次裁量。返回 dict（含 status / choice / score / reason + 落盘元信息）。

    status ∈ ok / degraded / unreproducible

    :param cache_ttl: 覆盖默认缓存寿命（秒）。**默认 None = 沿用 `CACHE_TTL_SEC`（300s）**，
        故盘后链行为不变。R4.1 盘中否决权传「到当日收盘的剩余秒数」以实现**当日锁定** ——
        盘中 `snapshot_hash` 由调用方给成**日级稳定值**（不含分钟特征），键因此当日恒定，
        TTL 覆盖到收盘 ⇒ 同一标的当日**只真调一次**，且判定结果不会中途翻转
        （否则可能出现先 `C_reject` 否决、后 `A_optimum` 放行的自我矛盾）。
    """
    p = POINTS[point_id]
    cfg = load_config()
    if not cfg.get("ok"):
        d = conservative_default(point_id, cfg.get("reason", "no_config"))
        return dict(status="degraded", **d, date=date, point=point_id)
    key = _key(point_id, snapshot_hash, p["template_version"], cfg["model"])
    prompt = build_prompt(point_id, obs, sop)
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

    cached = None if (no_cache or replay_of) else _cache_get(
        key, ttl=(CACHE_TTL_SEC if cache_ttl is None else cache_ttl))
    if cached and replay_of is None:
        # ⚠️ 返回**原始 status**（不是一律 ok）+ **prompt_hash/cli_version**，
        #    否则 `llm_stats` 计数与 `discretions[].prompt_sha256` 会在缓存命中时变化 → `replay_hash` 漂移。
        return dict(status=cached.get("status", "ok"), **cached["output"],
                    date=date, point=point_id, cache_hit=True,
                    cache_key=key, model=cached.get("model") or cfg["model"],
                    prompt_template_version=p["template_version"],
                    prompt_hash=cached.get("prompt_hash") or prompt_hash,
                    cli_version=cached.get("cli_version") or CLI_VERSION)

    base = dict(date=date, point=point_id, cache_key=key,
                prompt_template_version=p["template_version"], model=cfg["model"],
                config_source=cfg.get("config_source"), snapshot_hash=snapshot_hash,
                prompt_hash=prompt_hash, temperature=temperature,
                cli_version=CLI_VERSION,
                created_at=_dt.datetime.now().isoformat(timespec="seconds"))

    def _finish(status: str, out: dict, **extra) -> dict:
        """统一出口：**所有终态写缓存（含原始 status + prompt_hash/cli_version）**。"""
        _put_cache(key, cfg, out, snapshot_hash, status=status,
                   prompt_hash=prompt_hash, cli_version=CLI_VERSION)
        return dict(status=status, **out, date=date, point=point_id,
                    prompt_template_version=p["template_version"],
                    model=cfg["model"], prompt_hash=prompt_hash,
                    cli_version=CLI_VERSION, **extra)

    try:
        raw = _raw_chat(cfg, prompt, temperature, max_tokens)
    except Exception as e:  # noqa: BLE001
        d = conservative_default(point_id, f"call_failed:{type(e).__name__}")
        _persist(dict(base, status="degraded", error=f"{type(e).__name__}: {e}", output=d))
        return _finish("degraded", d, error=f"{type(e).__name__}: {e}")

    # ⚠️ 解析失败**重试一次**（2026-09-12 实测：`deepseek-reasoner` 的 reasoning 会吃掉
    #    token 预算，导致 `content` 为空或 JSON 被截断 —— 属概率性，重试能救回大半）。
    out, errs, attempts = None, [], 0
    for attempt in (1, 2):
        attempts = attempt
        try:
            out = _parse_content(raw["payload"])
            errs = validate_output(point_id, out)
            if not errs:
                break
        except Exception as e:  # noqa: BLE001
            out, errs = ({"_raw": str(raw["payload"])[:300]},
                         [f"parse_failed: {type(e).__name__}: {e}"])
        if attempt == 1:
            time.sleep(0.8)
            try:
                raw = _raw_chat(cfg, prompt, temperature, max_tokens)
            except Exception as e:  # noqa: BLE001
                errs.append(f"retry_call_failed: {type(e).__name__}")
                break

    rec = dict(base, status=("ok" if not errs else "schema_failed"),
               elapsed_ms=raw.get("elapsed_ms"), served_model=raw.get("served_model"),
               finish_reason=raw.get("finish_reason"), has_reasoning=raw.get("has_reasoning"),
               attempts=attempts, max_tokens=max_tokens,
               tokens=raw.get("usage"), schema_errors=errs, output=out)
    _persist(rec)

    if errs:
        # 协议：输出必须 100% 过 schema → 不过则**弃用该次裁量**，回落保守档（并缓存，保证可重放）
        d = conservative_default(point_id, "schema_failed")
        return _finish("degraded", d, schema_errors=errs)

    if replay_of is not None:
        same = (out.get("choice") == replay_of.get("choice")
                and abs(float(out.get("score", 0)) - float(replay_of.get("score", 0))) < 1e-9)
        if not same:
            d = conservative_default(point_id, "unreproducible")
            _persist(dict(base, status="unreproducible", output=d,
                          first_output=replay_of, second_output=out))
            return _finish("unreproducible", d, first=replay_of, second=out)

    return _finish("ok", out, cache_hit=False, cache_key=key,
                   served_model=raw.get("served_model"),
                   elapsed_ms=raw["elapsed_ms"], tokens=raw["usage"])


def replay_check(point_id: str, *, date: str, obs: dict, snapshot_hash: str,
                 sop: dict | None = None) -> dict:
    """回放：同一快照重发一次，比对 choice+score。返回 {reproducible: bool, ...}。"""
    first = consult(point_id, date=date, obs=obs, snapshot_hash=snapshot_hash, sop=sop, no_cache=True)
    if first.get("status") != "ok":
        return dict(reproducible=None, reason=f"首跑非 ok: {first.get('status')}", first=first)
    second = consult(point_id, date=date, obs=obs, snapshot_hash=snapshot_hash, sop=sop,
                     no_cache=True, replay_of=first)
    ok = second.get("status") == "ok"
    return dict(reproducible=ok, first_choice=first.get("choice"),
                second_choice=second.get("choice"),
                first_score=first.get("score"), second_score=second.get("score"),
                status=second.get("status"))
