"""Watch the NPC emulator copy (PLAY-debug-npc.bat, debug server on 127.0.0.1:6547) while snaildev tests.

    venv\\Scripts\\python.exe tools\\npclive.py           wait for the game, then log every change of the
                                                        stored character number and of the menu's 3D view
    venv\\Scripts\\python.exe tools\\npclive.py --once    print the current state and exit

Memory reads only (tools\\dbg.py), never screenshots, never input. Every line also goes to
build-npc\\live-log.txt with the time, so what snaildev saw can be matched to a character, and after
a crash the last line tells which character was being loaded. When the game closes it waits
for the next one (ends after 8 hours without a game).
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))

import dbg  # noqa: E402
import gamedata  # noqa: E402
import live  # noqa: E402
import npcenv  # noqa: E402
from a64 import Image  # noqa: E402

LOG = os.path.join(os.path.dirname(HERE), "build-npc", "live-log.txt")
FLAG = "CharacterMenu_SelectedCharacter"
DISPLAY = {0: "character shown", 1: "nothing", 2: "tapestry", 3: "stones", 4: "hidden (swap)", 5: "menu closed"}


class NpcLive(live.Live):
    def __init__(self, port=npcenv.DEBUG_PORT):
        self.d = dbg.Debug(("127.0.0.1", port), timeout=150)
        img = Image()
        pat = img.data[live.PROBE - img.base:live.PROBE - img.base + 32]
        hits = self.d.search(pat, max_hits=4)
        assert len(hits) == 1, hits
        self.delta = hits[0] - live.PROBE

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

    def view(self):
        """(display type, name of the menu's 3D character actor or None)."""
        mm = self.q(self.q(live.MENU_GLOBAL + self.delta) + 0x4E8)
        display = self.u(mm + 0x228)
        link, hid = self.q(mm + 0x28 + 0x10), self.u(mm + 0x28 + 0x18)
        if hid == 0xFFFFFFFF or not link:
            return display, None
        actor = self.q(link + 0x40)
        return display, self.d.read(self.q(actor + 0x20), 40).split(b"\0")[0].decode("latin-1")


def names():
    import npc_characters as n
    ring = n.ring()
    exp = {n.EXPERIMENT_BASE + i: "experiment %s (%s)" % (mode, stem.split(".")[1])
           for i, (mode, stem) in enumerate(n.EXPERIMENTS)}
    return lambda v: exp.get(v) or "%s (ring %d)" % (ring[v % len(ring)], v % len(ring))


def say(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def state(lv, name_of):
    """One line for what can be read right now. A read that fails (no save loaded yet, an
    object not there) is part of the answer, not an error."""
    try:
        v = lv.gd_int(FLAG)
        who = "character %d = %s" % (v, name_of(v)) if v is not None else "character flag unknown to the game"
        age = lv.gd_bool("Sequence_IsChildhood")
        page = lv.gd_bool("CharacterMenu_IsPageOpen")
        who += " | %s | Characters page %s" % ("child" if age else "adult", "open" if page else "closed")
    except (RuntimeError, struct.error, ZeroDivisionError):
        who = "no game data yet"
    try:
        display, actor = lv.view()
        what = "%s, menu actor %s" % (DISPLAY.get(display, "display %d" % display), actor or "none")
    except (RuntimeError, struct.error, UnicodeDecodeError):
        what = "no menu actors (title screen or loading)"
    return who + " | " + what


IDLE_LIMIT = 8 * 3600      # give up after this long without a game


def watch(name_of, once):
    """Wait for a game, log it until it is gone. False: no game showed up in time."""
    lv, waited = None, time.time()
    while lv is None:
        try:
            lv = NpcLive()
        except (OSError, AssertionError, RuntimeError):
            if once:
                print("The NPC emulator copy is not running a game (no debug server on %d)." % npcenv.DEBUG_PORT)
                return False
            if time.time() - waited > IDLE_LIMIT:
                return False
            time.sleep(3)
    say("connected (main shifted by %#x)" % lv.delta)
    last = None
    while True:
        try:
            text = state(lv, name_of)
        except (OSError, ConnectionError) as e:
            say("the game is gone: %s" % (repr(e)[:80]))
            return True
        if text != last:
            say(text)
            last = text
        if once:
            return True
        time.sleep(0.4)


def main():
    name_of = names()
    once = "--once" in sys.argv
    # one logger serves a whole session: after a game closes (or crashes) it waits for the next
    while watch(name_of, once) and not once:
        time.sleep(3)


if __name__ == "__main__":
    main()
