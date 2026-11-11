"""Save the game's frames while the portrait run shows the characters (NPC emulator copy).

    venv\\Scripts\\python.exe tools\\npcshots.py

snaildev allowed this on 2026-11-10 for the portrait captures: the emulator's debug server
(127.0.0.1:6547) is asked for screenshots of the GAME picture (its "screenshot" method, saved
by the emulator into its dump folder); nobody here looks at them, tools\\npccutout.py reads
them as numbers. This tool reads memory and asks for pictures; it presses nothing.

It waits for the game, then, while the Characters page is open and a character is shown,
asks for one screenshot after another (at most PER_VISIT per stay on a character) and writes
one line per picture to build-npc\\portraits\\shots.jsonl: time, stored character number and
view state before and after the picture, the file. The portrait run (D-pad down on the page)
shows each character on black, then on white; which is which is read from the pictures later.
When the game closes it waits for the next one; it ends after IDLE seconds without a new
picture once some were taken (or after 8 hours).
"""
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))

import npcenv  # noqa: E402
import npclive  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "build-npc", "portraits")
INDEX = os.path.join(OUT, "shots.jsonl")
HEIGHT = 2160
PER_VISIT = 16
IDLE = 240


def state(lv):
    """(stored number, page open, display type, menu actor) or None while unreadable."""
    try:
        number = lv.gd_int(npclive.FLAG)
        page = lv.gd_bool("CharacterMenu_IsPageOpen")
        display, actor = lv.view()
        return number, bool(page), display, actor
    except (RuntimeError, struct.error, ZeroDivisionError, UnicodeDecodeError):
        return None


def main():
    """One tool serves a session: when a game closes it waits for the next one."""
    started = time.time()
    while time.time() - started < 8 * 3600:
        if watch() or "--once" in sys.argv:     # --once: this game only (the next one is not a capture)
            return
        time.sleep(3)


def watch():
    """Wait for a game and save pictures from it. True: finished (pictures taken, then quiet)."""
    os.makedirs(OUT, exist_ok=True)
    lv = None
    while lv is None:
        try:
            lv = npclive.NpcLive()
        except (OSError, AssertionError, RuntimeError):
            time.sleep(3)
    print("connected; pictures go to the emulator's dump folder, the list to", INDEX, flush=True)
    visit, taken, total, last = None, 0, 0, time.time()
    with open(INDEX, "a", encoding="utf-8") as index:
        index.write(json.dumps({"session": time.strftime("%Y-%m-%d %H:%M:%S"), "height": HEIGHT}) + "\n")
        while True:
            try:
                before = state(lv)
                ready = before is not None and before[1] and before[2] == 0 and before[3] is not None
                if not ready:
                    visit = None
                    if total and time.time() - last > IDLE:
                        break
                    time.sleep(0.05)
                    continue
                if before[0] != visit:
                    visit, taken = before[0], 0
                if taken >= PER_VISIT:
                    if time.time() - last > IDLE:
                        break
                    time.sleep(0.05)
                    continue
                t0 = time.time()
                shot = lv.d.call("screenshot", height=HEIGHT, save=True, inline=False)
                t1 = time.time()
                after = state(lv)
            except (OSError, ConnectionError) as e:
                print("the game is gone:", repr(e)[:80], flush=True)
                return False
            except RuntimeError as e:        # e.g. no frame presented in time
                print("screenshot failed:", str(e)[:120], flush=True)
                time.sleep(0.5)
                continue
            index.write(json.dumps({"t0": round(t0, 3), "t1": round(t1, 3), "before": before, "after": after,
                                    "path": shot.get("path"), "size": [shot.get("width"), shot.get("height")]}) + "\n")
            index.flush()
            taken += 1
            total += 1
            last = t1
            if total % 25 == 1:
                print("%s  %d pictures, character %s, %.2f s each" % (time.strftime("%H:%M:%S"), total, before[0], t1 - t0),
                      flush=True)
    print("done: %d pictures listed in %s" % (total, INDEX), flush=True)
    return True


if __name__ == "__main__":
    main()
