"""The UI sandbox's own emulator: a portable copy of the current Fritchii with its own mods folder.

    venv\\Scripts\\python.exe tools\\uienv.py setup    create / repair the copy (safe to rerun)
    venv\\Scripts\\python.exe tools\\uienv.py sync     refresh its config from the normal one

Layout (eden picks <exe dir>\\user over %APPDATA%\\eden when that folder exists):
    C:\\Repos\\Lichii\\dist\\Fritchii-ui\\          hard links to the current Fritchii (dist\\Fritchii)
        user\\load\\0400486024DEC000\\                the sandbox's mods (mods\\ui_sandbox.py --install)
        user\\config\\                                copy of %APPDATA%\\eden\\config, load_directory
                                                     pointed at user\\load (synced on every install)
        user\\keys, cache, ...                        junctions to %APPDATA%\\eden (shader caches shared)
Saves, SD card, dumps: the copied config pins them to %APPDATA%\\eden, so progress is shared.
The NX2 switches come from %APPDATA%\\eden\\nx2_switches.txt as usual; the launcher
(PLAY-debug-ui.bat) moves the debug server to 127.0.0.1:6546 so it never answers for the
normal setup's 6545. Don't run both emulators at once (shared shader caches and saves).
"""
import os
import re
import shutil
import subprocess
import sys

SRC = r"C:\Repos\Lichii\dist\Fritchii"   # the current Fritchii release
DST = r"C:\Repos\Lichii\dist\Fritchii-ui"
EDEN = os.path.join(os.environ["APPDATA"], "eden")
USER = os.path.join(DST, "user")
LOAD_ROOT = os.path.join(USER, "load")
LOAD = os.path.join(LOAD_ROOT, "0400486024DEC000")
SHARED = ["keys", "cache", "amiibo", "icons", "lossless", "post_presets", "post_shaders", "streamline", "play_time",
          "screenshots"]
EXE = os.path.join(DST, "lichii.exe")


def _link_tree(src, dst):
    for root, dirs, files in os.walk(src):
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
    assert os.path.isfile(os.path.join(SRC, "lichii.exe")), "Fritchii not found at " + SRC
    _link_tree(SRC, DST)
    os.makedirs(USER, exist_ok=True)
    for d in SHARED:
        if os.path.isdir(os.path.join(EDEN, d)):
            _junction(os.path.join(USER, d), os.path.join(EDEN, d))
    os.makedirs(LOAD, exist_ok=True)
    sync_config()
    print("UI sandbox emulator ready:", EXE)


def sync_config():
    """Copy the normal config, with the mods folder moved to the sandbox's own."""
    src, dst = os.path.join(EDEN, "config"), os.path.join(USER, "config")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.bak*"))
    ini = os.path.join(dst, "qt-config.ini")
    text = open(ini, encoding="utf-8").read()
    load = LOAD_ROOT.replace("\\", "/")
    text, n = re.subn(r"(?m)^load_directory=.*$", lambda m: "load_directory=" + load, text)
    assert n == 1, "load_directory not found in qt-config.ini"
    text, n = re.subn(r"(?m)^load_directory\\default=.*$", r"load_directory\\default=false", text)
    assert n == 1
    # Every mod in the sandbox's own folder is on, whatever is switched off in the normal
    # emulator's Add-Ons list (its entries name the normal folder's mods).
    text = re.sub(r"(?m)^(\d+\\disabled\\size)=\d+$", r"\1=0", text)
    open(ini, "w", encoding="utf-8").write(text)


def check():
    """The sandbox setup must not point at the normal mods folder, and none of its own
    mods may be switched off."""
    ini = open(os.path.join(USER, "config", "qt-config.ini"), encoding="utf-8").read()
    m = re.search(r"(?m)^load_directory=(.*)$", ini)
    assert m and os.path.normcase(os.path.normpath(m.group(1))) == os.path.normcase(os.path.normpath(LOAD_ROOT)), m
    assert all(v == "0" for v in re.findall(r"(?m)^\d+\\disabled\\size=(\d+)$", ini)), "a mod is switched off"
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    {"setup": setup, "sync": sync_config, "check": check}[cmd]()
