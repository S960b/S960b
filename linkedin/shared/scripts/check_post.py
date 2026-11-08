import re, sys

raw = open(sys.argv[1]).read()
m = re.search(r'POST TEXT:\s*\n(.*?)\n---', raw, re.S)
if not m:
    print("NO_POST_TEXT"); sys.exit(1)
text = m.group(1).strip()
print('RAW LEN:', len(text))
print('has em dash:', '\u2014' in text or '\u2013' in text)
print('lines:', text.count(chr(10)) + 1)
print('--- first 300 ---')
print(text[:300])