import re, sys
d = open(sys.argv[1], "rb").read()
pat = re.compile(sys.argv[2].encode())
seen = set()
for m in re.finditer(rb"[\x20-\x7e]{4,}", d):
    s = m.group()
    if pat.search(s) and s not in seen:
        seen.add(s)
        print(hex(m.start()), s.decode())
