"""SARC archives rebuilt from their entry list (so files can be added).

    entries, meta = parse(data)               # [(name, bytes)] in table order
    data = build(entries + [("New.buil", blob)] * 4, meta)

Node table sorted by name hash (multiplier 0x65; equal hashes keep their order),
one name string per node (4-byte aligned), file data in node order, each start
aligned to `meta["align"]` relative to the data section. The UI layout archives
and the Mals text archives rebuild byte for byte (checked in __main__).
"""
import struct


def sarc_hash(name, mult=0x65):
    h = 0
    for c in name.encode("utf-8"):
        h = (h * mult + (c if c < 0x80 else c - 0x100)) & 0xFFFFFFFF
    return h


def parse(data):
    assert data[:4] == b"SARC" and data[6:8] == b"\xff\xfe", "little-endian SARC only"
    doff = struct.unpack_from("<I", data, 0x0C)[0]
    cnt, mult = struct.unpack_from("<HI", data, 0x1A)
    nodes = [struct.unpack_from("<IIII", data, 0x20 + 16 * i) for i in range(cnt)]
    sfnt = 0x20 + 16 * cnt
    assert data[sfnt:sfnt + 4] == b"SFNT"
    names_base = sfnt + 8
    entries = []
    for h, attr, s, e in nodes:
        o = names_base + (attr & 0xFFFF) * 4
        name = data[o:data.index(b"\0", o)].decode("utf-8")
        assert sarc_hash(name, mult) == h
        entries.append((name, bytes(data[doff + s:doff + e])))
    starts = [n[2] for n in nodes]
    align = 1
    for a in (4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4):
        if all(s % a == 0 for s in starts):
            align = a
            break
    names_end = names_base + sum((len(n.encode()) + 4) & ~3 for n, _ in entries)
    off_align = 1
    while (names_end + off_align - 1) // off_align * off_align != doff and off_align < 0x10000:
        off_align *= 2
    assert (names_end + off_align - 1) // off_align * off_align == doff, (hex(names_end), hex(doff))
    return entries, {"mult": mult, "align": min(align, 16), "off_align": off_align,
                     "version": struct.unpack_from("<H", data, 0x10)[0]}


def build(entries, meta):
    mult = meta["mult"]
    order = sorted(range(len(entries)), key=lambda i: (sarc_hash(entries[i][0], mult), i))
    ents = [entries[i] for i in order]
    names = bytearray()
    name_offs = []
    for n, _ in ents:
        name_offs.append(len(names) // 4)
        names += n.encode("utf-8") + b"\0"
        while len(names) % 4:
            names.append(0)
    cnt = len(ents)
    sfnt = 0x20 + 16 * cnt
    names_end = sfnt + 8 + len(names)
    oa = meta["off_align"]
    doff = (names_end + oa - 1) // oa * oa
    body = bytearray()
    ranges = []
    for _, blob in ents:
        while len(body) % meta["align"]:
            body.append(0)
        ranges.append((len(body), len(body) + len(blob)))
        body += blob
    out = bytearray()
    out += b"SARC" + struct.pack("<HHIIHH", 0x14, 0xFEFF, doff + len(body), doff, meta["version"], 0)
    out += b"SFAT" + struct.pack("<HHI", 0xC, cnt, mult)
    for (n, _), no, (s, e) in zip(ents, name_offs, ranges):
        assert no < 0x10000
        out += struct.pack("<IIII", sarc_hash(n, mult), 0x01000000 | no, s, e)
    out += b"SFNT" + struct.pack("<HH", 8, 0) + names
    out += bytes(doff - len(out))
    out += body
    return bytes(out)


def add(data, files):
    """`data` with new files [(name, bytes)] added (a name may repeat)."""
    entries, meta = parse(data)
    return build(entries + list(files), meta)


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import nxfmt
    for rel in ("UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs",
                "UI/Layout2Archive/Title.Product.100.Nin_Ounce_NVN2.blarc.zs",
                "Mals/USen.Product.110.sarc.zs", "Mals/EUen.Product.110.sarc.zs"):
        d = nxfmt.read(os.path.join(nxfmt.ROMFS, rel))
        e, m = parse(d)
        print(rel, "rebuilds exactly:", build(e, m) == d, m)
