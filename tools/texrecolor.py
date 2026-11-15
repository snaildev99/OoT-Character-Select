"""Recolour a model texture file (Tex/<name>.bntx) in place: same size, same format, same mips, same alpha.

    data = recolour(original_bntx_bytes, new_name, fn)      # fn: (n, 3) floats 0..1 -> (n, 3) floats 0..1
    venv\\Scripts\\python.exe -I tools\\texrecolor.py        self test on a few of the game's textures

(customization session, 2026-11-14.) A hair's or a garment's colour is its texture's, and a
texture of another colour has to be a file of its own. Instead of decoding, repainting and
encoding again (which would need an encoder for BC7, a writer for mip chains, and would move
the cut-out alpha that shapes the strands), the colours are changed where a compressed block
keeps them: its END POINTS. Every 4 x 4 block of BC1 / BC3 / BC7 stores two (or four, six)
end point colours and, per texel, a place between them; `fn` is applied to the end points
and everything else (the places, the partitions, the alpha, the block order of the console,
every mip) is left as it is. So the result is exactly the old texture with every block's
palette recoloured.

- BC1 (8 bytes): two RGB565 end points, 2-bit places. The order of the two decides whether
  the block has a transparent place; the order is kept (swapped back, with the places, when
  `fn` turns it round; nudged apart when it makes them equal).
- BC3 (16): 8 bytes of alpha, then a BC1 colour block without the order rule.
- BC7 (16): eight modes (the mode is the place of the lowest set bit); the end points' red,
  green and blue fields (and, in modes 4 and 5, the alpha field that a "rotation" has
  swapped with one of them) are read, widened to 8 bits with their p-bit, changed, and
  written back at the field's width with the p-bit as it was.
Other formats are refused. The texture's name in the file is changed to `new_name`, which
must be as long as the old one (nothing in the file moves); its one-entry name dictionary
is rebuilt.
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bntx  # noqa: E402
import bntxwrite  # noqa: E402

# BC7: per mode (start bit of the colour fields, end points, bits a colour, alpha bits, p-bits, p-bit shared by a subset's two)
BC7 = {0: (5, 6, 4, 0, 6, False), 1: (8, 4, 6, 0, 2, True), 2: (9, 6, 5, 0, 0, False), 3: (10, 4, 7, 0, 4, False),
       4: (8, 2, 5, 6, 0, False), 5: (8, 2, 7, 8, 0, False), 6: (7, 2, 7, 7, 2, False), 7: (14, 4, 5, 5, 4, False)}


def _field(bits, start, width):
    """(n, 128) bits -> (n,) value of `width` bits from `start` (lowest bit first)."""
    return bits[:, start:start + width].astype(np.int64) @ (1 << np.arange(width, dtype=np.int64))


def _put(bits, start, width, value):
    bits[:, start:start + width] = (value[:, None] >> np.arange(width, dtype=np.int64)) & 1


def _widen(q, n, p=None):
    """A field of n bits (with a p-bit below it, if any) as 8 bits, the way a decoder does."""
    if p is not None:
        q, n = (q << 1) | p, n + 1
    return (q << (8 - n)) | (q >> (2 * n - 8))


def _narrow(c, n, p=None):
    """The n-bit field whose widening (with the given p-bit) is nearest to the 8-bit c."""
    if p is None:
        return np.clip(np.rint(c * ((1 << n) - 1) / 255.0), 0, (1 << n) - 1).astype(np.int64)
    v = c * ((1 << (n + 1)) - 1) / 255.0
    return np.clip(np.rint((v - p) / 2.0), 0, (1 << n) - 1).astype(np.int64)


def bc7_blocks(blocks, fn):
    """(n, 16) uint8 BC7 blocks with fn applied to every end point colour. Blocks of no mode
    (all zeros: padding) are left as they are."""
    out = blocks.copy()
    first = blocks[:, 0]
    low = first & (-first.astype(np.int16)).astype(np.uint8)            # the lowest set bit of the first byte
    for mode, (start, ends, cb, ab, pbits, shared) in BC7.items():
        sel = np.nonzero(low == (1 << mode))[0]
        if not len(sel):
            continue
        bits = np.unpackbits(blocks[sel], axis=1, bitorder="little").astype(np.int64)
        r0, g0, b0 = start, start + ends * cb, start + 2 * ends * cb
        a0 = b0 + ends * cb
        p0 = a0 + ends * ab
        rot = _field(bits, mode + 1, 2) if mode in (4, 5) else None
        for e in range(ends):
            p = None
            if pbits:
                p = bits[:, p0 + (e // 2 if shared else e)]
            raw = [_field(bits, base + e * cb, cb) for base in (r0, g0, b0)]
            rgb = np.stack([_widen(q, cb, p) for q in raw], axis=1).astype(np.float64)
            if rot is not None:             # the alpha field holds one of the colours (and that colour's field the alpha)
                alpha = _field(bits, a0 + e * ab, ab)
                a8 = _widen(alpha, ab)
                for k in range(3):
                    rgb[rot == k + 1, k] = a8[rot == k + 1]
            new = np.clip(fn(rgb / 255.0), 0.0, 1.0) * 255.0
            for k, base in enumerate((r0, g0, b0)):
                q = _narrow(new[:, k], cb, p)
                if rot is not None:
                    q = np.where(rot == k + 1, raw[k], q)       # this field is the alpha: as it was
                _put(bits, base + e * cb, cb, q)
            if rot is not None:
                qa = alpha.copy()
                for k in range(3):
                    m = rot == k + 1
                    qa[m] = _narrow(new[m, k], ab)
                _put(bits, a0 + e * ab, ab, qa)
        out[sel] = np.packbits(bits.astype(np.uint8), axis=1, bitorder="little")
    return out


def _from565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=1).astype(np.float64)


def _to565(rgb):
    r = np.clip(np.rint(rgb[:, 0] * 31 / 255.0), 0, 31).astype(np.int64)
    g = np.clip(np.rint(rgb[:, 1] * 63 / 255.0), 0, 63).astype(np.int64)
    b = np.clip(np.rint(rgb[:, 2] * 31 / 255.0), 0, 31).astype(np.int64)
    return (r << 11) | (g << 5) | b


def bc1_blocks(blocks, fn, ordered=True):
    """(n, 8) uint8 BC1 colour blocks with fn applied to both end points. ordered: the order of
    the two end points means something (BC1 itself) and is kept."""
    c = blocks[:, :4].copy().view("<u2").astype(np.int64)
    idx = blocks[:, 4:8].copy().view("<u4")[:, 0].astype(np.int64)
    new = [_to565(np.clip(fn(_from565(c[:, k]) / 255.0), 0.0, 1.0) * 255.0) for k in range(2)]
    n0, n1 = new
    if ordered:
        was = c[:, 0] > c[:, 1]                      # four colours; else three and a transparent place
        same = (n0 == n1) & was
        up = same & (n0 < 0xFFFF)
        n0 = np.where(up, n0 + 1, n0)                # one step apart (the lowest blue bit), the larger first
        n1 = np.where(same & ~up, n1 - 1, n1)
        turned = (n0 > n1) != was
        turned &= ~((n0 == n1) & ~was)               # equal is the three colour kind already
        n0, n1 = np.where(turned, n1, n0), np.where(turned, n0, n1)
        # the places of a block whose end points changed sides: 0 <-> 1 always; 2 <-> 3 in the four colour kind only
        lo, hi = idx & 0x55555555, (idx >> 1) & 0x55555555
        four = np.where(hi != 0, idx ^ 0x55555555, idx ^ 0x55555555) if False else idx ^ 0x55555555
        three = idx ^ ((~hi) & 0x55555555)
        idx = np.where(turned, np.where(was, four, three), idx)
    out = blocks.copy()
    out[:, :4] = np.stack([n0, n1], axis=1).astype("<u2").view(np.uint8).reshape(-1, 4)
    out[:, 4:8] = idx.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


# ---------------------------------------------------------------- ASTC
# An ASTC block has no end points to change in place (its colours are coded in one of many
# ways, with the bit widths traded against the weights'), so ASTC is decoded, repainted and
# encoded again; but block by block, never as a picture. A block is sixteen bytes that
# decode by themselves, and the console keeps a texture's blocks (every mip's, in its own
# order, with padding between) as a plain run of them. So: the run is decoded as it lies
# (the blocks laid out in a grid of our own), `fn` repaints the texels, the blocks it
# changed are encoded again and put back where they were. Nothing has to be known about
# the console's block order or the mips, and a block `fn` leaves alone (and every padding
# block) keeps its bytes.
ASTC_QUALITY = 80.0         # the encoder's 0..100 (60 = its "medium", 98 = "thorough")
ASTC_COLS = 256             # blocks a row in the grid they are decoded and encoded in
_astc_contexts = {}


def astc_size(fmt):
    """(block width, block height) of a format named ASTC<w>x<h>, else None."""
    if not fmt.startswith("ASTC"):
        return None
    w, h = fmt[4:].split("x")
    return int(w), int(h)


def _grid(n):
    cols = min(n, ASTC_COLS)
    return cols, -(-n // cols)


def astc_decode_blocks(blocks, bw, bh):
    """(n, 16) uint8 ASTC blocks -> (n, bh, bw, 4) uint8 RGBA, every block by itself."""
    import texture2ddecoder as t2d
    n = len(blocks)
    cols, rows = _grid(n)
    padded = np.zeros((rows * cols, 16), np.uint8)
    padded[:n] = blocks
    raw = t2d.decode_astc(padded.tobytes(), cols * bw, rows * bh, bw, bh)       # BGRA
    img = np.frombuffer(raw, np.uint8).reshape(rows, bh, cols, bw, 4)[..., [2, 1, 0, 3]]
    return img.transpose(0, 2, 1, 3, 4).reshape(rows * cols, bh, bw, 4)[:n].copy()


def astc_encode_blocks(texels, bw, bh, srgb=True, quality=None):
    """(n, bh, bw, 4) uint8 RGBA -> (n, 16) uint8 ASTC blocks."""
    import astc_encoder as ae
    n = len(texels)
    cols, rows = _grid(n)
    padded = np.zeros((rows * cols, bh, bw, 4), np.uint8)
    padded[:n] = texels
    padded[n:] = texels[-1]
    img = padded.reshape(rows, cols, bh, bw, 4).transpose(0, 2, 1, 3, 4).reshape(rows * bh, cols * bw, 4)
    key = (bw, bh, bool(srgb), ASTC_QUALITY if quality is None else quality)
    if key not in _astc_contexts:
        cfg = ae.ASTCConfig(ae.ASTCProfile.LDR_SRGB if srgb else ae.ASTCProfile.LDR, bw, bh, 1, key[3])
        _astc_contexts[key] = ae.ASTCContext(cfg, threads=max(1, (os.cpu_count() or 2) - 1))
    out = _astc_contexts[key].compress(ae.ASTCImage(ae.ASTCType.U8, cols * bw, rows * bh, 1, np.ascontiguousarray(img).tobytes()),
                                       ae.ASTCSwizzle.from_str("RGBA"))
    assert len(out) == rows * cols * 16
    return np.frombuffer(out, np.uint8).reshape(-1, 16)[:n].copy()


def astc_blocks(blocks, bw, bh, fn, srgb=True):
    """(n, 16) ASTC blocks with fn applied to every texel's colour; the alpha is given to the
    encoder as it was decoded. Blocks fn does not change by more than a step of 255, and
    blocks of zeros (padding), keep their bytes. -> (new blocks, how many were encoded)"""
    tex = astc_decode_blocks(blocks, bw, bh)
    n = len(blocks)
    rgb = tex[..., :3].reshape(-1, 3).astype(np.float64) / 255.0
    new = np.rint(np.clip(fn(rgb), 0.0, 1.0) * 255.0).astype(np.int16).reshape(n, bh * bw, 3)
    old = tex[..., :3].reshape(n, bh * bw, 3).astype(np.int16)
    changed = (np.abs(new - old).max(axis=(1, 2)) > 1) & blocks.any(axis=1)
    out = blocks.copy()
    if changed.any():
        texels = tex[changed].copy()
        texels[..., :3] = new[changed].reshape(-1, bh, bw, 3).astype(np.uint8)
        out[changed] = astc_encode_blocks(texels, bw, bh, srgb)
    return out, int(changed.sum())


def data_range(d):
    """(texture, start, end) of the one texture's data (every mip) in a Tex file."""
    arc = bntx.Bntx(d)
    assert len(arc.tex) == 1, "more than one texture in the file"
    t = list(arc.tex.values())[0]
    brtd = d.find(b"BRTD")
    rlt = struct.unpack_from("<I", d, 0x18)[0]
    assert d[rlt:rlt + 4] == b"_RLT" and brtd > 0
    assert brtd + 16 <= t.data_off and t.data_off + t.size <= rlt, (brtd, t.data_off, t.size, rlt)
    return t, t.data_off, t.data_off + t.size


def can_recolour(t):
    """A texture this can recolour: BC1 / BC3 / BC7 (end points) or ASTC (decoded and encoded
    again), its colours stored as red, green, blue."""
    return (t.format in ("BC1", "BC3", "BC7") or astc_size(t.format) is not None) and tuple(t.comp[:3]) == (2, 3, 4)


def recolour(data, new_name, fn):
    """The file's bytes with every block's colours through fn and the texture renamed."""
    d = bytearray(data)
    t, start, end = data_range(bytes(d))
    assert t.format in ("BC1", "BC3", "BC7") or astc_size(t.format), "a format this does not recolour: %s (%s)" % (t.format, t.name)
    size = 8 if t.format == "BC1" else 16
    assert (end - start) % size == 0
    blocks = np.frombuffer(bytes(d[start:end]), np.uint8).reshape(-1, size)
    if astc_size(t.format):
        new, _encoded = astc_blocks(blocks, *astc_size(t.format), fn, srgb=t.srgb)
    elif t.format == "BC7":
        new = bc7_blocks(blocks, fn)
    elif t.format == "BC1":
        new = bc1_blocks(blocks, fn)
    else:
        new = blocks.copy()
        new[:, 8:] = bc1_blocks(blocks[:, 8:], fn, ordered=False)
    d[start:end] = new.tobytes()
    rename(d, t.name, new_name)
    back = bntx.Bntx(bytes(d))
    assert list(back.tex) == [new_name]
    return bytes(d)


def rename(d, old, new):
    """The texture's name (as long as the old one): every place the file has it, and the
    name dictionary's one entry."""
    a, b = old.encode(), new.encode()
    assert len(a) == len(b) and a != b, (old, new)
    brtd = bytes(d).find(b"BRTD")
    head = bytes(d[:brtd])
    places = []
    at = head.find(a)
    while at >= 0:
        places.append(at)
        at = head.find(a, at + 1)
    assert 1 <= len(places) <= 3, (old, places)
    for at in places:
        d[at:at + len(b)] = b
    dic = head.find(b"_DIC")
    count = struct.unpack_from("<I", d, dic + 4)[0]
    assert dic > 0 and count == 1
    for k, (bit, left, right) in enumerate(bntxwrite.dictionary([new])):
        struct.pack_into("<IHH", d, dic + 8 + 16 * k, bit & 0xFFFFFFFF, left, right)


# ---------------------------------------------------------------- colours
def rgb_to_hsv(rgb):
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    d = mx - mn
    safe = np.where(d > 0, d, 1.0)
    h = np.where(mx == r, ((g - b) / safe) % 6.0, np.where(mx == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0)) / 6.0
    h = np.where(d > 0, h, 0.0)
    s = np.where(mx > 0, d / np.where(mx > 0, mx, 1.0), 0.0)
    return np.stack([h, s, mx], axis=1)


def hsv_to_rgb(hsv):
    h, s, v = hsv[:, 0] % 1.0, np.clip(hsv[:, 1], 0, 1), np.clip(hsv[:, 2], 0, 1)
    i = np.floor(h * 6.0).astype(np.int64) % 6
    f = h * 6.0 - np.floor(h * 6.0)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=1)


def decoded(data):
    """(h, w, 4) uint8 of the file's top mip, as stored (before the component selectors)."""
    arc = bntx.Bntx(data)
    return list(arc.tex.values())[0].raw()


def mean_colour(data):
    """Mean hue (circular, weighted by how coloured a texel is), saturation and value of the
    texels that are not cut away."""
    px = decoded(data).reshape(-1, 4).astype(np.float64) / 255.0
    keep = px[:, 3] > 0.5 if (px[:, 3] > 0.5).any() else np.ones(len(px), bool)
    hsv = rgb_to_hsv(px[keep, :3])
    w = hsv[:, 1] * hsv[:, 2]
    if w.sum() > 0:
        hue = float(np.arctan2((w * np.sin(2 * np.pi * hsv[:, 0])).sum(), (w * np.cos(2 * np.pi * hsv[:, 0])).sum()) / (2 * np.pi) % 1.0)
    else:
        hue = 0.0
    return hue, float(hsv[:, 1].mean()), float(hsv[:, 2].mean())


def check(old, new, fn, tolerance=0.06):
    """The recoloured file decodes to fn of what the old one decodes to (to a block's
    rounding), with the alpha untouched. -> mean error of the colours, 0..1."""
    a, b = decoded(old).astype(np.float64) / 255.0, decoded(new).astype(np.float64) / 255.0
    assert a.shape == b.shape
    if astc_size(list(bntx.Bntx(old).tex.values())[0].format):         # encoded again: the alpha is as near as the encoder gets it
        da = np.abs(a[:, :, 3] - b[:, :, 3])
        assert da.mean() < 0.01 and (da > 0.25).mean() < 0.002, "the alpha changed (mean %.4f)" % da.mean()
    else:
        assert np.array_equal(decoded(old)[:, :, 3], decoded(new)[:, :, 3]), "the alpha changed"
    want = np.clip(fn(a[:, :, :3].reshape(-1, 3)), 0, 1).reshape(a.shape[0], a.shape[1], 3)
    shown = a[:, :, 3] > 0.5 if (a[:, :, 3] > 0.5).any() else np.ones(a.shape[:2], bool)
    err = float(np.abs(want - b[:, :, :3])[shown].mean())
    assert err < tolerance, "the recoloured texture is not what was asked for (mean error %.3f)" % err
    return err


def main():
    import nxfmt
    tex = os.path.join(nxfmt.ROMFS, "Tex")

    def shift(rgb):                     # a hue a third of the way round, a little darker
        hsv = rgb_to_hsv(rgb)
        hsv[:, 0] += 1 / 3
        hsv[:, 2] *= 0.8
        return hsv_to_rgb(hsv)
    for name in ("Mt_Hairs_Player_Adult_Alb", "Npc_Kokiri_WC_Saria_Hair_Alb", "Mt_Hair_Npc_Gerudo_GEU_Nabooru_Alb",
                 "CmnTex_Chara_Mt_Hair_Npc_Hylia_MN_009_Alb", "Mt_Cloth_Npc_Hylia_WC_Malon_Child_Alb"):
        path = os.path.join(tex, name + ".bntx.zs")
        if not os.path.exists(path):
            print("%-48s (no such file)" % name)
            continue
        data = nxfmt.read(path)
        t, start, end = data_range(data)
        new_name = name[:-1] + ("X" if name[-1] != "X" else "Y")
        out = recolour(data, new_name, shift)
        err = check(data, out, shift)
        same = sum(x == y for x, y in zip(data[:start], out[:start]))
        print("%-48s %s %dx%d %d mips, %d bytes of blocks: mean error %.4f; mean colour %s -> %s; %d bytes of the head differ" % (
            name, t.format, t.width, t.height, t.mips, end - start, err, tuple(round(x, 3) for x in mean_colour(data)),
            tuple(round(x, 3) for x in mean_colour(out)), start - same))
        assert len(out) == len(data) and out[end:] == data[end:]


if __name__ == "__main__":
    main()
