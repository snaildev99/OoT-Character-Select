import re, sys
for p in sys.argv[1:]:
    d = open(p, "rb").read()
    s = [m.group().decode() for m in re.finditer(rb"[\x20-\x7e]{3,}", d)]
    print("===", p, len(s))
    print(" | ".join(s))
