
import urllib.request
urls = [
    'https://cdn.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README.md',
    'https://cdn.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README_en.md',
    'https://fastly.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README.md',
    'https://ghproxy.net/https://raw.githubusercontent.com/simonlin1212/Vibe-Research/main/README.md',
    'https://mirror.ghproxy.com/https://raw.githubusercontent.com/simonlin1212/Vibe-Research/main/README.md',
]
for url in urls:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace')
        print(f'===== OK {url[:60]} ({len(data)} chars) =====')
        print(data[:2500])
        print()
        break
    except Exception as e:
        print(f'FAIL {url[:60]}: {str(e)[:80]}')

