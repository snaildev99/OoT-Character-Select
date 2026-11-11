"""Write textures for this console: add textures to a layout archive's texture file
(UI/Layout2Archive/<name>...bntx), ASTC 4x4 encoding, the console's block order.

    data = add_textures(original_bntx_bytes, [(name, rgba_array), ...])
    venv\\Scripts\\python.exe tools\\bntxwrite.py          self test on the game's three files

The file (version 4.1.1, platform tag "Ounc"; all single-mip in the UI archives):
  0x00  header: "BNTX", version, BOM, +0x10 u32 file name (its characters), +0x16 u16 first
        block (_STR), +0x18 u32 relocation table, +0x1c u32 file size
  0x20  container: "Ounc", u32 texture count, pointers: record pointer array, BRTD, name
        dictionary, memory pool (0x58, 0x140 bytes of zeros)
  0x198 record pointer array
        _STR: next / size (both = distance to the first record), u32 string count, an
              empty string, then {u16 length, characters, NUL, pad to 2}; pointers to a
              string point at its length
        _DIC: u32 count, count + 1 entries {u32 bit, u16 left, u16 right, u64 key}: a
              Patricia tree over the names read as big-endian numbers (bit 0 = lowest bit
              of the last character); entry i + 1 is texture i
        records, 0x318 each: BRTI (0xa0), two runtime areas (0x100 each), the mip pointer,
              a user data entry "TextureOriginalSize" (two floats: the size in layout
              units, 1.5 per texel), its dictionary
        BRTD: 16-byte header ending on a 0x1000 boundary, texture data (each at its
              record's alignment), up to the relocation table (0x1000 boundary)
        _RLT: two sections (everything before BRTD; BRTD) and for each the places of the
              pointers that point into it: entries {u32 place, u16 structures, u8
              pointers per structure, u8 words skipped after each}
A written file is checked by reading it back: rebuilding the game's own files from their
parts gives the same bytes (the relocation entries are packed differently but list the
same pointers), and a texture taken out and added again decodes to the same picture.
"""
import os
import struct
import sys

import numpy as np

RECORD = 0x318          # with the user data "TextureOriginalSize"
RECORD_BARE = 0x2A8     # without
ASTC_4X4_SRGB = 0x2D06
# Component selectors (record +0x58), one byte per output channel: 0 zero, 1 one, 2 red,
# 3 green, 4 blue, 5 alpha. In the layout archives: 02 03 04 05 colour with alpha,
# 02 02 02 05 grey with alpha (284 of the 450 ASTC textures), 01 01 01 02 a mask (BC4).
RGBA = bytes([2, 3, 4, 5])
ALIGN = 0x1000


def _u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def _u64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


def _up(v, a):
    return (v + a - 1) // a * a


# ---------------------------------------------------------------- name dictionary
def _bit(key, pos):
    byte = pos >> 3
    return (key[len(key) - 1 - byte] >> (pos & 7)) & 1 if byte < len(key) else 0


def _first_difference(a, b):
    x = int.from_bytes(a, "big") ^ int.from_bytes(b, "big")
    return (x & -x).bit_length() - 1


def dictionary(names):
    """[(bit, left, right)] for the root and each name, as the files store them."""
    keys = [b""] + [n.encode() for n in names]
    ref, kids = [-1], [[0, 0]]
    for i in range(1, len(keys)):
        key = keys[i]
        prev, cur = 0, kids[0][0]
        while ref[prev] < ref[cur]:
            prev, cur = cur, kids[cur][_bit(key, ref[cur])]
        assert keys[cur] != key, "two textures named %r" % key
        bit = _first_difference(key, keys[cur])
        prev, cur = 0, kids[0][0]
        while ref[prev] < ref[cur] and ref[cur] < bit:
            prev, cur = cur, kids[cur][_bit(key, ref[cur])]
        ref.append(bit)
        kids.append([i, i])
        kids[i][1 - _bit(key, bit)] = cur
        side = 0 if prev == 0 else _bit(key, ref[prev])
        kids[prev][side] = i
    return [(ref[i] & 0xFFFFFFFF, kids[i][0], kids[i][1]) for i in range(len(keys))]


# ---------------------------------------------------------------- reading
class Parsed:
    """The parts of a texture file: file_name, strings (pool order), textures
    [{name, record (0x318 bytes), data, align}]."""

    def __init__(self, d):
        assert d[:4] == b"BNTX" and d[0x20:0x24] == b"Ounc", "not a texture file of this console"
        self.head = bytes(d[:0x20])
        n = _u32(d, 0x24)
        ptrs, brtd, dic, pool = _u64(d, 0x28), _u64(d, 0x30), _u64(d, 0x38), _u64(d, 0x40)
        assert (ptrs, pool) == (0x198, 0x58) and d[0x58:0x198] == bytes(0x140)
        self.container_tail = bytes(d[0x48:0x58])
        strs = ptrs + 8 * n
        assert strs == _u16(d, 0x16) and d[strs:strs + 4] == b"_STR"
        first = strs + _u32(d, strs + 4)
        assert _u32(d, strs + 8) == _u32(d, strs + 4) and _u32(d, strs + 12) == 0
        count = _u32(d, strs + 0x10)
        assert d[strs + 0x14:strs + 0x18] == bytes(4), "string pool does not start with the empty string"
        o, self.strings, where = strs + 0x18, [], {strs + 0x14: ""}
        for _ in range(count):
            ln = _u16(d, o)
            where[o] = d[o + 2:o + 2 + ln].decode()
            self.strings.append(where[o])
            o = _up(o + 2 + ln + 1, 2)
        assert dic == _up(o, 8) and d[dic:dic + 4] == b"_DIC" and _u32(d, dic + 4) == n
        assert first == dic + 8 + 16 * (n + 1) and d[first:first + 4] == b"BRTI"
        self.file_name = where[_u32(d, 0x10) - 2]
        self.textures = []
        r = first
        for i in range(n):
            assert r == _u64(d, ptrs + 8 * i) and d[r:r + 4] == b"BRTI" and _u32(d, r + 4) == _u32(d, r + 8)
            assert _u16(d, r + 0x16) == 1, "more than one mip"
            user = _u64(d, r + 0x78) != 0           # some textures have no "TextureOriginalSize"
            size = RECORD if user else RECORD_BARE
            assert _u32(d, r + 4) == (size if i < n - 1 else brtd - r), (i, hex(_u32(d, r + 4)))
            want = [(0x68, 0x20), (0x70, r + 0x2A0), (0x80, r + 0xA0), (0x88, r + 0x1A0)]
            want += [(0x78, r + 0x2A8), (0x98, r + 0x2F0), (0x2B0, r + 0x2E8)] if user else [(0x78, 0), (0x98, 0)]
            for off, to in want:
                assert _u64(d, r + off) == to, (i, hex(off))
            if user:
                assert where[_u64(d, r + 0x2A8)] == "TextureOriginalSize" and d[r + 0x2F0:r + 0x2F4] == b"_DIC"
            name = where[_u64(d, r + 0x60)]
            assert where[_u64(d, dic + 8 + 16 * (i + 1) + 8)] == name
            at = _u64(d, r + 0x2A0)
            self.textures.append({"name": name, "record": bytes(d[r:r + size]),
                                  "data": bytes(d[at:at + _u32(d, r + 0x50)]), "align": _u32(d, r + 0x54)})
            r += size
        assert d[brtd:brtd + 4] == b"BRTD" and (brtd + 0x10) % ALIGN == 0
        rlt = _u32(d, 0x18)
        assert d[rlt:rlt + 4] == b"_RLT" and _u64(d, brtd + 8) == rlt - brtd
        self.dict_entries = [struct.unpack_from("<IHH", d, dic + 8 + 16 * i) for i in range(n + 1)]


def relocations(d):
    """{section index: sorted places of the pointers into it}, from a file's table."""
    rlt = _u32(d, 0x18)
    assert d[rlt:rlt + 4] == b"_RLT" and _u32(d, rlt + 4) == rlt
    sections = _u32(d, rlt + 8)
    entries = rlt + 0x10 + 0x18 * sections
    out = {}
    for s in range(sections):
        _, _off, _size, first, count = struct.unpack_from("<QIIII", d, rlt + 0x10 + 0x18 * s)
        places = []
        for e in range(first, first + count):
            place, structs, per, skip = struct.unpack_from("<IHBB", d, entries + 8 * e)
            for _ in range(structs):
                places += [place + 8 * k for k in range(per)]
                place += 8 * (per + skip)
        out[s] = sorted(places)
    return out


# ---------------------------------------------------------------- writing
def build(parsed, textures=None):
    """The file from its parts (`textures` instead of the parsed ones if given)."""
    textures = parsed.textures if textures is None else textures
    n = len(textures)
    strings = list(parsed.strings)
    for t in textures:
        if t["name"] not in strings:
            strings.append(t["name"])
    out = bytearray(0x198)
    out[:0x20] = parsed.head
    out[0x20:0x28] = b"Ounc" + struct.pack("<I", n)
    out[0x48:0x58] = parsed.container_tail
    ptrs = 0x198
    out += bytes(8 * n)
    strs = len(out)
    pool = bytearray(b"_STR" + bytes(12) + struct.pack("<I", len(strings)) + bytes(4))
    where = {"": strs + 0x14}
    for st in strings:
        where[st] = strs + len(pool)
        raw = st.encode()
        pool += struct.pack("<H", len(raw)) + raw + b"\0"
        pool += bytes(len(pool) % 2)
    pool += bytes(-len(pool) % 8)
    out += pool
    dic = len(out)
    out += b"_DIC" + struct.pack("<I", n)
    for (bit, left, right), key in zip(dictionary([t["name"] for t in textures]), [""] + [t["name"] for t in textures]):
        out += struct.pack("<IHHQ", bit, left, right, where[key])
    first = len(out)
    struct.pack_into("<II", out, strs + 4, first - strs, first - strs)
    in_first = [0x28, 0x38, 0x40] + [ptrs + 8 * i for i in range(n)] + [dic + 16 + 16 * i for i in range(n + 1)]
    in_data = [0x30]      # places of pointers into the data block
    starts = []
    for i, t in enumerate(textures):
        r = len(out)
        starts.append(r)
        rec = bytearray(t["record"])
        user = len(rec) == RECORD
        assert user or len(rec) == RECORD_BARE
        fields = [(0x60, where[t["name"]]), (0x68, 0x20), (0x70, r + 0x2A0), (0x80, r + 0xA0), (0x88, r + 0x1A0)]
        if user:
            fields += [(0x78, r + 0x2A8), (0x98, r + 0x2F0), (0x2A8, where["TextureOriginalSize"]), (0x2B0, r + 0x2E8),
                       (0x300, where[""]), (0x310, where["TextureOriginalSize"])]
        for off, to in fields:
            struct.pack_into("<Q", rec, off, to)
        struct.pack_into("<I", rec, 0x50, len(t["data"]))
        out += rec
        struct.pack_into("<Q", out, ptrs + 8 * i, r)
        in_first += [r + off for off, _ in fields]
        in_data.append(r + 0x2A0)
    end_records = len(out)
    brtd = _up(end_records + 0x10, ALIGN) - 0x10
    out += bytes(brtd - len(out)) + b"BRTD" + bytes(12)
    for i, r in enumerate(starts):
        step = (starts[i + 1] if i < n - 1 else brtd) - r
        struct.pack_into("<II", out, r + 4, step, step)
    for i, t in enumerate(textures):
        out += bytes(-len(out) % t["align"])
        struct.pack_into("<Q", out, starts[i] + 0x2A0, len(out))
        out += t["data"]
    out += bytes(-len(out) % ALIGN)
    rlt = len(out)
    struct.pack_into("<Q", out, brtd + 8, rlt - brtd)
    struct.pack_into("<QQQQ", out, 0x28, ptrs, brtd, dic, 0x58)
    # the relocation table: entries {place, structures, pointers per structure, words skipped}
    def pack(places):
        runs = []                       # [place, pointers in a row]
        for pl in sorted(places):
            if runs and runs[-1][0] + 8 * runs[-1][1] == pl and runs[-1][1] < 255:
                runs[-1][1] += 1
            else:
                runs.append([pl, 1])
        entries, i = [], 0
        while i < len(runs):
            place, per = runs[i]
            j = i + 1
            if j < len(runs) and runs[j][1] == per:
                step = runs[j][0] - place
                skip = step // 8 - per
                if 0 <= skip <= 255:
                    while j < len(runs) and runs[j][1] == per and runs[j][0] - runs[j - 1][0] == step and j - i < 0xFFFF:
                        j += 1
                    entries.append((place, j - i, per, skip))
                    i = j
                    continue
            entries.append((place, 1, per, 0))
            i += 1
        return entries

    first_entries, data_entries = pack(in_first), pack(in_data)
    table = bytearray(b"_RLT" + struct.pack("<III", rlt, 2, 0))
    table += struct.pack("<QIIII", 0, 0, end_records, 0, len(first_entries))
    table += struct.pack("<QIIII", 0, brtd, rlt - brtd, len(first_entries), len(data_entries))
    for entry in first_entries + data_entries:
        table += struct.pack("<IHBB", *entry)
    out += table
    struct.pack_into("<I", out, 0x10, where[parsed.file_name] + 2)
    struct.pack_into("<H", out, 0x16, strs)
    struct.pack_into("<II", out, 0x18, rlt, len(out))
    return bytes(out)


# ---------------------------------------------------------------- pixels
def block_height_log2(rows):
    """The value a record stores at +0x34 for a texture `rows` blocks high: 16 gobs, halved
    while the texture fills no more than two thirds of that (all 921 UI textures: up to
    10 rows 0, 21 -> 1, 42 -> 2, 85 -> 3, above 4)."""
    bh = 16
    while bh > 1 and rows * 3 <= bh * 16:
        bh //= 2
    return bh.bit_length() - 1


def _addresses(w, h, bpp, bh_log2):
    """Byte address of every block (h rows of w blocks of bpp bytes) in the console's
    block-linear layout, and the data size (README, layouts section; the same mapping as
    tools\npcportrait.py ounce_deswizzle). `bh_log2` is the record's value; the layout
    uses it halved while the texture is no taller than half a block."""
    bh = 1 << bh_log2
    pow2 = 1
    while pow2 < h:
        pow2 *= 2
    while bh > 1 and pow2 < bh * 8:
        bh //= 2
    gobs_x = ((w * bpp + 63) & ~63) // 64
    ys, xs = np.mgrid[0:h, 0:w]
    xb = xs * bpp
    group = (ys // (8 * bh)) * 512 * bh * gobs_x + (xb // 64) * 512 * bh + ((ys % (8 * bh)) // 8) * 512
    x2, y2 = xb & 63, ys & 7
    off = ((x2 >> 5) << 8) + ((y2 >> 2) << 7) + (((x2 >> 4) & 1) << 6) + ((y2 & 3) << 4) + (x2 & 0xF)
    size = -(-h // (8 * bh)) * 512 * bh * gobs_x
    return (group + off).reshape(-1), size


def swizzle(rows, w, h, bpp, bh_log2):
    """Row-major blocks -> the console's layout."""
    addr, size = _addresses(w, h, bpp, bh_log2)
    out = np.zeros(size, np.uint8)
    idx = addr[:, None] + np.arange(bpp)[None, :]
    out[idx.reshape(-1)] = np.frombuffer(rows, np.uint8)
    return out.tobytes()


def deswizzle(data, w, h, bpp, bh_log2):
    addr, size = _addresses(w, h, bpp, bh_log2)
    assert len(data) >= size
    idx = addr[:, None] + np.arange(bpp)[None, :]
    return np.frombuffer(data, np.uint8)[idx.reshape(-1)].tobytes()


def astc_encode(rgba):
    """(h, w, 4) uint8, sizes multiples of 4 -> ASTC 4x4 blocks, row-major (sRGB profile)."""
    import astc_encoder as ae
    h, w = rgba.shape[:2]
    assert h % 4 == 0 and w % 4 == 0 and rgba.dtype == np.uint8 and rgba.shape[2] == 4
    cfg = ae.ASTCConfig(ae.ASTCProfile.LDR_SRGB, 4, 4, 1, 98.0)
    ctx = ae.ASTCContext(cfg)
    out = ctx.compress(ae.ASTCImage(ae.ASTCType.U8, w, h, 1, rgba.tobytes()), ae.ASTCSwizzle.from_str("RGBA"))
    assert len(out) == (w // 4) * (h // 4) * 16
    return out


def astc_decode(blocks, w, h):
    import texture2ddecoder
    raw = texture2ddecoder.decode_astc(blocks, w, h, 4, 4)       # BGRA
    a = np.frombuffer(raw, np.uint8).reshape(h, w, 4)
    return a[:, :, [2, 1, 0, 3]]


def texture(template, name, rgba):
    """A texture entry for build(): ASTC 4x4 sRGB, one mip. `template` = an entry of that
    format from the same file (its record's other fields are kept)."""
    h, w = rgba.shape[:2]
    rec = bytearray(template["record"])
    assert _u32(rec, 0x1C) == ASTC_4X4_SRGB and _u16(rec, 0x16) == 1 and len(rec) == RECORD
    data = swizzle(astc_encode(rgba), w // 4, h // 4, 16, block_height_log2(h // 4))
    struct.pack_into("<II", rec, 0x24, w, h)
    struct.pack_into("<I", rec, 0x34, block_height_log2(h // 4))
    rec[0x58:0x5C] = RGBA           # not the template's: most of the game's ASTC textures are grey + alpha
    struct.pack_into("<2f", rec, 0x2E8, w * 1.5, h * 1.5)
    return {"name": name, "record": bytes(rec), "data": data, "align": template["align"]}


def add_textures(data, pictures):
    """`data` with the textures [(name, rgba)] added (ASTC 4x4 sRGB)."""
    p = Parsed(data)
    template = next(t for t in p.textures if _u32(t["record"], 0x1C) == ASTC_4X4_SRGB and len(t["record"]) == RECORD
                    and t["record"][0x58:0x5C] == RGBA)
    have = {t["name"] for t in p.textures}
    new = []
    for name, rgba in pictures:
        assert name not in have, name
        new.append(texture(template, name, rgba))
    return build(p, p.textures + new)


# ---------------------------------------------------------------- self test
def _check_file(path):
    import nxfmt
    d = nxfmt.read(path)
    p = Parsed(d)
    assert p.dict_entries == dictionary([t["name"] for t in p.textures]), "dictionary differs"
    again = build(p)
    rlt = _u32(d, 0x18)
    assert _u32(again, 0x18) == rlt and again[:0x1C] == d[:0x1C] and again[0x20:rlt] == d[0x20:rlt],         "rebuilt file differs before the relocation table"       # (0x1c is the file size: our table is packed differently)
    assert _u32(again, 0x1C) == len(again)
    assert relocations(again) == relocations(d), "relocation tables list different pointers"
    # block height rule and data sizes
    for t in p.textures:
        fmt, w, h = _u32(t["record"], 0x1C), _u32(t["record"], 0x24), _u32(t["record"], 0x28)
        bw = 4
        rows = -(-h // 4)
        bpp = 16 if fmt >> 8 == 0x2D else 8
        assert _u32(t["record"], 0x34) == block_height_log2(rows), (t["name"], w, h)
        assert len(t["data"]) == _addresses(-(-w // bw), rows, bpp, block_height_log2(rows))[1], (t["name"], w, h)
    return p, d


def main():
    import nxfmt
    base = os.path.join(nxfmt.ROMFS, "UI", "Layout2Archive")
    for name in ("ZeldaNotes", "Title", "Common"):
        p, d = _check_file(os.path.join(base, name + ".Product.100.Nin_Ounce_NVN2.bntx.zs"))
        print("%-10s %d textures: rebuilt from its parts byte for byte up to the relocation table, which lists the same pointers" % (name, len(p.textures)))
        astc = [t for t in p.textures if _u32(t["record"], 0x1C) == ASTC_4X4_SRGB]
        # take one ASTC texture out and add it again from its decoded pixels
        t = max(astc, key=lambda t: len(t["data"]))
        w, h = _u32(t["record"], 0x24), _u32(t["record"], 0x28)
        if w % 4 or h % 4:
            t = next(x for x in astc if _u32(x["record"], 0x24) % 4 == 0 and _u32(x["record"], 0x28) % 4 == 0)
            w, h = _u32(t["record"], 0x24), _u32(t["record"], 0x28)
        blocks = deswizzle(t["data"], w // 4, h // 4, 16, _u32(t["record"], 0x34))
        assert swizzle(blocks, w // 4, h // 4, 16, _u32(t["record"], 0x34)) == t["data"], "block order does not round trip"
        rgba = np.ascontiguousarray(astc_decode(blocks, w, h))
        rest = [x for x in p.textures if x is not t]
        like = next(x for x in astc if x is not t and len(x["record"]) == RECORD)
        out = build(p, rest + [texture(like, t["name"], rgba)])
        q = Parsed(out)
        assert [x["name"] for x in q.textures] == [x["name"] for x in rest] + [t["name"]]
        assert q.dict_entries == dictionary([x["name"] for x in q.textures])
        back = q.textures[-1]
        got = astc_decode(deswizzle(back["data"], w // 4, h // 4, 16, _u32(back["record"], 0x34)), w, h)
        err = np.abs(got.astype(int) - rgba.astype(int)).mean()
        assert err < 3.0, err
        assert relocations(out)[0] and len(relocations(out)[1]) == len(q.textures) + 1
        print("           %s (%d x %d) taken out, encoded again and added: mean error %.2f of 255" % (t["name"], w, h, err))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
