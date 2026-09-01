
import urllib.request
url = 'https://cdn.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README.md'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
data = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace')
open('C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/Vibe-Research-README.md', 'w', encoding='utf-8').write(data)
print('README saved:', len(data), 'chars')
# also try to list repo file tree via jsdelivr API
try:
    api = urllib.request.Request('https://data.jsdelivr.com/v1/packages/gh/simonlin1212/Vibe-Research@main', headers={'User-Agent': 'Mozilla/5.0'})
    tree = urllib.request.urlopen(api, timeout=20).read().decode('utf-8', errors='replace')
    open('C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/Vibe-Research-tree.json', 'w', encoding='utf-8').write(tree)
    import json
    d = json.loads(tree)
    files = []
    def walk(n, p):
        for f in n.get('files', []):
            fp = p + '/' + f['name']
            if f.get('type') == 'directory':
                walk(f, fp)
            else:
                files.append(fp)
    walk(d.get('tree', {}), '')
    print('tree files:', len(files))
    for f in files[:60]:
        print(' ', f)
except Exception as e:
    print('tree API fail:', str(e)[:120])

