"""Record what the menu actor manager does, frame by frame, from the running game
(debug server, PLAY-debug.bat; read only). About 180 samples a second; one line per
value that changed.

    venv\\Scripts\\python.exe tools\\livetrace.py <out file> [seconds]

Stops after `seconds` (default 600) or when the file `<out file>.stop` exists.
Start it, do the thing in the game, read the file. It reconnects if the emulator is
restarted. Each sample:
  type / cam / snap / cat / age   display type, camera state, +0x31c, +0x320, +0x37a
  blk0, blk0b                     camera block 0 (position, aim, fovy, picture offset; up, near, far)
  flags                           our GameData flags (INTS, BOOLS below)
  shadow                          the menu scene's depth shadow: flag word (bit 2 = range
                                  overridden), near and far in metres from the camera
  cam, s0, s1, s4, bg             the handles at +0x08 (3D UI camera), +0x28, +0x48, +0xa8
                                  (menu slots 0, 1, 4) and +0x208 (backdrop): actor name,
                                  state [+0x2c] (4 awake, 6 asleep), flag words +0x4a8 /
                                  +0x4ac (bit 0x10000 = shown), position, the pending
                                  position request (x = nan: none), rotation row, scale, home
"""
import os
import struct
import sys
import time

import gamedata
import live
from dbg import Debug

INTS = ["CharacterMenu_SelectedCharacter", "CharacterMenu_Slide", "CharacterMenu_ViewCharacter",
        "CharacterMenu_SlideOut", "CharacterMenu_PoseCategory"]
BOOLS = ["CharacterMenu_KeepUICamera", "CharacterMenu_IsPageOpen"]
SLOTS = {"cam": 0x08, "s0": 0x28, "s1": 0x48, "s4": 0xA8, "bg": 0x208}
MENU_SCENES = 0x84eb50a8    # build.MENU_SCENES
POUCH_HOVER = 0x84e98e28    # build.POUCH_HOVER


class Tracer:
    def __init__(self):
        self.lv = live.Live()
        self.d = self.lv.d
        self.names = {}
        self.resolve()

    def resolve(self):
        """The manager and the flag records (looked up again now and then: a loaded
        save or a new scene may move them)."""
        top = self.lv.q(live.MENU_GLOBAL + self.lv.delta)
        assert top, "no menu manager yet"
        self.mm = self.lv.q(top + 0x4E8)
        gm = self.lv.q(live.GAMEDATA_GLOBAL + self.lv.delta)
        assert self.mm and gm, "no menu actor manager / GameData yet"
        self.recs = {}
        for n in INTS:
            self.recs[n] = self.find(gm, n, 0xE0, 0xEC, 0xD8, 0x28) + 0x1C
        for n in BOOLS:
            self.recs[n] = self.find(gm, n, 0x20, 0x2C, 0x18, 0x18) + 0x16
        # the menu's 3D scene (scene 1) and its depth shadow range
        self.scene = self.floats = 0
        scenes = self.lv.q(MENU_SCENES + self.lv.delta)
        if scenes and self.lv.u(scenes + 0x50) > 1:
            self.scene = self.lv.q(self.lv.q(scenes + 0x58) + 0x60)
            shadow = self.lv.q(self.scene + 0xD60) if self.scene else 0
            if shadow:
                self.floats = self.lv.q(shadow + 0x330)
                self.cascades = struct.unpack("<i", self.d.read(shadow + 0x250, 4))[0]

    def find(self, gm, name, map_off, cap_off, rec_off, size):
        h = gamedata.murmur3_32(name)
        m, cap = self.lv.q(gm + map_off), self.lv.u(gm + cap_off)
        i = h % cap
        for _ in range(cap):
            eh, idx = struct.unpack("<II", self.d.read(m + 8 * i, 8))
            if eh == h:
                return self.lv.q(gm + rec_off) + size * idx
            assert eh, name
            i = (i + 1) % cap
        raise KeyError(name)

    def name(self, a, ident):
        if (a, ident) not in self.names:        # addresses are reused by later actors
            try:
                self.names[a, ident] = self.d.read(self.lv.q(a + 0x20), 40).split(b"\0")[0].decode("latin-1")
            except Exception:
                return "?"
        return self.names[a, ident]

    def sample(self):
        raw = self.d.read(self.mm, 0x380)
        out = []
        out.append("type %d cam %d snap %d cat %d age %d" % (
            struct.unpack_from("<I", raw, 0x228)[0], struct.unpack_from("<I", raw, 0x30C)[0], raw[0x31C],
            struct.unpack_from("<I", raw, 0x320)[0], raw[0x37A]))
        out.append("blk0 " + " ".join("%.3f" % v for v in struct.unpack_from("<6f", raw, 0x22C))
                   + " fov %.3f off %.4f" % struct.unpack_from("<2f", raw, 0x22C + 0x2C))
        out.append("blk0b " + " ".join("%.3f" % v for v in struct.unpack_from("<5f", raw, 0x22C + 0x18)))
        flags = []
        for n in INTS:
            flags.append("%s=%d" % (n[14:], struct.unpack("<i", self.d.read(self.recs[n], 4))[0]))
        for n in BOOLS:
            flags.append("%s=%d" % (n[14:], self.d.read(self.recs[n], 1)[0]))
        hover = self.lv.q(POUCH_HOVER + self.lv.delta)
        if hover:
            flags.append("hovered category=%d" % struct.unpack("<i", self.d.read(hover + 0x7C8, 4))[0])
        out.append(" ".join(flags))
        if self.floats and 0 < self.cascades < 8:
            fl = struct.unpack("<H", self.d.read(self.scene + 0xD14, 2))[0]
            fa = struct.unpack("<%df" % (self.cascades + 1), self.d.read(self.floats, 4 * self.cascades + 4))
            out.append("shadow flags %04x near %.3f far %.3f" % (fl, fa[0], fa[-1]))
        else:
            out.append("shadow -")
        for label, off in SLOTS.items():
            pend, vt, link, hid, fl = struct.unpack_from("<QQQII", raw, off)
            s = "%s pend %x id %x fl %x" % (label, pend, hid, fl & 0xFF)
            if link and hid != 0xFFFFFFFF:
                a, lid = struct.unpack("<QI", self.d.read(link + 0x40, 12))
                if lid == hid and a:
                    st = self.lv.u(a + 0x2C)
                    body = self.d.read(a + 0x2EC, 0x90)
                    home = struct.unpack_from("<3f", body, 0)
                    pos = struct.unpack_from("<3f", body, 0x31C - 0x2EC)
                    rot = struct.unpack_from("<3f", body, 0x328 - 0x2EC)
                    scale = struct.unpack_from("<3f", body, 0x34C - 0x2EC)
                    req = struct.unpack_from("<3f", body, 0x358 - 0x2EC)
                    f8, fc = struct.unpack("<II", self.d.read(a + 0x4A8, 8))
                    f8 &= ~0x800000  # flips within every frame
                    s += (" %s@%x st %d f %08x %08x pos %.3f %.3f %.3f req %.3f %.3f %.3f rot %.2f %.2f %.2f"
                          " sc %.2f home %.2f %.2f %.2f") % (
                        self.name(a, hid), a & 0xFFFFFF, st, f8, fc, *pos, *req, *rot, scale[0], *home)
                else:
                    s += " stale (link id %x actor %x)" % (lid, a)
            out.append(s)
        return out


def main():
    out_path = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
    t0 = time.time()
    last = None
    tr = None
    n = 0
    errors, said = 0, None
    with open(out_path, "a", encoding="utf-8") as f:
        f.write("--- trace started %s\n" % time.strftime("%H:%M:%S"))
        f.flush()
        while time.time() - t0 < seconds and not os.path.exists(out_path + ".stop"):
            try:
                if tr is None:
                    # only once the game runs at speed: the search for the code is not
                    # something to do to an emulator that is still starting
                    if Debug(timeout=10).call("status").get("fps", 0) < 30:
                        time.sleep(3)
                        continue
                    tr = Tracer()
                    f.write("%8.3f connected, shift %#x, manager %x\n" % (time.time() - t0, tr.lv.delta, tr.mm))
                    f.flush()
                    last = None
                if n % 200 == 199:
                    tr.resolve()
                cur = tr.sample()
                n += 1
                errors = 0
            except Exception as e:  # emulator closed or not up yet, or a pointer changed mid-read
                errors += 1
                if tr is not None and repr(e) != said:
                    said = repr(e)
                    f.write("%8.3f ! %s\n" % (time.time() - t0, said))
                    f.flush()
                if tr is None or errors > 25 or isinstance(e, (ConnectionError, OSError, AssertionError)):
                    tr = None
                    time.sleep(3)
                else:
                    time.sleep(0.1)
                continue
            time.sleep(0.003)
            if cur != last:
                t = time.time() - t0
                for i, line in enumerate(cur):
                    if last is None or line != last[i]:
                        f.write("%8.3f #%d %s\n" % (t, n, line))
                f.flush()
                last = cur
        f.write("--- trace ended, %d samples\n" % n)


if __name__ == "__main__":
    main()
