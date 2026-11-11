"""Character voices: the picked character speaks with its own voice, Link keeps his.

    venv-voice\\Scripts\\python.exe mods\\voices.py [--install] [--redo] [--more N]
    venv-voice\\Scripts\\python.exe mods\\voices.py --everyone [voice] [--install]
    venv-voice\\Scripts\\python.exe mods\\voices.py --remove

The default build is the test folder MOD (README, mods list and voices section):

- per voice in VOICES one small sound archive, `Sound/Resource/PlayerVoice_Child_<voice>
  .bars`, with child Link's voice clips converted to that voice under Link's own asset
  names, and its stream files under names of their own;
- the archive list (`Sound/ResourceListPack/BarslistPack`): for every character that
  has a voice a list `Player_Child_V<number>` = the voice archive, then the player's
  own archives. The game searches a list's archives in order and takes the first that
  has the clip, so the voice archive wins and a clip it does not have is Link's;
- a code patch: where the game looks a list up by name, `Player_Child` with a stored
  character n becomes `Player_Child_V<n>` when the table has such a list;
- the combined size table with entries for the two files above.

Nothing of Link's own is replaced. `--everyone` builds the first test instead (passed
in game, 2026-11-11): one voice for everybody, child Link's own archive patched in place.

`voice` is a voice type (default Custom_Saria; README, voices section). The steps:

1. convert: every clip that has a voice in it goes through Seed-VC (tools\\voiceai.py
   has the setup) in rows of up to 7.6 seconds, as in the samples snaildev chose
   (the NPC's own clips as the reference, Link's pitch kept). Each converted clip is
   lined up with the original, brought to the original's loudness and checked
   (loudness curve, pitch); a clip that fails is tried again with another seed and in
   the end keeps Link's original. Breaths and bubbles are not converted at all.
   Results are kept in build-voice\\cache\\<voice>\\child (`--redo` starts over).
2. pack: the clips are encoded in the game's format with the same sample counts, so
   every file keeps its size: the sound archive is patched in place (no size table
   entry) and each stream file is replaced.
3. check: the built files are read back and compared with the originals and the cache.

`--install` copies the folder into the normal mods folder (nothing else is touched;
refuses while the emulator runs). Other voice test folders of this script are removed
there, since two would override each other.

The size table: MOD ships the table of the folders that are installed now plus its own
two entries, and its name sorts before theirs, so the emulator takes it. After another
script installs (install-mods.bat and the like) that copy is out of date: run this
script with --install again, or --remove.
"""
import copy, glob, json, os, re, shutil, struct, subprocess, sys, zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bwav  # noqa: E402
import byml  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402

BANK = "Player_Child"
# voice type -> (two characters that name its stream files, the characters that speak
# with it: labels of the NPC session's list, build-npc\characters.txt)
VOICES = {
    "Custom_Saria": ("Sa", ["Saria"]),
}
MOD = "Character (voice test)"      # "(" sorts before every letter: its size table is the one taken
ARCHIVE = "PlayerVoice_Child_%s.bars"
LIST = "%s_V%02X"                   # the player's list name, the stored character number
BARSLIST = "Sound/ResourceListPack/BarslistPack.Product.111.byml.zs"
# the game's code (image.bin addresses; README, voices section)
TABLE_LOOKUP = 0x805cc370           # (table, out, key) -> bit 0: found
LOOKUP_SITES = (0x805d5c24, 0x8088d1b4)     # the two places that look a list up by name
GAMEDATA_MGR_GOT = 0x84c9e180       # as in mods\build.py (checked against its text)
CHARACTER_FLAG = "CharacterMenu_SelectedCharacter"
# Where the hook's code goes: the body of a sequence node class that nothing uses (the
# padding after .text is full; the field swap does the same with other classes). What
# made it the one, all checked at build (check_dead_code): the body is referenced by
# its class's vtable alone, and no graph file names the class. Checked once by hand,
# 2026-11-11: none of the game's 1942 graph files and 8385 packs has the name.
DEAD_NODE = "SeqOneShotEnableRestoreBG"
DEAD = (0x832f0c80, 0x832f0e90)
DEAD_SHA1 = "b946b747bcd55cd13e0e16a78a122078c7e79340"      # its bytes in game v1.1.1
VT_NAME, VT_ONESHOT = 0x98, 0x138   # a node class's vtable: its name getter, its one-shot body
BUILD = os.path.join(ROOT, "build-voice", "mod")
CACHE = os.path.join(ROOT, "build-voice", "cache")
EDEN_LOAD = os.path.join(os.environ["APPDATA"], "eden", "load", "0400486024DEC000")
EMULATORS = ("lichii.exe", "eden.exe", "yuzu.exe")
FOLDER = "Voice test (%s)"
RATE = 48000
GAP = 0.35                  # silence after each clip in a row, as in the samples
ROW = 7.6                   # seconds per row: source + reference must fit the model's 30 s
LEAD = 0.10                 # silence before the first clip of a row
STEPS = 50
TRIES = 3
MIN_VOICED = 0.06           # seconds of voiced sound a clip needs to be converted
MIN_MATCH = 0.70            # loudness curve correlation with the original
MAX_PITCH_OFF = 2.0         # semitones between the medians
STEREO = "two channels (a cutscene's own mix)"


def label(voice):
    return voice.replace("Custom_", "").replace("_", " ")


def assets():
    names, d = bwav.bars(BANK)
    return sorted(n for n in names if n.startswith("Vo_")), names, d


def twins(vo, names):
    """{asset: the asset whose clip it shares}. Two names can be one clip in the
    archive (child: `Wait_32_01` is `EscapedBreath_00`), each with a stream file of
    its own; such a clip is converted once and written under both names."""
    first, out = {}, {}
    for a in vo:
        if names[a] in first:
            out[a] = first[names[a]]
        else:
            first[names[a]] = a
    return out


def tidy(report, vo, names, d):
    """Rules added after a cache was made: applied to its report too."""
    same = twins(vo, names)
    for a in list(report["converted"]):
        if len(bwav.header(d, names[a])["channels"]) != 1:
            del report["converted"][a]
            report["kept"][a] = STEREO
        elif a in same:
            del report["converted"][a]
    for a in same:
        report["kept"].pop(a, None)
    report["same"] = same
    return report


# ---------------------------------------------------------------- measuring
def pitch_of(x):
    """(median pitch in Hz or 0, seconds of voiced sound)."""
    import parselmouth
    if len(x) < RATE * 0.05:
        return 0.0, 0.0
    snd = parselmouth.Sound(x.astype(np.float64), sampling_frequency=RATE)
    f = snd.to_pitch_ac(time_step=0.005, pitch_floor=120, pitch_ceiling=1000).selected_array["frequency"]
    f = f[f > 0]
    return (float(np.median(f)), len(f) * 0.005) if len(f) else (0.0, 0.0)


def pitch_track(x):
    """Pitch per 5 ms frame (0 = unvoiced)."""
    import parselmouth
    if len(x) < RATE * 0.05:
        return np.zeros(0)
    snd = parselmouth.Sound(x.astype(np.float64), sampling_frequency=RATE)
    return snd.to_pitch_ac(time_step=0.005, pitch_floor=120, pitch_ceiling=1000).selected_array["frequency"]


def pitch_apart(x, y):
    """Semitones between two clips of the same length, over the frames where both
    have a pitch (median; None when they share less than 0.04 s). Frame by frame,
    because the medians of two clips differ as soon as one has a few more frames."""
    a, b = pitch_track(x), pitch_track(y)
    n = min(len(a), len(b))
    both = (a[:n] > 0) & (b[:n] > 0)
    if both.sum() < 8:
        return None
    return float(np.median(12 * np.log2(b[:n][both] / a[:n][both])))


def envelope(x, hop=240):
    n = len(x) // hop
    return np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))


def lined_up(out, pos, n, original, reach=8, hop=240):
    """The converted clip cut from the row at the offset (within 40 ms) where its
    loudness curve fits the original's best; returns (samples, match, offset in ms)."""
    want = envelope(original, hop)
    best = (-2.0, 0)
    for k in range(-reach, reach + 1):
        a = pos + k * hop
        if a < 0 or a + n > len(out):
            continue
        got = envelope(out[a:a + n], hop)
        if want.std() == 0 or got.std() == 0:
            continue
        c = float(np.corrcoef(want, got)[0, 1])
        if c > best[0]:
            best = (c, k)
    a = pos + best[1] * hop
    return out[a:a + n].copy(), best[0], best[1] * hop * 1000 // RATE


# ---------------------------------------------------------------- step 1: convert
def convert(voice, redo=False, more=0):
    """`more`: that many further tries (new seeds) for the clips of an existing cache
    that failed the check."""
    cache = os.path.join(CACHE, voice, "child")
    report_path = os.path.join(cache, "report.json")
    if redo:
        shutil.rmtree(cache, ignore_errors=True)
    vo, names, d = assets()
    old = json.load(open(report_path)) if os.path.exists(report_path) else None
    if old and not more:
        return tidy(old, vo, names, d)
    os.makedirs(cache, exist_ok=True)
    import librosa, soundfile, torch
    import voiceai
    clips = {a: bwav.clip(a, BANK)[0][0].astype(np.float64) for a in vo}
    same = twins(vo, names)
    if old:
        report = tidy(old, vo, names, d)
        todo = sorted(a for a, why in report["kept"].items() if why.startswith("failed"))
        for a in todo:
            del report["kept"][a]
        tries, seed0 = more, 1337 + report.get("tries", TRIES)
    else:
        report = {"voice": voice, "kept": {}, "converted": {}}
        todo, tries, seed0 = [], TRIES, 1337
        for a in vo:
            hz, voiced = pitch_of(clips[a])
            if a in same:
                continue
            if len(bwav.header(d, names[a])["channels"]) != 1:
                report["kept"][a] = STEREO
            elif voiced < MIN_VOICED:
                report["kept"][a] = "no voice in it (%.2f s voiced of %.2f s)" % (voiced, len(clips[a]) / RATE)
            else:
                todo.append(a)
    report["tries"] = seed0 - 1337 + tries
    ref, rate, used = voiceai.reference(voice)
    work = os.path.join(ROOT, "build-voice", "ai", "work", voice)
    os.makedirs(work, exist_ok=True)
    ref_path = os.path.join(work, "reference.wav")
    bwav.write_wav(ref_path, ref[None, :], rate)
    report["reference"] = {"clips": used, "seconds": round(len(ref) / rate, 1)}
    assert len(ref) / rate + ROW + 0.5 <= 30, "reference too long for one pass per row"

    # Seed-VC through its own entry point, with the models loaded once
    from argparse import Namespace
    os.chdir(voiceai.SEEDVC)
    sys.path.insert(0, voiceai.SEEDVC)
    import inference
    ns = Namespace(source=None, target=ref_path, output=os.path.join(work, "out"), diffusion_steps=STEPS,
                   length_adjust=1.0, inference_cfg_rate=0.7, f0_condition=True, auto_f0_adjust=False,
                   semi_tone_shift=0, checkpoint=None, config=None, fp16=True)
    models = inference.load_models(ns)
    inference.load_models = lambda args: models

    def run_row(row, seed, tag):
        gap = np.zeros(int(GAP * RATE))
        parts, places, pos = [np.zeros(int(LEAD * RATE))], {}, int(LEAD * RATE)
        for a in row:
            places[a] = pos
            parts += [clips[a], gap]
            pos += len(clips[a]) + len(gap)
        src = np.concatenate(parts)
        src = src * (0.8 / abs(src).max())
        ns.source = os.path.join(work, tag + ".wav")
        bwav.write_wav(ns.source, src[None, :], RATE)
        shutil.rmtree(ns.output, ignore_errors=True)
        torch.manual_seed(seed)
        inference.main(ns)
        got, got_rate = soundfile.read(glob.glob(os.path.join(ns.output, "*.wav"))[0], dtype="float64")
        got = librosa.resample(got, orig_sr=got_rate, target_sr=RATE)
        got = np.pad(got, (0, max(0, len(src) - len(got))))
        return got, places

    def rows_of(items):
        rows, row, total = [], [], LEAD
        for a in items:
            need = len(clips[a]) / RATE + GAP
            if row and total + need > ROW:
                rows.append(row)
                row, total = [], LEAD
            row.append(a)
            total += need
        return rows + ([row] if row else [])

    failed = []
    for attempt in range(tries if todo else 0):
        failed = []
        rows = rows_of(todo)
        number = seed0 - 1337 + attempt + 1
        for i, row in enumerate(rows):
            print("try %d, row %d of %d (%d clips)" % (number, i + 1, len(rows), len(row)), flush=True)
            out, places = run_row(row, seed0 + attempt, "row%03d" % i)
            for a in row:
                x = clips[a]
                y, match, off = lined_up(out, places[a], len(x), x)
                semis = pitch_apart(x, y)
                why = None
                if match < MIN_MATCH:
                    why = "loudness curve match %.2f" % match
                elif semis is not None and abs(semis) > MAX_PITCH_OFF:
                    why = "pitch %+.1f semitones off" % semis
                if why:
                    failed.append((a, why))
                    continue
                rms0, rms1 = np.sqrt((x ** 2).mean()), np.sqrt((y ** 2).mean())
                y = y * (rms0 / rms1)
                if abs(y).max() > 0.98:
                    y *= 0.98 / abs(y).max()
                fade = min(96, len(y) // 4)
                ramp = np.linspace(0, 1, fade)
                y[:fade] *= ramp
                y[-fade:] *= ramp[::-1]
                np.save(os.path.join(cache, a + ".npy"), np.round(y * 32767).astype(np.int16))
                report["converted"][a] = {"match": round(match, 2), "offset_ms": int(off),
                                          "pitch_semitones": None if semis is None else round(semis, 2),
                                          "try": number}
        todo = [a for a, why in failed]
        if not todo:
            break
    for a, why in failed:
        report["kept"][a] = "failed the check %d times (last: %s)" % (report["tries"], why)
    os.chdir(ROOT)
    json.dump(report, open(report_path, "w"), indent=1)
    return tidy(report, vo, names, d)


# ---------------------------------------------------------------- the first test: one voice for everybody
def pack_everyone(voice, report):
    mod = FOLDER % label(voice)
    out = os.path.join(BUILD, mod)
    shutil.rmtree(out, ignore_errors=True)
    stream_out = os.path.join(out, "romfs", "Sound", "Resource", "Stream")
    os.makedirs(stream_out)
    cache = os.path.join(CACHE, voice, "child")
    vo, names, d = assets()
    done = sorted(report["converted"])
    pcm = [np.load(os.path.join(cache, a + ".npy")) for a in done]
    print("encoding %d clips" % len(done), flush=True)
    encoded = bwav.encode_adpcm(pcm)
    new = bytearray(d)
    for a, samples, (coefs, data) in zip(done, pcm, encoded):
        o = names[a]
        ch = bwav.header(d, o)["channels"][0]
        assert len(samples) == ch["n_full"], a
        head = bwav.rewritten(d, coefs, data, o)
        here = (ch["n"] + 13) // 14 * 8                 # the archive's part: all of it, or a stream's head
        new[o:o + len(head)] = head
        new[o + len(head):o + len(head) + here] = data[:here]
        if bwav.header(d, o)["prefetch"]:
            for name in [a] + [b for b, first in report["same"].items() if first == a]:
                old = open(os.path.join(bwav.STREAM, name + ".bwav"), "rb").read()
                blob = bwav.rewritten(old, coefs, data) + data
                assert len(blob) == len(old), name
                with open(os.path.join(stream_out, name + ".bwav"), "wb") as f:
                    f.write(blob)
        else:
            assert here == len(data), a
    assert len(new) == len(d)
    rel = os.path.join("Sound", "Resource", BANK + ".bars.zs")
    raw = open(os.path.join(bwav.SOUND, BANK + ".bars.zs"), "rb").read()
    with open(os.path.join(out, "romfs", rel), "wb") as f:
        f.write(nxpack.zs_compress_like(bytes(new), raw))
    return mod


def check_everyone(voice, report, mod):
    """Read the built folder back. Returns the lines of the check's report."""
    import nxfmt
    out = os.path.join(BUILD, mod, "romfs", "Sound", "Resource")
    cache = os.path.join(CACHE, voice, "child")
    vo, names, d = assets()
    new = nxfmt.read(os.path.join(out, BANK + ".bars.zs"))
    assert len(new) == len(d)
    same = {a: first for a, first in report["same"].items() if first in report["converted"]}
    done = set(report["converted"]) | set(same)
    offs = sorted(set(names.values()))
    end = {o: (offs[i + 1] if i + 1 < len(offs) else len(d)) for i, o in enumerate(offs)}
    first = min(offs)
    assert new[:first] == d[:first], "the archive's tables and metadata changed"
    snrs, streams = [], 0
    for a, o in names.items():
        if a not in done:
            assert new[o:end[o]] == d[o:end[o]], "untouched asset changed: " + a
            assert not os.path.exists(os.path.join(out, "Stream", a + ".bwav")), a
            continue
        h0, h1 = bwav.header(d, o), bwav.header(new, o)
        c0, c1 = h0["channels"][0], h1["channels"][0]
        for k in ("enc", "pan", "rate", "n_full", "n", "start", "loop", "loop_start", "loop_end"):
            assert c0[k] == c1[k], (a, k)
        assert h0["prefetch"] == h1["prefetch"]
        want = np.load(os.path.join(cache, same.get(a, a) + ".npy")).astype(np.float64)
        p = os.path.join(out, "Stream", a + ".bwav")
        if h1["prefetch"]:
            s = open(p, "rb").read()
            assert len(s) == os.path.getsize(os.path.join(bwav.STREAM, a + ".bwav"))
            hs = bwav.header(s)["channels"][0]
            here = (c1["n"] + 13) // 14 * 8
            assert new[o + c1["start"]:o + c1["start"] + here] == s[hs["start"]:hs["start"] + here], a
            assert new[o + 8:o + 12] == s[8:12] and hs["coefs"] == c1["coefs"], a
            assert struct.unpack_from("<I", s, 8)[0] == zlib.crc32(s[hs["start"]:]), a
            got = bwav.decode(s)[0][0].astype(np.float64) * 32768
            streams += 1
        else:
            assert not os.path.exists(p), a
            data = new[o + c1["start"]:o + c1["start"] + (c1["n"] + 13) // 14 * 8]
            assert struct.unpack_from("<I", new, o + 8)[0] == zlib.crc32(data), a
            got = bwav.decode(new, o)[0][0].astype(np.float64) * 32768
        assert struct.unpack_from("<H", new, o + 0x10 + 0x44)[0] == new[o + c1["start"]], a
        noise = ((got - want) ** 2).mean()
        snrs.append(10 * np.log10(max(1e-9, (want ** 2).mean()) / max(1e-9, noise)))
    extra = set(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(out, "Stream", "*.bwav"))) - done
    assert not extra, extra
    snrs = np.array(snrs)
    return ["%d of %d voice clips converted (%d of them streams), %d keep Link's original" % (
                len(done), len(vo), streams, len(vo) - len(done)),
            "archive: same size, tables and the %d other assets byte for byte as the game's" % (len(names) - len(done)),
            "encoding: signal to noise median %.0f dB, worst %.0f dB" % (np.median(snrs), snrs.min())]


# ---------------------------------------------------------------- the per-character build
def ring():
    """{label: stored character number}, from the NPC session's list."""
    out = {}
    with open(os.path.join(ROOT, "build-npc", "characters.txt"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*(\d+)  (.+?)(?:\s{2,}|$)", line.rstrip("\n"))
            if m:
                out[m.group(2).strip()] = int(m.group(1))
    return out


def stream_name(voice, asset):
    """The name a voice's stream file has (and the name in its metadata block, which is
    all the game makes the file's name from): Link's asset name with `Vo_` replaced, so
    the length stays and the block keeps its layout. The game finds the clip by the
    hash of Link's name in the archive's table; nothing compares the two."""
    assert asset.startswith("Vo_") and len(VOICES[voice][0]) == 2
    return "V" + VOICES[voice][0] + asset[3:]


def voice_archive(voice, report):
    """-> (the voice's archive, {stream file name: bytes}). One asset per converted clip
    (and per second name of it), under Link's name hash: his metadata block (for a
    stream with the stream's own name in it) and the converted clip in his BWAV's exact
    shape (a whole clip, or a stream's first 0.30 s with the rest in the stream file)."""
    cache = os.path.join(CACHE, voice, "child")
    vo, names, d = assets()
    blocks = {h: amta for h, amta, _blob in bwav.bars_parts(d)[0]}
    done = sorted(report["converted"])
    pcm = [np.load(os.path.join(cache, a + ".npy")) for a in done]
    print("encoding %d clips" % len(done), flush=True)
    entries, streams = [], {}
    for a, samples, (coefs, data) in zip(done, pcm, bwav.encode_adpcm(pcm)):
        for name in [a] + sorted(b for b, first in report["same"].items() if first == a):
            o = names[name]
            h = bwav.header(d, o)
            ch = h["channels"][0]
            assert len(samples) == ch["n_full"], name
            here = (ch["n"] + 13) // 14 * 8                 # all of it, or a stream's head
            blob = bwav.rewritten(d, coefs, data, o) + data[:here]
            assert len(blob) == bwav.bwav_size(d, o), name
            amta = blocks[zlib.crc32(name.encode())]
            if h["prefetch"]:
                new = stream_name(voice, name)
                at = 0x24 + struct.unpack_from("<I", amta, 0x24)[0]
                assert amta[at:at + len(name) + 1] == name.encode() + b"\0", name
                amta = amta[:at] + new.encode() + amta[at + len(new):]
                old = open(os.path.join(bwav.STREAM, name + ".bwav"), "rb").read()
                streams[new] = bwav.rewritten(old, coefs, data) + data
                assert len(streams[new]) == len(old), name
                assert not os.path.exists(os.path.join(bwav.STREAM, new + ".bwav")), new
            else:
                assert here == len(data), name
            entries.append((zlib.crc32(name.encode()), amta, blob))
    entries.sort(key=lambda e: e[0])                 # the clips in the table's order
    return bwav.bars_build(entries), streams


def barslist(lists):
    """The archive list with `lists` ({list name: (the player's list it starts from, the
    voice archive)}) added: the voice archive first, then the player's own archives and
    its ChoiceAsset list. The voice archives join LargeBars as the player's archives do
    (that table picks the heap an archive is loaded into). -> (new bytes, expected content)."""
    data = nxfmt.read(os.path.join(bwav.ROMFS, BARSLIST.replace("/", os.sep)))
    root = byml.load(data)
    new = copy.deepcopy(root)
    for name, (base, archive) in sorted(lists.items()):
        assert name not in new["BarslistTable"] and base in root["BarslistTable"]
        entry = copy.deepcopy(root["BarslistTable"][base])
        assert archive not in entry["Bars"] and not os.path.exists(os.path.join(bwav.SOUND, archive + ".zs"))
        entry["Bars"] = [archive] + entry["Bars"]
        new["BarslistTable"][name] = entry
        new["LargeBars"][archive] = root["LargeBars"][base + ".bars"]
    out = byml.dump(new, byml.version_of(data))
    assert byml.load(out) == new and byml.load(byml.dump(root, byml.version_of(data))) == root
    return out, new


HOOK = """
        cbz x2, vh_pass
        adrp x9, #{names_page:#x}
        add x9, x9, #{names_off:#x}
        mov w13, wzr
    vh_try:
        mov x11, xzr
    vh_cmp:
        ldrb w12, [x2, x11]
        ldrb w14, [x9, x11]
        cmp w12, w14
        b.ne vh_next
        add x11, x11, #1
        cbnz w12, vh_cmp
        b vh_match
    vh_next:
        cbnz w13, vh_pass
        add x9, x9, #16
        mov w13, #1
        b vh_try
    vh_match:
        adrp x9, #{got_page:#x}
        ldr x9, [x9, #{got_off:#x}]
        ldr x9, [x9]
        cbz x9, vh_pass
        ldr x10, [x9, #0xe0]
        cbz x10, vh_pass
        ldr w11, [x9, #0xec]
        cbz w11, vh_pass
        movz w12, #{hlo:#x}
        movk w12, #{hhi:#x}, lsl #16
        udiv w14, w12, w11
        msub w14, w14, w11, w12
        mov w15, w14
    vh_probe:
        add x16, x10, w15, uxtw #3
        ldr w17, [x16]
        cmp w17, w12
        b.eq vh_found
        cbz w17, vh_pass
        add w15, w15, #1
        cmp w15, w11
        csel w15, wzr, w15, hs
        cmp w15, w14
        b.ne vh_probe
        b vh_pass
    vh_found:
        ldr w16, [x16, #4]
        ldr w17, [x9, #0xd0]
        cmp w16, w17
        b.hs vh_pass
        ldr x17, [x9, #0xd8]
        mov w15, #0x28
        umaddl x17, w16, w15, x17
        ldr w16, [x17, #0x1c]
        cbz w16, vh_pass
        cmp w16, #0xff
        b.hi vh_pass
        sub sp, sp, #0x40
        stp x29, x30, [sp, #0x30]
        stp x0, x1, [sp, #0x20]
        str x2, [sp, #0x18]
        mov x10, sp
    vh_copy:
        ldrb w12, [x2], #1
        strb w12, [x10], #1
        cbnz w12, vh_copy
        sub x10, x10, #1
        mov w12, #0x5f
        strb w12, [x10]
        mov w12, #0x56
        strb w12, [x10, #1]
        lsr w12, w16, #4
        add w13, w12, #0x30
        add w14, w12, #0x37
        cmp w12, #10
        csel w12, w13, w14, lo
        strb w12, [x10, #2]
        and w12, w16, #0xf
        add w13, w12, #0x30
        add w14, w12, #0x37
        cmp w12, #10
        csel w12, w13, w14, lo
        strb w12, [x10, #3]
        strb wzr, [x10, #4]
        mov x2, sp
        bl #{lookup:#x}
        tbnz w0, #0, vh_done
        ldp x0, x1, [sp, #0x20]
        ldr x2, [sp, #0x18]
        bl #{lookup:#x}
    vh_done:
        ldp x29, x30, [sp, #0x30]
        add sp, sp, #0x40
        ret
    vh_pass:
        b #{lookup:#x}
"""


def check_dead_code(img):
    """The code we overwrite is the routine we name, of the class we name, and nothing
    can reach it: one pointer to it in the whole image (its class's vtable), no call,
    no address formed, no branch into it from outside, and no graph file that names
    the class (the game's Sequence graphs and every graph in the installed folders)."""
    import hashlib
    assert hashlib.sha1(img.data[DEAD[0] - img.base:DEAD[1] - img.base]).hexdigest() == DEAD_SHA1, \
        "the unused routine is not the one this patch was written for"
    q = np.frombuffer(img.data[:len(img.data) // 8 * 8], dtype="<u8")
    getters = [x for s in img.strings(DEAD_NODE) for x in img.xrefs(s) if img.u32(x + 8) == 0xD65F03C0]
    tables = [img.base + int(i) * 8 - VT_NAME for x in getters for i in np.nonzero(q == x)[0]]
    assert len(tables) == 1, tables
    assert struct.unpack_from("<Q", img.data, tables[0] + VT_ONESHOT - img.base)[0] == DEAD[0]
    assert int((q == DEAD[0]).sum()) == 1 and not img.calls_to(DEAD[0]) and not img.xrefs(DEAD[0])
    w = img.words.astype(np.int64)
    idx = np.nonzero((w & 0x7C000000) == 0x14000000)[0]                 # every b and bl
    imm = w[idx] & 0x3FFFFFF
    target = img.base + idx * 4 + np.where(imm & (1 << 25), imm - (1 << 26), imm) * 4
    site = img.base + idx * 4
    inside = (target >= DEAD[0]) & (target < DEAD[1])
    assert not (inside & ((site < DEAD[0]) | (site >= DEAD[1]))).any(), "a branch into the unused routine"
    graphs = glob.glob(os.path.join(bwav.ROMFS, "Sequence", "*.ainb*"))
    graphs += glob.glob(os.path.join(EDEN_LOAD, "*", "romfs", "**", "*.ainb*"), recursive=True)
    assert len(graphs) > 100
    for path in graphs:
        data = nxfmt.read(path) if path.endswith(".zs") else open(path, "rb").read()
        assert DEAD_NODE.encode() not in data, "%s uses %s" % (path, DEAD_NODE)


def voice_patch():
    """The code patch. Both places that look a list up in the archive list table by name
    call `voice_hook` instead of the table lookup (same arguments: table, out, name).
    The hook passes everything through but the names `Player_Child` / `Player_Adult`
    while the stored character (CHARACTER_FLAG, read as the model hook reads it) is 1 to
    255: then it asks for `<name>_V<two hex digits>` first (the name built on its own
    stack) and for the game's own list only when the table has no such list. So which
    character has a voice is decided by the table alone, and without the flag (the mod
    `Character models` switched off) or with Link stored nothing changes."""
    import exepatch, gamedata
    src = open(os.path.join(HERE, "build.py"), encoding="utf-8").read()
    assert "GAMEDATA_MGR_GOT = %#x" % GAMEDATA_MGR_GOT in src and 'CHARACTER_FLAG = "%s"' % CHARACTER_FLAG in src
    img = exepatch.Image()
    check_dead_code(img)
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = DEAD
    try:
        p = exepatch.Patcher(image=img, cave=DEAD)      # the signature words go to DEAD's start
    finally:
        exepatch.CAVE_REGION = region

    def place(name, addr, blob):
        """As Patcher._place, but over the unused routine's code instead of zeros."""
        assert DEAD[0] <= addr and addr + len(blob) <= DEAD[1], "the unused routine is full"
        p.raw(addr, blob)
        p.symbols[name] = addr
        p.cave_ptr = addr + len(blob)
        return addr
    p._place = place
    names = p.data("voice_lists", b"".join(n.encode().ljust(16, b"\0") for n in ("Player_Child", "Player_Adult")))
    h = gamedata.murmur3_32(CHARACTER_FLAG)
    p.cave("voice_hook", HOOK.format(names_page=names & ~0xFFF, names_off=names & 0xFFF,
                                     got_page=GAMEDATA_MGR_GOT & ~0xFFF, got_off=GAMEDATA_MGR_GOT & 0xFFF,
                                     hlo=h & 0xFFFF, hhi=h >> 16, lookup=TABLE_LOOKUP))
    for site in LOOKUP_SITES:
        before = "\n".join(img.dis(site - 0x18, 6))
        assert "#0x440" in before, "not the archive list table at %x" % site
        p.asm(site, "bl {voice_hook}", expect="bl #%#x" % TABLE_LOOKUP)
    return p


def check_patch(p):
    """Run the hook in unicorn with a made-up flag store and a stub for the table lookup
    that records what it is asked. -> number of runs."""
    import unicorn
    from unicorn import arm64_const as A
    import gamedata
    hook = p.symbols["voice_hook"]
    code = dict(p.patches)[hook]
    ins = list(p.img.cs.disasm(code, hook))
    assert len(ins) * 4 == len(code), "the hook does not disassemble"
    assert len([i for i in ins if i.mnemonic in ("bl", "b") and i.op_str == "#%#x" % TABLE_LOOKUP]) == 3
    h = gamedata.murmur3_32(CHARACTER_FLAG)
    DATA, STACK, RET = 0x20000000, 0x30000000, 0x40000000
    CELL, MGR, MAP, REC, NAME, OUT, TABLE = (DATA + o for o in (0, 0x100, 0x1000, 0x2000, 0x3000, 0x3100, 0x3200))
    saved = [A.UC_ARM64_REG_X19 + i for i in range(10)] + [A.UC_ARM64_REG_X29]
    runs = [0]

    def run(name, value, lists, mgr=True, flag=True, cap=8, crowd=False):
        mu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        for page in sorted({hook & ~0xFFF, TABLE_LOOKUP & ~0xFFF, GAMEDATA_MGR_GOT & ~0xFFF, RET}):
            mu.mem_map(page, 0x1000)
        for addr, data in p.patches:
            if addr & ~0xFFF == hook & ~0xFFF:
                mu.mem_write(addr, data)
        mu.mem_write(TABLE_LOOKUP, struct.pack("<I", 0xD65F03C0))      # ret
        mu.mem_write(GAMEDATA_MGR_GOT, struct.pack("<Q", CELL))
        mu.mem_map(DATA, 0x10000)
        mu.mem_map(STACK, 0x10000)
        mu.mem_write(CELL, struct.pack("<Q", MGR if mgr else 0))
        mu.mem_write(MGR + 0xe0, struct.pack("<Q", MAP))
        mu.mem_write(MGR + 0xec, struct.pack("<I", cap))
        mu.mem_write(MGR + 0xd0, struct.pack("<I", 5))
        mu.mem_write(MGR + 0xd8, struct.pack("<Q", REC))
        slot = h % cap
        if crowd:           # another flag sits in our first place: ours is one further on
            mu.mem_write(MAP + 8 * slot, struct.pack("<II", h ^ 0x10000, 1))
            slot = (slot + 1) % cap
        if flag:
            mu.mem_write(MAP + 8 * slot, struct.pack("<II", h, 3))
        mu.mem_write(REC + 3 * 0x28 + 0x1c, struct.pack("<I", value))
        mu.mem_write(NAME, name.encode() + b"\0")
        asked = []

        def on_lookup(uc, address, size, data):
            x2 = uc.reg_read(A.UC_ARM64_REG_X2)
            s = bytes(uc.mem_read(x2, 40)).split(b"\0")[0].decode()
            asked.append((uc.reg_read(A.UC_ARM64_REG_X0), uc.reg_read(A.UC_ARM64_REG_X1), x2, s))
            uc.reg_write(A.UC_ARM64_REG_X0, 1 if s in lists else 0)
        mu.hook_add(unicorn.UC_HOOK_CODE, on_lookup, begin=TABLE_LOOKUP, end=TABLE_LOOKUP)
        sp = STACK + 0x8000
        mu.reg_write(A.UC_ARM64_REG_SP, sp)
        mu.reg_write(A.UC_ARM64_REG_X0, TABLE)
        mu.reg_write(A.UC_ARM64_REG_X1, OUT)
        mu.reg_write(A.UC_ARM64_REG_X2, NAME)
        mu.reg_write(A.UC_ARM64_REG_X30, RET)
        for k, r in enumerate(saved):
            mu.reg_write(r, 0x1111000 + k)
        mu.emu_start(hook, RET, count=2000)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == RET and mu.reg_read(A.UC_ARM64_REG_SP) == sp, name
        assert [mu.reg_read(r) for r in saved] == [0x1111000 + k for k in range(len(saved))], name
        runs[0] += 1
        return [a[3] for a in asked], [a[:3] for a in asked], mu.reg_read(A.UC_ARM64_REG_X0) & 1

    own = (TABLE, OUT, NAME)
    for base in ("Player_Child", "Player_Adult"):
        for n in (1, 2, 9, 10, 15, 16, 0x63, 0xAB, 0xFF):
            want = LIST % (base, n)
            for kw in ({}, {"cap": 64}, {"crowd": True}, {"cap": 1}):
                # the voice list is there: one question, for it, with the game's table and out
                names, args, found = run(base, n, {want, base}, **kw)
                assert names == [want] and args[0][:2] == own[:2] and found == 1, (base, n, names)
                # it is not: then the game's own list, with the game's own arguments
                names, args, found = run(base, n, {base}, **kw)
                assert names == [want, base] and args[1] == own and found == 1, (base, n, names)
        assert run(base, 2, set())[2] == 0                                  # neither: not found, as before
        for kw in ({"mgr": False}, {"flag": False}):
            names, args, found = run(base, 2, {LIST % (base, 2), base}, **kw)
            assert names == [base] and args == [own] and found == 1
        for n in (0, 0x100, 0xFFFFFFFF):                                    # Link, and numbers no list can have
            names, args, found = run(base, n, {LIST % (base, n & 0xFF), base})
            assert names == [base] and args == [own]
    for other in ("Player", "Player_", "Player_Child_", "Player_Children", "player_child", "Npc_Saria", "", "Player_Adul"):
        names, args, found = run(other, 2, {other, LIST % (other, 2)})
        assert names == [other] and args == [own], other
    return runs[0]


def ips_ranges(path):
    """[(start, end)] of the bytes an IPS32 file changes (file offsets)."""
    d = open(path, "rb").read()
    assert d[:5] == b"IPS32", path
    o, out = 5, []
    while d[o:o + 4] != b"EEOF":
        at, n = struct.unpack_from(">IH", d, o)
        o += 6
        if n == 0:                                  # a run of one byte
            n = struct.unpack_from(">H", d, o)[0]
            o += 3
        else:
            o += n
        out.append((at, at + n))
    return out


def covers(ours, theirs, own):
    """Our size table has every entry of `theirs` with the same value; only the paths
    in `own` (the files we ship) may be larger."""
    raised = {zlib.crc32(path.encode()) for path in own}
    return all(ours.crc.get(h) == v or (h in raised and ours.crc.get(h, 0) > v) for h, v in theirs.crc.items()) and \
        all(ours.names.get(k) == v or (k in own and ours.names.get(k, 0) > v) for k, v in theirs.names.items())


def installed_table():
    """(the size table the installed folders ship, the folder it was read from), or
    (None, None) when none ships one. All copies must be the same table."""
    import rstb
    table, where = None, None
    for folder in sorted(os.listdir(EDEN_LOAD), key=str.lower) if os.path.isdir(EDEN_LOAD) else []:
        p = os.path.join(EDEN_LOAD, folder, "romfs", rstb.PATH.replace("/", os.sep))
        if folder == MOD or not os.path.exists(p):
            continue
        data = nxfmt.read(p)
        if table is None:
            table, where = data, folder
        assert data == table, "the installed size tables differ: %s, %s" % (where, folder)
    return table, where


def build():
    """Build MOD into build-voice\\mod. -> the lines of the report."""
    import rstb
    out = os.path.join(BUILD, MOD)
    shutil.rmtree(out, ignore_errors=True)
    sound = os.path.join(out, "romfs", "Sound", "Resource")
    os.makedirs(os.path.join(sound, "Stream"))
    numbers = ring()
    vo, names, d = assets()
    stock_raw = open(os.path.join(bwav.SOUND, BANK + ".bars.zs"), "rb").read()
    lines, lists, sizes, built = [], {}, {}, {}
    for voice, (_code, characters) in sorted(VOICES.items()):
        report = convert(voice, "--redo" in sys.argv,
                         int(sys.argv[sys.argv.index("--more") + 1]) if "--more" in sys.argv else 0)
        archive, streams = voice_archive(voice, report)
        name = ARCHIVE % label(voice).replace(" ", "_")
        with open(os.path.join(sound, name + ".zs"), "wb") as f:
            f.write(nxpack.zs_compress_like(archive, stock_raw))
        for s, blob in streams.items():
            with open(os.path.join(sound, "Stream", s + ".bwav"), "wb") as f:
                f.write(blob)
        sizes["Sound/Resource/" + name] = ((len(archive) + 31) & ~31) + 616     # the largest the game's archives have
        for c in characters:
            lists[LIST % (BANK, numbers[c])] = (BANK, name)
        built[voice] = (name, report, archive, streams)
        lines.append("%s: %s, %.1f MB, %d stream files (%.1f MB); characters: %s" % (
            label(voice), name, len(archive) / 1e6, len(streams), sum(map(len, streams.values())) / 1e6,
            ", ".join("%s = %d" % (c, numbers[c]) for c in characters)))
        lines += ["  " + line for line in check_voice(voice, *built[voice])]
    # the archive list
    new_list, expect = barslist(lists)
    raw = open(os.path.join(bwav.ROMFS, BARSLIST.replace("/", os.sep)), "rb").read()
    p = os.path.join(out, "romfs", BARSLIST.replace("/", os.sep))
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as f:
        f.write(nxpack.zs_compress_like(new_list, raw))
    sizes[BARSLIST[:-3]] = ((len(new_list) + 31) & ~31) + 264               # the game's own rule for this file
    back = byml.load(nxfmt.read(p))
    stock = byml.load(nxfmt.unzs(raw))
    assert back == expect and all(back["BarslistTable"][k] == v for k, v in stock["BarslistTable"].items())
    assert set(back["BarslistTable"]) - set(stock["BarslistTable"]) == set(lists)
    lines.append("archive list: %s added (%d -> %d bytes); the game's %d lists unchanged" % (
        ", ".join(sorted(lists)), len(nxfmt.unzs(raw)), len(new_list), len(stock["BarslistTable"])))
    # the size table: what is installed now, plus ours
    base, where = installed_table()
    stock_table = nxfmt.read(os.path.join(bwav.ROMFS, rstb.PATH.replace("/", os.sep)))
    t, before = rstb.Rstb(base or stock_table), rstb.Rstb(base or stock_table)
    for path, size in sorted(sizes.items()):
        if not os.path.exists(os.path.join(bwav.ROMFS, path.replace("/", os.sep) + ".zs")):
            assert t.get(path) is None, "size table crc32 collision for new file " + path
        old, new = t.ensure(path, size)
        lines.append("size table: %s %s -> %s" % (path, old, new))
    p = os.path.join(out, "romfs", rstb.PATH.replace("/", os.sep))
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as f:
        f.write(nxpack.zs_compress_like(t.to_bytes(), open(os.path.join(bwav.ROMFS, rstb.PATH.replace("/", os.sep)), "rb").read()))
    back = rstb.Rstb(nxfmt.read(p))
    assert covers(back, before, sizes) and all(back.get(path) >= size for path, size in sizes.items())
    assert len(back.crc) + len(back.names) - len(before.crc) - len(before.names) == sum(
        1 for path in sizes if before.get(path) is None)
    lines.append("  = the table of %s plus these" % ("the installed folder '%s'" % where if where else "the game"))
    # the code patch
    import exepatch
    patch = voice_patch()
    p = os.path.join(out, "exefs", exepatch.BUILD_ID + ".ips")
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as f:
        f.write(patch.ips32())
    with open(os.path.join(BUILD, MOD + ".listing.txt"), "w") as f:
        f.write(patch.listing())
    lines.append("code patch: %d bytes at %x-%x inside the unused %s (%x-%x), calls at %s; %d runs emulated" % (
        patch.cave_ptr - DEAD[0], DEAD[0], patch.cave_ptr, DEAD_NODE, DEAD[0], DEAD[1],
        ", ".join("%x" % s for s in LOOKUP_SITES), check_patch(patch)))
    lines.append(fits_installed())
    for voice, (_name, report, _archive, _streams) in sorted(built.items()):
        if report["kept"]:
            lines.append("%s, clips that stay Link's:" % label(voice))
            lines += ["    %-44s %s" % (a, why) for a, why in sorted(report["kept"].items())]
    return lines


def check_voice(voice, name, report, archive, streams):
    """Read a built voice archive and its stream files back from the folder."""
    out = os.path.join(BUILD, MOD, "romfs", "Sound", "Resource")
    cache = os.path.join(CACHE, voice, "child")
    vo, names, d = assets()
    new = nxfmt.read(os.path.join(out, name + ".zs"))
    assert new == archive
    parts, extra = bwav.bars_parts(new)
    assert not extra and bwav.bars_build(parts) == new
    by_hash = {zlib.crc32(a.encode()): a for a in vo}
    stock = {h: (amta, blob) for h, amta, blob in bwav.bars_parts(d)[0]}
    same = {a: first for a, first in report["same"].items() if first in report["converted"]}
    done = set(report["converted"]) | set(same)
    assert sorted(by_hash[h] for h, _a, _b in parts) == sorted(done), "the archive's assets are not the converted clips"
    n = struct.unpack_from("<I", new, 12)[0]
    offs = struct.unpack_from("<%dI" % (2 * n), new, 16 + 4 * n)
    snrs, files = [], set()
    for k, (h, amta, blob) in enumerate(parts):
        a = by_hash[h]
        amta0, blob0 = stock[h]
        o, o0 = offs[2 * k + 1], names[a]
        assert o % 64 == 0 and new[o:o + len(blob)] == blob and len(blob) == len(blob0)
        h1, h0 = bwav.header(new, o), bwav.header(d, o0)
        c1, c0 = h1["channels"][0], h0["channels"][0]
        for key in ("enc", "pan", "rate", "n_full", "n", "start", "loop", "loop_start", "loop_end"):
            assert c0[key] == c1[key], (a, key)
        assert h0["prefetch"] == h1["prefetch"] and len(h1["channels"]) == 1
        assert struct.unpack_from("<H", new, o + 0x10 + 0x44)[0] == new[o + c1["start"]], a
        assert struct.unpack_from("<I", amta, 0x28)[0] == h and len(amta) == len(amta0)
        want = np.load(os.path.join(cache, same.get(a, a) + ".npy")).astype(np.float64)
        if h1["prefetch"]:
            s_name = stream_name(voice, a)
            at = 0x24 + struct.unpack_from("<I", amta, 0x24)[0]
            assert amta[at:amta.index(b"\0", at)].decode() == s_name
            assert amta[:at] == amta0[:at] and amta[at + len(s_name):] == amta0[at + len(s_name):], a
            s = open(os.path.join(out, "Stream", s_name + ".bwav"), "rb").read()
            assert s == streams[s_name] and len(s) == os.path.getsize(os.path.join(bwav.STREAM, a + ".bwav"))
            hs = bwav.header(s)["channels"][0]
            here = (c1["n"] + 13) // 14 * 8
            assert new[o + c1["start"]:o + c1["start"] + here] == s[hs["start"]:hs["start"] + here], a
            assert new[o + 8:o + 12] == s[8:12] and hs["coefs"] == c1["coefs"] and hs["n"] == c1["n_full"], a
            assert struct.unpack_from("<I", s, 8)[0] == zlib.crc32(s[hs["start"]:]), a
            got = bwav.decode(s)[0][0].astype(np.float64) * 32768
            files.add(s_name)
        else:
            assert amta == amta0, a
            data = new[o + c1["start"]:o + c1["start"] + (c1["n"] + 13) // 14 * 8]
            assert struct.unpack_from("<I", new, o + 8)[0] == zlib.crc32(data), a
            got = bwav.decode(new, o)[0][0].astype(np.float64) * 32768
        noise = ((got - want) ** 2).mean()
        snrs.append(10 * np.log10(max(1e-9, (want ** 2).mean()) / max(1e-9, noise)))
    on_disk = set(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(out, "Stream", "V%s*.bwav" % VOICES[voice][0])))
    assert on_disk == files == set(streams), "stream files and the archive's streams differ"
    # the game's search, as its code does it (README, voices section): the list's archives
    # in order, the first whose hash table has the name's crc32 answers
    order = [set(h for h, _a, _b in parts)]
    for other in ("Player", BANK):
        dd = d if other == BANK else nxfmt.read(os.path.join(bwav.SOUND, other + ".bars.zs"))
        m = struct.unpack_from("<I", dd, 12)[0]
        order.append(set(struct.unpack_from("<%dI" % m, dd, 16)))
    first = {a: next(i for i, hashes in enumerate(order) if zlib.crc32(a.encode()) in hashes) for a in vo}
    assert all(first[a] == (0 if a in done else 2) for a in vo), "a clip is found in the wrong archive"
    assert not (order[0] - set(by_hash)), "the voice archive answers for a name that is not a voice clip"
    snrs = np.array(snrs)
    return ["%d of %d voice clips in the voice (%d of them streams), %d stay Link's" % (
                len(done), len(vo), len(files), len(vo) - len(done)),
            "the game's search finds every converted clip in the voice archive, every other clip in Link's",
            "encoding: signal to noise median %.0f dB, worst %.0f dB" % (np.median(snrs), snrs.min())]


def refuse_while_running():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    running = [e for e in EMULATORS if '"%s"' % e in out]
    if running:
        print("The emulator is running (%s). Close it first." % ", ".join(running))
        sys.exit(1)


def remove():
    for old in glob.glob(os.path.join(EDEN_LOAD, FOLDER % "*")) + [os.path.join(EDEN_LOAD, MOD)]:
        if os.path.isdir(old):
            shutil.rmtree(old)
            print("removed", old)


def fits_installed():
    """MOD against the folders that are installed now. -> a line for the report."""
    import exepatch, rstb
    others = [f for f in os.listdir(EDEN_LOAD) if os.path.isdir(os.path.join(EDEN_LOAD, f)) and f != MOD]
    # our size table must be the one the emulator takes, and must have all of theirs
    assert all(MOD < f and MOD.lower() < f.lower() for f in others), "a folder sorts before ours"
    base, where = installed_table()
    ours = rstb.Rstb(nxfmt.read(os.path.join(BUILD, MOD, "romfs", rstb.PATH.replace("/", os.sep))))
    if base:
        own = [BARSLIST[:-3]] + ["Sound/Resource/" + ARCHIVE % label(v).replace(" ", "_") for v in VOICES]
        assert covers(ours, rstb.Rstb(base), own), "the installed size table changed since this build: build again"
    # no byte of the executable patched twice, no file of another folder shadowed but the size table
    mine = ips_ranges(os.path.join(BUILD, MOD, "exefs", exepatch.BUILD_ID + ".ips"))
    patches = 0
    for f in others:
        for p in glob.glob(os.path.join(EDEN_LOAD, f, "exefs", "*.ips")):
            patches += 1
            for a0, a1 in ips_ranges(p):
                assert all(a1 <= b0 or b1 <= a0 for b0, b1 in mine), "%s patches our bytes at %x" % (f, a0)
    root = os.path.join(BUILD, MOD, "romfs")
    for folder, _dirs, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(folder, name), root)
            if rel.replace(os.sep, "/") != rstb.PATH:
                clash = [f for f in others if os.path.exists(os.path.join(EDEN_LOAD, f, "romfs", rel))]
                assert not clash or all(f.startswith(FOLDER % "") for f in clash), "%s is also in %s" % (rel, clash)
    return "against the %d installed folders: ours sorts first, its size table has all of theirs, " \
           "no byte shared with their %d code patches, no file of theirs replaced" % (len(others), patches)


def install(mod):
    refuse_while_running()
    remove()
    if mod == MOD:
        print("  " + fits_installed())
    dst = os.path.join(EDEN_LOAD, mod)
    shutil.copytree(os.path.join(BUILD, mod), dst)
    print("installed ->", dst)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-") and not a.isdigit()]
    if "--remove" in sys.argv:
        refuse_while_running()
        remove()
        return
    if "--everyone" in sys.argv:
        voice = args[0] if args else "Custom_Saria"
        more = int(sys.argv[sys.argv.index("--more") + 1]) if "--more" in sys.argv else 0
        report = convert(voice, "--redo" in sys.argv, more)
        mod = pack_everyone(voice, report)
        print(mod)
        for line in check_everyone(voice, report, mod):
            print("  " + line)
        if report["kept"]:
            print("  keep Link's original:")
            for a, why in sorted(report["kept"].items()):
                print("    %-44s %s" % (a, why))
    else:
        mod = MOD
        lines = build()
        print(mod)
        for line in lines:
            print("  " + line)
    if "--install" in sys.argv:
        install(mod)


if __name__ == "__main__":
    main()
