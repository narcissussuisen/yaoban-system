import urllib.request
url = 'https://qt.gtimg.cn/q=usDJI,usIXIC,hf_CL,hf_GC'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
print('LEN:', len(raw))
print(raw[:500])
