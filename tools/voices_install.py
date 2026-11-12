"""Install the voices folder (mods\voices.py, the voices session's file, used unchanged as
a library) into the mods folder the normal build installs into (build.EDEN_LOAD: Lichii's
since 2026-11-12; voices.py itself still names Fritchii's).

    venv-voice\Scripts\python.exe -I tools\voices_install.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))
sys.path.insert(0, HERE)

import voices  # noqa: E402

# the same folder as build.EDEN_LOAD (build.py itself cannot be imported here: the voices
# environment lacks its packages)
voices.EDEN_LOAD = os.path.join(os.environ["APPDATA"], os.environ.get("OOT_MODS_APP", "lichii"), "load", "0400486024DEC000")
sys.argv = [sys.argv[0], "--install"]
voices.main()
