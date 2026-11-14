"""Read only: find where the Characters page's screen object keeps its body layout holder
(label test, 2026-11-14). Run while the page is open in PLAY-debug.bat, with the label test
folder NOT installed.

The game's own screen lookup (0x83be9670) is run in an ARM64 emulator whose memory is
fetched page by page from the running game (debug server, read_memory only). Then every
pointer in the first 0x400 bytes of the screen object (and one level deeper) is tried as a
holder: [holder+0x40] = layout, u16 at layout+0x13c = entries, entries at [layout+0x100]
(0xb0 each), a text pane's entry has (flags at +0xa8 & 0x3c00000) == 0x800000, its pane at
[entry+8], the pane's reference string at [pane+0xf8].
    venv\\Scripts\\python.exe -I tools\\labelprobe.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dbg import Debug  # noqa: E402
from exepatch import Image  # noqa: E402
import gamedata  # noqa: E402

PROBE = 0x80C38510
UI_GOT = 0x84CA0968
SCREEN_GET = 0x83BE9670
SCREEN = "CharacterMenu_00"


def main():
    import unicorn as uc
    from unicorn import arm64_const as A
    d = Debug(timeout=150)
    img = Image()
    hits = d.search(img.data[PROBE - img.base:PROBE - img.base + 32], max_hits=4)
    assert len(hits) == 1, hits
    delta = hits[0] - PROBE
    q = lambda a: struct.unpack("<Q", d.read(a, 8))[0]          # noqa: E731
    var = q(UI_GOT + delta)
    mgr = q(q(var) + 0x38)
    print("delta %x, global %x, manager %x" % (delta, var, mgr))
    m = uc.Uc(uc.UC_ARCH_ARM64, uc.UC_MODE_ARM)
    mapped = set()

    def fetch(mu, access, addr, size, value, _):
        page = addr & ~0xFFF
        for p in (page, page + 0x1000) if (addr + size) > page + 0x1000 else (page,):
            if p not in mapped:
                mu.mem_map(p, 0x1000)
                mu.mem_write(p, d.read(p, 0x1000))
                mapped.add(p)
        return True
    m.hook_add(uc.UC_HOOK_MEM_UNMAPPED, fetch)
    XREG = [getattr(A, "UC_ARM64_REG_X%d" % i) for i in range(31)]

    def ldapr(mu, addr, size, _):          # LDAPR(B/H/W/X): the emulator lacks them; a plain load does here
        w = int.from_bytes(bytes(mu.mem_read(addr, 4)), "little")
        if w in (0xD65F0BFF, 0xD65F0FFF):    # retaa / retab: a plain ret
            mu.reg_write(A.UC_ARM64_REG_PC, mu.reg_read(A.UC_ARM64_REG_X30))
            return
        if w & 0x3FFFFC00 == 0x38BFC000:
            rt, rn, sz = w & 31, (w >> 5) & 31, w >> 30
            base = mu.reg_read(A.UC_ARM64_REG_SP if rn == 31 else XREG[rn])
            fetch(mu, 0, base, 1 << sz, 0, None)
            val = int.from_bytes(bytes(mu.mem_read(base, 1 << sz)), "little")
            if rt != 31:
                mu.reg_write(XREG[rt], val)
            mu.reg_write(A.UC_ARM64_REG_PC, addr + 4)
    m.hook_add(uc.UC_HOOK_CODE, ldapr)
    STACK, DONE = 0x7F0000000, 0x7F1000000
    m.mem_map(STACK, 0x10000)
    m.mem_map(DONE, 0x1000)
    m.reg_write(A.UC_ARM64_REG_SP, STACK + 0x8000)
    m.reg_write(A.UC_ARM64_REG_X30, DONE)
    m.reg_write(A.UC_ARM64_REG_X0, mgr)
    m.reg_write(A.UC_ARM64_REG_X1, gamedata.murmur3_32(SCREEN))
    m.reg_write(A.UC_ARM64_REG_X2, 0)
    try:
        m.emu_start(SCREEN_GET + delta, DONE, count=200000)
    except uc.UcError:
        pc = m.reg_read(A.UC_ARM64_REG_PC)
        print("stopped at %x (link %x), word %08x" % (pc, pc - delta, int.from_bytes(bytes(m.mem_read(pc, 4)), "little")))
        raise
    scr = m.reg_read(A.UC_ARM64_REG_X0)
    print("screen object %x (vtable %x = link %x)" % (scr, q(scr), q(scr) - delta))
    if not scr:
        return

    def texts(holder):
        try:
            lay = q(holder + 0x40)
            n = struct.unpack("<H", d.read(lay + 0x13C, 2))[0]
            if not 0 < n < 2000:
                return None
            arr = q(lay + 0x100)
            out = []
            for i in range(n):
                e = d.read(arr + 0xB0 * i, 0xB0)
                if struct.unpack_from("<I", e, 0xA8)[0] & 0x3C00000 != 0x800000:
                    continue
                pane = struct.unpack_from("<Q", e, 8)[0]
                ref = d.read(q(pane + 0xF8), 32).split(b"\0")[0].decode("latin-1")
                out.append(ref)
            return n, out
        except Exception:
            return None

    body = d.read(scr, 0x400)
    for off in range(0, 0x400, 8):
        p = struct.unpack_from("<Q", body, off)[0]
        if not 0x1000000 <= p < 1 << 40:
            continue
        r = texts(p)
        if r:
            print("  screen+%#x -> holder %x: %d entries, text refs %s" % (off, p, r[0], r[1][:12]))
        for off2 in (0x20, 0x28, 0x30):
            try:
                p2 = q(p + off2)
            except Exception:
                continue
            r = texts(p2) if 0x1000000 <= p2 < 1 << 40 else None
            if r:
                print("  [screen+%#x]+%#x -> holder %x: %d entries, text refs %s" % (off, off2, p2, r[0], r[1][:12]))


if __name__ == "__main__":
    main()
