"""EvoAlpha 两阶段开盘门禁 + tpoint 风格盘前自检。
08:45 基础设施：preflight.py
08:55 计划验收：preflight.py --post-plan（可 --push 推送飞书状态卡片）
输出 outputs/preflight_<date>_<stage>.json（门禁契约，fail-closed）+ outputs/selfcheck/ 下 Markdown 报告与异常日志
失败返回非零，runner fail-closed。
"""
from __future__ import annotations
import argparse,hashlib,json,os,pathlib,subprocess,sys,tempfile,time,urllib.request
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent; OUT=BASE/'outputs'; PY=sys.executable
SELFCHK_DIR=OUT/'selfcheck'; ANOMALY_LOG=SELFCHK_DIR/'anomalies.log'
SECRET_FILE=pathlib.Path(os.environ.get("YAOBAN_FEISHU_SECRET_FILE", r"C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt"))
DISK_WARN_PCT=90; DISK_FAIL_PCT=95; MEM_WARN_PCT=85; CPU_WARN_PCT=80; CPU_FAIL_PCT=95
EXPECTED={
 'YaobanPreflight':'infra','YaobanPremarket':'premarket','YaobanPlanGate':'plan-gate',
 'YaobanAuctionMonitor':'auction','YaobanTickDaemon':'tick','YaobanScanConfirm':'scan','YaobanIntradayMonitor':'monitor',
 'YaobanEventNotify':'notify','YaobanClosePipeline':'close','YaobanPostCloseChain':'post-close',
}
# P0-3修复(2026-08-31): ASCII_TASKS 从 BASE 上溯3级=WorkBuddy/yaoban_tasks(旧parents[2]=Claw下, 不存在导致任务Action全FAIL);
# 增加存在性候选回退, 避免路径级数变化再回归
ASCII_TASKS=next((p for p in (BASE.parents[3]/'yaoban_tasks', BASE.parents[2]/'yaoban_tasks') if (p/'launch.ps1').exists()), BASE.parents[3]/'yaoban_tasks')
LAUNCHER=ASCII_TASKS/'launch.ps1'
ROOT_FILE=ASCII_TASKS/'root.txt'
# P0-7修复(2026-09-01): 实测47节点仅115.238.56.198/115.238.90.165行情可用(连接+K线双重验证);
# mootdx bestip 仅TCP探测(0.7s)不验证行情, "TCP可达"≠"行情可用"(如119.97.185.59 TCP通但K线全空)。
# 清单保留多节点回退; 检测增加TCP预筛, 全挂时从~2min(10节点×5s×3轮)降至~10s。
TDX_SERVERS=[('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]
# 2026-09-10: 计划表即代码 —— 触发器时刻契约(与 scripts/register_schedule.ps1 的表一致);
# 漂移属非致命告警(critical=False): 人为改点不应拦住整条开盘链, 但必须每天可见。
TRIGGER_EXPECTED={'YaobanPreflight':['08:35'],'YaobanSelfHeal':['08:36'],'YaobanPremarket':['08:50'],
 'YaobanPlanGate':['08:55'],'YaobanMorningCheck':['08:58'],'YaobanTdxProbe':['09:00'],
 'YaobanAuctionMonitor':['09:15'],'YaobanEventNotify':['09:15'],'YaobanTickDaemon':['09:30'],
 'YaobanScanConfirm':['09:30'],'YaobanIntradayMonitor':['09:30'],'YaobanClosePipeline':['15:10'],
 'YaobanPostCloseChain':['15:35'],'YaobanEveningCheck':['17:30'],'YaobanTdxServerVerify':['10:00'],
 'YaobanBoardRefresh':['09:35','13:05']}
TDX_CATEGORIES=[0,4,9,7]
TDX_TCP_TIMEOUT=1.0  # P0-7: TCP预筛超时(秒), 快速排除死节点

def run_capture(cmd):
 fd,tmp=tempfile.mkstemp(); rc=-1
 try:
  with os.fdopen(fd,'w') as f: rc=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=45).returncode
  raw=pathlib.Path(tmp).read_bytes()
  for enc in ('utf-8','gbk','utf-16'):
   try:return rc,raw.decode(enc)
   except Exception:pass
  return rc,raw.decode('utf-8','replace')
 finally:
  try:os.unlink(tmp)
  except Exception:pass

def health_ok(http_status, body):
 return http_status==200 and (body.get('ok') is True or body.get('status') in ('ok','healthy'))

def _tcp_alive(servers):
 import socket
 alive=[]
 for host,port in servers:
  s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.settimeout(TDX_TCP_TIMEOUT)
  try:
   s.connect((host,port));alive.append((host,port))
  except Exception:pass
  finally:s.close()
 return alive

def tdx_health(api_factory, servers=None, symbol='600000'):
 """连接+真实K线双重验证；单服务器/单线型失败自动切换，避免空返回误判。
 P0-7: 先TCP预筛(1s/节点)快速排除死节点, 再对存活节点做完整验证; 全挂时快速失败。"""
 failures=[]
 candidates=servers or TDX_SERVERS
 alive=_tcp_alive(candidates)
 for host,port in candidates:
  if (host,port) not in alive: failures.append(f'{host}:{port}=connect_false')
 if not alive:return {'ok':False,'server':'','cat':None,'bars':0,'last':'','failures':failures}
 for host, port in alive:
  api=None
  try:
   api=api_factory(heartbeat=False)
   if not api.connect(host, port, time_out=5):
    failures.append(f'{host}:{port}=connect_false'); continue
   hit=None
   for cat in TDX_CATEGORIES:
    try:
     bars=api.get_security_bars(cat, 1, symbol, 0, 2)
     valid=(bool(bars) and len(bars)>=2 and
            all(k in bars[-1] for k in ('datetime','open','high','low','close')) and
            float(bars[-1]['close'])>0)
     if valid:
      hit={'cat':cat,'bars':len(bars),'last':bars[-1].get('datetime','')}; break
    except Exception:pass
   if hit:
    return {'ok':True,'server':f'{host}:{port}','cat':hit['cat'],'bars':hit['bars'],
            'last':hit['last'],'failures':failures}
   failures.append(f'{host}:{port}=empty_or_invalid_bars')
  except Exception as exc:
   failures.append(f'{host}:{port}={type(exc).__name__}: {exc}')
  finally:
   if api is not None:
    try: api.disconnect()
    except Exception: pass
 return {'ok':False,'server':'','cat':None,'bars':0,'last':'','failures':failures}

def fetch_trade_calendar(day, stage):
 out=OUT/'preflight_calendar'/day/stage
 out.mkdir(parents=True,exist_ok=True)
 script=BASE.parent/'Vibe-Research'/'.agents'/'skills'/'data-access'/'scripts'/'fetch_trade_calendar.py'
 rc,log=run_capture([PY,str(script),'--symbol','300308','--days','45','--check-date',day,'--out-dir',str(out)])
 fp=out/'fetch'/'fetch_trade_calendar.json'
 if rc!=0 or not fp.exists(): return None,f'rc={rc} log={log[-300:]}'
 try:
  data=json.loads(fp.read_text(encoding='utf-8'))
  if data.get('status')!='ok': return None,json.dumps(data.get('errors',[]),ensure_ascii=False)
  return data.get('extra',{}),str(fp)
 except Exception as e:return None,str(e)

def task_xml(name):
 rc,out=run_capture(['schtasks','/Query','/TN',chr(92)+name,'/XML']); return rc,out

# ========== tpoint 风格增强（2026-09-01 对齐 selfcheck_daily.py） ==========

def _system_resources():
 """CPU/内存/磁盘 C:/F: 使用率（PowerShell CIM，不依赖 psutil）。
 返回 {'cpu':pct,'mem':pct,'disk_c':pct,'disk_c_free_gb':gb,'disk_f':pct,'disk_f_free_gb':gb}。"""
 ps=("$os=Get-CimInstance Win32_OperatingSystem;"
     "$cpu=(Get-CimInstance Win32_Processor|Measure-Object LoadPercentage -Average).Average;"
     "$mt=$os.TotalVisibleMemorySize;$mf=$os.FreePhysicalMemory;"
     "$mem=[math]::Round(($mt-$mf)/$mt*100,1);"
     "Write-Output ('cpu='+$cpu+' mem='+$mem);"
     "foreach($d in 'C:','F:'){"
     " $dd=Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='$d'\" -ErrorAction SilentlyContinue;"
     " if($dd){$pct=[math]::Round($dd.Size/($dd.Size+$dd.FreeSpace)*100,1);"
     " Write-Output ('disk='+$d+' '+$pct+' '+[math]::Round($dd.FreeSpace/1GB,1))}}")
 rc,out=run_capture(['powershell','-NoProfile','-Command',ps])
 if rc!=0: return {}
 res={}
 for ln in out.splitlines():
  ln=ln.strip()
  try:
   if ln.startswith('cpu='):
    vals=ln[4:].split()
    res['cpu']=float(vals[0])
    for v in vals[1:]:
     if v.startswith('mem='): res['mem']=float(v[4:])
   elif ln.startswith('disk='):
    p=ln[5:].split()
    key='disk_'+p[0][0].lower()
    res[key]=float(p[1]);res[key+'_free_gb']=float(p[2])
  except Exception:
   pass
 return res

SUGGESTIONS={
 '交易日历':'交易日历证据缺失或非交易日判定失败。检查网络与 Vibe-Research/.agents/skills/data-access/scripts/fetch_trade_calendar.py；非交易日 preflight 失败属预期，不得作为交易日门禁证据。',
 'TDX行情':'通达信服务器连接或K线验证失败。2026-09-10 起: TDX 全挂时自动验腾讯备胎(mkline m1)，备胎可用则降级 WARN 不阻断(tick/scan/monitor 已接入同源备胎)。备胎也不可用时盘中/盘后仍 FAIL。',
 '腾讯快照':'腾讯实时接口 qt.gtimg.cn 请求失败。检查外网连通性；主源 TDX 可用时仅影响兜底数据源。',
 '账本/持仓':'持仓 parquet 缺失或数据落后于上一交易日。检查 F:/WorkBuddyItem/a股level2/daily_rebuilt/ 与 YaobanDailyRebuild 任务是否完成；缺失标的需补重建。',
 '净值守恒':'equity_curve 与现金+市值计算不一致。检查 ledger.json 记账是否正确（禁止人工改账），查看 _revision 与当日成交记录。',
 '情绪表':'sentiment_full_2026.csv 最新日期落后于上一交易日。运行 scripts/r5p_sentiment_build.py 重建情绪表。',
 '候选表':'r6p_candidates_2026.csv 最新日期落后。运行 scripts/r6p_candidates_build.py 重建候选表。',
 '任务Action':'计划任务 Action 校验失败（XML 指向 launcher/模式/runner 参数不符）。运行 scripts/update_tasks_ps1.ps1 重新注册；检查 yaoban_tasks/root.txt 与 launch.ps1；runner 风险参数扫描=run_trading_task.ps1+_tick_watch.py（tick 链 9/4 起移驻 watcher）。',
 '任务历史':'部分任务尚无成功运行记录（unproven）。若为盘前未到触发时间的任务属预期；收盘后仍无记录需检查任务注册。',
 '看板':'Vibe 看板 http://127.0.0.1:5930/api/health 不可用。检查 Vibe 编排器进程（8766）与前端（5930）。',
 '残留进程':'检测到残留 tick_monitor/scan_and_confirm 进程。手动结束残留进程，避免与计划任务实例并发写账。',
 '脚本语法':'脚本编译失败。用 python -m py_compile 定位语法错误并修复。',
 '当日计划':'当日计划缺失/无候选/模式不含前一日/发布时间异常/外盘未刷新。检查 generate_next_plan.py 盘后是否成功；08:50 premarket 是否刷新外盘并原子更新计划。',
 '外盘新鲜度':'global_snapshot.json 非当日生成。检查 pull_global.py 与 YaobanPremarket 任务。',
 'CPU 使用率':'CPU 持续高负载。检查异常进程占用；扫描/回测任务单轮不应长期占满多核。',
 '内存使用率':'内存紧张。检查常驻进程内存增长，必要时重启相关服务。',
 '磁盘 C: 使用率':'系统盘空间不足。清理旧日志与临时文件，保持 C: 剩余空间。',
 '磁盘 F: 使用率':'数据盘空间不足。清理 outputs/ 旧 CSV 与 backtest 产物；检查 daily_rebuilt parquet 体积。',
}

def _suggestion(name):
 return SUGGESTIONS.get(name,'查看对应日志排查；如持续异常请人工介入。')

def _status_of(r):
 return 'PASS' if r['ok'] else ('FAIL' if r['critical'] else 'WARN')

def build_report(report, stage_label):
 """生成 Markdown 自检报告（tpoint selfcheck 同构）。"""
 now=datetime.now()
 lines=["# EvoAlpha盘前自检报告", "",
        f"- **检查时间**: {report['time']}",
        f"- **阶段**: {stage_label}",
        f"- **检查项**: {len(report['results'])} 项",
        "", "## 总体结果", "",
        "| 状态 | 数量 |", "|------|------|",
        f"| ✅ PASS | {report['summary']['pass']} |",
        f"| ⚠️ WARN | {report['summary']['warn']} |",
        f"| ❌ FAIL | {report['summary']['fail']} |", ""]
 cats=[]
 for r in report['results']:
  cat=r.get('category','其他')
  if cat not in cats: cats.append(cat)
 for cat in cats:
  lines.append(f"## {cat}"); lines.append("")
  lines.append("| 状态 | 检查项 | 详情 |"); lines.append("|------|--------|------|")
  for r in report['results']:
   if r.get('category','其他')!=cat: continue
   st=_status_of(r); icon={'PASS':'✅','FAIL':'❌','WARN':'⚠️'}[st]
   detail=r['detail'].replace('|','\\|')
   lines.append(f"| {icon} {st} | {r['name']} | {detail} |")
  lines.append("")
 anomalies=[r for r in report['results'] if not r['ok']]
 if anomalies:
  lines.append("## 异常项与建议处理措施"); lines.append("")
  for r in anomalies:
   st=_status_of(r); icon={'FAIL':'❌','WARN':'⚠️'}[st]
   lines.append(f"### {icon} [{st}] {r['name']}")
   lines.append(f"- **详情**: {r['detail']}")
   lines.append(f"- **建议**: {_suggestion(r['name'])}"); lines.append("")
 else:
  lines.append("## 无异常 ✨"); lines.append("")
 return '\n'.join(lines)

def _log_anomalies(report):
 anomalies=[r for r in report['results'] if not r['ok']]
 if not anomalies: return
 SELFCHK_DIR.mkdir(parents=True,exist_ok=True)
 with ANOMALY_LOG.open('a',encoding='utf-8') as f:
  f.write(f"\n{'='*60}\n")
  f.write(f"[{report['time']}] {report['stage']} 自检异常记录\n")
  f.write(f"{'='*60}\n")
  for r in anomalies:
   f.write(f"\n[{_status_of(r)}] {r.get('category','其他')}/{r['name']}\n")
   f.write(f"  详情: {r['detail']}\n")
   f.write(f"  建议: {_suggestion(r['name'])}\n")

def _console_summary(report):
 """控制台彩色摘要（ANSI，Windows 10+）。"""
 def _c(code,text):
  colors={'green':32,'red':31,'yellow':33,'cyan':36,'gray':90,'bold':1}
  return f"\033[{colors.get(code,0)}m{text}\033[0m"
 now=datetime.now()
 print()
 print(_c('bold',f"{'='*60}"))
 print(_c('bold',f" EvoAlpha盘前自检（{report['stage']}） {now.strftime('%Y-%m-%d %H:%M:%S')}"))
 print(_c('bold',f"{'='*60}"))
 for r in report['results']:
  st=_status_of(r); color={'PASS':'green','FAIL':'red','WARN':'yellow'}[st]
  icon={'PASS':'✅','FAIL':'❌','WARN':'⚠️'}[st]
  print(f"  {_c(color,icon)} [{st:4}] {r.get('category','其他')}/{r['name']}")
  if r['detail']: print(f"          {r['detail']}")
 print(_c('bold',f"{'-'*60}"))
 s=report['summary']; overall='fail' if s['fail']>0 else ('warn' if s['warn']>0 else 'pass')
 icon={'pass':'✅','fail':'❌','warn':'⚠️'}[overall]
 color={'pass':'green','fail':'red','warn':'yellow'}[overall]
 print(_c(color,f"  总体: {icon} {overall.upper()}  |  ✅{s['pass']}  ⚠️{s['warn']}  ❌{s['fail']}"))
 print()

def _webhook():
 value=os.environ.get("YAOBAN_FEISHU_WEBHOOK","").strip()
 if not value and SECRET_FILE.exists():
  value=SECRET_FILE.read_text(encoding="utf-8").strip()
 if not value.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
  raise RuntimeError("Feishu webhook is missing or invalid")
 return value

def _build_status_card(report, stage_label):
 """构建飞书交互卡片（tpoint selfcheck 卡片同构）。"""
 now=datetime.now(); s=report['summary']
 overall='fail' if s['fail']>0 else ('warn' if s['warn']>0 else 'pass')
 tpl_map={'pass':'green','warn':'yellow','fail':'red'}
 emoji_map={'pass':'✅','warn':'⚠️','fail':'❌'}
 state_label={'pass':'正常','warn':'注意','fail':'异常'}[overall]
 def find(name):
  for r in report['results']:
   if r['name']==name: return r
  return None
 md=lambda t:{"tag":"lark_md","content":t}
 elements=[]
 elements.append({"tag":"div","text":md(
  f"**运行状态：{emoji_map[overall]} {state_label}**　　　时间：{now.strftime('%m-%d %H:%M')}")})
 elements.append({"tag":"div","text":md(
  f"阶段：{stage_label}　　✅{s['pass']} 项　⚠️{s['warn']} 项　❌{s['fail']} 项")})
 elements.append({"tag":"hr"})
 elements.append({"tag":"div","text":md("**🔧 关键组件健康检查**")})
 comps=['账本/持仓','净值守恒','TDX行情','腾讯快照','交易日历','任务Action','看板','当日计划','外盘新鲜度','CPU 使用率','内存使用率','磁盘 C: 使用率']
 for name in comps:
  r=find(name)
  if r is None: continue
  st=_status_of(r)
  mark={'PASS':'🟢','WARN':'🟡','FAIL':'🔴'}[st]
  d=r['detail'][:100] if len(r['detail'])>100 else r['detail']
  elements.append({"tag":"div","text":md(f"{mark} {name}：{d}")})
 warns=[r for r in report['results'] if not r['ok'] and not r['critical']]
 if warns:
  elements.append({"tag":"hr"}); elements.append({"tag":"div","text":md("**⚠️ 潜在风险告警**")})
  for r in warns[:8]:
   d=r['detail'][:120] if len(r['detail'])>120 else r['detail']
   elements.append({"tag":"div","text":md(f"🟡 {r['name']}：{d}")})
 fails=[r for r in report['results'] if not r['ok'] and r['critical']]
 if fails:
  elements.append({"tag":"hr"}); elements.append({"tag":"div","text":md("**❌ 异常项及处理建议**")})
  for r in fails[:8]:
   d=r['detail'][:120] if len(r['detail'])>120 else r['detail']
   sug=_suggestion(r['name']); s2=sug[:120] if len(sug)>120 else sug
   elements.append({"tag":"div","text":md(f"🔴 {r['name']}：{d}")})
   elements.append({"tag":"div","text":md(f"　↳ 建议：{s2}")})
 elements.append({"tag":"hr"})
 footer=f"完整报告 | outputs/selfcheck/  |  EvoAlpha盘前自检 · 仅供参考"
 elements.append({"tag":"note","elements":[{"tag":"plain_text","content":footer}]})
 return {"msg_type":"interactive","card":{"header":{"template":tpl_map[overall],
        "title":{"tag":"plain_text","content":f"EvoAlpha盘前自检 · {emoji_map[overall]} {state_label}"}},
        "elements":elements}}

def _push_card(card, event_key):
 """推送交互卡片到飞书；返回是否成功。审计写入 notifications/delivery_*.jsonl（kind=selfcheck）。"""
 webhook=_webhook(); body=json.dumps(card,ensure_ascii=False).encode('utf-8')
 req=urllib.request.Request(webhook,data=body,headers={'Content-Type':'application/json'},method='POST')
 status=None;code=None;err=None;ok=False
 try:
  with urllib.request.urlopen(req,timeout=15) as resp:
   status=int(resp.status); raw=resp.read(16384)
  bj=json.loads(raw.decode('utf-8')); code=bj.get('code'); ok=(status==200 and code==0)
 except Exception as exc:
  ok=False; err=type(exc).__name__
 try:
  digest=hashlib.sha256(body).hexdigest()
  audit=OUT/'notifications'/f'delivery_{datetime.now():%Y%m%d}.jsonl'
  audit.parent.mkdir(parents=True,exist_ok=True)
  row={'time':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'kind':'selfcheck','event_key':event_key,
       'message_sha256':digest,'http_status':status,'business_code':code,'ok':ok,'error_type':err}
  with audit.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n')
 except Exception:
  pass
 return ok

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',default='');ap.add_argument('--post-plan',action='store_true')
 ap.add_argument('--push',action='store_true',help='交易日推送飞书盘前状态卡片（plan-gate 阶段使用）');a=ap.parse_args()
 now=datetime.now();day=a.date or now.strftime('%Y-%m-%d');stage='post_plan' if a.post_plan else 'infra';cal_info,cal_ref=fetch_trade_calendar(day,stage);pday=(cal_info or {}).get('previous_trading_day','');res=[]
 def ck(name,ok,detail,critical=True,category='其他'):
  res.append({'name':name,'ok':bool(ok),'detail':str(detail),'critical':critical,'category':category});print(('PASS' if ok else('FAIL' if critical else'WARN'))+' '+name+': '+str(detail))
 # registered exchange calendar evidence
 cal_ok=bool(cal_info) and cal_info.get('checked_date')==day and cal_info.get('is_checked_date_trading_day') is True
 trading_day=bool(cal_info) and cal_info.get('is_checked_date_trading_day') is True
 ck('交易日历',cal_ok and bool(pday) and pday<day,f'day={day} previous_completed_day={pday} evidence={cal_ref}',category='日历')
 # TDX actual data
 try:
  from pytdx.hq import TdxHq_API
  health=None
  for _attempt in range(3):  # P0-3: 失败重试2次(间隔8s), 吸收临时性空返回
   health=tdx_health(TdxHq_API)
   if health['ok']:break
   time.sleep(8)
  detail=(f"server={health['server']} cat={health['cat']} bars={health['bars']} last={health['last']}"
          if health['ok'] else 'all_servers_failed: ' + '; '.join(health['failures']))
  # P0-3: 开盘前(<09:15)TDX无数据降级为WARN, 避免08:55类连锁阻塞(auction/scan被gate挡死);
  #       盘中/盘后仍为FAIL(critical) —— 盘中scan/tick依赖实时行情, fail-closed合理
  preopen=now.time() < datetime.strptime('09:15','%H:%M').time()
  # 2026-09-10: TDX 全挂时验腾讯备胎(mkline m1, a-stock-data 备用源速查)——备胎可用则降级 WARN,
  # 生产消费方(tick/scan/monitor)已接入同源备胎, 行情链路仍成立, 不再 fail-closed 拦全天
  fb_ok=False
  if not health['ok']:
   try:
    sys.path.insert(0,str(BASE/'src'))
    from core.tencent_minline import health_probe
    fb_ok=health_probe()
    if fb_ok: detail+=f'; TDX不可用, 腾讯备胎可用(mkline m1 OK)'
   except Exception as _fbe:fb_ok=False
  ck('TDX行情',health['ok'] or fb_ok,detail,critical=(health['ok'] or (not preopen and not fb_ok)),category='行情')
 except Exception as e:ck('TDX行情',False,e,category='行情')
 try:
  raw=urllib.request.urlopen(urllib.request.Request('https://qt.gtimg.cn/q=sh600000',headers={'User-Agent':'Mozilla/5.0'}),timeout=8).read().decode('gbk','ignore')
  ck('腾讯快照',len(raw)>50 and '600000' in raw,f'{len(raw)} bytes',category='行情')
 except Exception as e:ck('腾讯快照',False,e,category='行情')
 # ledger + complete position data + invariants
 try:
  import pandas as pd
  led=json.loads((BASE/'portfolio'/'ledger.json').read_text(encoding='utf-8'));acct=led['account'];pos=acct['positions'];missing=[];marks={};mark_days={}
  for s in pos:
   f=pathlib.Path(f'F:/WorkBuddyItem/a股level2/daily_rebuilt/{s}.parquet')
   if not f.exists():missing.append(s);continue
   d=pd.read_parquet(f);last=str(d['date'].iloc[-1])[:10];marks[s]=float(d['close'].iloc[-1]);mark_days[s]=last
   # P0-3修复(2026-08-31): 收盘后窗口期数据已含当日(rebuild完成), last==day 合法; 仅数据落后于上一交易日才算缺失
   if last not in (pday, day):missing.append(s+'@'+last)
  eq=round(float(acct['cash'])+sum(int(pos[s]['qty'])*marks[s] for s in marks),2)
  curve=acct.get('equity_curve',[]);days=[x['date'] for x in curve]
  ck('账本/持仓',not missing and len(days)==len(set(days)),f'rev={led.get("_revision")} pos={len(pos)} transition={led.get("policy",{}).get("transition_reduce_only")} equity={eq} missing={missing}',category='账本')
  # 2026-09-10 修复(净值守恒假阳性): 账本 equity_curve 与独立价源(daily_rebuilt)经常不在同一"数据世代"。
  #   收盘 15:00 之后到 rebuild 完成(16:30-17:40)之间, 账本已写入当日点而 parquet 仍停在上一交易日,
  #   直接比对必然不等 —— 9/10 实测差 125.00, 恰等于 300468/300394 两持仓 9/9->9/10 的浮动盈亏,
  #   属假 FAIL; 它会连带产生假的 failure:infra / gate-infra 推送并污染 selfcheck 与 anomalies.log,
  #   把"门禁误报"训练成常态。凡 15:00-16:56 窗口内手工跑 infra 必现。
  #   修复=按世代比较并区分方向:
  #     账本落后于价源 => 真问题(保持 critical, 如 9/2 曲线缺 9/1 点);
  #     价源落后于账本 => 数据未就绪(benign, 降为 WARN 且不置 status=fail)。
  cday=curve[-1].get('date') if curve else None
  sday=min(mark_days.values()) if mark_days else None
  if not curve or missing:
   ck('净值守恒',False,f'curve={curve[-1]["equity"] if curve else None} calc={eq} missing={missing}',category='账本')
  elif cday and sday and str(cday)>str(sday):
   ck('净值守恒',False,f'独立价源尚未含当日(价源世代={sday} < 账本世代={cday})，跳过守恒比对; curve={curve[-1]["equity"]} calc={eq}',critical=False,category='账本')
  else:
   ck('净值守恒',abs(float(curve[-1]['equity'])-eq)<0.02,f'curve={curve[-1]["equity"]} calc={eq} 世代={cday}/{sday}',category='账本')
 except Exception as e:ck('账本/持仓',False,e,category='账本')
 # data freshness
 try:
  import pandas as pd
  sf=pd.read_csv(OUT/'sentiment_full_2026.csv');_last=str(sf['date'].iloc[-1]);ck('情绪表',_last in (pday, day),f'last={_last} expected={pday} or {day}',category='研究数据')  # P0-3修复: 盘后已更新到当日时 last==day 合法(与账本口径一致)
  cf=pd.read_csv(OUT/'r6p_candidates_2026.csv',dtype=str);cmax=max(cf['date']);ck('候选表',cmax>=pday,f'max={cmax} expected>={pday}',category='研究数据')
 except Exception as e:ck('研究数据',False,e,category='研究数据')
 # tasks exact action and schedule
 bad=[];unproven=[]
 runner=(BASE/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8') if (BASE/'scripts'/'run_trading_task.ps1').exists() else ''
 launch=LAUNCHER.read_text(encoding='utf-8') if LAUNCHER.exists() else ''
 root=ROOT_FILE.read_text(encoding='utf-8-sig').strip() if ROOT_FILE.exists() else ''  # P0-3修复: utf-8-sig 剥离BOM, 否则Path比较恒False导致任务Action全FAIL
 watch=(BASE/'scripts'/'_tick_watch.py').read_text(encoding='utf-8') if (BASE/'scripts'/'_tick_watch.py').exists() else ''  # 9/7修复: 9/4起tick链风险参数移驻_tick_watch.py prod_cfg(daemon_argv), 文本签名并入扫描, 否则runner_ok=False→任务Action全bad→全天gate连锁
 runner_ok=all(x in (runner+watch) for x in ('--execute-risk','--e4-support','--temp-ladder','--min-amt','10','--execute','feishu_notify.py','generate_next_plan.py'))
 launch_ok='run_trading_task.ps1' in launch and pathlib.Path(root)==BASE
 for name,mode in EXPECTED.items():
  rc,x=task_xml(name)
  if rc!=0 or str(LAUNCHER) not in x or f'-Mode {mode}' not in x or not runner_ok or not launch_ok:bad.append(name)
  rc2,o=run_capture(['schtasks','/Query','/TN',chr(92)+name,'/FO','LIST','/V'])
  if '267011' in o or '1999/11/30' in o:unproven.append(name)
 ck('任务Action',not bad,f'bad={bad} runner_ok={runner_ok} launch_ok={launch_ok}',category='计划任务')
 ck('任务历史',not unproven,f'unproven={unproven}',critical=False,category='计划任务')
 # 触发器时刻漂移(非致命): 与计划表契约比对, 只取 HH:mm。
 try:
  drift=[]
  for tname,twant in TRIGGER_EXPECTED.items():
   rc3,x3=task_xml(tname)
   if rc3!=0:
    drift.append(tname+':xml_rc='+str(rc3));continue
   got=[]
   for seg in x3.split('<StartBoundary>')[1:]:
    v=seg.split('</StartBoundary>')[0]
    if 'T' in v: got.append(v.split('T')[1][:5])
   got=sorted(set(got))
   if got!=sorted(twant):drift.append(tname+':got='+','.join(got)+' want='+','.join(sorted(twant)))
  ck('任务触发器',not drift,f'drift={drift}',critical=False,category='计划任务')
 except Exception as e:ck('任务触发器',False,e,critical=False,category='计划任务')
 # dashboard freshness
 try:
  response=urllib.request.urlopen('http://127.0.0.1:5930/api/health',timeout=5);body=response.read().decode('utf-8');bj=json.loads(body)
  ck('看板',health_ok(response.status,bj),f'status={response.status} ok={bj.get("ok")} body_status={bj.get("status")}',category='看板')
 except Exception as e:ck('看板',False,e,category='看板')
 # process check fail-closed
 try:
  cmd = "Get-CimInstance Win32_Process | Where-Object {$_.Name -like 'python*' -and $_.CommandLine -match 'tick_monitor|scan_and_confirm'} | Select-Object -ExpandProperty ProcessId"
  rc,o=run_capture(['powershell','-NoProfile','-Command',cmd])
  ck('残留进程',rc==0 and not [x for x in o.splitlines() if x.strip().isdigit()],o.strip() or 'none',category='进程')
 except Exception as e:ck('残留进程',False,e,category='进程')
 # syntax
 bad=[]
 for s in ('tick_monitor.py','scan_and_confirm.py','decision_cli.py','close_pipeline.py','premarket.py','build_board.py','plan_daily.py','trader_daily.py'):
  rc,_=run_capture([PY,'-m','py_compile',str(BASE/'scripts'/s)])
  if rc:bad.append(s)
 ck('脚本语法',not bad,f'bad={bad}',category='脚本')
 # resources (tpoint 对齐: CPU/内存/磁盘 C:/F:)
 try:
  r=_system_resources()
  cpu=r.get('cpu');mem=r.get('mem')
  if cpu is None: ck('CPU 使用率',False,'无法获取',critical=False,category='资源')
  else: ck('CPU 使用率',cpu<CPU_WARN_PCT,f'{cpu}%',critical=cpu>=CPU_FAIL_PCT,category='资源')
  if mem is None: ck('内存使用率',False,'无法获取',critical=False,category='资源')
  else: ck('内存使用率',mem<MEM_WARN_PCT,f'{mem}%',critical=False,category='资源')
  for dkey in ('disk_c','disk_f'):
   pct=r.get(dkey);free=r.get(dkey+'_free_gb')
   if pct is None: ck(dkey.upper().replace('_',' ')+' 使用率',False,'无法获取',critical=False,category='资源')
   else: ck(dkey.upper().replace('_',' ')+' 使用率',pct<DISK_WARN_PCT,f'{pct}%（剩余 {free}GB）',critical=pct>=DISK_FAIL_PCT,category='资源')
 except Exception as e:ck('资源检查',False,e,critical=False,category='资源')
 # stage specific
 if a.post_plan:
  pf=OUT/'plans'/f'{day}_plan.json'
  try:
   plan=json.loads(pf.read_text(encoding='utf-8'));pub=plan.get('published_at','');mode=str(plan.get('mode',''))
   cutoff_ok=pday in mode and '输入≤' in mode
   published_ok=bool(pub) and pub < day+' 09:25:00';refreshed=plan.get('premarket_refreshed_at','');snapshot=OUT/'global_snapshot.json'
   refreshed_today=refreshed.startswith(day) and refreshed < day+' 15:05:00'
   refresh_ok=refreshed_today and plan.get('global_snapshot_sha256')==hashlib.sha256(snapshot.read_bytes()).hexdigest()
   ck('当日计划',plan.get('date')==day and bool(plan.get('picks')) and cutoff_ok and published_ok and refresh_ok,
      f'picks={len(plan.get("picks",[]))} cutoff={mode} published={pub} refreshed={refreshed} snapshot_match={refresh_ok}',category='当日计划')
  except Exception as e:ck('当日计划',False,e,category='当日计划')
  try:
   stat=(OUT/'global_snapshot.json').stat();mtime=datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
   ck('外盘新鲜度',mtime==day,f'generated={mtime} expected={day}',category='当日计划')
  except Exception as e:ck('外盘新鲜度',False,e,category='当日计划')
 fails=[x for x in res if not x['ok'] and x['critical']];warn=[x for x in res if not x['ok'] and not x['critical']]
 report={'date':day,'stage':stage,'time':now.strftime('%Y-%m-%d %H:%M:%S'),'results':res,'summary':{'pass':sum(x['ok'] for x in res),'fail':len(fails),'warn':len(warn)},'status':'pass' if not fails else'fail'}
 (OUT/f'preflight_{day}_{stage}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 # tpoint 风格产物: Markdown 报告 + 异常日志 + 彩色摘要
 stage_label='计划验收' if a.post_plan else '基础设施'
 try:
  SELFCHK_DIR.mkdir(parents=True,exist_ok=True)
  md=build_report(report,stage_label)
  md_path=SELFCHK_DIR/f'{day}_{now.strftime("%H%M%S")}_{stage}.md'
  md_path.write_text(md,encoding='utf-8')
  report['report_path']=str(md_path)
 except Exception as e:
  print(f'WARN 报告写入失败: {e}',file=sys.stderr)
 try: _log_anomalies(report)
 except Exception as e:
  print(f'WARN 异常日志写入失败: {e}',file=sys.stderr)
 _console_summary(report)
 # 飞书推送: 交易日必发状态卡片; 非交易日仅异常告警(文本)
 if a.push:
  try:
   if trading_day:
    card=_build_status_card(report,stage_label)
    ok=_push_card(card,f'selfcheck:{day}:{stage}')
    print(f"  📡 盘前自检卡片已推送: {'OK' if ok else 'FAIL'}")
   elif report['status']!='pass':
    msg=(f"🔴 EvoAlpha盘前自检告警（非交易日）\n时间: {report['time']}  阶段: {stage_label}\n"
         f"统计: ✅{report['summary']['pass']} ⚠️{report['summary']['warn']} ❌{report['summary']['fail']}\n"
         + '\n'.join(f"  • {r['name']}: {r['detail'][:80]}" for r in fails[:8]))
    body=json.dumps({'msg_type':'text','content':{'text':msg}},ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request(_webhook(),data=body,headers={'Content-Type':'application/json'},method='POST')
    urllib.request.urlopen(req,timeout=15)
    print("  📡 非交易日异常告警已推送")
  except Exception as e:
   print(f"WARN 飞书推送失败: {e}",file=sys.stderr)
 print(json.dumps(report['summary'],ensure_ascii=False));return 1 if fails else 0
if __name__=='__main__':sys.exit(main())
