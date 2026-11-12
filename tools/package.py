"""Make the package that is handed to other people: the mod folders of the full build as
one zip, with a list of SHA-256 sums and the readable listings of the code patches.

    venv\\Scripts\\python.exe tools\\package.py <version> [--name <name>] [--allow-untested] [--max-mb <size>] [--full]
    venv\\Scripts\\python.exe tools\\package.py <version> --install

A package is ONE mod folder (2026-11-12; a download site's moderator asked why there
were eight): the working folders merged the way the emulator sees them together (merge).
--install puts that one folder into the emulator's mods folder in place of the working
folders, for a look in game; a package made after that is checked against it.
--max-mb cuts the package into parts no larger than that (a download site's limit); the
parts unpack into the same folder; with --from-zip the parts are cut from the package
of that version already in dist (the same files and texts). --full: the text says 99 characters (a --full build).

What goes in is build\\<folder> for the folders install-mods.bat installs, plus the
voices folder of mods\\voices.py (build-voice\\mod; it is installed after the others). Unless
--allow-untested is given, each of them must be byte for byte what is installed in the
emulator's mods folder, i.e. what was played. Left out: files of third-party models that
are not ours to pass on (RETRO: Retro Link; nothing in the package can ask for them, the
picker does not offer that character). The package holds data files only (the game's
file types in DATA, .ips and texts); this script refuses anything else. The zip is written with fixed dates
and in a fixed order, so the same build always gives the same zip (and the same sum).
"""
import hashlib
import os
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "mods"))
sys.path.insert(0, HERE)

import build  # noqa: E402
import exepatch  # noqa: E402
import nxfmt  # noqa: E402
import rstb  # noqa: E402

NAME = "OoT-Character-Select"
DIST = os.path.join(ROOT, "dist")
VOICE_BUILD = os.path.join(ROOT, "build-voice", "mod")
# in the order the emulator layers them; a folder not in SOURCES comes from build.BUILD
FOLDERS = ["Character (voice test)", "Character cloth (test)", "Character dress (test)", "Character field swap",
           "Character models", "Characters page", "Header four tabs", "NPC characters"]
SOURCES = {"Character (voice test)": VOICE_BUILD}
RETRO = "_Retro"                    # in the file names of the Retro Link model and its textures
RETRO_FILES = 10
DATA = (".zs", ".ainb", ".ips", ".bwav", ".bphcl")     # .bwav: voice clips, .bphcl: a cloth file
DATE = (2026, 1, 1, 0, 0, 0)        # every entry's date: the zip depends on the contents only

ONE = "OoT Character Select"        # the one mod folder of a package (also in build.RETIRED: the working install removes it)

INSTALL = """{name} {version}
Mod for The Legend of Zelda: Ocarina of Time (Switch 2), game version 1.1.1 only
(title id 0400486024DEC000, build id {build_id}).

What it does
  A "Characters" page in the - menu, left of Key Items: pick one of {characters} characters
  (Link and the game's NPCs) and play as them. A picks, B closes the menu, and the
  character you picked becomes the one you play at once.

  The characters are model swaps on Link's animations, with basic facial animation
  and legs that follow slopes. Saria and young Zelda also have their own voices, and
  young Zelda's dress has experimental cloth physics. Expect visual faults: only some
  of the characters have been tested in play.

Install (emulator)
  1. Close the game.
  2. In the emulator's game list, right-click the game and choose "Open Mod Data
     Location". That opens the folder ...\load\0400486024DEC000.
  3. Copy the folder "{one}" from this zip's "mods" folder into it.
  4. Start the game. The mod shows up in the game's properties under Add-Ons and can
     be switched off there.

Remove
  Delete the folder. The mod adds one saved number to the save file (the picked
  character); without the mod the game ignores it.

Good to know
  - Back up your save first. This is an early test version.
  - Only game version 1.1.1. On another version the code patch does not apply and the
    rest will not work: switch the mod off.
  - It replaces the layout texture file that button prompt mods also replace, so one of
    those and this mod cannot both be on.

What is in here
  Data files only: game files changed or added by the build (.zs, .ainb, .bwav,
  .bphcl) and one code patch for the game itself (.ips, applied to the game's code
  inside the emulator; it is the build's {patches} patches in one file).
  Nothing in this zip runs on your computer: no programs, scripts or installers.
  "patch-listings" has every part of the code patch as readable assembly.
  SHA256SUMS.txt lists the sum of every file. The sources that build these files from
  your own copy of the game, and the sum of this zip, are public:
  https://github.com/snaildev99/OoT-Character-Select
"""

PARTS = """This is part {k} of {n}. The mod needs every part ({words} in all):
{names}
  Unpack them into the same place: they hold the same folder, and Windows merges
  them (choose to merge folders if it asks). Then install as described below.

"""


def ips_records(data):
    """[(file offset, bytes)] of an IPS32 file."""
    assert data[:5] == b"IPS32"
    o, out = 5, []
    while data[o:o + 4] != b"EEOF":
        at, n = struct.unpack_from(">IH", data, o)
        o += 6
        if n == 0:                                  # a run of one byte
            n = struct.unpack_from(">H", data, o)[0]
            out.append((at, data[o + 2:o + 3] * n))
            o += 3
        else:
            out.append((at, data[o:o + n]))
            o += n
    assert o + 4 == len(data)
    return out


def merge(files):
    """The folders of FOLDERS as the one folder ONE, the way the emulator sees them
    together: of a file that several folders have, the first folder's (it layers them in
    name order and the first one wins); the code patches as one patch (no byte is in two
    of them). -> the files of the package, the number of patches merged."""
    out, seen, patches = [], {}, []
    for folder in FOLDERS:
        prefix = "mods/%s/" % folder
        for n, data in files:
            if not n.startswith(prefix):
                continue
            rel = n[len(prefix):]
            if rel.startswith("exefs/"):
                patches.append((folder, data))
            elif rel not in seen:
                seen[rel] = folder
                out.append(("mods/%s/%s" % (ONE, rel), data))
    assert sorted(FOLDERS, key=str.lower) == FOLDERS == sorted(FOLDERS)
    # the size table that wins has every entry of the others
    table = "romfs/" + rstb.PATH
    tables = [(f, rstb.Rstb(nxfmt.unzs(d))) for f in FOLDERS for n, d in files if n == "mods/%s/%s" % (f, table)]
    assert tables and tables[0][0] == seen[table]
    for f, t in tables[1:]:
        assert covers(tables[0][1], t), "%s's size table has entries the first one lacks" % f
    # one patch: every record of every patch, none overlapping; applied, it gives the same bytes
    records = sorted((at, chunk, folder) for folder, data in patches for at, chunk in ips_records(data))
    for (a, ca, fa), (b, _cb, fb) in zip(records, records[1:]):
        assert a + len(ca) <= b, "%s and %s patch the same bytes at %x" % (fa, fb, b)
    one = bytearray(b"IPS32")
    for at, chunk, _f in records:
        for i in range(0, len(chunk), 0xFFFF):
            part = chunk[i:i + 0xFFFF]
            one += struct.pack(">IH", at + i, len(part)) + part
    one += b"EEOF"
    apart, together = {}, {}
    for _folder, data in patches:
        for at, chunk in ips_records(data):
            apart.update((at + i, c) for i, c in enumerate(chunk))
    for at, chunk in ips_records(bytes(one)):
        together.update((at + i, c) for i, c in enumerate(chunk))
    assert apart == together and len(apart) == sum(len(c) for _a, c, _f in records)
    out.append(("mods/%s/exefs/%s.ips" % (ONE, exepatch.BUILD_ID), bytes(one)))
    out += [(n, d) for n, d in files if not n.startswith("mods/")]
    print("one folder: %d files (%d shadowed ones left out), %d code patches in one (%d bytes patched)"
          % (len(seen), sum(n.startswith("mods/") and "/exefs/" not in n for n, _ in files) - len(seen),
             len(patches), len(apart)))
    return out, len(patches)


def covers(first, other):
    """Every entry of `other` is in `first`, at least as large (the game refuses a file
    larger than its entry; the voices folder raises the entry of the list it replaces)."""
    return all(first.crc.get(h, -1) >= size for h, size in other.crc.items()) and \
        all(first.names.get(n, -1) >= size for n, size in other.names.items())



def sha256(data):
    return hashlib.sha256(data).hexdigest()


def collect(check_installed=True):
    """[(path in the zip, bytes)] and the names left out."""
    out, left_out = [], []
    for folder in FOLDERS:
        base = os.path.join(SOURCES.get(folder, build.BUILD), folder)
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
        listing = os.path.join(SOURCES.get(folder, build.BUILD), folder + ".listing.txt")
        if os.path.exists(listing):
            out.append(("patch-listings/%s.txt" % folder, open(listing, "rb").read()))
    assert len(left_out) == RETRO_FILES and not any(RETRO in name for name, _ in out), left_out
    # every code patch has its listing, and the patches are for the game build they name
    patches = [n for n, _ in out if n.endswith(".ips")]
    assert all(n.endswith("/exefs/%s.ips" % exepatch.BUILD_ID) for n in patches)
    assert sorted(n.split("/")[1] for n in patches) == sorted(n.split("/")[1][:-4] for n, _ in out
                                                            if n.startswith("patch-listings/")), patches
    return out, left_out


def write_zip(path, top, files):
    """One zip of [(name, bytes)] plus its SHA256SUMS.txt, read back; -> its sum."""
    files = sorted(files)
    sums = "".join("%s  %s\n" % (sha256(data), n) for n, data in files)
    files.append(("SHA256SUMS.txt", sums.encode("utf-8")))
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files:
            info = zipfile.ZipInfo("%s/%s" % (top, n), DATE)
            info.external_attr = 0o644 << 16
            # the game's files are compressed already
            info.compress_type = zipfile.ZIP_STORED if n.endswith(".zs") else zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    # read back: every file as listed
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert len(names) == len(files) and all(n.lower().endswith(DATA + (".txt",)) for n in names)
        for line in z.read("%s/SHA256SUMS.txt" % top).decode().splitlines():
            digest, n = line.split("  ", 1)
            assert sha256(z.read("%s/%s" % (top, n))) == digest, n
    total = sha256(open(path, "rb").read())
    with open(path + ".sha256", "w") as f:
        f.write("%s  %s\n" % (total, os.path.basename(path)))
    print("%s: %d files, %.1f MB" % (path, len(files), os.path.getsize(path) / 1e6))
    print("  sha256 %s" % total)
    return total


def split(files, limit):
    """The mod files in order, cut into as few parts as stay under `limit` bytes each (a
    file's own size counts in full: most are stored, not deflated). A folder may be cut;
    the parts unpack into the same folders."""
    parts, size = [[]], 0
    for n, data in files:
        if size + len(data) > limit and parts[-1]:
            parts.append([])
            size = 0
        parts[-1].append((n, data))
        size += len(data)
    return parts


def install_one(files):
    """Put the one folder into the emulator's mods folder in place of the working folders
    (for the look in game before a release; `mods\\build.py --install`, then
    `mods\\voices.py --install`, bring the working folders back and remove this one)."""
    if build.emulator_running():
        print("The emulator is running (%s). Close it first." % ", ".join(build.emulator_running()))
        sys.exit(1)
    for folder in FOLDERS + [ONE]:
        dst = os.path.join(build.EDEN_LOAD, folder)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
            print("removed", dst)
    for n, data in files:
        if n.startswith("mods/"):
            dst = os.path.join(build.EDEN_LOAD, *n.split("/")[1:])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
    print("installed ->", os.path.join(build.EDEN_LOAD, ONE))


def main():
    args =[a for a in sys.argv[1:] if not a.startswith("--")]
    name, max_mb = NAME, None
    if "--name" in sys.argv:
        name = sys.argv[sys.argv.index("--name") + 1]
        args.remove(name)
    if "--max-mb" in sys.argv:
        max_mb = sys.argv[sys.argv.index("--max-mb") + 1]
        args.remove(max_mb)
        max_mb = float(max_mb)
    assert len(args) == 1, __doc__
    version = args[0]
    if "--from-zip" in sys.argv:
        # cut a package this script made earlier into parts (same files, same texts)
        assert max_mb is not None
        src = os.path.join(DIST, "%s-%s.zip" % (name, version))
        top = "%s-%s" % (name, version)
        with zipfile.ZipFile(src) as z:
            files = [(n[len(top) + 1:], z.read(n)) for n in z.namelist()]
        text = dict(files)["INSTALL.txt"].decode("utf-8").replace("\r\n", "\n")
        files = [(n, d) for n, d in files if n not in ("INSTALL.txt", "SHA256SUMS.txt")]
        assert all(n.startswith(("mods/%s/" % ONE, "patch-listings/")) for n, _d in files)
        write_parts(top, files, text, max_mb)
        return
    # what was played: the folders as the working install has them, or the one folder
    # (--install of an earlier run); the one folder is then compared after the merge
    as_one = os.path.isdir(os.path.join(build.EDEN_LOAD, ONE))
    assert not (as_one and any(os.path.isdir(os.path.join(build.EDEN_LOAD, f)) for f in FOLDERS)), \
        "both the one folder and the working folders are installed"
    untested = "--allow-untested" in sys.argv
    files, left_out = collect(not untested and not as_one)
    files, n_patches = merge(files)
    if as_one and not untested:
        there = os.path.join(build.EDEN_LOAD, ONE)
        installed = sorted(os.path.relpath(os.path.join(r, n), there).replace(os.sep, "/")
                           for r, _d, names in os.walk(there) for n in names)
        mine = {n[len("mods/%s/" % ONE):]: d for n, d in files if n.startswith("mods/")}
        assert installed == sorted(mine), "not the files that are installed in " + ONE
        for rel in installed:
            assert open(os.path.join(there, rel), "rb").read() == mine[rel], rel + " differs from the installed file"
        print("the one folder is byte for byte the installed one")
    if "--install" in sys.argv:
        install_one(files)
        return
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    text = INSTALL.format(name=name, version=version, build_id=exepatch.BUILD_ID, one=ONE,
                          characters=99 if "--full" in sys.argv else len(build.PICKER_ROSTER),
                          patches=words[n_patches])
    top = "%s-%s" % (name, version)
    os.makedirs(DIST, exist_ok=True)
    print("left out (%d): %s" % (len(left_out), ", ".join(os.path.basename(n) for n in left_out)))
    if max_mb is None:
        files.append(("INSTALL.txt", text.replace("\n", "\r\n").encode("utf-8")))
        write_zip(os.path.join(DIST, top + ".zip"), top, files)
        return
    write_parts(top, files, text, max_mb)


def write_parts(top, files, text, max_mb):
    """The package in parts: the texts and the listings go into every part, the mod files are cut."""
    words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    common = [(n, d) for n, d in files if not n.startswith("mods/")]
    budget = int(max_mb * 1e6) - sum(len(d) for _n, d in common) - (1 << 20)     # a margin for the zip's own records
    parts = split(sorted((n, d) for n, d in files if n.startswith("mods/")), budget)
    for k, part in enumerate(parts):
        note = PARTS.format(k=k + 1, n=len(parts), words=words[len(parts)],
                            names="\n".join("  %s-part%d.zip" % (top, i + 1) for i in range(len(parts))))
        mine = text.replace("What it does\n", note + "What it does\n", 1)
        assert mine != text
        path = os.path.join(DIST, "%s-part%d.zip" % (top, k + 1))
        write_zip(path, top, common + part + [("INSTALL.txt", mine.replace("\n", "\r\n").encode("utf-8"))])
        assert os.path.getsize(path) <= max_mb * 1e6, path
    # every mod file is in exactly one part
    got = sorted(n for part in parts for n, _d in part)
    assert got == sorted(n for n, _d in files if n.startswith("mods/"))


if __name__ == "__main__":
    main()
