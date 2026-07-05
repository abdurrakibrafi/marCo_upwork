
import requests, re

url = 'https://news.google.com/rss/articles/CBMikwFBVV95cUxQdzU0dWNOcE9wNDBQdzNnSFY4VGJNVkJWS0FvV2s2Z3Y0RlJxRTAyWVp0Tnhkdi1UZWZSYmRzckZLMUFtR2hlZ0JYTEx5YVpYZ0ttZzRuVG1UTmlJdVlfX1ZkRnl3RkFMX202WDVuM0szSXQxYnNXOEZlY1M3d0hyb1V4SDJjaTZyelBJSVVQVGpGaFU?oc=5'
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=10)
html = resp.text

print("HTML length:", len(html))
# Let's print any URLs in JS script blocks or page sources
urls = re.findall(r'(https?://[^\s\"\'\<\>]+)', html)
for u in set(urls):
    if 'google' not in u and 'gstatic' not in u and 'w3.org' not in u:
        print("FOUND:", u)

# Let's print any text that looks like base64
print("\nScanning for links...")
matches = re.findall(r'href=["\']([^"\']+)["\']', html)
for m in matches:
    if 'google' not in m:
        print("HREF:", m)
