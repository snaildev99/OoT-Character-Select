"""The field swap session's own emulator: a portable copy of Lichii build 160 with its own mods
folder (the scheme of tools\\moreenv.py; that file is not touched). Until 2026-11-14 this was a
Fritchii copy (C:\\Repos\\Lichii\\dist\\Fritchii-field, left as it was); CLAUDE.md, "For every
session": use build 160 and Lichii's data from now on.

    venv\\Scripts\\python.exe tools\\fieldenv.py setup    create / repair the copy (safe to rerun)
    venv\\Scripts\\python.exe tools\\fieldenv.py sync     refresh its config from the normal one

Layout (the emulator takes <exe dir>\\user over %APPDATA%\\lichii when that folder exists):
    C:\\Repos\\Lichii\\dist\\build-160-field\\      hard links to build 160 (dist\\build-160-pgo)
        user\\load\\0400486024DEC000\\              the mods (mods\\field_swap.py ... --install)
        user\\config\\                              copy of %APPDATA%\\lichii\\config, load_directory
                                                   pointed at user\\load (synced on every install)
        user\\keys, cache, ...                      junctions to %APPDATA%\\lichii (shader cache shared)
Saves, SD card, dumps: the copied config pins them to %APPDATA%\\lichii, so progress is SHARED
with the normal Lichii install. The launcher (PLAY-debug-field.bat) puts the debug server on
127.0.0.1:6549 (normal 6545, UI 6546, NPC 6547, more characters 6548). Don't run two of the
emulators at once.
"""
import os
import re
import shutil
import subprocess
import sys

SRC = r"C:\Repos\Lichii\dist\build-160-pgo"
DST = r"C:\Repos\Lichii\dist\build-160-field"
DATA = os.path.join(os.environ["APPDATA"], "lichii")
USER = os.path.join(DST, "user")
LOAD_ROOT = os.path.join(USER, "load")
LOAD = os.path.join(LOAD_ROOT, "0400486024DEC000")
SHARED = ["keys", "cache", "amiibo", "icons", "lossless", "post_presets", "post_shaders", "streamline", "play_time",
          "screenshots", "fidelityfx"]
EXE = os.path.join(DST, "lichii.exe")
LOG = os.path.join(USER, "log", "eden_log.txt")
DEBUG_PORT = 6549


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


def setup():
    assert os.path.isfile(os.path.join(SRC, "lichii.exe")), "build 160 not found at " + SRC
    assert not os.path.isdir(os.path.join(SRC, "user")), "the source build has a portable user folder of its own"
    _link_tree(SRC, DST)
    os.makedirs(USER, exist_ok=True)
    for d in SHARED:
        if os.path.isdir(os.path.join(DATA, d)):
            _junction(os.path.join(USER, d), os.path.join(DATA, d))
    os.makedirs(LOAD, exist_ok=True)
    sync_config()
    print("Field swap emulator ready:", EXE)


def sync_config():
    """Copy the normal config, with the mods folder moved to this copy's own. Made beside the
    real folder and swapped in at the end: a config that was only copied would point this copy
    at the NORMAL mods folder."""
    src, final = os.path.join(DATA, "config"), os.path.join(USER, "config")
    dst = final + ".new"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.bak*"))
    ini = os.path.join(dst, "qt-config.ini")
    text = open(ini, encoding="utf-8").read()
    load = LOAD_ROOT.replace("\\", "/")
    text, n = re.subn(r"(?m)^load_directory=.*$", lambda m: "load_directory=" + load, text)
    assert n == 1, "load_directory not found in qt-config.ini"
    text, n = re.subn(r"(?m)^load_directory\\default=.*$", r"load_directory\\default=false", text)
    assert n == 1
    for key in ("nand_directory", "sdmc_directory"):    # saves must stay the shared ones: pinned, not defaults
        m = re.search(r"(?m)^%s=(.*)$" % key, text)
        assert m and os.path.normcase(os.path.normpath(m.group(1))).startswith(os.path.normcase(DATA)), key
        assert re.search(r"(?m)^%s\\default=false$" % key, text), key
    # every mod of this copy's own folder switched on
    text = re.sub(r"(?m)^(\d+\\disabled\\size)=\d+$", r"\1=0", text)
    open(ini, "w", encoding="utf-8").write(text)
    if os.path.isdir(final):
        shutil.rmtree(final)
    os.rename(dst, final)


def check():
    """This copy must not point at the normal mods folder."""
    ini = open(os.path.join(USER, "config", "qt-config.ini"), encoding="utf-8").read()
    m = re.search(r"(?m)^load_directory=(.*)$", ini)
    assert m and os.path.normcase(os.path.normpath(m.group(1))) == os.path.normcase(os.path.normpath(LOAD_ROOT)), m
    assert all(v == "0" for v in re.findall(r"(?m)^\d+\\disabled\\size=(\d+)$", ini)), "a mod is switched off"
    return True


def running():
    """Paths of processes started from THIS copy (only they keep its mods folder open)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path } | "
                          "Select-Object -ExpandProperty Path"], capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if os.path.normcase(p).startswith(os.path.normcase(DST + os.sep))]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    {"setup": setup, "sync": sync_config, "check": check}[cmd]()
