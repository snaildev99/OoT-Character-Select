"""BYML v2-v7 read/write with exact scalar types, for editing game tables.

load(data) -> Python objects: dict, list, str, bool, int (s32), float (f32)
and the wrappers U32, I64, U64, F64 for the other scalar types.
dump(obj, version, big_endian=False) writes the way the game's files are laid
out: header, sorted key table, sorted string table, root container, then
child containers depth first, identical containers stored once.
Hash/relocated node types (0xA1 binary, 0x20/0x21 hash dicts) are not handled.
"""
import struct


class U32(int):
    pass


class I64(int):
    pass


class U64(int):
    pass


class F64(float):
    pass


class _Reader:
    def __init__(self, d):
        self.d = d
        self.e = "<" if d[:2] == b"YB" else ">"
        assert d[:2] in (b"YB", b"BY"), d[:2]
        self.version, kt, st, root = struct.unpack_from(self.e + "HIII", d, 2)
        self.keys = self._strtab(kt) if kt else []
        self.strs = self._strtab(st) if st else []
        self.root = self._node(root) if root else None

    def u32(self, o):
        return struct.unpack_from(self.e + "I", self.d, o)[0]

    def hdr(self, o):
        w = self.u32(o)
        return (w & 0xFF, w >> 8) if self.e == "<" else (w >> 24, w & 0xFFFFFF)

    def _strtab(self, o):
        t, n = self.hdr(o)
        assert t == 0xC2, hex(t)
        out = []
        for i in range(n):
            so = o + self.u32(o + 4 + i * 4)
            out.append(self.d[so:self.d.index(b"\0", so)].decode("utf-8"))
        return out

    def _val(self, t, raw):
        e = self.e
        if t == 0xA0:
            return self.strs[raw]
        if t in (0xC0, 0xC1):
            return self._node(raw)
        if t == 0xD0:
            return bool(raw)
        if t == 0xD1:
            return struct.unpack(e + "i", struct.pack(e + "I", raw))[0]
        if t == 0xD2:
            return struct.unpack(e + "f", struct.pack(e + "I", raw))[0]
        if t == 0xD3:
            return U32(raw)
        if t == 0xD4:
            return I64(struct.unpack_from(e + "q", self.d, raw)[0])
        if t == 0xD5:
            return U64(struct.unpack_from(e + "Q", self.d, raw)[0])
        if t == 0xD6:
            return F64(struct.unpack_from(e + "d", self.d, raw)[0])
        if t == 0xFF:
            return None
        raise ValueError("unsupported BYML node type 0x%02x" % t)

    def _node(self, o):
        t, n = self.hdr(o)
        if t == 0xC0:
            types = self.d[o + 4:o + 4 + n]
            vo = o + 4 + ((n + 3) & ~3)
            return [self._val(types[i], self.u32(vo + i * 4)) for i in range(n)]
        if t == 0xC1:
            out = {}
            for i in range(n):
                eo = o + 4 + i * 8
                w = self.u32(eo)
                if self.e == "<":
                    ki, vt = w & 0xFFFFFF, w >> 24
                else:
                    ki, vt = w >> 8, w & 0xFF
                out[self.keys[ki]] = self._val(vt, self.u32(eo + 4))
            return out
        raise ValueError("unsupported BYML container 0x%02x" % t)


def load(data):
    r = _Reader(data)
    return r.root


def version_of(data):
    return _Reader(data).version


def _scalar_type(v):
    if v is None:
        return 0xFF
    if isinstance(v, bool):
        return 0xD0
    if isinstance(v, str):
        return 0xA0
    if isinstance(v, U32):
        return 0xD3
    if isinstance(v, I64):
        return 0xD4
    if isinstance(v, U64):
        return 0xD5
    if isinstance(v, F64):
        return 0xD6
    if isinstance(v, int):
        return 0xD1
    if isinstance(v, float):
        return 0xD2
    raise TypeError(type(v))


def _collect(obj, keys, strs):
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            _collect(v, keys, strs)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, keys, strs)
    elif isinstance(obj, str):
        strs.add(obj)


def _sig(obj):
    """Hashable identity of a container's content (for sharing identical ones)."""
    if isinstance(obj, dict):
        return ("d", tuple((k, _sig(v)) for k, v in sorted(obj.items())))
    if isinstance(obj, list):
        return ("a", tuple(_sig(v) for v in obj))
    return (_scalar_type(obj), obj)


def dump(obj, version=7, big_endian=False):
    e = ">" if big_endian else "<"
    keys, strs = set(), set()
    _collect(obj, keys, strs)
    keys = sorted(keys, key=lambda s: s.encode())
    strs = sorted(strs, key=lambda s: s.encode())
    kidx = {k: i for i, k in enumerate(keys)}
    sidx = {s: i for i, s in enumerate(strs)}
    out = bytearray(0x10)

    def align():
        while len(out) % 4:
            out.extend(b"\0")

    def hdr(t, n):
        return struct.pack(e + "I", (n << 8) | t if e == "<" else (t << 24) | n)

    def strtab(lst):
        align()
        start = len(out)
        blob = bytearray()
        offs = []
        base = 4 + 4 * (len(lst) + 1)
        for s in lst:
            offs.append(base + len(blob))
            blob += s.encode("utf-8") + b"\0"
        offs.append(base + len(blob))
        out.extend(hdr(0xC2, len(lst)) + struct.pack(e + "%dI" % len(offs), *offs) + blob)
        return start

    kt = strtab(keys) if keys else 0
    st = strtab(strs) if strs else 0
    align()
    written = {}
    big = []  # (patch offset, value) for 64-bit scalars, written after all containers

    def raw_value(v, patch_at):
        t = _scalar_type(v)
        if t == 0xA0:
            return sidx[v]
        if t == 0xD0:
            return 1 if v else 0
        if t == 0xD1:
            return struct.unpack(e + "I", struct.pack(e + "i", v))[0]
        if t == 0xD2:
            return struct.unpack(e + "I", struct.pack(e + "f", v))[0]
        if t == 0xD3:
            return int(v)
        if t in (0xD4, 0xD5, 0xD6):
            big.append((patch_at, t, v))
            return 0
        if t == 0xFF:
            return 0
        raise TypeError(type(v))

    def write_container(node):
        sig = _sig(node)
        if sig in written:
            return written[sig]
        align()
        start = len(out)
        written[sig] = start
        children = []  # (patch offset, child container)
        if isinstance(node, list):
            n = len(node)
            types = bytes(0xC0 if isinstance(v, list) else 0xC1 if isinstance(v, dict) else _scalar_type(v)
                          for v in node)
            out.extend(hdr(0xC0, n) + types + b"\0" * (((n + 3) & ~3) - n))
            for v in node:
                at = len(out)
                if isinstance(v, (list, dict)):
                    children.append((at, v))
                    out.extend(b"\0\0\0\0")
                else:
                    out.extend(struct.pack(e + "I", raw_value(v, at)))
        else:
            items = sorted(node.items(), key=lambda kv: kidx[kv[0]])
            out.extend(hdr(0xC1, len(items)))
            for k, v in items:
                t = 0xC0 if isinstance(v, list) else 0xC1 if isinstance(v, dict) else _scalar_type(v)
                w = (t << 24) | kidx[k] if e == "<" else (kidx[k] << 8) | t
                out.extend(struct.pack(e + "I", w))
                at = len(out)
                if isinstance(v, (list, dict)):
                    children.append((at, v))
                    out.extend(b"\0\0\0\0")
                else:
                    out.extend(struct.pack(e + "I", raw_value(v, at)))
        for at, child in children:
            struct.pack_into(e + "I", out, at, write_container(child))
        return start

    root = write_container(obj) if obj is not None else 0
    for at, t, v in big:
        while len(out) % 8:
            out.extend(b"\0")
        struct.pack_into(e + "I", out, at, len(out))
        out.extend(struct.pack(e + {0xD4: "q", 0xD5: "Q", 0xD6: "d"}[t], v))
    struct.pack_into(e + "2sHIII", out, 0, b"YB" if e == "<" else b"BY", version, kt, st, root)
    return bytes(out)
