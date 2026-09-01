
import urllib.request
for url in ['https://raw.githubusercontent.com/simonlin1212/Vibe-Research/main/README.md',
            'https://raw.githubusercontent.com/simonlin1212/Vibe-Research/main/README_en.md',
            'https://raw.githubusercontent.com/simonlin1212/Vibe-Research/main/backend/README.md']:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='replace')
        print(f'===== {url.split("/")[-1]} ({len(data)} chars) =====')
        print(data[:3000])
        print()
    except Exception as e:
        print(f'{url}: FAIL {e}')

