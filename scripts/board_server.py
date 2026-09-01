"""操盘看板 Web 服务（动态装配版）
- 静态服务 outputs/board/ + 请求时动态合并: pos_live.json 持仓实时价 / 最新 market_scan / board_momentum
- 页面每 30s fetch board.json 自动刷新(非交易日数据不变即静默)
用法: python scripts/board_server.py [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE = pathlib.Path(__file__).resolve().parent.parent
ROOT = BASE / 'outputs' / 'board'
INTRADAY = BASE / 'outputs' / 'intraday'


def _read_json(p: pathlib.Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def assemble() -> dict:
    """board.json + pos_live 实时价 + 最新扫描/动量 动态合并"""
    board = _read_json(ROOT / 'board.json') or {}
    live = dict(board.get('live') or {})
    # 1) 持仓实时价 (tick 守护写 pos_live.json)
    pos_live = _read_json(INTRADAY / 'pos_live.json')
    if pos_live:
        live['pos_live'] = pos_live
        px_map = {}
        for q in (pos_live.get('positions') or []):
            if q.get('px') is not None:
                px_map[q['sym']] = q['px']
        if px_map and board.get('positions'):
            for p in board['positions']:
                px = px_map.get(p['sym'])
                if px:
                    p['last'] = round(px, 3)
                    p['mv'] = round(p['qty'] * px, 2)
                    p['pnl'] = round((px - p['cost']) * p['qty'], 2)
                    p['pnl_pct'] = round((px / p['cost'] - 1) * 100, 2) if p.get('cost') else None
            # 统一实时口径: 盈亏分析/总资产与持仓明细一致
            _rebuild_figures(board)
    # 2) 最新全市场扫描
    try:
        scans = sorted(INTRADAY.glob('market_scan_*.json'))
        if scans:
            live['scan'] = _read_json(scans[-1]) or live.get('scan')
    except Exception:
        pass
    # 3) 板块动量
    mom = _read_json(INTRADAY / 'board_momentum.json')
    if mom:
        live['momentum'] = mom
    board['live'] = live
    return board


def _rebuild_figures(b: dict) -> None:
    """实时价合并后重算总资产/盈亏分析，与持仓明细同口径。"""
    pos = b.get('positions') or []
    mv = round(sum(float(p.get('mv') or 0) for p in pos), 2)
    pnl_amt = round(sum(float(p.get('pnl') or 0) for p in pos), 2)
    total = round(float(b.get('cash') or 0) + mv, 2)
    b['mv'] = mv
    b['total_asset'] = total
    pct_tot = (total / float(b['start_cash']) - 1) * 100 if b.get('start_cash') else None
    detail = []
    for row in b.get('pnl_detail') or []:
        p = next((x for x in pos if x.get('sym') == row['sym']), None)
        if p is not None:
            row['pnl'] = float(p.get('pnl') or 0.0)
            row['pnl_pct'] = float(p.get('pnl_pct') or 0.0)
        row['share'] = round(row['pnl'] / pnl_amt * 100, 2) if pnl_amt else None
        detail.append(row)
    detail.sort(key=lambda x: -(x['pnl'] or 0))
    b['pnl_detail'] = detail
    m = re.search(r'已实现盈亏 ([+-]?[\d,.]+) 元 · 已完成交易 (\d+) 笔', str(b.get('pnl_summary') or ''))
    realized_s, done_s = (m.group(1), m.group(2)) if m else ('+0', '0')
    b['pnl_summary'] = (f'累计收益 {pct_tot:+.2f}% · 当前浮动盈亏 {pnl_amt:+.0f} 元 · '
                        f'已实现盈亏 {realized_s} 元 · 已完成交易 {done_s} 笔')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            # 页面: 内嵌动态 B
            try:
                tpl = (BASE / 'templates' / 'board_template.html').read_text(encoding='utf-8')
                board = assemble()
                html = tpl.replace('__BOARD_JSON__', json.dumps(board, ensure_ascii=False))
                data = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                sys.stderr.write('[board] assemble fail: %s\n' % e)
        elif self.path == '/board.json':
            try:
                board = assemble()
                data = json.dumps(board, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as e:
                sys.stderr.write('[board] json fail: %s\n' % e)
        super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write('[board] %s\n' % (fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8765)
    args = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'操盘看板(动态装配): http://127.0.0.1:{args.port}/  (Ctrl+C 停止)', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
