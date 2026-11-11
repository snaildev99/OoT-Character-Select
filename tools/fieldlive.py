"""Watch the field swap emulator copy (PLAY-debug-field.bat, debug server on 127.0.0.1:6549)
while snaildev tests.

    venv\\Scripts\\python.exe tools\\fieldlive.py           wait for the game, then log every change
    venv\\Scripts\\python.exe tools\\fieldlive.py --once    print the current state and exit

Memory reads only (tools\\dbg.py), never screenshots, never input. Every line also goes to
build-field\\live-log.txt with the time. What it reads: the stored character, the "swap
pending" flag, what our two routines left in their corner of memory (mods\\field_swap.py,
stash_address: calls of fs_delete / fs_wake, swaps finished, the remembered position) and,
once a swap has run (the routines note the resident actor manager's address), the resident
actors: name, state, position. After a hang the last lines tell which step never ended.
When the game closes it waits for the next one (ends after 8 hours without a game).
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dbg  # noqa: E402
import exepatch  # noqa: E402
import fieldenv  # noqa: E402
import gamedata  # noqa: E402
import live  # noqa: E402
from a64 import Image  # noqa: E402

LOG = os.path.join(os.path.dirname(HERE), "build-field", "live-log.txt")
FLAG = "CharacterMenu_SelectedCharacter"
PENDING = "CharacterMenu_FieldSwapPending"
STASH_SIZE = 0x80           # as mods\field_swap.py
STATES = {3: "asleep(3)", 4: "awake", 6: "asleep", 7: "going(7)", 8: "going(8)", 9: "going(9)", 10: "going(10)", 11: "going(11)"}


class FieldLive(live.Live):
    def __init__(self, port=fieldenv.DEBUG_PORT):
        self.d = dbg.Debug(("127.0.0.1", port), timeout=150)
        img = Image()
        pat = img.data[live.PROBE - img.base:live.PROBE - img.base + 32]
        hits = self.d.search(pat, max_hits=4)
        assert len(hits) == 1, hits
        self.delta = hits[0] - live.PROBE
        mod0 = exepatch.MAIN + struct.unpack_from("<I", img.data, exepatch.MAIN + 4 - img.base)[0]
        bss_end = mod0 + struct.unpack_from("<i", img.data, mod0 + 12 - img.base)[0]
        self.stash_at = ((bss_end + 0xFFF) & ~0xFFF) - STASH_SIZE + self.delta
        self.patched = self.d.read(0x83302000 + self.delta, 8) == struct.pack("<II", *exepatch.SIGNATURE)

    def gd_bool(self, name):
        gm = self.q(live.GAMEDATA_GLOBAL + self.delta)
        h = gamedata.murmur3_32(name)
        m, cap = self.q(gm + 0x20), self.u(gm + 0x2C)
        i = h % cap
        for _ in range(cap):
            eh, idx = struct.unpack("<II", self.d.read(m + 8 * i, 8))
            if eh == h:
                return self.d.read(self.q(gm + 0x18) + 0x18 * idx + 0x16, 1)[0]
            if eh == 0:
                return None
            i = (i + 1) % cap
        return None

    def stash(self):
        raw = self.d.read(self.stash_at, STASH_SIZE)
        valid, deletes = struct.unpack_from("<II", raw, 0)
        pos = struct.unpack_from("<3f", raw, 8)
        rot = struct.unpack_from("<9f", raw, 0x14)
        mgr, actor, wakes, swaps = struct.unpack_from("<QQII", raw, 0x38)
        return dict(valid=valid, deletes=deletes, pos=pos, rot=rot, mgr=mgr, actor=actor, wakes=wakes, swaps=swaps)

    def residents(self, mgr):
        """[(list name, actor name, handle id or None, state, position)] of the manager's three lists."""
        out = []
        head = self.d.read(mgr + 0x28, 0x50)
        for k, name in enumerate(("common", "child", "adult")):
            count, ptr = struct.unpack_from("<I4xQ", head, 0x10 * k)
            for i in range(min(count, 16)):
                e = self.d.read(ptr + 0x70 * i, 0x30)
                link, hid, pending, sptr = struct.unpack_from("<QI4xQQ", e, 8)
                label = self.d.read(sptr, 80).split(b"\0")[0].decode("latin-1").split("/")[-1].split(".")[0] if sptr else "?"
                if hid == 0xFFFFFFFF or not link:
                    out.append((name, label, None, "creating" if pending else "none", None))
                    continue
                actor, lid = struct.unpack("<QI", self.d.read(link + 0x40, 12))
                if lid != hid or not actor:
                    out.append((name, label, hid, "gone", None))
                    continue
                st = self.u(actor + 0x2C)
                t = struct.unpack("<27f", self.d.read(actor + 0x2EC, 0x6C))     # home pos, home rot, pos, rot
                turn = "yaw %s (home %s) near (%d, %d, %d)" % ((yaw(t[15:24]), yaw(t[3:12])) + tuple(
                    2 * round(v / 2) for v in t[12:15]))
                out.append((name, label, hid, STATES.get(st, "state %d" % st), turn))
        flags = self.d.read(mgr + 0x70, 2)
        return out, flags[0], flags[1]


def say(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def vec(v):
    return "(%.2f, %.2f, %.2f)" % tuple(v)


def yaw(m):
    """Turn about the vertical axis of a 3x3 rotation, in degrees rounded to 5 (so that
    standing still does not fill the log)."""
    import math
    return "%d" % (5 * round(math.degrees(math.atan2(m[2], m[0])) / 5))


def state(lv):
    """Lines for what can be read right now. A read that fails (no save loaded yet, an
    object not there) is part of the answer, not an error."""
    try:
        v, pend = lv.gd_int(FLAG), lv.gd_bool(PENDING)
        age = lv.gd_bool("Sequence_IsChildhood")
        who = "character %s | %s | swap pending %s" % (v, "child" if age else "adult", pend)
    except (RuntimeError, struct.error, ZeroDivisionError):
        return ["no game data yet"]
    try:
        s = lv.stash()
    except (RuntimeError, struct.error):
        return [who + " | stash unreadable"]
    lines = [who + " | fs_delete calls %d, fs_wake calls %d, swaps done %d, place %s%s yaw %s"
             % (s["deletes"], s["wakes"], s["swaps"], "remembered " if s["valid"] else "used / none ", vec(s["pos"]),
                yaw(s["rot"]))]
    if s["mgr"]:
        try:
            res, is_child, deleting = lv.residents(s["mgr"])
            lines.append("  residents (%s%s): " % ("child" if is_child else "adult", ", a delete is under way" if deleting else "") +
                         "; ".join("%s %s%s" % (label, st, " " + turn if turn and label.startswith("Player") else "")
                                   for _l, label, _h, st, turn in res))
        except (RuntimeError, struct.error, UnicodeDecodeError) as e:
            lines.append("  residents unreadable: %s" % repr(e)[:60])
    return lines


IDLE_LIMIT = 8 * 3600      # give up after this long without a game


def watch(once):
    """Wait for a game, log it until it is gone. False: no game showed up in time."""
    lv, waited = None, time.time()
    while lv is None:
        try:
            lv = FieldLive()
        except (OSError, AssertionError, RuntimeError):
            if once:
                print("The field swap emulator copy is not running a game (no debug server on %d)." % fieldenv.DEBUG_PORT)
                return False
            if time.time() - waited > IDLE_LIMIT:
                return False
            time.sleep(3)
    say("connected (main shifted by %#x); our code patch is %s" % (lv.delta, "in place" if lv.patched else "NOT there"))
    last = None
    while True:
        try:
            lines = state(lv)
        except (OSError, ConnectionError) as e:
            say("the game is gone: %s" % (repr(e)[:80]))
            return True
        if lines != last:
            for line in lines:
                say(line)
            last = lines
        if once:
            return True
        time.sleep(0.1)


def main():
    once = "--once" in sys.argv
    # one logger serves a whole session: after a game closes (or crashes) it waits for the next
    while watch(once) and not once:
        time.sleep(3)


if __name__ == "__main__":
    main()
