"""Read the game's sound clips: BARS archives and BWAV files (read only).

A BARS archive (`Sound/Resource/<bank>.bars.zs`) holds one metadata block and
one BWAV per asset. Long clips are "prefetch" BWAVs: the archive has only the
head, the whole clip is `Sound/Resource/Stream/<asset>.bwav`.

    python tools\\bwav.py list Player_Child [regex]
    python tools\\bwav.py wav  <asset> <out.wav> [bank]
"""
import os, re, struct, sys, wave, zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nxfmt

ROMFS = os.environ.get("OOT_ROMFS", r"%USERPROFILE%\Games\Hookshot\romfs")
SOUND = os.path.join(ROMFS, "Sound", "Resource")
STREAM = os.path.join(SOUND, "Stream")


def bars(bank):
    """{asset name: BWAV bytes offset} and the archive's bytes."""
    d = nxfmt.read(os.path.join(SOUND, bank + ".bars.zs"))
    assert d[:4] == b"BARS"
    n = struct.unpack_from("<I", d, 12)[0]
    hashes = struct.unpack_from("<%dI" % n, d, 16)
    offs = struct.unpack_from("<%dI" % (2 * n), d, 16 + 4 * n)
    out = {}
    for i in range(n):
        ao, bo = offs[2 * i], offs[2 * i + 1]
        rel = struct.unpack_from("<I", d, ao + 0x24)[0]
        p = ao + 0x24 + rel
        name = d[p:d.index(b"\0", p)].decode("latin1")
        if zlib.crc32(name.encode()) != hashes[i]:
            name = "%08x" % hashes[i]
        out[name] = bo
    return out, d


def header(d, o=0):
    assert d[o:o + 4] == b"BWAV", d[o:o + 4]
    prefetch, nch = struct.unpack_from("<HH", d, o + 0x0C)
    chans = []
    for c in range(nch):
        b = o + 0x10 + 0x4C * c
        enc, pan, rate, n_full, n_here = struct.unpack_from("<HHIII", d, b)
        coefs = struct.unpack_from("<16h", d, b + 0x10)
        start_full, start_here, loop, loop_end, loop_start = struct.unpack_from("<5I", d, b + 0x30)
        chans.append(dict(enc=enc, pan=pan, rate=rate, n_full=n_full, n=n_here, coefs=coefs,
                          start=start_here, loop=loop, loop_start=loop_start, loop_end=loop_end))
    return dict(prefetch=prefetch, channels=chans)


def _adpcm(d, start, n, coefs):
    """Nintendo DSP ADPCM: 8-byte frames, a header byte and 14 samples."""
    out = np.zeros(n, dtype=np.int16)
    h1 = h2 = 0
    pos, i = start, 0
    while i < n:
        head = d[pos]
        scale = 1 << (head & 15)
        c1, c2 = coefs[2 * (head >> 4)], coefs[2 * (head >> 4) + 1]
        for k in range(14):
            if i >= n:
                break
            byte = d[pos + 1 + k // 2]
            nib = (byte >> 4) if k % 2 == 0 else (byte & 15)
            if nib >= 8:
                nib -= 16
            s = ((nib * scale) << 11) + 1024 + c1 * h1 + c2 * h2 >> 11
            s = max(-32768, min(32767, s))
            out[i] = s
            h2, h1 = h1, s
            i += 1
        pos += 8
    return out


def decode(d, o=0):
    """(samples as float in -1..1, one row per channel; sample rate)."""
    h = header(d, o)
    rows = []
    for ch in h["channels"]:
        if ch["enc"] == 1:
            rows.append(_adpcm(d, o + ch["start"], ch["n"], ch["coefs"]))
        elif ch["enc"] == 0:
            rows.append(np.frombuffer(d, "<i2", ch["n"], o + ch["start"]))
        else:
            raise ValueError("encoding %d" % ch["enc"])
    return np.stack(rows).astype(np.float32) / 32768.0, h["channels"][0]["rate"]


# ---------------------------------------------------------------- writing clips
# The encoder: eight predictor pairs per clip (fitted to the clip: frames are grouped
# by which pair predicts them best, each pair refitted to its group, a few rounds), then
# every 14-sample frame tries each pair at two step sizes with the decoder's own
# arithmetic and keeps the one with the least error. Many clips are encoded side by
# side (one numpy row each), which is what makes it fast enough in Python.
_START = [(0.0, 0.0), (0.9, 0.0), (1.8, -0.9), (1.5, -0.6), (1.2, -0.4), (1.95, -0.97), (0.5, 0.2), (1.65, -0.75)]


def _fit_coefs(x, rounds=12):
    """16 coefficients (8 pairs, 1/2048 units) for one clip of int samples."""
    n = len(x) // 14 * 14
    if n < 28:
        return [int(round(v * 2048)) for pair in _START for v in pair]
    p = np.concatenate([[0.0, 0.0], x[:n].astype(np.float64)])
    s0, s1, s2 = p[2:].reshape(-1, 14), p[1:-1].reshape(-1, 14), p[:-2].reshape(-1, 14)
    r0, r1, r2 = (s0 * s0).sum(1), (s0 * s1).sum(1), (s0 * s2).sum(1)
    r11, r12, r22 = (s1 * s1).sum(1), (s1 * s2).sum(1), (s2 * s2).sum(1)
    pairs = np.array(_START)
    for _ in range(rounds):
        a1, a2 = pairs[:, 0][:, None], pairs[:, 1][:, None]
        err = r0 - 2 * a1 * r1 - 2 * a2 * r2 + a1 * a1 * r11 + 2 * a1 * a2 * r12 + a2 * a2 * r22
        best = err.argmin(0)
        for k in range(8):
            m = best == k
            if not m.any():
                continue
            a = np.array([[r11[m].sum() + 1.0, r12[m].sum()], [r12[m].sum(), r22[m].sum() + 1.0]])
            pairs[k] = np.linalg.solve(a, np.array([r1[m].sum(), r2[m].sum()]))
    pairs = np.clip(pairs, -15.9, 15.9)
    return [int(round(v * 2048)) for pair in pairs for v in pair]


def encode_adpcm(clips):
    """[(16 coefficients, data bytes)] for a list of int16 clips (DSP ADPCM)."""
    count = len(clips)
    frames = [(len(c) + 13) // 14 for c in clips]
    most = max(frames)
    x = np.zeros((count, most * 14), dtype=np.int64)
    for i, c in enumerate(clips):
        x[i, :len(c)] = c
    coefs = [_fit_coefs(np.asarray(c)) for c in clips]
    ca = np.array(coefs, dtype=np.int64).reshape(count, 8, 2)
    c1 = np.repeat(ca[:, :, 0], 2, axis=1)            # (clips, 16): pair k at two step sizes
    c2 = np.repeat(ca[:, :, 1], 2, axis=1)
    h1 = np.zeros(count, dtype=np.int64)
    h2 = np.zeros(count, dtype=np.int64)
    out = np.zeros((count, most, 8), dtype=np.uint8)
    rows = np.arange(count)
    for f in range(most):
        fr = x[:, f * 14:f * 14 + 14]
        # step size from the prediction error with the true samples as history
        m1 = np.concatenate([h1[:, None], fr[:, :-1]], axis=1)
        m2 = np.concatenate([h2[:, None], h1[:, None], fr[:, :-2]], axis=1)
        res = fr[:, None, :] - ((ca[:, :, 0:1] * m1[:, None, :] + ca[:, :, 1:2] * m2[:, None, :] + 1024) >> 11)
        peak = np.abs(res).max(axis=2)                                   # (clips, 8)
        exp = np.clip(np.ceil(np.log2(np.maximum(peak, 1) / 7.5)), 0, 12).astype(np.int64)
        exps = np.clip(np.stack([exp, exp + 1], axis=2).reshape(count, 16), 0, 12)
        scale = 1 << exps
        a1 = np.repeat(h1[:, None], 16, axis=1)
        a2 = np.repeat(h2[:, None], 16, axis=1)
        err = np.zeros((count, 16), dtype=np.int64)
        nibs = np.zeros((count, 16, 14), dtype=np.int64)
        for k in range(14):
            base = 1024 + c1 * a1 + c2 * a2
            want = fr[:, k:k + 1]
            nib = np.clip(np.rint((want - (base >> 11)) / scale), -8, 7).astype(np.int64)
            got = np.clip((((nib * scale) << 11) + base) >> 11, -32768, 32767)
            err += (got - want) ** 2
            nibs[:, :, k] = nib
            a2, a1 = a1, got
        pick = err.argmin(axis=1)
        h1, h2 = a1[rows, pick], a2[rows, pick]
        chosen = nibs[rows, pick] & 15                                   # (clips, 14)
        out[:, f, 0] = ((pick // 2) << 4) | exps[rows, pick]
        out[:, f, 1:] = (chosen[:, 0::2] << 4) | chosen[:, 1::2]
    return [(coefs[i], out[i, :frames[i]].tobytes()) for i in range(count)]


def rewritten(original, coefs, data, start=0):
    """A BWAV's header (bytes up to its sample data) with new coefficients, the new
    data's checksum and first frame header; everything else as in `original`."""
    h = header(original, start)
    assert len(h["channels"]) == 1 and h["channels"][0]["enc"] == 1
    head = bytearray(original[start:start + h["channels"][0]["start"]])
    struct.pack_into("<I", head, 8, zlib.crc32(data))
    struct.pack_into("<16h", head, 0x10 + 0x10, *coefs)
    struct.pack_into("<Hhh", head, 0x10 + 0x44, data[0], 0, 0)
    return bytes(head)


def bwav_size(d, o=0):
    """Bytes of the BWAV at `o` (header and the sample data that is there)."""
    return max(c["start"] + (c["n"] + 13) // 14 * 8 if c["enc"] == 1 else c["start"] + 2 * c["n"]
               for c in header(d, o)["channels"])


def bars_parts(d):
    """A BARS archive taken apart: ([(name hash, AMTA block, BWAV or None)] in table
    order, the hashes of the second table). `bars_build` puts it together again."""
    n = struct.unpack_from("<I", d, 12)[0]
    hashes = struct.unpack_from("<%dI" % n, d, 16)
    offs = struct.unpack_from("<%dI" % (2 * n), d, 16 + 4 * n)
    m = struct.unpack_from("<I", d, 16 + 12 * n)[0]
    extra = struct.unpack_from("<%dI" % m, d, 20 + 12 * n)
    out = []
    for i in range(n):
        a, b = offs[2 * i], offs[2 * i + 1]
        assert d[a:a + 4] == b"AMTA"
        size = struct.unpack_from("<I", d, a + 8)[0]
        out.append((hashes[i], bytes(d[a:a + size]), None if b == 0xFFFFFFFF else bytes(d[b:b + bwav_size(d, b)])))
    return out, list(extra)


def bars_build(assets, extra=(), order=None):
    """A BARS archive (version 0x102) from [(name hash, AMTA block, BWAV or None)].
    Layout as the game's files: header, the hashes (sorted), per asset the offsets of
    its AMTA and its BWAV, a second table (count + hashes: a subset of the assets, the
    looping slide sounds in the player's archive; empty in voice archives), the AMTA
    blocks in table order, then each BWAV at a multiple of 64. Equal BWAVs are stored
    once, as in the game's files (two names, one clip). `order`: the assets' indices in
    the order their BWAVs are stored (default: as given)."""
    table = sorted(range(len(assets)), key=lambda i: assets[i][0])
    n = len(assets)
    assert len(set(a[0] for a in assets)) == n, "two assets with one name hash"
    pos = 16 + 12 * n + 4 + 4 * len(extra)
    amta_at = {}
    for i in table:
        assert len(assets[i][1]) % 4 == 0 and struct.unpack_from("<I", assets[i][1], 8)[0] == len(assets[i][1])
        amta_at[i] = pos
        pos += len(assets[i][1])
    bwav_at, placed, blobs = {}, {}, []
    for i in (order if order is not None else range(n)):
        blob = assets[i][2]
        if blob is None:
            bwav_at[i] = 0xFFFFFFFF
            continue
        if blob not in placed:
            pos = (pos + 63) & ~63
            placed[blob] = pos
            blobs.append((pos, blob))
            pos += len(blob)
        bwav_at[i] = placed[blob]
    out = bytearray(pos)
    struct.pack_into("<4sIHHI", out, 0, b"BARS", pos, 0xFEFF, 0x102, n)
    struct.pack_into("<%dI" % n, out, 16, *[assets[i][0] for i in table])
    for k, i in enumerate(table):
        struct.pack_into("<II", out, 16 + 4 * n + 8 * k, amta_at[i], bwav_at[i])
        out[amta_at[i]:amta_at[i] + len(assets[i][1])] = assets[i][1]
    struct.pack_into("<I%dI" % len(extra), out, 16 + 12 * n, len(extra), *sorted(extra))
    for at, blob in blobs:
        out[at:at + len(blob)] = blob
    return bytes(out)


def clip(asset, bank=None, _cache={}):
    """The whole clip of an asset as the game plays it: the archive's own if it is
    whole there, else its stream file. (Three child voice assets are whole in the
    archive and also have a stream file of the same name with another, longer take:
    `Climb_02`, `Climb_03`, `Dm_TimeTemple_0030_0004`. The game cannot reach those files.)
    Without `bank`: the stream file."""
    p = os.path.join(STREAM, asset + ".bwav")
    if bank is None:
        return decode(open(p, "rb").read())
    if bank not in _cache:
        _cache[bank] = bars(bank)
    names, d = _cache[bank]
    if header(d, names[asset])["prefetch"]:
        return decode(open(p, "rb").read())
    return decode(d, names[asset])


def write_wav(path, samples, rate):
    x = np.clip(np.atleast_2d(samples), -1, 1)
    pcm = (x.T * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "list":
        names, d = bars(sys.argv[2])
        pat = sys.argv[3] if len(sys.argv) > 3 else ""
        for name in sorted(names):
            if re.search(pat, name):
                h = header(d, names[name])
                c = h["channels"][0]
                print("%-56s %dch enc%d %d Hz %6.2f s%s%s" % (
                    name, len(h["channels"]), c["enc"], c["rate"], c["n_full"] / c["rate"],
                    "  stream, head %.2f s" % (c["n"] / c["rate"]) if h["prefetch"] else "",
                    "  loop" if c["loop"] else ""))
    elif cmd == "wav":
        s, rate = clip(sys.argv[2], sys.argv[4] if len(sys.argv) > 4 else None)
        write_wav(sys.argv[3], s, rate)
        print("%s: %d channel(s), %d Hz, %.2f s, peak %.2f" % (sys.argv[3], s.shape[0], rate, s.shape[1] / rate, abs(s).max()))
