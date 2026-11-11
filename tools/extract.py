import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nxfmt import *
src, dst = sys.argv[1], sys.argv[2]
files = sarc_files(read(src))
for n, b in files.items():
    p = os.path.join(dst, n); os.makedirs(os.path.dirname(p) or dst, exist_ok=True)
    open(p, "wb").write(b)
print(len(files))
