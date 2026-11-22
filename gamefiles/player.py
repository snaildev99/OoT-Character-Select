r"""OoT Character Select: make the mod's files from your copy of the game.

This tool makes the mod's character models, colour textures and menus from your own copy
of the game, using the recipes beside it, and writes them into the mod folder.

    python player.py [--romfs <the game's extracted RomFS folder>] [--mod <mod folder>]

--romfs  the game's RomFS (Ocarina of Time for Switch 2, version 1.1.1), as an emulator's
         "Dump RomFS" writes it, or a Squadala / Hookshot kit's game\romfs folder. Without
         it the tool looks in the emulators' dump folders (Eden, Lichii, Citron, Sudachi,
         Suyu, Torzu, yuzu: <user folder>\dump\0400486024DEC000\romfs).
--mod    the "OoT Character Select" mod folder to fill (default: the one in "mods" next to
         this folder, as the release zip has it).
--jobs   how many files at a time (default: all processor cores).
--exclude  a regular expression of RomFS paths not to make (a kit that installs part of the mod).

Needs Python 3.9+ with numpy and pyzstd or zstandard. Every file is checked: a file made
from a different game version is refused. Running it again makes the same files.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import struct
import sys
import time

if getattr(sys, "frozen", False):          # the Windows .exe: the recipes are beside it, the code inside
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path[:0] = [HERE, os.path.join(HERE, "lib")]

# ---------------------------------------------------------------- zstd, either library

try:
    import pyzstd as _pz
    _zs = None
except ImportError:
    _pz = None
    import zstandard as _zs


def _frame_dict_id(raw):
    if raw[:4] != b"\x28\xb5\x2f\xfd":
        return None
    fhd = raw[4]
    p = 5 + (0 if fhd & 0x20 else 1)
    n = (0, 1, 2, 4)[fhd & 3]
    return int.from_bytes(raw[p:p + n], "little") if n else 0


class Zstd:
    def __init__(self, romfs):
        raw = self.decompress(open(os.path.join(romfs, "Pack", "ZsDic.pack.zs"), "rb").read(), None)
        self.dicts = {}
        for blob in sarc_files(raw).values():
            if _pz:
                d = _pz.ZstdDict(blob)
                self.dicts[d.dict_id] = d
            else:
                d = _zs.ZstdCompressionDict(blob)
                self.dicts[d.dict_id()] = d

    @staticmethod
    def decompress(raw, d):
        if _pz:
            return _pz.decompress(raw, zstd_dict=d) if d is not None else _pz.decompress(raw)
        dctx = _zs.ZstdDecompressor(dict_data=d) if d is not None else _zs.ZstdDecompressor()
        try:
            return dctx.decompress(raw)             # one shot: the frame says its size (the game's do)
        except _zs.ZstdError:
            import io
            return dctx.stream_reader(io.BytesIO(raw)).read()

    def read(self, path):
        raw = open(path, "rb").read()
        did = _frame_dict_id(raw)
        if did is None:
            return raw
        return self.decompress(raw, self.dicts[did] if did else None)

    def compress(self, data, did, level):
        d = self.dicts[did] if did else None
        if _pz:
            return _pz.compress(data, level, zstd_dict=d) if d is not None else _pz.compress(data, level)
        return (_zs.ZstdCompressor(level=level, dict_data=d) if d is not None else _zs.ZstdCompressor(level=level)).compress(data)


def sarc_files(data):
    assert data[:4] == b"SARC"
    e = "<" if data[6:8] == b"\xff\xfe" else ">"
    data_off = struct.unpack_from(e + "I", data, 0x0C)[0]
    count = struct.unpack_from(e + "H", data, 0x14 + 6)[0]
    sfnt = 0x14 + 12 + count * 16
    out = {}
    for i in range(count):
        _h, attr, s, en = struct.unpack_from(e + "IIII", data, 0x14 + 12 + i * 16)
        noff = sfnt + 8 + (attr & 0xFFFF) * 4
        out[data[noff:data.index(b"\0", noff)].decode()] = data[data_off + s:data_off + en]
    return out


# ---------------------------------------------------------------- one file

_state = {}


def _init(romfs, patches, level):
    _state.update(romfs=romfs, zs=Zstd(romfs), patches=patches, level=level, cache={})


def _source(rel, size, sha):
    cache = _state["cache"]
    if rel not in cache:
        path = os.path.join(_state["romfs"], *rel.split("/"))
        if not os.path.exists(path):
            raise RuntimeError("your game has no %s (is --romfs the game's RomFS folder?)" % rel)
        data = _state["zs"].read(path)
        if len(data) != size or hashlib.sha1(data).hexdigest() != sha:
            raise RuntimeError("%s is not the one of game version 1.1.1" % rel)
        if len(cache) > 8:
            cache.clear()
        cache[rel] = data
    return cache[rel]


def _make(job):
    e, out_root = job
    try:
        if e["kind"] == "patch":
            import delta
            with open(_state["patches"], "rb") as fh:
                fh.seek(e["at"])
                z = fh.read(e["len"])
            patch = Zstd.decompress(z, None)
            data = delta.decode(patch, b"".join(_source(*s) for s in e["sources"]))
            note = None
        else:
            import colours
            src = _source(*e["source"])
            name = e["path"][len("Tex/"):-len(".bntx.zs")]
            data = colours.make(src, name, tuple(e["recipe"]))
            note = None
            if hashlib.sha1(data).hexdigest() != e["sha1"]:
                if not e.get("astc"):
                    raise RuntimeError("the recoloured texture is not the one we made")
                note = "rounding"        # the ASTC encoder of this machine rounds a block another way
        dst = os.path.join(out_root, *e["path"].split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        packed = _state["zs"].compress(data, e["zstd"], _state["level"]) if e["path"].endswith(".zs") else data
        with open(dst + ".part", "wb") as fh:
            fh.write(packed)
        os.replace(dst + ".part", dst)
        return e["path"], None, note
    except Exception as ex:      # noqa: BLE001  (reported per file)
        return e["path"], "%s: %s" % (type(ex).__name__, ex), None


TITLE = "0400486024DEC000"
EMULATORS = ("eden", "lichii", "citron", "sudachi", "suyu", "torzu", "yuzu", "fritchii")


def find_romfs():
    """The first emulator dump of the game's RomFS there is, or None."""
    homes = [os.environ.get("APPDATA"), os.path.expanduser("~/.local/share"), os.path.expanduser("~/Library/Application Support")]
    for home in filter(None, homes):
        for emu in EMULATORS:
            for name in (emu, emu.capitalize()):
                cand = os.path.join(home, name, "dump", TITLE, "romfs")
                if os.path.exists(os.path.join(cand, "Pack", "ZsDic.pack.zs")):
                    print("Using the RomFS dump in " + cand)
                    return cand
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--romfs")
    ap.add_argument("--mod")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--level", type=int, default=9, help="zstd level of the files written (default 9)")
    ap.add_argument("--exclude", help="a regular expression: RomFS paths (as Model/x.bfres.zs) it matches are not made")
    args = ap.parse_args()
    recipes = json.load(open(os.path.join(HERE, "recipes.json"), encoding="utf-8"))
    mod = args.mod
    if not mod:         # the release zip has it in mods\, next to this folder; a test has it beside
        here = [os.path.join(os.path.dirname(HERE), sub, recipes["mod_folder"]) for sub in ("mods", "")]
        mod = next((m for m in here if os.path.isdir(m)), here[0])
    romfs = os.path.abspath(args.romfs) if args.romfs else find_romfs()
    if romfs is None and sys.stdin and sys.stdin.isatty():
        print("No dumped RomFS found in the emulators' folders. (In your emulator: right-click the game,")
        print("Dump RomFS, choose Full.) Or drag the game's RomFS folder onto this window and press Enter:")
        try:
            typed = input("> ").strip().strip('"')
        except EOFError:
            typed = ""
        romfs = os.path.abspath(typed) if typed else None
    if romfs is None:
        sys.exit("No RomFS. In your emulator, right-click the game, choose Dump RomFS (Full), "
                 "then run this again (or give the folder with --romfs).")
    if os.path.isdir(os.path.join(romfs, "romfs")):
        romfs = os.path.join(romfs, "romfs")
    if not os.path.exists(os.path.join(romfs, "Pack", "ZsDic.pack.zs")):
        sys.exit("%s is not the game's RomFS folder (no Pack\\ZsDic.pack.zs in it)" % romfs)
    if not os.path.isdir(mod):
        sys.exit("no mod folder %s (give it with --mod)" % mod)
    out_root = os.path.join(mod, "romfs")
    files = recipes["files"]
    if args.exclude:
        import re
        files = [e for e in files if not re.search(args.exclude, e["path"])]
    print("Making %d files from %s into %s" % (len(files), romfs, out_root))
    t = time.time()
    errors, rounded, done = [], 0, 0
    order = sorted(files, key=lambda e: (e["kind"] != "colour", -e["size"]))     # the slow ones first
    with mp.Pool(args.jobs, _init, (romfs, os.path.join(HERE, "patches.bin"), args.level)) as pool:
        for path, err, note in pool.imap_unordered(_make, [(e, out_root) for e in order], chunksize=1):
            done += 1
            if err:
                errors.append((path, err))
            rounded += note == "rounding"
            if done % 100 == 0 or done == len(files):
                print("  %d of %d (%.0f s)" % (done, len(files), time.time() - t), flush=True)
    if rounded:
        print("%d textures differ from ours by the texture encoder's rounding (not visible)." % rounded)
    if errors:
        for p, e in errors[:20]:
            print("  FAILED %s: %s" % (p, e))
        sys.exit("%d files could not be made; the mod is not complete." % len(errors))
    print("Done: %d files in %.0f s. The mod folder is ready." % (len(files), time.time() - t))


if __name__ == "__main__":
    mp.freeze_support()
    code = 0
    try:
        main()
    except SystemExit as ex:
        if ex.code not in (None, 0):
            print(ex.code if isinstance(ex.code, str) else "Stopped.")
            code = 1
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():      # double-clicked: keep the window open
        try:
            input("Press Enter to close.")
        except EOFError:        # (no console after all: NUL counts as one)
            pass
    sys.exit(code)
