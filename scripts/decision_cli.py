"""EvoAlpha 人工确认 CLI。

list: 列出待确认请求
approve REQUEST_ID --actor EvoAlpha --reason ... [--px-min --px-max] [--execute-px]
reject REQUEST_ID --actor EvoAlpha --reason ...

批准可选立即执行模拟单；数量由账本按45%单票、90%敞口、现金和100股整数倍重算。
"""
from __future__ import annotations
import argparse, json, pathlib, sys
from datetime import datetime

BASE=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'portfolio'))
from ledger import transact, record_human_decision, buy, buy_net


def _qty(state, px):
    acct=state['account']; policy=state['policy']
    curve=acct.get('equity_curve') or []
    eq=float(curve[-1]['equity']) if curve else float(state.get('start_cash',100000))
    gross=sum(float(p.get('cost',0))*int(p.get('qty',0)) for p in acct['positions'].values())
    budget=min(eq*float(policy['max_single_weight']), eq*float(policy['max_gross_exposure'])-gross, float(acct['cash']))
    return max(0,int(budget/buy_net(px)//100*100))


def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('list')
    for name in ('approve','reject'):
        p=sub.add_parser(name); p.add_argument('request_id'); p.add_argument('--actor',default='EvoAlpha'); p.add_argument('--reason',required=True)
        p.add_argument('--px-min',type=float); p.add_argument('--px-max',type=float)
        if name=='approve': p.add_argument('--execute-px',type=float)
    a=ap.parse_args()
    if a.cmd=='list':
        from ledger import load
        st=load(); pending=[r for r in st.get('signal_requests',{}).values() if r.get('status')=='pending']
        print(json.dumps(pending,ensure_ascii=False,indent=2)); return 0
    def mutate(st):
        req=st.get('signal_requests',{}).get(a.request_id)
        if not req: raise ValueError('request missing')
        if req.get('status')!='pending': raise ValueError('request not pending')
        if req.get('expires_at','') < datetime.now().strftime('%Y-%m-%d %H:%M'): raise ValueError('request expired')
        decision='approve' if a.cmd=='approve' else 'reject'
        did=record_human_decision(st,a.request_id,decision,a.actor,a.reason,a.px_min,a.px_max)
        fill=None
        if a.cmd=='approve' and a.execute_px is not None:
            qty=_qty(st,a.execute_px)
            if qty<100: raise ValueError('可用仓位不足100股')
            # P0.2: 人工执行同样受时序契约约束（信号过期即拒单——见 timing_contract）
            _now_str=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            buy(st,req['sym'],datetime.now().strftime('%Y-%m-%d %H:%M'),a.execute_px,qty,req['kind'],stop_pct=5.0,plan_ref=req.get('plan_ref',''),decision_id=did,
                signal_ts=req.get('signal_ts'),decision_ts=_now_str,
                candidates_ref=req.get('candidates_ref'),
                plan_match=req.get('plan_match') or {'in_plan': False, 'pick_id': None,
                                                      'off_plan_reason': {'code': 'human_confirmed', 'detail': '人工确认执行'}},
                off_plan_reason={'code': 'human_confirmed', 'detail': '人工确认执行'} if not (req.get('plan_match') or {}).get('in_plan') else None)
            fill={'sym':req['sym'],'qty':qty,'px':a.execute_px,'decision_id':did}
            st['signal_requests'][a.request_id]['status']='executed'
        return {'decision_id':did,'fill':fill}
    st,res=transact(mutate)
    print(json.dumps(res,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as e:
        print(f'ERROR: {e}',file=sys.stderr); sys.exit(1)
