"""TDX 服务器清单迁移 (2026-09-10 根因更正):
可用段 117.34.114.13/.14/.15/.16/.17/.18/.20/.27 置顶(实测 mootdx+pytdx 双库取数验活通过,
60m 800 根可用, 与账本 9/10 收盘逐标的吻合); 旧节点(当前返空)保留尾部作备用。
同步修正: 这些清单此前全是"TCP 通但 bars 返空"的坏节点, 是 mootdx 长期"读取不了"的真正原因。
"""
import pathlib, py_compile, re, sys

NEW = ("[('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),"
       "\n ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),"
       "\n ('117.34.114.15',7709),('115.238.56.198',7709)]")  # 按 verify_tdx_servers 实测延迟排序(9/10)
NEW_DQ = NEW.replace("'", '"')  # 双引号风格文件
TARGETS = ["scripts/preflight.py", "scripts/tick_monitor.py", "scripts/scan_and_confirm.py",
           "scripts/monitor_intraday.py", "scripts/close_pipeline.py",
           "scripts/fetch_daily_minute_rebuild.py", "scripts/tdx_recovery_probe.py",
           "scripts/collect_tick_daily.py", "scripts/pull_intraday.py",
           "scripts/fill_daily_all_0827.py", "scripts/fill_daily_history.py",
           "scripts/stress_tdx_tick.py", "src/data/minute.py"]
BASE = pathlib.Path(".")
expect = re.compile(r"^(TDX_)?SERVERS\s*=\s*\[.*?\]", re.M | re.S)
n_done = 0
for rel in TARGETS:
    p = BASE / rel
    if not p.exists():
        print("MISS", rel); continue
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    txt = raw.decode("utf-8-sig")
    m = expect.search(txt)
    if not m:
        print("NO MATCH", rel); continue
    name = m.group(1) or ""
    literal = NEW_DQ if '"' in txt[m.start():m.end()] else NEW
    repl = (name + "SERVERS=" + literal) if "=" in m.group(0)[:len(name) + 8] and " " not in m.group(0)[:len(name) + 9] else (name + "SERVERS = " + literal)
    txt2 = txt[:m.start()] + repl + txt[m.end():]
    out = txt2.encode("utf-8")
    p.write_bytes((b"\xef\xbb\xbf" + out) if bom else out)
    try:
        py_compile.compile(str(p), doraise=True)
        print("OK  ", rel, "(BOM)" if bom else "")
        n_done += 1
    except Exception as e:
        print("COMPILE FAIL", rel, str(e)[:100])
print("迁移完成", n_done, "/", len(TARGETS))
