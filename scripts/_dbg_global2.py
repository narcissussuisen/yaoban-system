import urllib.request, re
url = 'https://qt.gtimg.cn/q=usDJI,usIXIC,usINX,hf_CL,hf_GC,hf_SI,hf_CAD,hf_USD'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
lines = raw.strip().split(';')
print('lines:', len(lines))
for line in lines:
    m = re.match(r'v_(\w+)="(.*)"', line.strip())
    if not m:
        print('NO MATCH:', line[:60]); continue
    parts = m.group(2).split('~')
    name = parts[1]
    price = parts[3]
    prev = parts[4]
    chg = parts[32] if len(parts) > 32 else '?'
    print(f'{m.group(1)} {name}: {price} / prev {prev} / chg {chg}')
