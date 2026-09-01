
import urllib.request
url = 'https://cdn.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README.md'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
data = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace')
# print sections: 快速开始, 工作方式, 数据与市场, 当前边界
import re
for sec in ['快速开始', '工作方式', '模型接入', '数据与市场', '开发与测试', '当前边界']:
    i = data.find('## ' + sec)
    if i < 0:
        i = data.find(sec)
    j = data.find('
## ', i + 2) if i >= 0 else -1
    if i >= 0:
        seg = data[i: j if j > 0 else i + 2600]
        print(f'===== {sec} =====')
        print(seg[:2400])
        print()

