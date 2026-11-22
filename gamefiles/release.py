"""The release zip without the game's files: our files, the recipes and the player's tool.

    venv\\Scripts\\python.exe -I gamefiles\\release.py <version> <release mod folder>
        [--work G:\\OoT-Modding-work\\gamefiles] [--skip-make]

<release mod folder> is the one folder tools\\package.py makes ("OoT Character Select").
Steps (everything large goes under --work, on G:, since C is nearly full):
  1. gamefiles\\make.py: the folder -> <work>\\release-<version>\\ (our files + recipes)
  2. the Windows tool, build-from-your-game.exe (PyInstaller in G:\\OoT-Modding-work\\venv-exe)
  3. the round trip: the .exe makes every file from this PC's romfs into a fresh copy, and
     every file is compared with the release folder (decompressed): the same bytes, or for
     an ASTC texture within 0.5 of a step on average, decoded (the encoder's block choices). Anything else stops the release.
  4. INSTALL.txt and the zip: <work>\\dist\\OoT-Character-Select-<version>.zip
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(ROOT, "tools")]

WORK_ROOT = r"G:\OoT-Modding-work"
VENV_EXE = os.path.join(WORK_ROOT, "venv-exe")
EXE_NAME = "build-from-your-game"
EXE_SOURCES = [os.path.join(HERE, f) for f in ("player.py", "delta.py", "colours.py")] + \
              [os.path.join(ROOT, "tools", f) for f in ("texrecolor.py", "bntx.py", "bntxwrite.py")]

INSTALL = """OoT-Character-Select {version}
Mod for The Legend of Zelda: Ocarina of Time (Switch 2), game version 1.1.1 only
(title id 0400486024DEC000).

This download does not contain the game's files. The characters' models, their colour
textures and the menu are made on your PC from YOUR copy of the game, by the tool in
"build-from-your-game". That takes a minute or two, once.

Install (emulator, Windows)
  1. Dump the game's RomFS: in the emulator's game list, right-click the game, choose
     "Dump RomFS" and, if it asks, "Full". (Ryujinx: right-click, Extract Data, RomFS,
     and note the folder you pick.) The game must be updated to 1.1.1 first.
  2. Open the folder "build-from-your-game" and double-click build-from-your-game.exe.
     It finds the dump of Eden, Lichii, Citron, Sudachi, Suyu, Torzu and yuzu by itself;
     otherwise it asks you to drag the RomFS folder onto its window. It fills the folder
     "OoT Character Select" next to it and says "Done" when the mod is ready.
  3. In the emulator, right-click the game and choose "Open Mod Data Location". Copy the
     folder "OoT Character Select" (from this zip's "mods" folder, after step 2) into it.
  4. Start the game.

Linux / Steam Deck / macOS
  python3 -m pip install numpy pyzstd astc-encoder-py texture2ddecoder
  python3 build-from-your-game/player.py --romfs <the dumped RomFS folder>
  then step 3.

Squadala / Hookshot kits
  The kit has the game's RomFS in hookshot\\game\\romfs:
  <kit python> build-from-your-game/player.py --romfs <kit>\\hookshot\\game\\romfs --mod <the mod folder in the kit>

If the tool says a file "is not the one of game version 1.1.1", your dump is of another
version (update the game, dump again). Running the tool again is safe.

Remove
  Delete the folder. The mod adds one saved number to the save file (the picked
  character); without the mod the game ignores it.
"""


def run(cmd, **kw):
    print("> " + " ".join('"%s"' % c if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def build_exe(work):
    """The .exe, built again when one of its sources changed. -> its path"""
    build = os.path.join(work, "exe-build")
    stamp = hashlib.sha1(b"".join(open(p, "rb").read() for p in EXE_SOURCES)).hexdigest()
    exe = os.path.join(build, "dist", EXE_NAME + ".exe")
    stamp_file = os.path.join(build, "sources.sha1")
    if os.path.exists(exe) and os.path.exists(stamp_file) and open(stamp_file).read() == stamp:
        return exe
    if os.path.exists(build):
        shutil.rmtree(build)
    os.makedirs(build)
    for p in EXE_SOURCES:
        shutil.copy2(p, build)
    py = os.path.join(VENV_EXE, "Scripts", "python.exe")
    if not os.path.exists(py):
        sys.exit("no %s: make it with python -m venv, then pip install pyinstaller numpy pyzstd "
                 "astc-encoder-py==0.1.12 texture2ddecoder==1.0.6" % VENV_EXE)
    run([py, "-m", "PyInstaller", "--onefile", "--name", EXE_NAME, "--log-level", "WARN",
         "--hidden-import", "delta", "--hidden-import", "colours", "--hidden-import", "texrecolor",
         "--hidden-import", "bntx", "--hidden-import", "bntxwrite", "--hidden-import", "texture2ddecoder",
         "--collect-all", "astc_encoder", "--collect-all", "archspec", "player.py"], cwd=build)
    open(stamp_file, "w").write(stamp)
    return exe


def compare(made, release, only):
    """[(path, what is wrong)] of the files `only` (romfs paths) between two mod folders."""
    import numpy as np
    import nxfmt
    import texrecolor
    bad, astc = [], 0
    for rel in only:
        a_path = os.path.join(made, "romfs", *rel.split("/"))
        if not os.path.exists(a_path):
            bad.append((rel, "not made"))
            continue
        a, b = nxfmt.read(a_path), nxfmt.read(os.path.join(release, "romfs", *rel.split("/")))
        if a == b:
            continue
        if rel.startswith("Tex/"):
            da, db = texrecolor.decoded(a).astype(np.int16), texrecolor.decoded(b).astype(np.int16)
            if da.shape == db.shape and float(np.abs(da - db).mean()) <= 0.5:     # (make.ASTC_MEAN: the encoder's block choices)
                astc += 1
                continue
        bad.append((rel, "differs"))
    return bad, astc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("release")
    ap.add_argument("--work", default=os.path.join(WORK_ROOT, "gamefiles"))
    ap.add_argument("--romfs", default=os.environ.get("OOT_ROMFS") or os.path.expanduser("~/Games/Hookshot/romfs"))
    ap.add_argument("--skip-make", action="store_true", help="the recipes in <work>\\release-<version> as they are")
    args = ap.parse_args()
    import json
    t0 = time.time()
    release = os.path.abspath(args.release)
    folder = os.path.basename(os.path.normpath(release))
    out = os.path.join(args.work, "release-" + args.version)
    if not args.skip_make:
        run([sys.executable, "-I", os.path.join(HERE, "make.py"), release, out, "--romfs", args.romfs, "--work", args.work])
    tool = os.path.join(out, "build-from-your-game")
    exe = build_exe(WORK_ROOT)
    shutil.copy2(exe, tool)

    # ---- the round trip, with the .exe as players run it
    trip = os.path.join(args.work, "roundtrip-" + args.version)
    if os.path.exists(trip):
        shutil.rmtree(trip)
    shutil.copytree(os.path.join(out, folder), os.path.join(trip, folder))
    run([os.path.join(tool, EXE_NAME + ".exe"), "--romfs", args.romfs, "--mod", os.path.join(trip, folder)],
        stdin=subprocess.DEVNULL)
    recipes = json.load(open(os.path.join(tool, "recipes.json"), encoding="utf-8"))
    bad, astc = compare(os.path.join(trip, folder), release, [e["path"] for e in recipes["files"]])
    have = {os.path.relpath(os.path.join(d, f), os.path.join(trip, folder)).replace("\\", "/")
            for d, _, fs in os.walk(os.path.join(trip, folder)) for f in fs}
    want = {os.path.relpath(os.path.join(d, f), release).replace("\\", "/") for d, _, fs in os.walk(release) for f in fs}
    missing = sorted(want - have)
    print("round trip: %d files made, %d of them ASTC textures within the encoder's rounding, %d wrong, %d of the release's files missing"
          % (len(recipes["files"]), astc, len(bad), len(missing)))
    for p, why in bad[:20]:
        print("  %s: %s" % (p, why))
    for p in missing[:20]:
        print("  missing: " + p)
    if bad or missing:
        sys.exit("the round trip failed: no zip")

    # ---- the zip
    dist = os.path.join(args.work, "dist")
    os.makedirs(dist, exist_ok=True)
    name = "OoT-Character-Select-" + args.version
    zpath = os.path.join(dist, name + ".zip")
    tops = [(os.path.join(out, folder), name + "/mods/" + folder), (tool, name + "/build-from-your-game")]
    listings = os.path.join(os.path.dirname(os.path.dirname(release)), "patch-listings")      # (beside the package's mods folder)
    if os.path.isdir(listings):
        tops.append((listings, name + "/patch-listings"))
    members = [(os.path.join(d, f), arc + "/" + os.path.relpath(os.path.join(d, f), top).replace("\\", "/"))
               for top, arc in tops for d, _, fs in os.walk(top) for f in sorted(fs)]
    install = INSTALL.format(version=args.version).replace("\n", "\r\n").encode("utf-8")
    sums = [(hashlib.sha256(install).hexdigest(), name + "/INSTALL.txt")]
    sums += [(hashlib.sha256(open(p, "rb").read()).hexdigest(), a) for p, a in members]
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(name + "/INSTALL.txt", install)
        z.writestr(name + "/SHA256SUMS.txt", "".join("%s  %s\r\n" % (h, a[len(name) + 1:]) for h, a in sorted(sums, key=lambda x: x[1])))
        for p, a in members:
            z.write(p, a)
    sha = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    open(zpath + ".sha256", "w").write("%s  %s\n" % (sha, os.path.basename(zpath)))
    print("\n%s: %.1f MB, sha256 %s (%.0f s)" % (zpath, os.path.getsize(zpath) / 1e6, sha, time.time() - t0))


if __name__ == "__main__":
    main()
