"""Make the package that is handed to other people: the mod folders of the full build as
one zip, with a list of SHA-256 sums and the readable listings of the code patches.

    venv\\Scripts\\python.exe tools\\package.py <version> [--name <name>] [--allow-untested]

What goes in is build\\<folder> for the folders install-mods.bat installs. Unless
--allow-untested is given, each of them must be byte for byte what is installed in the
emulator's mods folder, i.e. what was played. Left out: files of third-party models that
are not ours to pass on (RETRO: Retro Link; nothing in the package can ask for them, the
picker does not offer that character). The package holds data files only (.zs, .ainb,
.ips and texts); this script refuses anything else. The zip is written with fixed dates
and in a fixed order, so the same build always gives the same zip (and the same sum).
"""
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "mods"))
sys.path.insert(0, HERE)

import build  # noqa: E402
import exepatch  # noqa: E402

NAME = "OoT-Character-Select"
DIST = os.path.join(ROOT, "dist")
FOLDERS = ["Character field swap", "Character models", "Characters page", "Header four tabs", "NPC characters"]
RETRO = "_Retro"                    # in the file names of the Retro Link model and its textures
RETRO_FILES = 10
DATA = (".zs", ".ainb", ".ips")
DATE = (2026, 1, 1, 0, 0, 0)        # every entry's date: the zip depends on the contents only

INSTALL = """{name} {version}
Mod for The Legend of Zelda: Ocarina of Time (Switch 2), game version 1.1.1 only
(title id 0400486024DEC000, build id {build_id}).

What it does
  A "Characters" page in the - menu, left of Key Items: pick one of 99 characters
  (Link and the game's NPCs) and play as them. A picks, B closes the menu, and the
  character you picked becomes the one you play at once.

Install (emulator)
  1. Close the game.
  2. In the emulator's game list, right-click the game and choose "Open Mod Data
     Location". That opens the folder ...\\load\\0400486024DEC000.
  3. Copy the five folders from this zip's "mods" folder into it, names unchanged:
{folders}
     (The names matter: the emulator layers mod folders in name order.)
  4. Start the game. The folders show up in the game's properties under Add-Ons and
     can be switched off there one by one.

Remove
  Delete the five folders. The mod adds one saved number to the save file (the picked
  character); without the mod the game ignores it.

Good to know
  - Back up your save first. This is a first test version.
  - Only game version 1.1.1. On another version the code patches do not apply and the
    rest will not work: switch the folders off.
  - It replaces the layout texture file that button prompt mods also replace, so one of
    those and this mod cannot both be on.
  - "Character field swap" alone can be switched off: the picked character then shows
    in the field after save, return to title and Continue.

What is in here
  Data files only: game files changed by the build (.zs, .ainb) and four small code
  patches for the game itself (.ips, applied to the game's code inside the emulator).
  Nothing in this zip runs on your computer: no programs, scripts or installers.
  "patch-listings" has every code patch as readable assembly.
  SHA256SUMS.txt lists the sum of every file. The sources that build these files from
  your own copy of the game, and the sum of this zip, are public:
  https://github.com/snaildev99/OoT-Character-Select
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def collect(check_installed=True):
    """[(path in the zip, bytes)] and the names left out."""
    out, left_out = [], []
    for folder in FOLDERS:
        base = os.path.join(build.BUILD, folder)
        assert os.path.isdir(base), base
        files = sorted(os.path.relpath(os.path.join(r, n), base) for r, _d, names in os.walk(base) for n in names)
        if check_installed:
            there = os.path.join(build.EDEN_LOAD, folder)
            installed = sorted(os.path.relpath(os.path.join(r, n), there) for r, _d, names in os.walk(there) for n in names)
            assert files == installed, "%s: not the files that are installed" % folder
        for rel in files:
            data = open(os.path.join(base, rel), "rb").read()
            if check_installed:
                assert data == open(os.path.join(build.EDEN_LOAD, folder, rel), "rb").read(), \
                    "%s\\%s differs from the installed file: install and test first" % (folder, rel)
            top = rel.split(os.sep)[0]
            assert top in ("romfs", "exefs") and rel.lower().endswith(DATA), "not a data file: " + rel
            assert (top == "exefs") == rel.endswith(".ips")
            if RETRO in rel:
                left_out.append(folder + "/" + rel.replace(os.sep, "/"))
                continue
            out.append(("mods/%s/%s" % (folder, rel.replace(os.sep, "/")), data))
        listing = os.path.join(build.BUILD, folder + ".listing.txt")
        if os.path.exists(listing):
            out.append(("patch-listings/%s.txt" % folder, open(listing, "rb").read()))
    assert len(left_out) == RETRO_FILES and not any(RETRO in name for name, _ in out), left_out
    # every code patch has its listing, and the patches are for the game build they name
    patches = [n for n, _ in out if n.endswith(".ips")]
    assert all(n.endswith("/exefs/%s.ips" % exepatch.BUILD_ID) for n in patches)
    assert sorted(n.split("/")[1] for n in patches) == sorted(n.split("/")[1][:-4] for n, _ in out
                                                            if n.startswith("patch-listings/")), patches
    return out, left_out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name = NAME
    if "--name" in sys.argv:
        name = sys.argv[sys.argv.index("--name") + 1]
        args.remove(name)
    assert len(args) == 1, __doc__
    version = args[0]
    files, left_out = collect("--allow-untested" not in sys.argv)
    text = INSTALL.format(name=name, version=version, build_id=exepatch.BUILD_ID,
                          folders="\n".join("       " + f for f in FOLDERS))
    files.append(("INSTALL.txt", text.replace("\n", "\r\n").encode("utf-8")))
    files.sort()
    sums = "".join("%s  %s\n" % (sha256(data), n) for n, data in files)
    files.append(("SHA256SUMS.txt", sums.encode("utf-8")))
    os.makedirs(DIST, exist_ok=True)
    path = os.path.join(DIST, "%s-%s.zip" % (name, version))
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files:
            info = zipfile.ZipInfo("%s-%s/%s" % (name, version, n), DATE)
            info.external_attr = 0o644 << 16
            # the game's files are compressed already
            info.compress_type = zipfile.ZIP_STORED if n.endswith(".zs") else zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    # read back: every file as listed
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert len(names) == len(files) and all(n.lower().endswith(DATA + (".txt",)) for n in names)
        for line in z.read("%s-%s/SHA256SUMS.txt" % (name, version)).decode().splitlines():
            digest, n = line.split("  ", 1)
            assert sha256(z.read("%s-%s/%s" % (name, version, n))) == digest, n
    total = sha256(open(path, "rb").read())
    with open(path + ".sha256", "w") as f:
        f.write("%s  %s\n" % (total, os.path.basename(path)))
    print("%s: %d files, %.1f MB" % (path, len(files), os.path.getsize(path) / 1e6))
    print("  left out (%d): %s" % (len(left_out), ", ".join(os.path.basename(n) for n in left_out)))
    print("  sha256 %s" % total)


if __name__ == "__main__":
    main()
