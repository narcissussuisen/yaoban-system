
import urllib.request
url = 'https://cdn.jsdelivr.net/gh/simonlin1212/Vibe-Research@main/README.md'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
data = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace')
print(data[2500:7000])

