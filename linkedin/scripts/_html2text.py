#!/usr/bin/env python3
import re, html, sys
raw = open(sys.argv[1], encoding='utf-8', errors='replace').read()
raw = re.sub(r'<script.*?</script>', ' ', raw, flags=re.S | re.I)
raw = re.sub(r'<style.*?</style>', ' ', raw, flags=re.S | re.I)
text = re.sub(r'<[^>]+>', ' ', raw)
text = html.unescape(text)
text = re.sub(r'\s+', ' ', text)
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
end = int(sys.argv[3]) if len(sys.argv) > 3 else 7000
print(text[start:end])