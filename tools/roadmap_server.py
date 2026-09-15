# -*- coding: utf-8 -*-
"""EvoAlpha 路线图**动态看板服务**（stdlib only，无第三方依赖）。

与静态 HTML 的区别（这是这个文件存在的唯一理由）：
- 每次浏览器请求都在**服务端重新探测**（账本 / 19 个计划任务 / 归档 sha256 / manifest / 测试基线），
  所以**按 F5 就是最新进度**，不需要任何人再生成一份 HTML；
- 叙事状态仍来自 `docs/dashboard/status.json`（我改那一处即可）。

设计要点
--------
- 仅绑定 127.0.0.1（不对外暴露）。
- 探测结果带 10s TTL 缓存：连点刷新不会反复 shell out 调 schtasks；`?fresh=1` 可强制绕过。
- 顶部无条件把 stdout/stderr 重定向到 UTF-8 日志文件：pythonw 下 stderr 可能是 GBK 管道或 None，
  任何非 GBK 字符的 print 都会抛 UnicodeEncodeError 并在启动后毫秒级打死进程（本项目踩过）。
- 屏蔽 SIGINT/SIGBREAK/SIGTERM，避免宿主会话被 Ctrl+C 时连带带走。

用法：
    python tools/roadmap_server.py --port 8790            # 前台
    pythonw tools/roadmap_server.py --port 8790           # 无窗口常驻
    浏览器: http://127.0.0.1:8790/
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LOGDIR = REPO / "outputs" / "dashboard"
LOGDIR.mkdir(parents=True, exist_ok=True)
LOGPATH = LOGDIR / "roadmap_server.log"

# ---- 必须在任何 print / logging 之前重定向（pythonw 下 stderr 可能为 None 或 GBK）----
try:
    _log = open(LOGPATH, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _log
    sys.stderr = _log
except Exception:
    pass

import argparse  # noqa: E402
import signal  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

for _s in ("SIGINT", "SIGBREAK", "SIGTERM"):
    try:
        signal.signal(getattr(signal, _s), signal.SIG_IGN)
    except (AttributeError, ValueError, OSError):
        pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_roadmap_dashboard import STATUS, build_page, collect_probes  # noqa: E402

CACHE_TTL = 10.0
_lock = threading.Lock()
_cache: dict = {"t": 0.0, "html": None, "payload": None}


def _fresh(force: bool = False) -> tuple[str, dict]:
    with _lock:
        now = time.time()
        if force or _cache["html"] is None or (now - _cache["t"]) > CACHE_TTL:
            spec = json.loads(STATUS.read_text(encoding="utf-8"))
            probes = collect_probes()
            _cache["html"] = build_page(spec, probes)
            _cache["payload"] = {"spec": spec, "probes": probes}
            _cache["t"] = now
            print(f"[{time.strftime('%H:%M:%S')}] re-probed"
                  f" ledger_rev={probes['ledger'].get('revision')}"
                  f" tasks={probes['tasks'].get('ready')}/{probes['tasks'].get('total')}")
        return _cache["html"], _cache["payload"]


class Handler(BaseHTTPRequestHandler):
    server_version = "EvoAlphaRoadmap/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = self.path.split("?", 1)[0]
            force = "fresh=1" in self.path
            if path in ("/", "/roadmap", "/roadmap.html", "/index.html"):
                html_text, _ = _fresh(force)
                self._send(200, html_text.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/status":
                _, payload = _fresh(force)
                self._send(200, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                           "application/json; charset=utf-8")
            elif path == "/healthz":
                _, payload = _fresh(force)
                led = payload["probes"]["ledger"]
                self._send(200, json.dumps({"ok": True, "ledger_revision": led.get("revision"),
                                            "ledger_start_date": led.get("start_date")},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception:
            print(traceback.format_exc())
            self._send(500, traceback.format_exc().encode("utf-8", "replace"),
                       "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # 抑制默认 stderr 噪声，只写我们自己的日志
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"=== roadmap server listening on http://{args.host}:{args.port}/ "
          f"(pid={__import__('os').getpid()}) ===")
    try:
        httpd.serve_forever()
    except Exception:
        print(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
