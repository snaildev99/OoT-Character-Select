"""UI texture archives (.bntx, target "Ounc"): list textures and decode them to RGBA numbers.

    import bntx
    arc = bntx.Bntx(nxfmt.read(path))      # Common.Product.100.Nin_Ounce_NVN2.bntx.zs
    arc.names()                            # 878 textures in Common
    t = arc["ChallengeIndexDot_01"]        # Tex: name, width, height, format, comp (selectors)
    rgba = t.rgba()                        # numpy (h, w, 4) uint8 as the game samples it
    bntx.stats(rgba)                       # coverage / colour numbers (no pictures)

BRTI record: +0x16 mip count, +0x1c format, +0x24 width, +0x28 height, +0x34 block height
log2, +0x50 data size, +0x58 component selectors (0 zero, 1 one, 2 red, 3 green, 4 blue,
5 alpha), +0x60 name (u16 length + chars), +0x70 pointer to the mip offset table. Data is
Tegra block-linear ("swizzled"); the block height is halved while the texture is shorter
than half a block (so small textures need less than the header says).
Formats handled: BC1/3/4/5/7, ASTC, RGBA8.
"""
import struct

import numpy as np

FORMATS = {0x0b: ("RGBA8", 1, 1, 4), 0x1a: ("BC1", 4, 4, 8), 0x1b: ("BC2", 4, 4, 16), 0x1c: ("BC3", 4, 4, 16),
           0x1d: ("BC4", 4, 4, 8), 0x1e: ("BC5", 4, 4, 16), 0x1f: ("BC6H", 4, 4, 16), 0x20: ("BC7", 4, 4, 16),
           0x2d: ("ASTC4x4", 4, 4, 16), 0x2e: ("ASTC5x4", 5, 4, 16), 0x2f: ("ASTC5x5", 5, 5, 16),
           0x30: ("ASTC6x5", 6, 5, 16), 0x31: ("ASTC6x6", 6, 6, 16), 0x32: ("ASTC8x5", 8, 5, 16),
           0x33: ("ASTC8x6", 8, 6, 16), 0x34: ("ASTC8x8", 8, 8, 16), 0x35: ("ASTC10x5", 10, 5, 16),
           0x36: ("ASTC10x6", 10, 6, 16), 0x37: ("ASTC10x8", 10, 8, 16), 0x38: ("ASTC10x10", 10, 10, 16),
           0x39: ("ASTC12x10", 12, 10, 16), 0x3a: ("ASTC12x12", 12, 12, 16)}


def _pow2_up(v):
    p = 1
    while p < v:
        p *= 2
    return p


def _div_up(a, b):
    return (a + b - 1) // b


def deswizzle(width, height, blk_w, blk_h, bpp, block_height_log2, data):
    """Tegra block-linear -> linear; width/height in pixels, bpp = bytes per block."""
    w, h = _div_up(width, blk_w), _div_up(height, blk_h)
    block_height = 1 << block_height_log2
    while block_height > 1 and _pow2_up(h) < block_height * 8:
        block_height //= 2
    bh_log2 = block_height.bit_length() - 1
    pitch = (w * bpp + 63) & ~63                       # bytes per row, aligned to a GOB (64 bytes)
    gobs_x = pitch // 64
    ys, xs = np.mgrid[0:h, 0:w]
    xb = xs * bpp
    gob_addr = (ys // (8 * block_height)) * 512 * block_height * gobs_x + (xb // 64) * 512 * block_height + \
        ((ys % (8 * block_height)) // 8) * 512
    xb2, y2 = xb & 63, ys & 7
    off = ((xb2 & 0x3F) >> 5 << 8) + ((y2 >> 1) & 3) * 64 + ((xb2 & 0x1F) >> 4 << 5) + (y2 & 1) * 16 + (xb2 & 0xF)
    addr = (gob_addr + off).reshape(-1)
    src = np.frombuffer(data, dtype=np.uint8)
    idx = addr[:, None] + np.arange(bpp)[None, :]
    assert idx.max() < len(src), "swizzled data too short (%d needed, %d there)" % (idx.max() + 1, len(src))
    return src[idx].reshape(h, w * bpp).tobytes(), bh_log2


class Tex:
    def __init__(self, arc, off):
        d = arc.d
        assert d[off:off + 4] == b"BRTI"
        self.arc, self.off = arc, off
        self.mips, = struct.unpack_from("<H", d, off + 0x16)
        fmt, = struct.unpack_from("<I", d, off + 0x1C)
        self.format_code, self.srgb = fmt >> 8, (fmt & 0xFF) == 6
        self.width, self.height = struct.unpack_from("<II", d, off + 0x24)
        self.block_height_log2, = struct.unpack_from("<I", d, off + 0x34)
        self.size, = struct.unpack_from("<I", d, off + 0x50)
        self.comp = tuple(d[off + 0x58:off + 0x5C])
        name, = struct.unpack_from("<Q", d, off + 0x60)
        n, = struct.unpack_from("<H", d, name)
        self.name = d[name + 2:name + 2 + n].decode()
        table, = struct.unpack_from("<Q", d, off + 0x70)
        self.data_off, = struct.unpack_from("<Q", d, table)
        self.format = FORMATS.get(self.format_code, ("fmt%#x" % self.format_code, 0, 0, 0))[0]

    def __repr__(self):
        return "<Tex %s %dx%d %s%s comp=%s>" % (self.name, self.width, self.height, self.format,
                                                " srgb" if self.srgb else "", self.comp)

    def raw(self):
        """Decoded top mip as (h, w, 4) uint8 RGBA, before the component selectors."""
        import texture2ddecoder as t2d
        name, bw, bh, bpp = FORMATS[self.format_code]
        w, h = self.width, self.height
        data = self.arc.d[self.data_off:self.data_off + self.size]
        lin, _ = deswizzle(w, h, bw, bh, bpp, self.block_height_log2, data)
        if name == "RGBA8":
            return np.frombuffer(lin, np.uint8).reshape(h, w, 4).copy()
        pw, ph = _div_up(w, bw) * bw, _div_up(h, bh) * bh
        if name.startswith("ASTC"):
            out = t2d.decode_astc(lin, pw, ph, bw, bh)
        else:
            out = {"BC1": t2d.decode_bc1, "BC3": t2d.decode_bc3, "BC4": t2d.decode_bc4, "BC5": t2d.decode_bc5,
                   "BC7": t2d.decode_bc7}[name](lin, pw, ph)
        bgra = np.frombuffer(out, np.uint8).reshape(ph, pw, 4)[:h, :w]
        return bgra[:, :, [2, 1, 0, 3]].copy()

    def rgba(self):
        """As sampled: component selectors applied (0 zero, 1 one, 2 r, 3 g, 4 b, 5 a)."""
        raw = self.raw()
        chans = [np.zeros(raw.shape[:2], np.uint8), np.full(raw.shape[:2], 255, np.uint8),
                 raw[:, :, 0], raw[:, :, 1], raw[:, :, 2], raw[:, :, 3]]
        return np.stack([chans[c] for c in self.comp], axis=2)


class Bntx:
    def __init__(self, data):
        assert data[:4] == b"BNTX"
        self.d = data
        count, = struct.unpack_from("<I", data, 0x24)
        table, = struct.unpack_from("<Q", data, 0x28)
        self.tex = {}
        for i in range(count):
            t = Tex(self, struct.unpack_from("<Q", data, table + 8 * i)[0])
            self.tex[t.name] = t

    def names(self):
        return sorted(self.tex)

    def __getitem__(self, name):
        return self.tex[name]

    def __contains__(self, name):
        return name in self.tex


def stats(rgba):
    """Numbers that describe a texture without looking at it."""
    a = rgba[:, :, 3].astype(np.float32) / 255
    rgb = rgba[:, :, :3].astype(np.float32) / 255
    h, w = a.shape
    cov = float((a > 0.5).mean())
    wsum = max(float(a.sum()), 1e-6)
    mean = [float((rgb[:, :, i] * a).sum() / wsum) for i in range(3)]
    cy, cx = h // 2, w // 2
    centre = float(a[cy - h // 8:cy + h // 8 + 1, cx - w // 8:cx + w // 8 + 1].mean())
    ring = float(np.concatenate([a[h // 4, w // 4:3 * w // 4], a[3 * h // 4, w // 4:3 * w // 4]]).mean())
    corners = float(np.mean([a[:h // 8, :w // 8].mean(), a[:h // 8, -w // 8:].mean(),
                             a[-h // 8:, :w // 8].mean(), a[-h // 8:, -w // 8:].mean()]))
    grey = float(np.abs(rgb[:, :, 0] - rgb[:, :, 1]).max() + np.abs(rgb[:, :, 1] - rgb[:, :, 2]).max())
    lum = (rgb.mean(axis=2) * a)
    return {"size": (w, h), "alpha>0.5": round(cov, 3), "alpha mean": round(float(a.mean()), 3),
            "alpha centre": round(centre, 3), "alpha mid ring": round(ring, 3), "alpha corners": round(corners, 3),
            "mean rgb (alpha weighted)": [round(v, 3) for v in mean], "rgb spread (0 = grey)": round(grey, 3),
            "rgb min/max": (round(float(rgb.min()), 3), round(float(rgb.max()), 3)),
            "lum centre": round(float(lum[cy - h // 8:cy + h // 8 + 1, cx - w // 8:cx + w // 8 + 1].mean()), 3)}
