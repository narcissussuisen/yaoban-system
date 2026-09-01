import urllib.request, re, json, pathlib
url = 'https://qt.gtimg.cn/q=usDJI,usIXIC,usINX,hf_CL,hf_GC,hf_SI'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
print('RAW len:', len(raw))
print(repr(raw[:200]))
lines = raw.strip().split(';')
print('lines:', len(lines))
out = {}
for line in lines:
    m = re.match(r'v_(\w+)="(.*)"', line.strip())
    if not m:
        print('nomatch:', repr(line[:50]))
        continue
    parts = m.group(2).split('~')
    print('code:', m.group(1), 'parts:', len(parts), 'p3:', repr(parts[3]) if len(parts) > 3 else None)
    if len(parts) < 5 or not parts[3]:
        continue
    out[m.group(1)] = parts[3]
print('out:', out)
