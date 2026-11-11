"""In-place edits of a BYML (v7, either byte order) that keep every existing byte.

For big tables such as the GameDataList (big-endian), where a full rewrite with
byml.py does not give back the original bytes: change inline scalar values, and
append a dict to an array. The grown array is written at the end of the file
and the parent's reference moved to it; the old array stays behind as unused
bytes (readers only follow references).

    p = Patch(data)
    p.append_dict(["Data", "Int"], template=0, values={"Hash": 0x1234})
    p.add_int(["MetaData", "AllDataSaveSize"], 8)
    new = p.bytes()
"""
import struct

ARRAY, DICT = 0xC0, 0xC1
INT, FLOAT, UINT = 0xD1, 0xD2, 0xD3
INLINE = (0xD0, INT, FLOAT, UINT)


class Patch:
    def __init__(self, data):
        assert data[:2] in (b"YB", b"BY"), data[:2]
        self.e = "<" if data[:2] == b"YB" else ">"
        self.d = bytearray(data)
        self.version, kt, _st, self.root = struct.unpack_from(self.e + "HIII", data, 2)
        self.keys = self._strtab(kt)
        self.key_index = {k: i for i, k in enumerate(self.keys)}

    def _u32(self, o):
        return struct.unpack_from(self.e + "I", self.d, o)[0]

    def _node(self, o):
        w = self._u32(o)
        return (w & 0xFF, w >> 8) if self.e == "<" else (w >> 24, w & 0xFFFFFF)

    def _node_word(self, t, n):
        return struct.pack(self.e + "I", (t | (n << 8)) if self.e == "<" else ((t << 24) | n))

    def _entry_word(self, w):
        """dict entry word -> (key index, value type)"""
        return (w & 0xFFFFFF, w >> 24) if self.e == "<" else (w >> 8, w & 0xFF)

    def _strtab(self, o):
        t, n = self._node(o)
        assert t == 0xC2, hex(t)
        out = []
        for i in range(n):
            so = o + self._u32(o + 4 + 4 * i)
            out.append(bytes(self.d[so:self.d.index(b"\0", so)]).decode("utf-8"))
        return out

    def _dict_entry(self, o, key):
        """(entry offset, value type) of `key` in the dict at o."""
        t, n = self._node(o)
        assert t == DICT, "not a dict at %#x" % o
        ki = self.key_index[key]
        for i in range(n):
            eo = o + 4 + 8 * i
            k, vt = self._entry_word(self._u32(eo))
            if k == ki:
                return eo, vt
        raise KeyError(key)

    def _array_slot(self, o, index):
        """(value offset, value type) of element `index` of the array at o."""
        t, n = self._node(o)
        assert t == ARRAY, "not an array at %#x" % o
        assert 0 <= index < n, (index, n)
        return o + 4 + ((n + 3) & ~3) + 4 * index, self.d[o + 4 + index]

    def _slot(self, path):
        """Value offset and type of the item at `path` (dict keys / array indices)."""
        o, vo, t = self.root, None, DICT
        for step in path:
            if vo is not None:
                assert t in (ARRAY, DICT), path
                o = self._u32(vo)
            if isinstance(step, int):
                vo, t = self._array_slot(o, step)
            else:
                eo, t = self._dict_entry(o, step)
                vo = eo + 4
        return vo, t

    def _fmt(self, t):
        return self.e + ("i" if t == INT else "I")

    def get_int(self, path):
        vo, t = self._slot(path)
        assert t in (INT, UINT), (path, hex(t))
        return struct.unpack_from(self._fmt(t), self.d, vo)[0]

    def set_int(self, path, value):
        vo, t = self._slot(path)
        assert t in (INT, UINT), (path, hex(t))
        struct.pack_into(self._fmt(t), self.d, vo, value)

    def add_int(self, path, delta):
        old = self.get_int(path)
        self.set_int(path, old + delta)
        return old, old + delta

    def _align(self, n):
        while len(self.d) % n:
            self.d += b"\0"

    def append_dict(self, array_path, template, values):
        """Append a copy of the array's dict element `template` with inline
        int values replaced ({key: int}; every key must exist in the template
        as an Int or UInt). Returns the new element's index."""
        return self.append_dicts(array_path, template, [values])[0]

    def append_dicts(self, array_path, template, values_list):
        """append_dict for several elements at once (the array is moved once)."""
        vo, t = self._slot(array_path)
        assert t == ARRAY, array_path
        arr = self._u32(vo)
        _, n = self._node(arr)
        types = bytes(self.d[arr + 4:arr + 4 + n])
        vals_at = arr + 4 + ((n + 3) & ~3)
        vals = [self._u32(vals_at + 4 * i) for i in range(n)]
        assert types[template] == DICT
        src = vals[template]
        _, m = self._node(src)
        new_dicts = []
        for values in values_list:
            entries, seen = [], set()
            for i in range(m):
                eo = src + 4 + 8 * i
                raw_w = bytes(self.d[eo:eo + 4])
                ki, vt = self._entry_word(self._u32(eo))
                key = self.keys[ki]
                raw_v = bytes(self.d[eo + 4:eo + 8])
                if key in values:
                    assert vt in (INT, UINT), (key, hex(vt))
                    raw_v = struct.pack(self._fmt(vt), values[key])
                    seen.add(key)
                else:
                    assert vt in INLINE, "template entry %s is not inline" % key
                entries.append(raw_w + raw_v)
            assert seen == set(values), set(values) - seen
            self._align(4)
            new_dicts.append(len(self.d))
            self.d += self._node_word(DICT, m) + b"".join(entries)
        self._align(4)
        new_arr = len(self.d)
        n2 = n + len(new_dicts)
        self.d += self._node_word(ARRAY, n2)
        self.d += types + bytes([DICT] * len(new_dicts)) + b"\0" * (((n2 + 3) & ~3) - n2)
        for v in vals + new_dicts:
            self.d += struct.pack(self.e + "I", v)
        struct.pack_into(self.e + "I", self.d, vo, new_arr)
        return list(range(n, n2))

    def bytes(self):
        return bytes(self.d)
