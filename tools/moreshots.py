"""Save the game's frames during the portrait run of the "more characters" capture build.

    venv\\Scripts\\python.exe -I tools\\moreshots.py

tools\\npcshots.py as it is (snaildev allowed saving frames for the portrait captures, and only
for them, on 2026-11-10: the debug server's "screenshot" of the game picture; nobody looks at
the pictures, tools\\morecutout.py reads them as numbers; this presses nothing), pointed at
our capture copy's debug server (tools\\moreenv.py, 127.0.0.1:6548) and at our own list,
build-more\\portraits\\shots.jsonl. It waits for the game (PLAY-debug-more-capture.bat), saves
frames while the Characters page shows a character, and ends 240 s after the last picture.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))

import moreenv  # noqa: E402
import npclive  # noqa: E402
import npcshots  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "build-more", "portraits")


class Live(npclive.NpcLive):
    def __init__(self):
        super().__init__(port=moreenv.DEBUG_PORT)


def main():
    npcshots.OUT = OUT
    npcshots.INDEX = os.path.join(OUT, "shots.jsonl")
    npclive.NpcLive = Live
    npcshots.main()


if __name__ == "__main__":
    main()
