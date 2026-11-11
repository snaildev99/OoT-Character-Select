"""Minimal readers for TotK-engine files: zstd (with ZsDic), SARC, BYML."""
import struct, sys, os, pyzstd

# the game's extracted romfs; set OOT_ROMFS to use another place
ROMFS = os.environ.get("OOT_ROMFS") or os.path.expanduser("~/Games/Hookshot/romfs").replace(os.sep, "/")
_dicts = None


def sarc_entries(data):
    """All SARC entries as (name, start, end) in table order. Names can repeat:
    the UI layout archives store every layout 4 times under the same name."""
    assert data[:4] == b"SARC", data[:4]
    bom = data[6:8]
    e = "<" if bom == b"\xff\xfe" else ">"
    data_off = struct.unpack_from(e + "I", data, 0x0C)[0]
    sfat = 0x14
    assert data[sfat:sfat + 4] == b"SFAT"
    count = struct.unpack_from(e + "H", data, sfat + 6)[0]
    sfnt = sfat + 12 + count * 16
    assert data[sfnt:sfnt + 4] == b"SFNT"
    names_base = sfnt + 8
    out = []
    for i in range(count):
        h, attr, s, en = struct.unpack_from(e + "IIII", data, sfat + 12 + i * 16)
        noff = (attr & 0xFFFF) * 4
        nend = data.index(b"\0", names_base + noff)
        out.append((data[names_base + noff:nend].decode("utf-8"), data_off + s, data_off + en))
    return out


def sarc_files(data):
    """{name: bytes}. Repeated names must hold identical data (else this raises,
    since a dict would silently keep only one of them)."""
    out = {}
    for name, s, en in sarc_entries(data):
        blob = data[s:en]
        if name in out:
            assert out[name] == blob, "SARC has differing entries named " + name
        out[name] = blob
    return out


def load_dicts():
    global _dicts
    if _dicts is None:
        raw = pyzstd.decompress(open(os.path.join(ROMFS, "Pack/ZsDic.pack.zs"), "rb").read())
        _dicts = {k: pyzstd.ZstdDict(v) for k, v in sarc_files(raw).items()}
    return _dicts


def unzs(data, name=""):
    if data[:4] != b"\x28\xb5\x2f\xfd":
        return data
    try:
        return pyzstd.decompress(data)
    except Exception:
        pass
    d = load_dicts()
    order = []
    if name.endswith(".pack.zs"):
        order.append("pack.zsdic")
    if ".bcett.byml" in name:
        order.append("bcett.byml.zsdic")
    order += [k for k in d if k not in order]
    for k in order:
        try:
            return pyzstd.decompress(data, zstd_dict=d[k])
        except Exception:
            continue
    raise RuntimeError("cannot decompress " + name)


def read(path):
    return unzs(open(path, "rb").read(), path)


class Byml:
    def __init__(self, data):
        self.d = data
        if data[:2] == b"BY":
            self.e = ">"
        elif data[:2] == b"YB":
            self.e = "<"
        else:
            raise ValueError("not byml")
        e = self.e
        self.ver, kt, st, root = struct.unpack_from(e + "HIII", data, 2)
        self.keys = self._strtab(kt) if kt else []
        self.strs = self._strtab(st) if st else []
        self.root = self._node(root) if root else None

    def _u32(self, o):
        return struct.unpack_from(self.e + "I", self.d, o)[0]

    def _strtab(self, o):
        hdr = self._u32(o)
        n = (hdr >> 8) if self.e == "<" else (hdr & 0xFFFFFF)  # LE: type byte is the low byte
        out = []
        for i in range(n):
            so = o + self._u32(o + 4 + i * 4)
            out.append(self.d[so:self.d.index(b"\0", so)].decode("utf-8", "replace"))
        return out

    def _val(self, t, raw, o):
        e = self.e
        if t == 0xA0:
            return self.strs[raw]
        if t == 0xA1:
            size = self._u32(raw)
            return ("bin", size)
        if t in (0xC0, 0xC1, 0xC8, 0xC9):
            return self._node(raw)
        if t == 0xD0:
            return bool(raw)
        if t == 0xD1:
            return struct.unpack(e + "i", struct.pack(e + "I", raw))[0]
        if t == 0xD2:
            return struct.unpack(e + "f", struct.pack(e + "I", raw))[0]
        if t == 0xD3:
            return raw
        if t in (0xD4, 0xD5, 0xD6):
            fmt = {0xD4: "q", 0xD5: "Q", 0xD6: "d"}[t]
            return struct.unpack_from(e + fmt, self.d, raw)[0]
        if t == 0xFF:
            return None
        return ("?%02x" % t, raw)

    def _node(self, o):
        t = self.d[o]
        e = self.e
        n = struct.unpack_from(e + "I", self.d, o)[0]
        n = (n >> 8) if e == ">" else (n >> 8)
        hdr = self._u32(o)
        if e == "<":
            t = hdr & 0xFF
            n = hdr >> 8
        else:
            t = self.d[o]
            n = hdr & 0xFFFFFF
        if t == 0xC0:
            types = self.d[o + 4:o + 4 + n]
            vo = o + 4 + ((n + 3) & ~3)
            return [self._val(types[i], self._u32(vo + i * 4), vo + i * 4) for i in range(n)]
        if t == 0xC1:
            out = {}
            for i in range(n):
                eo = o + 4 + i * 8
                w = self._u32(eo)
                if e == "<":
                    ki, vt = w & 0xFFFFFF, w >> 24
                else:
                    ki, vt = w >> 8, w & 0xFF
                out[self.keys[ki]] = self._val(vt, self._u32(eo + 4), eo + 4)
            return out
        if t == 0xA0:
            return self.strs[n]
        return ("node?%02x" % t, o)


def dump(obj, ind=0, maxlist=400, out=sys.stdout):
    p = "  " * ind
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.write(f"{p}{k}:\n")
                dump(v, ind + 1, maxlist, out)
            else:
                out.write(f"{p}{k}: {v!r}\n")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:maxlist]):
            if isinstance(v, (dict, list)) and v:
                out.write(f"{p}- [{i}]\n")
                dump(v, ind + 1, maxlist, out)
            else:
                out.write(f"{p}- {v!r}\n")
        if len(obj) > maxlist:
            out.write(f"{p}... {len(obj) - maxlist} more\n")
    else:
        out.write(f"{p}{obj!r}\n")
