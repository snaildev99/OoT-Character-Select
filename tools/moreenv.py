"""The "more characters" test build's own emulator: a portable copy of Lichii build 160 with its
own mods folder (the scheme of tools\\npcenv.py, moved to build 160 and Lichii's data; that file
and its Fritchii copy are not touched).

    venv\\Scripts\\python.exe tools\\moreenv.py setup    create / repair the copy (safe to rerun)
    venv\\Scripts\\python.exe tools\\moreenv.py sync     refresh its config from the normal one

Layout (the emulator takes <exe dir>\\user over %APPDATA%\\lichii when that folder exists):
    C:\\Repos\\Lichii\\dist\\build-160-more\\       hard links to build 160 (dist\\build-160-pgo)
        user\\load\\0400486024DEC000\\              the mods (mods\\more_characters.py --install)
        user\\config\\                              copy of %APPDATA%\\lichii\\config, load_directory
                                                   pointed at user\\load (synced on every install)
        user\\keys, cache, ...                      junctions to %APPDATA%\\lichii (shader cache shared;
                                                   the graphics settings are the normal ones, unchanged)
Saves, SD card, dumps: the copied config pins them to %APPDATA%\\lichii, so progress is SHARED
with the normal Lichii install, and the saved character number with it. A save made here with
one of the new characters picked (stored number 100 and up) asks the normal build for a model
file it does not ship: pick a character of the normal roster before saving.
A second copy, CAPTURE (C:\\Repos\\Lichii\\dist\\build-160-more-capture, PLAY-debug-more-capture.bat), is
for the portrait captures (mods\\more_characters.py --capture --install): the same scheme, but with
the capture settings of the first portrait runs (CAPTURE_SETTINGS: the game's picture is 3840 x
2160 itself) and a shader cache of its OWN (an empty folder, not the junction), so that a
different resolution scale can never make the normal install prepare its shaders again.
The launchers (PLAY-debug-more.bat, PLAY-debug-more-capture.bat) put the debug server on 127.0.0.1:6548 (normal 6545, UI
6546, NPC 6547, field 6549). Don't run two of the emulators at once.
"""
import os
import re
import shutil
import subprocess
import sys

SRC = r"C:\Repos\Lichii\dist\build-160-pgo"
DATA = os.path.join(os.environ["APPDATA"], "lichii")
SHARED = ["keys", "cache", "amiibo", "icons", "lossless", "post_presets", "post_shaders", "streamline", "play_time",
          "screenshots", "fidelityfx"]
DEBUG_PORT = 6548
# Graphics settings of the capture copy (tools\npcenv.py has why: the game presents 2560 x 1440
# docked, the debug server's screenshots are at most 3840 x 2160; scale 1.5 makes one screenshot
# pixel one rendered pixel, as in the frames the first 100 portraits were cut from).
CAPTURE_SETTINGS = {
    "resolution_setup": 5,      # Res3_2X: 2560 x 1440 -> 3840 x 2160
    "scaling_filter": 1,        # Bilinear: the identity at 1 : 1
    "gpu_accuracy": 1,          # High
    "max_anisotropy": 5,        # 16x
}


def _link_tree(src, dst):
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(out, exist_ok=True)
        for f in files:
            target = os.path.join(out, f)
            if not os.path.exists(target):
                try:
                    os.link(os.path.join(root, f), target)
                except OSError:
                    shutil.copy2(os.path.join(root, f), target)


def _junction(link, target):
    if os.path.lexists(link):
        return
    subprocess.run(["cmd", "/c", "mklink", "/J", link, target], check=True, capture_output=True)


class Copy:
    """One portable copy. The names are those mods/npc_characters.py uses of tools/npcenv.py."""

    def __init__(self, dst, settings=None, own=()):
        self.DST, self.settings, self.own = dst, dict(settings or {}), tuple(own)
        self.USER = os.path.join(dst, "user")
        self.LOAD_ROOT = os.path.join(self.USER, "load")
        self.LOAD = os.path.join(self.LOAD_ROOT, "0400486024DEC000")
        self.EXE = os.path.join(dst, "lichii.exe")
        self.DEBUG_PORT = DEBUG_PORT

    def setup(self):
        assert os.path.isfile(os.path.join(SRC, "lichii.exe")), "build 160 not found at " + SRC
        assert not os.path.isdir(os.path.join(SRC, "user")), "the source build has a portable user folder of its own"
        _link_tree(SRC, self.DST)
        os.makedirs(self.USER, exist_ok=True)
        for d in SHARED:
            if d in self.own:
                assert not os.path.islink(os.path.join(self.USER, d)), d
                os.makedirs(os.path.join(self.USER, d), exist_ok=True)
            elif os.path.isdir(os.path.join(DATA, d)):
                _junction(os.path.join(self.USER, d), os.path.join(DATA, d))
        os.makedirs(self.LOAD, exist_ok=True)
        self.sync_config()
        print("more characters emulator ready:", self.EXE)

    def sync_config(self):
        """Copy the normal config, with the mods folder moved to this copy's own."""
        # made beside the real folder and swapped in at the end: a config that was only copied
        # would point this copy at the NORMAL mods folder
        src, final = os.path.join(DATA, "config"), os.path.join(self.USER, "config")
        dst = final + ".new"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.bak*"))
        ini = os.path.join(dst, "qt-config.ini")
        text = open(ini, encoding="utf-8").read()
        load = self.LOAD_ROOT.replace("\\", "/")
        text, n = re.subn(r"(?m)^load_directory=.*$", lambda m: "load_directory=" + load, text)
        assert n == 1, "load_directory not found in qt-config.ini"
        text, n = re.subn(r"(?m)^load_directory\\default=.*$", r"load_directory\\default=false", text)
        assert n == 1
        for key in ("nand_directory", "sdmc_directory"):    # saves must stay the shared ones: pinned, not defaults
            m = re.search(r"(?m)^%s=(.*)$" % key, text)
            assert m and os.path.normcase(os.path.normpath(m.group(1))).startswith(os.path.normcase(DATA)), key
            assert re.search(r"(?m)^%s\\default=false$" % key, text), key
        for key, value in self.settings.items():
            text, n = re.subn(r"(?m)^%s=.*$" % re.escape(key), lambda m: "%s=%s" % (key, value), text)
            assert n == 1, key + " not found in qt-config.ini"
            text, n = re.subn(r"(?m)^%s\\default=.*$" % re.escape(key), lambda m: key + "\\default=false", text)
            assert n == 1, key
        open(ini, "w", encoding="utf-8").write(text)
        tuned = os.path.join(dst, "frizti.ini")     # the tuned settings' own copy
        if self.settings and os.path.exists(tuned):
            text = open(tuned, encoding="utf-8").read()
            for key, value in self.settings.items():
                text, n = re.subn(r"(?m)^global\.%s=.*$" % re.escape(key), lambda m: "global.%s=%s" % (key, value), text)
                if n == 0:
                    text += "global.%s=%s\n" % (key, value)
            open(tuned, "w", encoding="utf-8").write(text)
        if os.path.isdir(final):
            shutil.rmtree(final)
        os.rename(dst, final)

    def check(self):
        """This copy must not point at the normal mods folder."""
        ini = open(os.path.join(self.USER, "config", "qt-config.ini"), encoding="utf-8").read()
        m = re.search(r"(?m)^load_directory=(.*)$", ini)
        assert m and os.path.normcase(os.path.normpath(m.group(1))) == os.path.normcase(os.path.normpath(self.LOAD_ROOT)), m
        return True

    def running(self):
        """Paths of processes started from THIS copy (only they keep its mods folder open)."""
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path } | "
                              "Select-Object -ExpandProperty Path"], capture_output=True, text=True).stdout
        return [p for p in out.splitlines() if os.path.normcase(p).startswith(os.path.normcase(self.DST + os.sep))]


PLAY = Copy(r"C:\Repos\Lichii\dist\build-160-more")
CAPTURE = Copy(r"C:\Repos\Lichii\dist\build-160-more-capture", CAPTURE_SETTINGS, own=("cache",))
DST, USER, LOAD_ROOT, LOAD, EXE = PLAY.DST, PLAY.USER, PLAY.LOAD_ROOT, PLAY.LOAD, PLAY.EXE
setup, sync_config, check, running = PLAY.setup, PLAY.sync_config, PLAY.check, PLAY.running


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    copy = CAPTURE if "capture" in sys.argv[2:] else PLAY
    {"setup": copy.setup, "sync": copy.sync_config, "check": copy.check}[cmd]()
