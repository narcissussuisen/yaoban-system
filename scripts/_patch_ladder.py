import io
fp = 'C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/yaoban-system/scripts/r6p_replication.py'
src = io.open(fp, encoding='utf-8').read()
NL = chr(10)
old = "    ap.add_argument('--min-amt', type=float, default=0.0, help='前日成交额门槛(亿): 选手买入日成交额中位18.9亿/89%%>=5亿, 回放仅4.9亿 -> 妖票活跃底线 (0=不限)')"
new = old + NL + "    ap.add_argument('--temp-ladder', action='store_true', help='温度联动仓位(泛化发现): 前日温度>=70满档/50-70单仓/<50空仓(弱市降档, 2023/2024弱市0.75-0.80x应对)')"
assert old in src, 'a1'
src = src.replace(old, new)
# effective max-pos by temp in buy loop
old2 = "        bought_today = []" + NL + "        n_pos = len(pos)"
new2 = "        bought_today = []" + NL + "        n_pos = len(pos)" + NL + "        eff_max = args.max_pos" + NL + "        if args.temp_ladder:" + NL + "            if temp >= 70: eff_max = args.max_pos" + NL + "            elif temp >= 50: eff_max = min(1, args.max_pos)" + NL + "            else: eff_max = 0" + NL + "            if args.verbose and eff_max != args.max_pos:" + NL + "                print(f'[{d}] 温度{temp:.0f} -> 仓位档{eff_max}仓', flush=True)"
assert old2 in src, 'a2'
src = src.replace(old2, new2)
old3 = "            if n_pos >= args.max_pos or cash < 3000:" + NL + "                break"
new3 = "            if n_pos >= eff_max or cash < 3000:" + NL + "                break"
assert old3 in src, 'a3'
src = src.replace(old3, new3)
io.open(fp, 'w', encoding='utf-8').write(src)
print('temp-ladder added')
