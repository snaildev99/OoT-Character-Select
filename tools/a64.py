"""Static helpers for the game executable image (AArch64), for reverse engineering.

    from a64 import Image
    img = Image()                       # the game image (IMAGE below) at 0x80000000
    img.strings("MinusMenuHeaderState") # addresses of exact NUL-terminated strings
    img.xrefs(addr)                     # code addresses whose ADRP+ADD/LDR computes addr
    img.dis(addr, count)                # capstone disassembly lines
    img.func_start(addr)                # nearest preceding function prologue (heuristic)
"""
import os
import re

import numpy as np
import capstone

# the game's executable image as dumped from memory; set OOT_IMAGE to use another place
IMAGE = os.environ.get("OOT_IMAGE") or os.path.expanduser("~/Games/Hookshot/image.bin").replace(os.sep, "/")
BASE = 0x80000000


class Image:
    def __init__(self, path=IMAGE, base=BASE):
        self.data = open(path, "rb").read()
        self.base = base
        n = len(self.data) // 4
        self.words = np.frombuffer(self.data[:n * 4], dtype="<u4")
        self.cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        self._refs = None

    def strings(self, s, exact=True):
        pat = (b"\0" + re.escape(s.encode()) + b"\0") if exact else re.escape(s.encode())
        return [self.base + m.start() + (1 if exact else 0) for m in re.finditer(pat, self.data)]

    def cstr(self, addr, maxlen=200):
        o = addr - self.base
        e = self.data.index(b"\0", o, o + maxlen)
        return self.data[o:e].decode("latin-1")

    def _build_refs(self):
        w = self.words.astype(np.int64)
        idx = np.nonzero((w & 0x9F000000) == 0x90000000)[0]
        aw = w[idx]
        imm = ((aw >> 29) & 3) | (((aw >> 5) & 0x7FFFF) << 2)
        imm = np.where(imm & (1 << 20), imm - (1 << 21), imm)
        pc = self.base + idx * 4
        page = (pc & ~0xFFF) + (imm << 12)
        rd = aw & 31
        targets, sites = [], []
        for k in (1, 2, 3, 4):
            j = idx + k
            ok = j < len(w)
            j2 = np.where(ok, j, 0)
            nw = w[j2]
            rn = (nw >> 5) & 31
            add = ok & ((nw & 0xFFC00000) == 0x91000000) & (rn == rd)
            ldr = ok & ((nw & 0xFFC00000) == 0xF9400000) & (rn == rd)
            ldrw = ok & ((nw & 0xFFC00000) == 0xB9400000) & (rn == rd)
            imm12 = (nw >> 10) & 0xFFF
            targets.append(np.where(add, page + imm12, np.where(ldr, page + imm12 * 8, page + imm12 * 4))[add | ldr | ldrw])
            sites.append(pc[add | ldr | ldrw])
        t = np.concatenate(targets)
        s = np.concatenate(sites)
        order = np.argsort(t, kind="stable")
        self._refs = (t[order], s[order])

    def xrefs(self, addr):
        if self._refs is None:
            self._build_refs()
        t, s = self._refs
        lo = np.searchsorted(t, addr, "left")
        hi = np.searchsorted(t, addr, "right")
        return sorted(set(int(x) for x in s[lo:hi]))

    def calls_to(self, addr):
        """BL instructions targeting addr."""
        w = self.words.astype(np.int64)
        idx = np.nonzero((w & 0xFC000000) == 0x94000000)[0]
        imm = w[idx] & 0x3FFFFFF
        imm = np.where(imm & (1 << 25), imm - (1 << 26), imm)
        tgt = self.base + idx * 4 + imm * 4
        return [int(self.base + i * 4) for i in idx[tgt == addr]]

    def dis(self, addr, count=40):
        o = addr - self.base
        out = []
        for ins in self.cs.disasm(self.data[o:o + count * 4], addr):
            out.append("%x: %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
        return out

    def u32(self, addr):
        return int(self.words[(addr - self.base) // 4])

    PACIBSP = 0xD503237F

    def func_start(self, addr, limit=0x8000):
        """Nearest preceding function start: a 'pacibsp' (this game signs return
        addresses), or a stp x29,x30 / sub sp prologue right after ret/b/udf."""
        a = addr & ~3
        while a > addr - limit:
            w = self.u32(a)
            if w == self.PACIBSP:
                return a
            if (w & 0xFFC07FFF) == 0xA9807BFD or (w & 0xFF0003FF) == 0xD10003FF:
                prev = self.u32(a - 4)
                if prev == 0xD65F03C0 or (prev & 0xFC000000) == 0x14000000 or prev == 0 or \
                        (prev & 0xFFE0001F) == 0xD4200000:
                    return a
            a -= 4
        return None

    def func_end(self, start, limit=0x8000):
        """End of a function: first ret/retab/b (tail) followed by udf/next pacibsp (heuristic)."""
        a = start + 4
        while a < start + limit:
            nxt = self.u32(a + 4) if a + 4 - self.base < len(self.data) else 0
            if nxt == self.PACIBSP or nxt == 0:
                return a + 4
            a += 4
        return a

    def func(self, addr):
        s = self.func_start(addr)
        return s, self.func_end(s)

    def summary(self, start, end=None):
        """Strings and call targets used by the function [start, end)."""
        end = end or self.func_end(start)
        regs = {}
        strs, calls = [], []
        o = start - self.base
        for ins in self.cs.disasm(self.data[o:end - self.base], start):
            m, ops = ins.mnemonic, ins.op_str
            if m == "adrp":
                r, imm = ops.split(", #")
                regs[r] = int(imm, 16)
            elif m == "add" and ops.count(",") == 2 and "#" in ops:
                d, n, imm = [x.strip() for x in ops.split(",")]
                if n in regs and imm.startswith("#0x"):
                    a = regs[n] + int(imm[1:], 16)
                    try:
                        s = self.cstr(a, 80)
                        if len(s) >= 3 and s.isprintable():
                            strs.append((ins.address, s))
                    except ValueError:
                        pass
            elif m == "bl":
                calls.append((ins.address, int(ops[1:], 16)))
        return strs, calls
