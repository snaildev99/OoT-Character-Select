"""Read game state from the running emulator through the debug server (PLAY-debug.bat).

    venv\\Scripts\\python.exe tools\\live.py              flag + menu actors
    venv\\Scripts\\python.exe tools\\live.py <flag name>  one GameData Int

Image addresses (a64.Image) are shifted at run time; the shift is found by
searching memory for code bytes from image.bin (0xacb000 on 2026-11-09).
"""
import struct
import sys

import gamedata
from a64 import Image
from dbg import Debug

PROBE = 0x80c38510          # any code address; its bytes are searched for
GAMEDATA_GLOBAL = 0x84ea42a8  # holds the GameData manager (GOT 0x84c9e180 points here)
MENU_GLOBAL = 0x84e9cfa8      # [this] + 0x4e8 = menu actor manager (GOT 0x84c9e2a8)
MENU_HANDLES = {"3D UI camera": 0x08, "pouch Link": 0x28, "slot +0x48": 0x48,
                "tapestry": 0x68, "collection background": 0x88}


class Live:
    def __init__(self):
        self.d = Debug(timeout=150)
        img = Image()
        pat = img.data[PROBE - img.base:PROBE - img.base + 32]
        hits = self.d.search(pat, max_hits=4)
        assert len(hits) == 1, hits
        self.delta = hits[0] - PROBE

    def q(self, a):
        return struct.unpack("<Q", self.d.read(a, 8))[0]

    def u(self, a):
        return struct.unpack("<I", self.d.read(a, 4))[0]

    def gd_int(self, name):
        """Current value of a GameData Int (None if the name is unknown)."""
        gm = self.q(GAMEDATA_GLOBAL + self.delta)
        h = gamedata.murmur3_32(name)
        m, cap = self.q(gm + 0xE0), self.u(gm + 0xEC)
        i = h % cap
        for _ in range(cap):
            eh, idx = struct.unpack("<II", self.d.read(m + 8 * i, 8))
            if eh == h:
                rec = self.q(gm + 0xD8) + 0x28 * idx
                return struct.unpack("<i", self.d.read(rec + 0x1C, 4))[0]
            if eh == 0:
                return None
            i = (i + 1) % cap
        return None

    def menu_actors(self):
        """[(handle name, handle id, actor address, actor name, current position)]"""
        mm = self.q(self.q(MENU_GLOBAL + self.delta) + 0x4E8)
        out = []
        for name, off in MENU_HANDLES.items():
            link, hid = self.q(mm + off + 0x10), self.u(mm + off + 0x18)
            if hid == 0xFFFFFFFF or not link:
                out.append((name, None, None, None, None))
                continue
            a = self.q(link + 0x40)
            actor = self.d.read(self.q(a + 0x20), 40).split(b"\0")[0].decode("latin-1")
            pos = struct.unpack("<3f", self.d.read(a + 0x31C, 12))
            out.append((name, hid, a, actor, pos))
        return out


def main():
    lv = Live()
    print("main shifted by %#x" % lv.delta)
    names = sys.argv[1:] or ["CharacterMenu_SelectedCharacter"]
    for n in names:
        print("%s = %s" % (n, lv.gd_int(n)))
    if len(sys.argv) == 1:
        for name, hid, a, actor, pos in lv.menu_actors():
            if hid is None:
                print("  %-22s (none)" % name)
            else:
                print("  %-22s id %#x  %x %s  pos (%.3f, %.3f, %.3f)" % (name, hid, a, actor, *pos))


if __name__ == "__main__":
    main()
