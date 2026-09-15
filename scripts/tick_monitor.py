"""EvoAlpha 持仓秒级守护。

默认只读；--execute-risk 时是唯一盘中卖出执行器：止损、炸板、破VWAP减半。
保守成交：跌停附近、最近3根无量、无T+1可卖份额时只写告警不成交。
"""
from __future__ import annotations
import argparse,json,os,pathlib,sys,time,traceback
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'src'))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'portfolio'))
import pandas as pd
from pytdx.hq import TdxHq_API
from core.intraday import vwap_series
from core.tencent_minline import min_df as tencent_min_df, quote as tencent_quote
from core.sell import limit_price,limit_pct_of,manage_day,DEFAULT_PARAMS as SELL_DEFAULTS
from ledger import load,transact,sell,sellable_qty,record_autonomous_decision
from decision_chain import brain as _brain  # 2026-09-13 D11 卖出裁量（LLM 判轻重）；brain 自带 sys.path 注入
BASE=pathlib.Path(__file__).resolve().parent.parent
OUT=BASE/'outputs'/'intraday'; OUT.mkdir(parents=True,exist_ok=True)
# 2026-09-11 P0 修复(唤醒信号解耦): daemon 每轮开头无条件写心跳, 与 pos_live 写入彻底分开。
# 根因: 此前 watchdog 唯一健康判据是 pos_live mtime, 而"数据未就绪/写盘被跳过"会让一个
# **完全健康的循环**被判成"挂死"→ 反复 kill+重启 → 烧穿重启额度 → 当日隔离(9/11 实录:
# 174 次 scan 被 fail-closed 拦截, 买入链全天停摆)。心跳只证明"循环在转", 数据新鲜度另算。
BEAT=OUT/'_tick_daemon.beat'


def write_beat(n=None, interval=None):
    """把"我还活着"写进独立文件; 任何异常都不得影响主循环。"""
    try:
        t=n or datetime.now(ZoneInfo('Asia/Shanghai'))
        payload={'datetime':t.strftime('%Y-%m-%d %H:%M:%S'),'pid':os.getpid()}
        if interval is not None: payload['interval']=interval
        tmp=BEAT.with_name(BEAT.name+f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        os.replace(tmp,BEAT)
    except Exception:
        pass


SERVERS=[('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]
# 2026-09-11 P0 修复: 原取 get_security_bars(0,...) 是 5 分钟类别, 但下游按 1 分钟口径过滤当日 bar
# (rows=[b for b in bars if b['datetime'].startswith(day)])。实测 TDX 对持仓股返回 5 分钟 bar, 开盘
# 9 分钟内只有 2 根 -> 撞上 len(rows)<3 门槛 -> 每轮 continue -> pos_live 永不落盘。watchdog 以
# mtime 为唯一健康判据, 遂判"挂死"反复重启; 每次重启重置 150s 宽限, 永远攒不够 3 根 = 死循环烧额度
# (9/11 实录: 3 次假重启, pos_live 陈旧 18.5h, 全部飞书误告警)。
# 唯一顺带后果更严重: 止损/炸板/VWAP 触发判定与 pos_live 写在同一个被 continue 跳过的循环体内,
# tick 作为唯一盘中卖出执行器对全部持仓失明(9/11 300468 压在止损线上仍未执行)。
KLINE_1MIN=8  # pytdx KLINE_TYPE_1MIN; get_security_bars 的 category 参数

# ===== 交易时段判定（2026-09-12 R0.8：补上盘后固定价格窗口）=====
# 背景：深交所 2026-07-06 起把**盘后固定价格交易**扩围到全部 A 股 + ETF，
#   15:05-15:30 是合法可交易窗口，**成交价 = 当日收盘价**。
# 原实现缺口：exec_window 上限写死 15:00、daemon 又 `hm>'15:05'` 自退
#   ⇒ 15:00-15:30 共 30 分钟（其中 15:05-15:30 可交易）**零守护**：
#   既没有持仓保护，也没有任何卖出通道。
# 处置：窗口判定**收敛成一处**（下面三个函数），run 守卫 / daemon 循环 / 卖出触发共用。
#   盘后窗口是**纯时间窗，不区分触发类型** —— 这样 R0.5 把止盈类触发接进生产后，
#   它们在盘后同样能成交（用户裁定：R0.8 的卖出窗口必须含止盈，不只是止损/风控补单）。
# ⚠️ 15:00-15:05 刻意排除：连续竞价已结束、盘后固定价格交易尚未开始，
#   在这 5 分钟里成交会产生"按最后一根 m1 收盘价"的伪记录。
AFTER_HOURS_START, AFTER_HOURS_END = '15:05', '15:30'
SESSION_AM = ('09:30', '11:30')
SESSION_PM = ('13:00', '15:00')

def in_continuous_session(hm: str) -> bool:
    """连续竞价时段（09:30-11:30 / 13:00-15:00）。"""
    return (SESSION_AM[0] <= hm <= SESSION_AM[1]) or (SESSION_PM[0] <= hm <= SESSION_PM[1])

def in_after_hours_session(hm: str) -> bool:
    """盘后固定价格交易窗口（15:05-15:30；成交价 = 当日收盘价）。"""
    return AFTER_HOURS_START <= hm <= AFTER_HOURS_END

def in_exec_window(hm: str) -> bool:
    """可成交时刻。**三个调用点必须共用本函数**，别再各写一遍字面量。"""
    return in_continuous_session(hm) or in_after_hours_session(hm)


# ===== 2026-09-12 R0.5：完整卖点引擎参数 =====
# 基数取自 config/parameters.toml [sell.intraday]（经 core.sell.DEFAULT_PARAMS 合入），
# 即生产配置里早已写好、但从未被生产脚本使用的那份完整卖出纪律。
# 三处**刻意关掉**的（不是遗漏）：
#   - t_enabled=False：做T 会发**买单**（t_buy 正T先买 / t_buy_back 反T买回），
#     而本 daemon 是「与门禁解耦」的（2026-09-10 裁定：门禁失败只停买入类）。
#     让守护者能买入 = 绕过计划门禁 ⇒ 做T 必须另走一条带门禁的路径。
#   - dragon_link_sell / sector_retreat_sell：需组合上下文（combo={"dragon_time":…}），
#     daemon 侧没有该上下文，留着只会永远不触发（显式关闭以免误读为"已启用"）。
SELL_ENGINE_PARAMS = {**SELL_DEFAULTS,
                      "t_enabled": False,
                      "dragon_link_sell": False,
                      "sector_retreat_sell": False}

# ===== 2026-09-13 D11 卖出裁量接线：急停开关 =====
# 语义: **默认关闭**（未设 = 纯机械卖出，行为与接线前逐字一致）; `EVOALPHA_BRAIN_SELL=1` 启用。
#   ⇒ 上线流程为「先空跑观察 → 设 =1 观察 1~2 个交易日 → 通过后才把默认值改为启用」。
# 用途: 一级回退开关 —— 置 0/删掉该变量并重启 daemon 即完全退回纯机械卖出，无需改代码。
# ⚠️ 模块层读取 ⇒ 改 env 后**必须重启 daemon** 才生效（watchdog 会带起）。
BRAIN_SELL_ENABLED = os.environ.get('EVOALPHA_BRAIN_SELL','').strip().lower() == '1'

# ===== 2026-09-12 R0.5 Layer 2：日线兜底（破 MA5 减半 / 破 MA10 清 / 时间止损）=====
# 这三条**不在** manage_day 里，而在 core/sell.py::simulate_hold（L416-457）——
# 所以 Layer 1（盘中引擎）接完还有这一层。判据**逐条照搬、不改**：
#   k          = 距买入日的交易日期序号（0-based，买入当日=0）—— 与 simulate_hold 的
#                `for k, d in enumerate(hold_dates)` 同义
#   ma5/ma10   = 该股日线收盘的 5/10 日均（as-of 当日，与 simulate_hold 的 di 同义）
#   ma10_clear : close<ma10 且 k>=3 且**前两日也收盘<MA10**（v05「跌破10日线3天不收回必须止损」）
#   ma5_halve  : close<ma5  且 k>=2 且 close>entry_px（利润兑现语境；回档刚买入不启用）
#   time_stop  : k>=time_stop_days（默认 5）
# ⚠️ **不能用 pos['days']**：ledger.buy() 只在买入时置 0，而生产链路里**没有任何脚本 +1**
#    （只有 iteration/shadow.py 与 r6p_* 研究脚本会加）⇒ 生产值恒为 0 ⇒ 三条规则的
#    k>=2/3/5 前置条件永远不成立，日线兜底会成为**接了但不触发的死代码**。
#    故 k 必须从 entry_ts 与日线日期**自算**。
DAILY_REBUILT = pathlib.Path(r'F:\WorkBuddyItem\a股level2\daily_rebuilt')
_daily_guard_cache: dict = {}   # (sym, day) -> signals；同一交易日内只算一次，避免盘后窗口每轮读 parquet

def daily_guard_signals(sym: str, entry_date: str, day: str, close_px: float,
                        entry_px: float, params: dict) -> list:
    """返回 [{'reason': 'ma10_clear'|'ma5_halve'|'time_stop', 'frac': 1.0|0.5}]；取不到日线则 []（保守跳过）。"""
    if not entry_date or not sym:
        return []
    key = (sym, day)
    if key in _daily_guard_cache:
        return _daily_guard_cache[key]
    out: list = []
    try:
        p = DAILY_REBUILT / f'{sym}.parquet'
        if not p.exists():
            _daily_guard_cache[key] = out
            return out
        d = pd.read_parquet(p)
        d['date'] = d['date'].astype(str).str[:10]
        d = d.sort_values('date')
        all_dates = d['date'].tolist()
        if entry_date not in all_dates:
            _daily_guard_cache[key] = out
            return out
        i0 = all_dates.index(entry_date)
        # 今日尚未落日线（盘中）→ 用上一交易日为基准；盘后窗口当日 bar 通常也还没落
        di = all_dates.index(day) if day in all_dates else len(all_dates) - 1
        k = di - i0
        if k < 0:
            _daily_guard_cache[key] = out
            return out
        c = d['close'].reset_index(drop=True)
        ma5 = float(c.rolling(5).mean().iloc[di]) if di >= 4 else None
        ma10 = float(c.rolling(10).mean().iloc[di]) if di >= 9 else None
        horizon = int(params.get('time_stop_days', 5) or 5)
        # ⚠️ **清仓类优先于减半类**（顺序即语义，别改成叠加）：
        #    simulate_hold 的实际顺序是 ma10_clear → ma5_halve → **之后独立地** k>=horizon 清仓，
        #    所以 k=horizon 当天且 MA5 也破时，它先减半再清掉剩余 ⇒ 净效果 = **全清**。
        #    若这里按"互斥且 ma5 优先"写，horizon 日只会卖一半 ⇒ **少卖**、不等价；
        #    若同时发两条（半仓 + 全仓），执行侧会用同一份 t1 重复卖 ⇒ **超卖** →
        #    账本抛 T+1/持仓不足 → 卖单失败 → daemon `return 4` 退出（守护进程死）。
        #    ⇒ 取"单条、按清仓优先"：经济效果与 simulate_hold 等价，且不会重复卖。
        if params.get('daily_ma10_clear') and ma10 and close_px < ma10 and k >= 3 and di >= 2:
            ma10_arr = c.rolling(10).mean().to_numpy()
            c_arr = c.to_numpy()
            if (float(c_arr[di - 1]) < float(ma10_arr[di - 1])
                    and float(c_arr[di - 2]) < float(ma10_arr[di - 2])):
                out.append({'reason': 'ma10_clear', 'frac': 1.0})
        if not out and k >= horizon:
            out.append({'reason': 'time_stop', 'frac': 1.0})
        if not out and params.get('daily_ma5_halve') and ma5 and close_px < ma5 and k >= 2 and close_px > entry_px:
            out.append({'reason': 'ma5_halve', 'frac': 0.5})
    except Exception as e:
        print('[DAILY-GUARD-FAIL] '+sym+' '+repr(e), file=sys.stderr)
        out = []
    _daily_guard_cache[key] = out
    return out


def market_of(s):
 if s.startswith('900'): return 1
 if s[0] in ('4','8') or s.startswith('92'): return 2
 return 1 if s[0] in ('6','9','5') else 0

def prev_close(s,d):
 from core.daily_src import prev_close_of
 return prev_close_of(s,d)

def atomic_json(path,obj):
 tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:
  json.dump(obj,f,ensure_ascii=False); f.flush(); os.fsync(f.fileno())
 # 2026-09-03 修复(daemon 11:16 死亡根因): Windows 下 monitor/scan/notify 并发读 pos_live.json 时
 # os.replace 目标被短暂锁住抛 PermissionError(WinError 5); 原实现无重试, 一次撞锁即打死唯一卖出执行器。
 # 读者读完即释放, 50ms 级退避重试可覆盖; 耗尽后抛出交由调用方兜底。
 for _i in range(8):
  try:os.replace(tmp,path);return
  except PermissionError:
   if _i==7:raise
   time.sleep(0.05)

def append_event(ev):
 with (OUT/'risk_events.jsonl').open('a',encoding='utf-8') as f:
  f.write(json.dumps(ev,ensure_ascii=False)+chr(10)); f.flush(); os.fsync(f.fileno())

# ===== 2026-09-14 修复：窗口外(午休)也必须刷新 pos_live =====
# 根因（当日实录）：daemon 主循环在 `not in_exec_window(hm2)` 时 `time.sleep(60);continue`
#   ⇒ **午休 11:30-13:00 不写 pos_live**；而 scan_and_confirm.companion_health() 对
#   `pos_live.time` 有 **120s** 硬判据 ⇒ **每日 13:00 首轮 scan 恒定 rc=6「tick stale >2m」
#   ⇒ 13:00 这一轮禁止新仓**（实测 13:00:03 rc=6 / 13:01 起 rc=0）。
# 修法（与文件顶部 2026-09-11「心跳与状态解耦」同族）：窗口外**照写一份 pos_live**，
#   内容 = 上一轮成交时段快照的**结转**（positions 原样带过，只把 time 刷新为当前），
#   并显式打 `out_of_session: true` 标记，便于复盘区分「实时快照」与「结转快照」。
#   ⚠️ 语义要点：午休/盘后无任何执行器（scan 自身也只在 09:30-11:30/13:00-15:00 跑），
#      ⇒ 结转的 positions 与真实持仓**一致**（不是过期值），故刷新 time 不掩盖任何真实变化。
#   ⚠️ 方向必须安全：daemon 真死 ⇒ 没人写 ⇒ pos_live 仍会陈旧 >120s ⇒ companion_health
#     照旧判失效禁新仓（**不得**在 scan 侧开午休豁免，那会把「11:31 死掉的 daemon」
#     一直放过到 13:01，等于持仓保护降级）。
def write_idle_pos_live(day, n):
 """窗口外结转写 pos_live（存活证明 + 持仓视图保持新鲜）。

 优先用本进程上一轮成交时段的 `live`；daemon 若在午休重启（进程内无上一轮），
 则从磁盘读回当日 pos_live 结转；两者都不可得时退化为空持仓（与既有语义一致：
 scan 的重复买入保护读的是 **ledger**，不读 pos_live）。
 """
 carry = None
 try:
  prev = json.loads((OUT/'pos_live.json').read_text(encoding='utf-8-sig'))
  if prev.get('date') == day and isinstance(prev.get('positions'), list):
   carry = prev
 except Exception:
  carry = None
 if carry is None:
  carry = {'date': day, 'positions': []}
 carry = dict(carry)
 carry['date'] = day
 carry['time'] = n.strftime('%H:%M:%S')
 carry['out_of_session'] = True
 try:
  atomic_json(OUT/'pos_live.json', carry)
 except Exception as e:
  print(f'[WARN] 窗口外 pos_live 结转写失败(下轮重试): {e}', file=sys.stderr, flush=True)

def prev_limit_close(sym,day):
 try:
  from core.daily_src import load_daily
  d=load_daily(sym)
  if d is None or len(d)<3:return False
  if str(d['date'].iloc[-1])==day:c1,c2=float(d['close'].iloc[-2]),float(d['close'].iloc[-3])
  else:c1,c2=float(d['close'].iloc[-1]),float(d['close'].iloc[-2])
  return c1>=round(c2*(1+limit_pct_of(sym)),2)-0.005
 except Exception:return False

def execute_tick_risk_sell(s,sym,day,ev_time,epx,qty,trig):
 """tick 风险卖出执行器（P0.4/C1）：先登记自主决策 provenance，后卖出。

 模块级可测函数（计划 §3.4 真实路径测试直接驱动，替代原 mut 闭包）；
 登记返回 dec-auto-* 合法 decision_id 透传给 ledger 卖出调用，退役人工拼接的 dec-tick-*。
 """
 if s.get('policy', {}).get('account_mode') != 'autonomous_paper' or s.get('policy', {}).get('require_human_decision'):
  raise ValueError('risk execution is limited to autonomous_paper')
 q=min(qty,sellable_qty(s,sym,day))//100*100
 if q<100:raise ValueError('无可卖份额')
 ts=f'{day} {ev_time}'
 did=record_autonomous_decision(s,{'sym':sym,'signal_ts':ts,'signal_px':epx,
  'rule':f'tick_risk:{trig}','plan_ref':'tick-risk'})
 sell(s,sym,ts,epx,q,trig,plan_ref='tick-risk',signal_ts=ts,decision_ts=ts,decision_id=did)
 return {'sym':sym,'qty':q,'px':epx,'trigger':trig,'decision_id':did}

def brain_sell_decide(sym,trig,day,df,st,t1,orig_qty,entry_px,now=None):
 """D11 卖出裁量: 机械卖点 → 判轻重 → 目标卖出量。
 返回 dict(qty,hold,action,status,choice,sop,sop_weak,reason)。

 ⭐ 判定语义（**与 brain 内部默认不同**, 这是本接线的安全核心, 务必读懂）:
   · 只有 brain **真裁量成功且判 hold**（status=='ok' and not degraded and choice=='hold'）才跳过该笔;
   · **其余一律机械兜底** = 用 orig_qty 原量执行（含 degraded/error/llm_disabled/schema_failed/异常）。
   理由: 接线前这些卖点是**直接执行**的 ⇒ 降级时必须"退回接线前行为"= 执行, 绝不能静默不卖
        （否则 LLM 一挂 = 全部非风控卖点同时失效, 那是新引入的洞而非回退）。
        与 R4.1 D6「降级=放行」同族: 降级 = 退回本次改动之前的行为。
 · halve 基数 = **可卖持仓 t1**（不是 engine 给的 qty, 也不是 pos.qty）: t1//2 向下取整到整手。
   ⚠️ 不可用 engine 的 qty 作基数 —— 对 vwap_halve 它**本身已是半仓**, 再折半会变 1/4 仓。
 · 本函数**绝不抛异常**（brain 已承诺不抛, 此处再兜一层）, 绝不打死 daemon。
 """
 rec={'qty':orig_qty,'hold':False,'action':'mechanical','status':'disabled','choice':None,
      'sop':'','sop_weak':False,'reason':''}
 if not BRAIN_SELL_ENABLED:return rec
 try:
  v=_brain.sell_verdict(sym,day=day,trigger=trig,df=df,state=st,
                        entry_px=(float(entry_px) if entry_px else None),now=now,
                        use_llm=True,persist=True)
 except Exception as e:
  rec.update(status='error',reason=repr(e)[:120]);return rec
 rule,weak=_brain.sop_for(trig)   # weak=弱依据/未登记(含 sop_missing 留痕)
 rec.update(status=v.get('status'),choice=v.get('choice'),sop=rule,sop_weak=weak,
            reason=str(v.get('reason') or '')[:120])
 ok=(v.get('status')=='ok' and not v.get('degraded'))
 if ok and v.get('choice')=='hold':
  rec.update(qty=None,hold=True,action='hold');return rec
 if ok and v.get('action')=='clear_all':
  rec.update(qty=int(t1),action='clear_all');return rec
 if ok and v.get('action')=='halve':
  rec.update(qty=int(t1)//2//100*100,action='halve');return rec
 return rec   # 降级/异常/越界 → 机械原量

def _holder_pid(lock):
 """读锁内记录的持有者 pid; 解析失败返回 None(视为不可判定, 保守当作活锁)。"""
 try:
  return int(lock.read_text(encoding='ascii').strip() or 0) or None
 except (OSError, ValueError):
  return None


def _pid_alive(pid):
 """进程存活探测: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 成功即可(不关心退出码)。"""
 if not pid:
  return False
 try:
  import ctypes
  h=ctypes.windll.kernel32.OpenProcess(0x1000,False,int(pid))
  if not h:
   return False
  ctypes.windll.kernel32.CloseHandle(h)
  return True
 except Exception:
  return True  # 探测本身失败时保守视为存活, 绝不误抢活锁


def _acquire_daemon_lock(out):
 """daemon 单实例锁(2026-09-10 加固): 环境层进程复制会造出双 daemon 交替写
 pos_live(9/10 实录 count 1/2 每5秒跳变); 持锁者每轮刷新 mtime, 重复者 120s 内检测到活锁即退出。

 2026-09-11 P0 修复(死锁文件掐死重启链): 原实现只认 mtime, 被 kill 的 daemon 会留下一个
 时间戳全新的锁文件, 于是"kill → 立刻重启"必然被拒(9/11 实录: restart #4/#5/#6 三连 spawn
 全部秒退, dmesg 只剩"已有活 daemon 持有单实例锁", watchdog 白烧 3 次额度且期间持仓裸奔)。
 修复: 先判持锁 pid 是否真的存活 —— 已死即立刻回收, 不再白等 120s; mtime 判据保留为兜底
 (pid 不可解析 / 探测不可用时), 因此单实例语义不变, 只是死锁不再需要等满 120s。"""
 lock = out / '_tick_daemon.lock'
 for _ in range(2):
  try:
   fd=os.open(str(lock), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
   os.write(fd, str(os.getpid()).encode()); os.close(fd)
   return True
  except FileExistsError:
   holder=_holder_pid(lock)
   dead = holder is not None and not _pid_alive(holder)
   try:
    stale = time.time()-lock.stat().st_mtime > 120
   except OSError:
    continue
   if dead or stale:
    if not dead:
     print(f'[daemon] 锁 mtime 陈旧({int(time.time()-lock.stat().st_mtime)}s) 且 pid={holder} 不可判定, 回收', file=sys.stderr)
    lock.unlink(missing_ok=True); continue
   return False
 return False


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--interval',type=int,default=5);ap.add_argument('--rounds',type=int,default=0);ap.add_argument('--daemon',action='store_true');ap.add_argument('--execute-risk',action='store_true')
 a=ap.parse_args(); now=datetime.now(ZoneInfo('Asia/Shanghai')); day=now.strftime('%Y-%m-%d'); hm=now.strftime('%H:%M')
 if a.daemon and not _acquire_daemon_lock(OUT):
  print('[daemon] 已有活 daemon 持有单实例锁, 退出', file=sys.stderr)
  return 0
 if now.weekday()>=5: print(f'[{hm}] 周末非交易日，退出'); return 0
 if not a.daemon and not in_exec_window(hm):
  print(f'[{hm}] 非交易时段，退出'); return 0
 if a.daemon and hm>AFTER_HOURS_END: print(f'[{hm}] 已过盘后窗口({AFTER_HOURS_END})，退出'); return 0
 api=TdxHq_API(heartbeat=False); connected=False
 for h,p in SERVERS:
  try:
   if api.connect(h,p,time_out=8):connected=True;break
  except Exception:pass
 if not connected:
  print('[WARN] TDX连接失败, 启用腾讯备胎(min5+quote)',file=sys.stderr)
  api=None
 def reconnect():
  nonlocal api
  for _ in range(3):
   try:api.disconnect()
   except Exception:pass
   for h,p in SERVERS:
    try:
     api=TdxHq_API(heartbeat=False)
     if api.connect(h,p,time_out=8):return True
    except Exception:pass
   time.sleep(3)
  return False
 fired=set(); pc_missing=set(); rounds=0; interval=a.interval; err=0
 # 2026-09-11 P0: 主循环包异常留痕 —— 9/11 实录 daemon 静默停写、stderr 无任何痕迹,
  # 导致"进程死/写卡住"无法判定(守护链二次死亡至今死因不明)。异常必须落 stderr 尾部
  # 与 risk_events.jsonl, 让下一次同类故障可定因。
 try:
  while a.rounds==0 or rounds<a.rounds:
   rounds+=1; n=datetime.now(ZoneInfo('Asia/Shanghai')); hm2=n.strftime('%H:%M')
   # 心跳必须写在所有 continue 分支之前 —— 本轮被任何守卫跳过也一样证明"进程活着、循环在转"。
   write_beat(n, interval)
   if a.daemon and not in_exec_window(hm2):
    if hm2>AFTER_HOURS_END:break
    # 2026-09-14 修复(午休 rc=6): 窗口外也刷 pos_live —— 仅限**上午收盘之后**
    # (午休 11:30-13:00 / 15:00-15:05 空档)，避免开盘前 09:00-09:29 用空持仓
    # 覆盖隔夜持仓视图。见 write_idle_pos_live 的完整根因与安全性论证。
    if hm2>SESSION_AM[1]:write_idle_pos_live(day,n)
    time.sleep(60);continue
   st=load(); live={'date':day,'time':n.strftime('%H:%M:%S'),'positions':[]}
   for sym,pos in list(st['account']['positions'].items()):
    pc=prev_close(sym,day)
    if not pc:
     # 2026-09-11: prev_close 缺失会让该票静默 continue(旧实现无任何痕迹), 每票每日只留一条事件,
     # 避免 5 秒轮询刷爆 risk_events.jsonl。
     if sym not in pc_missing:
      pc_missing.add(sym)
      append_event({'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':'prev_close_missing',
                    'action':'warn','detail':f'{sym} prev_close 取不到, 本轮跳过该票(不写 pos_live)'})
     continue
    bars=tx=None
    if api is not None:
     try:
      bars=api.get_security_bars(KLINE_1MIN,market_of(sym),sym,0,300); tx=api.get_transaction_data(market_of(sym),sym,0,30)
     except Exception:bars=tx=None
    if bars is None:
     # 2026-09-10: TDX 不可用时降级腾讯 mkline m5 + qt 报价 (a-stock-data 备用源速查)
     fb=tencent_min_df(sym,day)
     if fb is None:
      err+=1
      if err>=3:interval=min(interval*2,30)
      if err>=6 and not reconnect():
       append_event({'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':'data_failure','action':'halt','detail':'TDX/腾讯双源失败'})
       print('TDX/腾讯双源失败',file=sys.stderr);return 3
      continue
     df=fb;vw=vwap_series(df);last=float(df['close'].iloc[-1]);hi=float(df['high'].max());lo=float(df['low'].min())
     px=float(tencent_quote([sym]).get(sym) or last)
    else:
     err=0; interval=a.interval
     rows=[[str(b['datetime']),float(b['open']),float(b['high']),float(b['low']),float(b['close']),float(b['vol']),float(b['amount'])] for b in bars if str(b['datetime']).startswith(day)]
     # 2026-09-11: 门槛由 <3 放宽到 <1 —— 1 分钟类别的首根 bar 一到即可落盘, 消除开盘 ~3 分钟盲区
     # (旧 <3 门槛叠加 5 分钟 bar 是 pos_live 永不落盘的直接原因)。
     if len(rows)<1:continue
     df=pd.DataFrame(rows,columns=['ts','open','high','low','close','volume','amount']);vw=vwap_series(df);last=float(df['close'].iloc[-1]);hi=float(df['high'].max());lo=float(df['low'].min());px=float(tx[-1]['price']) if tx else last
    t1=sellable_qty(st,sym,day); live['positions'].append({'sym':sym,'px':px,'chg':(px/pc-1)*100,'t1_locked':t1<=0})
    lup=limit_price(pc,sym); ldn=round(pc*(1-limit_pct_of(sym)),2)
    # 2026-09-12 R0.8: 由写死字面量改为共用判定 —— 上限从 15:00 扩到盘后窗口 15:30。
    # 盘后成交价语义（深交所）：**成交价 = 当日收盘价**；本处 epx 取实时价，
    # 在 15:05 之后即等于当日收盘价，与该规则一致。
    exec_window=in_exec_window(hm2)
    # ===== 2026-09-12 R0.5：完整卖点引擎（core.sell::manage_day）接生产 =====
    # 原实现只有 3 类简化触发；完整卖出纪律（破均价线减半→再破清仓 / 冲高回落破位 /
    # 冲高止盈 / 炸板 / 次高点）一直只活在 core/sell.py、只被研究脚本引用。
    # 接线 = 「引擎决策 → tick 执行」的**增量执行**：manage_day 是整天重放的确定性函数
    # （每根 bar 的判定只依赖 ≤ 该 bar 的输入），故每轮用"当日至今 bars"重跑得到当日
    # 完整 fills，只执行**尚未执行过的**（去重键含该 fill 的 bar 时间戳）—— daemon 侧
    # 无需自建引擎状态机，也不会重复卖。low_track 只是成本基准入参（函数体从不改写它）。
    # ⚠️ 两处刻意保留/排除（不是遗漏）：
    #   ① 保留 legacy 秒级 stop_px 硬止损 —— 引擎的 stop_loss 盘中触及**只标记**、
    #      把即时执行留给日线兜底（sell.py:172-177）。直接替换会丢掉盘中硬止损，
    #      而"持仓保护不可降级"是 P0 底线 ⇒ 两者并存（引擎其余理由照常生效）。
    #   ② ⚠️ **成交价一律用实时 px，不用引擎给的 bar 价** —— 引擎的 fills 落在
    #      high[i]（t_sell）/ low[i]（break_low）等**回测理想价**上，生产中不可达；
    #      拿它记账会系统性高估收益。生产按市价成交，故统一取实时价。
    trigs=[]
    if pos.get('stop_px') and px<=float(pos['stop_px']):
     trigs.append(('stop_loss',int(pos['qty']),''))
    try:
     eng=manage_day(df,pc,max(0,int(t1)),pos.get('stop_px'),float(pos.get('cost') or 0),
                    params=SELL_ENGINE_PARAMS,limit_px=lup)
     for f in (eng.get('fills') or []):
      if f.get('side')!='sell':continue          # 做T买单不接，见 SELL_ENGINE_PARAMS 注释
      if int(f.get('qty') or 0)>0:
       trigs.append((str(f.get('reason') or 'engine'),int(f['qty']),str(f.get('ts') or '')))
    except Exception as e:
     # 引擎异常**不得**打死 daemon（守护进程死 = 持仓裸奔，9/3 事故形态）。
     # 降级为 legacy 3 类触发，保住基本保护。
     print('[ENGINE-FAIL] '+repr(e),file=sys.stderr)
     if hi>=lup-0.01 and last<lup*0.995:trigs.append(('zhaban_sell',int(pos['qty']),''))
     elif not prev_limit_close(sym,day) and hi>=pc*1.07 and last<float(vw.iloc[-1])*0.997:trigs.append(('vwap_halve',int(pos['qty'])//2//100*100,''))
     # 2026-09-12 R0.5 Layer 2：日线兜底 —— **只在盘后固定价格窗口**执行。
     # 成交价语义：simulate_hold 把这三条记在 "d 15:00"（收盘价）；生产放在 15:05-15:30
     # 盘后窗口，成交价=当日收盘价，与之一致（正好用上 R0.8 补的窗口）。
     # 盘中**不评估**：这是日频判据，放盘中会在同一批日线数据上反复命中。
     if in_after_hours_session(hm2):
      for g in daily_guard_signals(sym,str(pos.get('entry_ts') or '')[:10],day,last,
                                   float(pos.get('cost') or 0),SELL_ENGINE_PARAMS):
       trigs.append((g['reason'],int(int(pos['qty'])*float(g['frac'])),''))
     for trig,qty,fts in trigs:
      if not exec_window:break
     if (sym,trig,fts) in fired:continue
     fired.add((sym,trig,fts));epx=px;volok=float(df['volume'].iloc[-3:].sum())>0
     hard=epx<=ldn+0.005 or not volok
     # ===== 2026-09-13 D11：机械卖点 → 让「选手人格」(LLM)判轻重 =====
     # 只在"真要执行且未被硬挡"时咨询：省调用；brain 的日锁(到收盘)保证同卖点当日只真调一次。
     b={'qty':qty,'hold':False,'action':'mechanical','status':'disabled','choice':None,
        'sop':'','sop_weak':False,'reason':''}
     if a.execute_risk and not hard:
      # ⚠️ trigger **原样透传，不可拼 fts**：brain 当日锁粒度是 (标的×日×卖点类型)，
      #    拼上 bar 时间戳会导致每根 bar 都真调一次 LLM 且中途改判。
      b=brain_sell_decide(sym,trig,day,df,st,t1,qty,float(pos.get('cost') or 0),now=n)
      if b['hold']:
       # 真判 hold → 跳过该笔并留痕(不静默)；用 continue，绝不碰下面的 return 4/5 终止路径。
       ev={'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':trig,'px':round(epx,3),
           'qty':qty,'limit_down':ldn,'action':'brain_hold','blocked_reason':'brain:hold',
           'brain_status':b['status'],'brain_choice':b['choice'],'brain_sop':b['sop'],
           'sop_weak':b['sop_weak'],'brain_reason':b['reason']}
       append_event(ev);print('[BRAIN-HOLD] '+json.dumps(ev,ensure_ascii=False),flush=True);continue
      qty=b['qty']
     bex=({'brain_status':b['status'],'brain_choice':b['choice'],'brain_sop':b['sop'],
           'sop_weak':b['sop_weak'],'brain_reason':b['reason']} if BRAIN_SELL_ENABLED else {})
     # ⚠️ 用**新鲜**可卖量（循环内 st 会被 transact 更新），不能用循环外的 t1 快照 —— 否则
     #    「前一笔已把可卖份额卖光」时 qty 仍 ≥100 → 进执行 → execute_tick_risk_sell 抛 → return 4 打死 daemon。
     qty=min(qty,sellable_qty(st,sym,day))//100*100
     blocked=hard or qty<100
     ev={'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':trig,'px':round(epx,3),'qty':qty,'limit_down':ldn,'action':'alert_only' if(blocked or not a.execute_risk)else'execute','blocked_reason':'brain:halve<1手' if(b['action']=='halve' and qty<100)else('跌停/无量/无可卖份额' if blocked else ''),**bex}
     append_event(ev);print('[RISK] '+json.dumps(ev,ensure_ascii=False),flush=True)
     if a.execute_risk and not blocked:
      if st.get('policy', {}).get('account_mode') != 'autonomous_paper' or st.get('policy', {}).get('require_human_decision'):
       ev['action']='blocked_account_mode';ev['blocked_reason']='risk execution is limited to autonomous_paper';append_event(ev);print('[RISK-BLOCK] account mode',file=sys.stderr);return 5
      def mut(s):
       return execute_tick_risk_sell(s,sym,day,ev['time'],epx,qty,trig)
      try:st,res=transact(mut);print('[RISK-EXEC] '+json.dumps(res,ensure_ascii=False),flush=True)
      except Exception as e:ev['action']='failed';ev['blocked_reason']=str(e);append_event(ev);print('[RISK-FAIL] '+str(e),file=sys.stderr);return 4
    # 2026-09-03 修复: pos_live 单轮写失败(锁冲突耗尽等)不得打死 daemon —— 守护进程死 = 持仓裸奔,
    # 失败方向应安全(pos_live 陈旧 -> scan 禁新仓), 打警告后下一轮重写。
    # 注意: 本块必须在 while 循环体内 —— 首版修复误写成 1 空格导致语句被挪出循环,
    # daemon 空转不写 pos_live 不 sleep(9/3 14:00-14:05 全部"挂死"假象即此)。
    # ⚠️⚠️ 2026-09-14 首日「绝食」故障修复: 本块此前实际缩进在 **for 循环体内**
    #   (AST 取证: While@350 → For@358 → atomic_json@476; 9/11 的 .bak 同样是 for 体内,
    #    属**潜伏缺陷** —— 此前每天开盘都带着隔夜持仓, for 体恒有内容, 所以从未暴露)。
    #   后果(今日全实录): 账户首次从**空仓**起跑 ⇒ for 体一次都不执行 ⇒
    #     ① pos_live 永不刷新 ⇒ 其 date 停在上一交易日(9/11) ⇒ scan_and_confirm 的
    #        companion_health() 判 'tick stale >2m' ⇒ **09:40 宽限一过每轮 rc=6「禁止新仓」**
    #        ⇒ 死锁: 空仓→不写→禁新仓→永远空仓(今天全天都买不进第一笔);
    #     ② 连 time.sleep(interval) 也一起被跳过 ⇒ daemon 在交易时段**满速空转**(每轮 load 账本)。
    #   修法 = 把「写 pos_live + 刷锁 mtime + sleep」整体回到 while 体: 空仓也照写
    #   `positions: []`, 让 companion_health 的「监控是否活着」判据重新成立。
   try:atomic_json(OUT/'pos_live.json',live)
   except Exception as e:print(f'[WARN] pos_live 写入失败(下轮重试): {e}',file=sys.stderr,flush=True)
   if a.daemon:
    try:os.utime(str(OUT/'_tick_daemon.lock'), None)  # 刷新单实例锁 mtime(存活证明)
    except OSError:pass
   time.sleep(interval)
 except SystemExit:
  raise
 except BaseException as e:
  tb=traceback.format_exc()
  try:append_event({'date':day,'time':datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S'),
                    'sym':'-','trigger':'daemon_crash','action':'halt',
                    'detail':f'{type(e).__name__}: {e} | {tb.strip().splitlines()[-1][:200]}'})
  except Exception:pass
  print('[daemon] 致命异常, 退出:\n'+tb,file=sys.stderr,flush=True)
  return 9
 if api is not None:
  try:api.disconnect()
  except Exception:pass
 return 0
if __name__=='__main__':sys.exit(main())
