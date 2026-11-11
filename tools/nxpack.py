"""Writers to go with nxfmt: SARC file replacement and zstd with the game's dictionaries."""
import struct

import pyzstd

import nxfmt


def sarc_replace(data, changes, align=8):
    """Return a copy of SARC `data` with files in `changes` ({name: bytes}) replaced.

    Header, SFAT and SFNT are kept; file data is laid out again in its
    original order, each start aligned to `align` (this game packs Mals
    archives with 8). Unmodified input rebuilds byte for byte.
    """
    assert data[:4] == b"SARC"
    e = "<" if data[6:8] == b"\xff\xfe" else ">"
    data_off = struct.unpack_from(e + "I", data, 0x0C)[0]
    count = struct.unpack_from(e + "H", data, 0x1A)[0]
    files = nxfmt.sarc_files(data)
    names = {}
    nodes = []
    for i in range(count):
        h, attr, s, en = struct.unpack_from(e + "IIII", data, 0x20 + i * 16)
        nodes.append([i, h, attr, s, en])
    # map node index -> name via the same rule as nxfmt.sarc_files
    sfnt = 0x20 + count * 16
    for i, h, attr, s, en in nodes:
        noff = (attr & 0xFFFF) * 4
        nend = data.index(b"\0", sfnt + 8 + noff)
        names[i] = data[sfnt + 8 + noff:nend].decode("utf-8")
    missing = set(changes) - set(names.values())
    assert not missing, ("not in archive", missing)
    head = bytearray(data[:data_off])
    body = bytearray()
    for n in sorted(nodes, key=lambda n: n[3]):
        i = n[0]
        blob = changes.get(names[i], files[names[i]])
        while len(body) % align:
            body += b"\0"
        start = len(body)
        body += blob
        struct.pack_into(e + "II", head, 0x20 + i * 16 + 8, start, start + len(blob))
    out = head + body
    struct.pack_into(e + "I", out, 0x08, len(out))
    return bytes(out)


def sarc_file_ranges(data, name):
    """[(start, end)] of every entry with this name inside SARC `data` (for
    same-size in-place edits). UI layout archives hold each layout 4 times, and
    the game uses more than the first, so edits must go to all of them."""
    ranges = [(s, e) for n, s, e in nxfmt.sarc_entries(data) if n == name]
    if not ranges:
        raise KeyError(name)
    return ranges


def sarc_file_range(data, name):
    """(start, end) of the first entry with this name (see sarc_file_ranges)."""
    return sarc_file_ranges(data, name)[0]


def sarc_patch_all(data, name, edited):
    """Replace every same-named entry with `edited` (same size) in bytearray `data`;
    the entries must all be identical to begin with."""
    ranges = sarc_file_ranges(data, name)
    first = bytes(data[ranges[0][0]:ranges[0][1]])
    for s, e in ranges:
        assert bytes(data[s:e]) == first, "entries of %s differ" % name
        assert e - s == len(edited), "size change for " + name
        data[s:e] = edited
    return len(ranges)


def zs_compress_like(data, original_zs, level=16):
    """Compress `data` with the same ZsDic dictionary the original .zs file used."""
    did = pyzstd.get_frame_info(original_zs).dictionary_id
    if did == 0:
        return pyzstd.compress(data, level)
    for d in nxfmt.load_dicts().values():
        if d.dict_id == did:
            return pyzstd.compress(data, level, zstd_dict=d)
    raise RuntimeError("no dictionary with id %d" % did)
