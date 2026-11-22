"""Turns a release folder into the recipe release: our own files, plus recipes that make the
rest from the player's own copy of the game.

    venv\\Scripts\\python.exe -I gamefiles\\make.py <release mod folder> <output folder>
        [--romfs <romfs>] [--work <scratch folder on G:>]

<release mod folder> is the one folder tools\\package.py makes ("OoT Character Select").
<output folder> gets:
    OoT Character Select\\      exefs and the voices (ours), the folder players install
    build-from-your-game\\      the player's tool (gamefiles\\player.py and what it imports),
                                recipes.json and patches.bin
Every file of the release that is not ours gets a recipe:
    "patch"  a byte patch (gamefiles\\delta.py) against one or more of the game's files,
             found by content (sampled blocks of the game's Model, Phive and Sequence files,
             and the file of the same path)
    "colour" a recoloured texture (gamefiles\\colours.py): the source texture and the
             parameters, found by trying them against the release's file
Ours, shipped as they are: exefs (the code patch) and the voices (Sound/Resource/
PlayerVoice_*). A file with no game source worth the name (more than half of it literal)
is listed in the report and shipped as it is.

Then the tool is run on this machine's romfs into the work folder and every file is
compared with the release: patches byte for byte (decompressed), BC textures byte for
byte, ASTC textures decoded within 0.5 of a step on average (the encoder's block choices) and the recipe's
own output is what the checksum in recipes.json holds.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import sys
import time

import numpy as np
import pyzstd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(ROOT, "tools")]

import delta  # noqa: E402

STEP = 1024                         # the pool's sampling of the game's files
POOL_DIRS = ("Model", "Phive/Cloth", "Sequence")
MAX_SOURCES = 4
OWN_SHARE = 0.4                     # a new file whose patch is this much literal is ours: shipped as it is
OURS = (re.compile(r"^exefs/"), re.compile(r"^romfs/Sound/Resource/PlayerVoice_"), re.compile(r"^romfs/Sound/Resource/Stream/"),
        re.compile(r"^romfs/Sequence/CharacterMenu\.module\.ainb$"))     # (the code patch, the voices, the page's own graph)
PLAYER_FILES = ["player.py", "delta.py", "colours.py"]
PLAYER_TOOLS = ["texrecolor.py", "bntx.py", "bntxwrite.py"]     # from tools\, what colours.py imports


def romfs_dir(args):
    return args.romfs or os.environ.get("OOT_ROMFS") or os.path.expanduser("~/Games/Hookshot/romfs")


def rel_files(top):
    out = []
    for d, _, fs in os.walk(top):
        for f in fs:
            out.append(os.path.relpath(os.path.join(d, f), top).replace("\\", "/"))
    return sorted(out)


def read_any(path):
    import nxfmt
    return nxfmt.read(path)


# ---------------------------------------------------------------- the pool of the game's files

def _sample_hashes(data):
    n = (len(data) - 16) // STEP + 1 if len(data) >= 16 else 0
    if n <= 0:
        return np.zeros(0, np.uint64), np.zeros(0, np.int64)
    pos = np.arange(n, dtype=np.int64) * STEP
    a = np.frombuffer(data, np.uint8)
    idx = pos[:, None] + np.arange(16)
    blk = np.ascontiguousarray(a[idx]).view("<u8").reshape(n, 2)
    with np.errstate(over="ignore"):
        return (blk[:, 0] * delta.K1) ^ (blk[:, 1] * delta.K2), pos


def _pool_file(job):
    romfs, rel = job
    data = read_any(os.path.join(romfs, rel))
    h, _ = _sample_hashes(data)
    return rel, len(data), hashlib.sha1(data).hexdigest(), h


COMMON = 64                         # a sampled block in more game files than this says nothing (zeros, padding)


def pool_index(romfs, work):
    """(files, hashes, file ids): every sampled block of the pool once per file, sorted, the
    common ones left out."""
    path = os.path.join(work, "pool2.npz")
    meta = os.path.join(work, "pool2.json")
    if os.path.exists(path) and os.path.exists(meta):
        m = json.load(open(meta, encoding="utf-8"))
        if m.get("romfs") == romfs and m.get("step") == STEP and m.get("dirs") == list(POOL_DIRS) and m.get("common") == COMMON:
            z = np.load(path)
            return m["files"], z["h"], z["f"]
    jobs = [(romfs, d + "/" + r) for d in POOL_DIRS for r in rel_files(os.path.join(romfs, *d.split("/")))]
    t = time.time()
    files, hs, fs = [], [], []
    with mp.Pool() as pool:
        for i, (rel, size, sha, h) in enumerate(pool.imap_unordered(_pool_file, jobs, chunksize=8)):
            files.append([rel, size, sha])
            h = np.unique(h)
            hs.append(h)
            fs.append(np.full(len(h), i, np.int32))
    h, f = np.concatenate(hs), np.concatenate(fs)
    order = np.argsort(h, kind="stable")
    h, f = h[order], f[order]
    uh, start, cnt = np.unique(h, return_index=True, return_counts=True)
    keep = np.repeat(cnt <= COMMON, cnt)
    h, f = h[keep], f[keep]
    np.savez(path, h=h, f=f)
    json.dump({"romfs": romfs, "step": STEP, "dirs": list(POOL_DIRS), "common": COMMON, "files": files},
              open(meta, "w", encoding="utf-8"))
    print("  pool: %d files, %d samples kept (%d common left out), %.0f s" % (len(files), len(h), int((~keep).sum()), time.time() - t))
    return files, h, f


def name_neighbours(rel_game, romfs, n=2):
    """Game files of the same folder and kind whose names begin as this one's (the first two
    parts): a copy of Link's skeleton under another set's name is found by its name, since its
    blocks are in too many of the game's files for the pool to say."""
    folder, name = rel_game.rsplit("/", 1)
    stem, _, suffix = name.partition(".")
    head = "_".join(stem.split("_")[:2]) + "_"
    try:
        names = os.listdir(os.path.join(romfs, *folder.split("/")))
    except OSError:
        return []
    cand = [f for f in names if f != name and f.endswith("." + suffix) and (f.startswith(head) or f.partition(".")[0] == head[:-1])]
    if not cand:            # (Player_Dark_Animation.Skeleton: no Player_Dark_ file; the player's ones then)
        head = stem.split("_")[0] + "_"
        cand = [f for f in names if f != name and f.endswith("." + suffix) and f.startswith(head)]
    common = lambda f: len(os.path.commonprefix([f, name]))
    return [folder + "/" + f for f in sorted(cand, key=lambda f: (-common(f), f))[:n]]


def find_sources(data, rel_game, romfs, pool):
    """[game file] the patch takes: the same path first (if the game has it), then the pool's
    files by how many of their sampled blocks the file holds."""
    files, ph, pf = pool
    out = []
    if os.path.exists(os.path.join(romfs, *rel_game.split("/"))):
        out.append(rel_game)
    out += name_neighbours(rel_game, romfs)
    ho = delta._hashes(data)
    if len(ho):
        uo = np.unique(ho)
        lo = np.searchsorted(ph, uo, "left")
        hi = np.searchsorted(ph, uo, "right")
        n = hi - lo
        if n.sum():
            idx = np.repeat(lo, n) + (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n))
            counts = np.bincount(pf[idx], minlength=len(files))
            top = int(counts.max())
            for i in np.argsort(-counts, kind="stable"):
                if counts[i] < max(4, top // 20) or len(out) >= MAX_SOURCES:
                    break
                if files[i][0] not in out:
                    out.append(files[i][0])
    return out


# ---------------------------------------------------------------- one file

def _patch_job(job):
    rel, release_path, romfs, sources = job
    import nxfmt
    raw = open(release_path, "rb").read()
    data = nxfmt.unzs(raw, rel)
    did = pyzstd.get_frame_info(raw).dictionary_id if raw[:4] == b"\x28\xb5\x2f\xfd" else None
    srcs = [read_any(os.path.join(romfs, *s.split("/"))) for s in sources]
    patch, st = delta.encode(data, b"".join(srcs))
    assert delta.decode(patch, b"".join(srcs)) == data
    z = pyzstd.compress(patch, 19)
    return {"path": rel, "kind": "patch", "sources": [[s, len(b), hashlib.sha1(b).hexdigest()] for s, b in zip(sources, srcs)],
            "zstd": did, "size": len(data), "sha1": hashlib.sha1(data).hexdigest(), "stats": st}, z


def _tex_source(romfs, name):
    for m in re.finditer(r"A([a-z])(\d)", name):
        i = m.start()
        cand = name[:i] + "Alb" + name[i + 3:]
        if cand.rfind("Alb") == i and os.path.exists(os.path.join(romfs, "Tex", cand + ".bntx.zs")):
            return cand, m.group(1), int(m.group(2))
    return None


ASTC_MEAN = 0.5                     # an ASTC copy is the release's when its decoded texels differ by this much on average
                                    # (the encoder's block choices: 0.004 to 0.32 seen with the right recipe, the most on
                                    # 10x10 blocks; a neighbouring hue bin 0.34 to 2). A recipe is chosen by the best score
                                    # when it is within this, 1.5 times better than the second and 0.1 ahead.


def _diff(a, b):
    import texrecolor
    da, db = texrecolor.decoded(a).astype(np.int16), texrecolor.decoded(b).astype(np.int16)
    return float(np.abs(da - db).mean()) if da.shape == db.shape else 1e9


def _close(a, b):
    return _diff(a, b) <= ASTC_MEAN


def _colour_job(job):
    """(texture group) -> [entries]: the source, its tag and the copies of it in the release.
    The parameters are found on the first copy and must make every other one."""
    import nxfmt
    import texrecolor
    import colours
    romfs, src, tag, copies = job          # copies: [(rel, release path, k)]
    data = nxfmt.read(os.path.join(romfs, "Tex", src + ".bntx.zs"))
    raw_src = open(os.path.join(romfs, "Tex", src + ".bntx.zs"), "rb").read()
    did = pyzstd.get_frame_info(raw_src).dictionary_id
    t = texrecolor.data_range(data)[0]
    astc = texrecolor.astc_size(t.format) is not None
    wants = {k: nxfmt.read(p) for _rel, p, k in copies}
    rel0, _p0, k0 = copies[0]
    name0 = rel0[4:-len(".bntx.zs")]
    found = None
    if tag == "h":
        found = ("hair",)
    else:
        scored = []
        for mode, b in [("neutral", None)] + [("hue", b) for b in colours.hue_order(data)]:
            share, s, v = colours.cloth_means(data, mode, None if b is None else colours.centre_of(b))
            if not share >= (0.02 if mode == "hue" else 0.05):
                continue
            out = colours.make(data, name0, ("cloth", mode, b, s, v, k0))
            if out == wants[k0]:
                found = ("cloth", mode, b, s, v)
                break
            if astc:
                scored.append((_diff(out, wants[k0]), ("cloth", mode, b, s, v)))
        if found is None and scored:
            scored.sort(key=lambda x: x[0])
            best = scored[0][0]
            second = scored[1][0] if len(scored) > 1 else 1e9
            if best <= ASTC_MEAN and second >= 1.5 * best and second - best >= 0.1:
                found = scored[0][1]
    res = []
    for rel, _p, k in copies:
        name = rel[4:-len(".bntx.zs")]
        if found is None:
            res.append({"path": rel, "kind": "fail", "why": "no recipe makes it"})
            continue
        recipe = list(found) + [k]
        out = colours.make(data, name, tuple(recipe))
        exact = out == wants[k]
        if not exact and not (astc and _close(out, wants[k])):
            res.append({"path": rel, "kind": "fail", "why": "the recipe found on %s does not make it" % name0})
            continue
        res.append({"path": rel, "kind": "colour", "source": ["Tex/%s.bntx.zs" % src, len(data), hashlib.sha1(data).hexdigest()],
                    "recipe": recipe, "zstd": did, "size": len(out), "sha1": hashlib.sha1(out).hexdigest(),
                    "astc": astc, "release_exact": exact})
    return res


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("release")
    ap.add_argument("out")
    ap.add_argument("--romfs")
    ap.add_argument("--work", default=r"G:\OoT-Modding-work\gamefiles")
    ap.add_argument("--only", help="a regex: only these release paths (a test)")
    ap.add_argument("--reuse", help="an earlier output of this script for the same release: its recipes are kept, only the rest is made")
    args = ap.parse_args()
    romfs = os.path.abspath(romfs_dir(args))
    os.environ["OOT_ROMFS"] = romfs
    os.makedirs(args.work, exist_ok=True)
    t0 = time.time()
    rels = rel_files(args.release)
    if args.only:
        rels = [r for r in rels if re.search(args.only, r)]
    ours = [r for r in rels if any(p.search(r) for p in OURS)]
    tex = [r for r in rels if r.startswith("romfs/Tex/") and r not in ours]
    rest = [r for r in rels if r not in ours and r not in tex]
    print("%d files: %d ours, %d textures, %d others" % (len(rels), len(ours), len(tex), len(rest)))

    reused = {}                          # path -> (entry, patch bytes or None)
    if args.reuse:
        tool_dir = os.path.join(args.reuse, "build-from-your-game")
        blob = open(os.path.join(tool_dir, "patches.bin"), "rb").read()
        for e in json.load(open(os.path.join(tool_dir, "recipes.json"), encoding="utf-8"))["files"]:
            z = None
            if e["kind"] == "patch":
                z = blob[e.pop("at"):][:e.pop("len")]
                e["stats"] = {"literal": e.pop("literal"), "changed": e.pop("changed", 0), "out": e["size"]}
            reused[e["path"]] = (e, z)
        print("  reusing %d recipes of %s" % (len(reused), args.reuse))
    pool = pool_index(romfs, args.work)
    jobs = []
    for r in rest:
        if r[len("romfs/"):] in reused:
            continue
        game = r[len("romfs/"):]
        data = read_any(os.path.join(args.release, *r.split("/")))
        jobs.append((game, os.path.join(args.release, *r.split("/")), romfs, find_sources(data, game, romfs, pool)))
    print("  sources found, %.0f s" % (time.time() - t0))

    groups = {}
    entries, blobs, failed = [], [], []
    for r in tex:
        name = r[len("romfs/Tex/"):-len(".bntx.zs")]
        s = _tex_source(romfs, name)
        if s is None:
            failed.append({"path": r[len("romfs/"):], "kind": "fail", "why": "no source texture by its name"})
            continue
        groups.setdefault((s[0], s[1]), []).append((r[len("romfs/"):], os.path.join(args.release, *r.split("/")), s[2]))
    cjobs = [(romfs, src, tag, sorted(c, key=lambda x: x[2])) for (src, tag), c in sorted(groups.items())
             if not all(rel in reused for rel, _p, _k in c)]
    for e, z in reused.values():
        entries.append(e)
        if z is not None:
            blobs.append((e["path"], z))

    with mp.Pool() as p:
        for e, z in p.imap_unordered(_patch_job, [j for j in jobs if j[3]], chunksize=1):
            st = e["stats"]
            if not os.path.exists(os.path.join(romfs, *e["path"].split("/"))) and st["literal"] > OWN_SHARE * st["out"]:
                failed.append({"path": e["path"], "kind": "fail", "why": "ours: %d of %d bytes are literal" % (
                    st["literal"], st["out"])})
                continue
            entries.append(e)
            blobs.append((e["path"], z))
        print("  %d patches, %.0f s" % (len(entries), time.time() - t0))
        for res in p.imap_unordered(_colour_job, cjobs, chunksize=1):
            for e in res:
                (entries if e["kind"] == "colour" else failed).append(e)
        print("  %d recipes in all, %d failed, %.0f s" % (len(entries), len(failed), time.time() - t0))
    for j in jobs:
        if not j[3]:
            failed.append({"path": j[0], "kind": "fail", "why": "no game file has its content"})

    # ---- write
    out_mod = os.path.join(args.out, os.path.basename(os.path.normpath(args.release)))
    out_tool = os.path.join(args.out, "build-from-your-game")
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(out_tool)
    literal_heavy = []
    blob_of = dict(blobs)
    entries = sorted({e["path"]: e for e in entries}.values(), key=lambda e: e["path"])     # (a group made again wins over its reused copies)
    off = 0
    with open(os.path.join(out_tool, "patches.bin"), "wb") as fh:
        for e in entries:
            if e["kind"] == "patch":
                z = blob_of[e["path"]]
                e["at"], e["len"] = off, len(z)
                fh.write(z)
                off += len(z)
                st = e.pop("stats")
                e["literal"] = st["literal"]
                if st["literal"] > 0.5 * st["out"] and st["out"] > 65536:
                    literal_heavy.append((e["path"], st["literal"], st["out"]))
                e["changed"] = st["changed"]
    shipped = list(ours) + ["romfs/" + f["path"] for f in failed]
    for r in shipped:
        dst = os.path.join(out_mod, *r.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(args.release, *r.split("/")), dst)
    json.dump({"format": 1, "game": "0400486024DEC000 v1.1.1", "mod_folder": os.path.basename(os.path.normpath(args.release)),
               "files": entries}, open(os.path.join(out_tool, "recipes.json"), "w", encoding="utf-8"), indent=0)
    for f in PLAYER_FILES:
        shutil.copy2(os.path.join(HERE, f), out_tool)
    os.makedirs(os.path.join(out_tool, "lib"), exist_ok=True)
    for f in PLAYER_TOOLS:
        shutil.copy2(os.path.join(ROOT, "tools", f), os.path.join(out_tool, "lib", f))

    size = lambda top: sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(top) for f in fs)
    print("\nwritten: %s (%.1f MB) and %s (%.1f MB), %.0f s" % (out_mod, size(out_mod) / 1e6, out_tool, size(out_tool) / 1e6, time.time() - t0))
    lit = sum(e.get("literal", 0) for e in entries)
    print("patches: %d files, %.1f MB made from %.1f MB of patches, %.2f MB of it literal (ours)" % (
        sum(e["kind"] == "patch" for e in entries), sum(e["size"] for e in entries if e["kind"] == "patch") / 1e6, off / 1e6, lit / 1e6))
    print("colours: %d textures (%d ASTC; %d byte for byte the release's)" % (
        sum(e["kind"] == "colour" for e in entries), sum(e.get("astc", False) for e in entries if e["kind"] == "colour"),
        sum(e.get("release_exact", False) for e in entries if e["kind"] == "colour")))
    for p, l, n in literal_heavy:
        print("  mostly literal: %s (%d of %d bytes)" % (p, l, n))
    for f in failed:
        print("  shipped as it is: %s: %s" % (f["path"], f["why"]))
    report = os.path.join(args.work, "make-report.json")
    json.dump({"failed": failed, "literal_heavy": literal_heavy}, open(report, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
