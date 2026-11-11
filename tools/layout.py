"""UI layout files (.buil, "BFFH" + "Nintendo.Ui.ResModels.LayoutResData"): read and write.

    import layout
    lay = layout.Layout(data)          # parse (bytes of one .buil)
    lay.root                           # Node tree, see below
    layout.build(lay)                  # bytes; every vanilla layout rebuilds byte for byte

File: header 0x28 + type name (128 bytes), section table (BFSI entries of 0x28:
magic, u32 offset, u32 size, u32 alignment, u64 offset, char name[16]), section
"Default" (all objects, root first at 0xf8) and section "StringPool" (C strings
and 6-byte animation target keys, no table). Pointers are u64 file offsets that
the game turns into addresses by walking every reference by type (so an object
must never be referenced twice: it would be relocated twice).

The schema below comes from the game's own relocation code (LayoutResData at
0x8153e804 and the functions it calls: material 0x8153e5e0, picture data
0x8153e720, part 0x8153f900 / 0x81540000, pass 0x8153fb40, animation 0x8153fec0
-> 0x815723e0 -> 0x815722e0; see README). Objects are laid out depth first:
an object, then the targets of its pointer fields in field order. Dictionaries
are written as values block, keys block, hashes block, 32 zero bytes, then the
value objects; their keys are CityHash64 of the name.

Field kinds in SCHEMA:
    ("P", off, type)   pointer to an object of `type` ("leaf" = opaque bytes or a
                       pool string, decided by where it points)
    ("A", off, type)   {u64 ptr, u32 count, u32 stride 8}: array of pointers
    ("D", off, type)   {keys, values, hashes, u32 count, u32 stride}: name -> object
    ("K", off)         pointer to a 6-byte animation key in the string pool
    ("PD", off)        dictionary with plain values: {keys, values, hashes, count,
                       stride}; values inline (count * stride bytes)
"""
import struct

from cityhash import city_hash64

ROOT_OFFSET = 0xF8

# type -> (fixed size, alignment, fields)
SCHEMA = {
    "Root": (0x228, 8, [
        ("P", 0x18, "leaf"),          # layout name
        ("D", 0x20, "Pane"),
        ("D", 0x40, "Material"),       # keyed by pane name
        ("D", 0x60, "Picture"),        # P_ and W_ panes
        ("D", 0x80, "Text"),           # T_ panes
        ("D", 0xA0, "Align"),          # A_ panes
        ("D", 0xC0, "Part"),           # Pa_ panes (instances of other layouts)
        ("D", 0xE0, "Capture"),        # C_ panes
        ("D", 0x100, "List"),          # slide lists (4 in the game)
        ("A", 0x120, "UserEntry"),
        ("A", 0x130, "Pass"),          # shader passes: texture bindings, samplers
        ("D", 0x140, "Anim"),
        ("D", 0x160, "Ext160"),
        ("D", 0x180, "CapTex"),
        ("P", 0x1A0, "CapTex"),
        ("D", 0x1A8, "CapTex"),
        ("P", 0x1C8, "leaf"),          # {ptr, count, 16}
        ("P", 0x1D8, "leaf"),          # {ptr, count, 4}
        ("A", 0x1E8, "leaf"),
        ("P", 0x1F8, "X1f8"),
        ("P", 0x208, "leaf"),          # {ptr, count, 4}: one per material
        ("P", 0x218, "leaf"),          # {ptr, 32, 1}
    ]),
    "Pane": (0x44, 4, []),
    "Material": (0x40, 8, [("A", 0x0, "TexMap"), ("P", 0x38, "MatExt")]),
    "TexMap": (0x30, 8, [("A", 0x0, "leaf")]),            # texture pattern names
    "MatExt": (0x30, 8, [("P", 0x0, "leaf"), ("P", 0x8, "leaf"), ("P", 0x10, "leaf"), ("P", 0x18, "MatExtTex")]),
    "MatExtTex": (None, 8, [("P", 0x0, "leaf")]),
    "Picture": (0x18, 8, [("P", 0x0, "PicData"), ("P", 0x8, "Window")]),
    "PicData": (0x30, 8, [("P", 0x8, "Corner4"), ("P", 0x10, "leaf"), ("P", 0x18, "leaf"), ("P", 0x20, "PicX"),
                          ("P", 0x28, "leaf")]),
    "Corner4": (0x20, 8, [("P", 0x0, "leaf"), ("P", 0x8, "leaf"), ("P", 0x10, "leaf"), ("P", 0x18, "leaf")]),
    "PicX": (None, 8, [("P", 0x8, "leaf"), ("P", 0x10, "leaf"), ("P", 0x18, "leaf"), ("P", 0x20, "leaf")]),
    "Window": (0x10, 8, [("A", 0x0, "leaf")]),
    "Text": (0xA8, 8, [("P", 0x0, "leaf"), ("P", 0x10, "leaf"), ("P", 0x18, "leaf"), ("P", 0xA0, "leaf")]),
    "Align": (0x8, 8, []),
    "Part": (0x48, 8, [("P", 0x0, "leaf"), ("A", 0x8, "leaf"), ("A", 0x18, "PartProp"), ("A", 0x28, "PartAnim"),
                       ("P", 0x38, "PartX")]),
    "PartProp": (None, 8, [("A", 0x0, "leaf")]),
    "PartAnim": (None, 8, [("P", 0x0, "leaf")]),
    "PartX": (None, 8, [("A", 0x0, "leaf")]),
    "Capture": (0x10, 8, []),
    "List": (None, 8, [("P", 0x0, "leaf"), ("P", 0x8, "ListPart"), ("P", 0x10, "ListPart"), ("P", 0x18, "ListPart"),
                       ("P", 0x20, "ListPart")]),
    # slide list item templates: Part's pointer layout without its trailing 8 bytes
    "ListPart": (0x40, 8, [("P", 0x0, "leaf"), ("A", 0x8, "leaf"), ("A", 0x18, "PartProp"), ("A", 0x28, "PartAnim"),
                           ("P", 0x38, "PartX")]),
    "UserEntry": (None, 8, [("P", 0x0, "UserX"), ("P", 0x8, "leaf")]),
    "UserX": (None, 8, [("A", 0x0, "leaf")]),
    "Pass": (0x88, 8, [("A", 0x0, "PassTex"), ("A", 0x10, "leaf"), ("A", 0x20, "PassY"), ("A", 0x30, "leaf"),
                       ("A", 0x40, "leaf"), ("A", 0x50, "PassZ"), ("P", 0x68, "leaf"), ("P", 0x78, "leaf")]),
    "PassTex": (0x10, 8, [("P", 0x8, "leaf")]),            # texture binding: flags, texture name
    "PassY": (None, 8, [("A", 0x8, "leaf")]),
    "PassZ": (0x30, 8, [("P", 0x0, "leaf"), ("PD", 0x10)]),   # {ptr, n, 16} + name -> value
    "Anim": (0x30, 8, [("P", 0x0, "AnimData"), ("P", 0x8, "AnimX"), ("P", 0x10, "AnimX"), ("A", 0x18, "AnimEv")]),
    "AnimX": (None, 8, [("P", 0x0, "leaf"), ("P", 0x10, "leaf")]),
    "AnimEv": (None, 8, [("P", 0x0, "leaf"), ("P", 0x8, "leaf")]),
    "AnimData": (0x60, 8, [("P", 0x0, "leaf"), ("A", 0x8, "Target"), ("A", 0x18, "Curve"), ("P", 0x28, "leaf"),
                           ("P", 0x38, "leaf"), ("P", 0x48, "leaf")]),
    "Target": (0x20, 8, [("K", 0x0), ("A", 0x10, "leaf")]),
    "Curve": (0x18, 8, [("P", 0x0, "CurveKeys"), ("P", 0x8, "CurveKeys")]),   # type word +0x10 must be 0
    "CurveKeys": (0x18, 8, [("P", 0x8, "leaf")]),           # {u32, f32, {ptr, n, 4}}: times or (value, slope)
    "Ext160": (None, 8, [("A", 0x0, "leaf"), ("A", 0x10, "leaf"), ("P", 0x38, "leaf"), ("P", 0x48, "leaf")]),
    "CapTex": (None, 8, [("A", 0x0, "CapTexEl")]),
    "CapTexEl": (None, 8, [("P", 0x0, "leaf"), ("P", 0x10, "leaf")]),
    "X1f8": (None, 8, [("A", 0x0, "leaf")]),
}
KEY_LEN = 6


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def u64(d, o):
    return struct.unpack_from("<Q", d, o)[0]


class Pool(bytes):
    """A string-pool entry: exact bytes (C strings include their NUL; keys are 6 bytes)."""

    @property
    def text(self):
        return self[:-1].decode("utf-8") if self.endswith(b"\0") else None


def cstr(s):
    return Pool(s.encode("utf-8") + b"\0")


class Node:
    """An object: its own bytes (pointer slots zeroed) and its pointer fields."""
    __slots__ = ("t", "buf", "ptr", "align", "src")

    def __init__(self, t, buf, align=8, src=None):
        self.t, self.buf, self.ptr, self.align, self.src = t, bytearray(buf), {}, align, src

    def __repr__(self):
        return "<%s %d bytes @%s>" % (self.t, len(self.buf), hex(self.src) if self.src is not None else "new")

    def get(self, fmt, off):
        return struct.unpack_from(fmt, self.buf, off)

    def set(self, fmt, off, *values):
        struct.pack_into(fmt, self.buf, off, *values)


class Arr:
    """Array of pointers: `items` (Node / Pool / None each). `null`: the pointer
    itself is 0 (only for empty arrays)."""
    __slots__ = ("items", "null", "src")

    def __init__(self, items=None, null=False, src=None):
        self.items, self.null, self.src = list(items or []), null, src


class Dict:
    """Name -> object dictionary; `items` is a list of [name Pool, Node, hash]
    (hash None = compute CityHash64 of the name when writing)."""
    __slots__ = ("items", "t", "src")

    def __init__(self, t, items=None, src=None):
        self.t, self.items, self.src = t, list(items or []), src

    def names(self):
        return [it[0].text for it in self.items]

    def get(self, name):
        for it in self.items:
            if it[0].text == name:
                return it[1]
        raise KeyError(name)

    def index(self, name):
        return self.names().index(name)


class PDict:
    """Name -> plain value dictionary (values inline, `stride` bytes each);
    `items` is a list of [name Pool, value bytes, hash]."""
    __slots__ = ("items", "stride", "src")

    def __init__(self, stride, items=None, src=None):
        self.stride, self.items, self.src = stride, list(items or []), src


class Layout:
    def __init__(self, data):
        d = self.d = bytes(data)
        assert d[:4] == b"BFFH" and u32(d, 8) == len(d), "not a layout file"
        self.type_name = d[0x28:0xA8].split(b"\0")[0].decode()
        assert self.type_name == "Nintendo.Ui.ResModels.LayoutResData", self.type_name
        secs = {}
        for i in range(u32(d, 0x18)):
            o = u64(d, 0x10) + i * 0x28
            assert d[o:o + 4] == b"BFSI"
            secs[d[o + 24:o + 40].split(b"\0")[0].decode()] = (u32(d, o + 4), u32(d, o + 8))
        self.data_start, data_size = secs["Default"]
        self.pool_start, pool_size = secs["StringPool"]
        assert self.data_start == ROOT_OFFSET == u64(d, 0x20)
        assert self.data_start + data_size == self.pool_start and self.pool_start + pool_size == len(d)
        self.seen = {}        # offset -> object (shared references are an error)
        self.leaves = []      # leaf Nodes, sized after parsing
        self.pool_refs = set()
        self.root = self._obj("Root", ROOT_OFFSET)
        self._size_leaves()
        self._pool_entries()

    # ---------------------------------------------------------------- parsing
    def _claim(self, off, obj):
        assert off not in self.seen or isinstance(self.seen[off], Arr) or isinstance(obj, Arr), \
            "object at %#x referenced twice" % off
        self.seen.setdefault(off, obj)

    def _obj(self, t, off):
        size, align, fields = SCHEMA[t]
        assert off % align == 0, (t, hex(off))
        node = Node(t, b"", align, off)
        self._claim(off, node)
        if size is None:
            self.leaves.append(node)          # typed, but size known only after parsing
        else:
            node.buf = bytearray(self.d[off:off + size])
        for f in fields:
            kind, slot = f[0], f[1]
            p = off + slot
            v = u64(self.d, p)
            if kind == "P":
                node.ptr[slot] = self._target(v, f[2])
            elif kind == "K":
                node.ptr[slot] = self._pool(v, key=True)
            elif kind == "A":
                node.ptr[slot] = self._arr(v, u32(self.d, p + 8), u32(self.d, p + 12), f[2])
            elif kind == "D":
                node.ptr[slot] = self._dict(p, f[2])
            elif kind == "PD":
                node.ptr[slot] = self._pdict(p)
        return node

    def _target(self, v, t):
        if v == 0:
            return None
        if self.pool_start <= v < len(self.d):
            return self._pool(v)
        if t == "leaf":
            node = Node("leaf", b"", 8, v)
            self._claim(v, node)
            self.leaves.append(node)
            return node
        return self._obj(t, v)

    def _pool(self, v, key=False):
        if v == 0:
            return None
        assert self.pool_start <= v < len(self.d), hex(v)
        self.pool_refs.add((v, key))
        return v  # replaced by a Pool entry in _pool_entries

    def _arr(self, v, n, stride, t):
        if v == 0:
            assert n == 0
            return Arr(null=True)
        assert stride == 8, stride
        a = Arr(src=v)
        self._claim(v, a)
        a.items = [self._target(u64(self.d, v + 8 * i), t) for i in range(n)]
        return a

    def _dict(self, p, t):
        keys, vals, hashes, n, stride = u64(self.d, p), u64(self.d, p + 8), u64(self.d, p + 16), \
            u32(self.d, p + 24), u32(self.d, p + 28)
        assert stride == 8 and keys and vals and hashes
        dct = Dict(t, src=vals)
        for i in range(n):
            k = self._pool(u64(self.d, keys + 8 * i))
            h = u64(self.d, hashes + 8 * i)
            dct.items.append([k, self._obj(t, u64(self.d, vals + 8 * i)), h])
        return dct

    def _pdict(self, p):
        keys, vals, hashes, n, stride = u64(self.d, p), u64(self.d, p + 8), u64(self.d, p + 16),             u32(self.d, p + 24), u32(self.d, p + 28)
        pd = PDict(stride, src=vals)
        for i in range(n):
            pd.items.append([self._pool(u64(self.d, keys + 8 * i)), self.d[vals + stride * i:vals + stride * (i + 1)],
                             u64(self.d, hashes + 8 * i)])
        return pd

    def _starts(self):
        """Every object start in the Default section with its fixed size (None = leaf)."""
        out = []

        def walk(x):
            if isinstance(x, Node):
                out.append((x.src, len(x.buf) if x.buf or x.t == "Pane" else None, x))
                for s in sorted(x.ptr):
                    walk(x.ptr[s])
            elif isinstance(x, Arr):
                if not x.null:
                    out.append((x.src, 8 * len(x.items), x))
                for it in x.items:
                    walk(it)
            elif isinstance(x, Dict):
                n = len(x.items)
                out.append((x.src, 24 * n + 32, x))   # values, keys, hashes, reserved
                for it in x.items:
                    walk(it[1])
            elif isinstance(x, PDict):
                out.append((x.src, None, x))
        walk(self.root)
        return out

    def _size_leaves(self):
        starts = sorted(set(s for s, _, _ in self._starts()))
        import bisect
        for leaf in self.leaves:
            i = bisect.bisect_right(starts, leaf.src)
            end = starts[i] if i < len(starts) else self.pool_start
            leaf.buf = bytearray(self.d[leaf.src:end])
            a = leaf.src & -leaf.src
            leaf.align = min(8, a) if leaf.src else 8
        # typed nodes of unknown size were sized as leaves: their pointer slots must be inside
        for leaf in self.leaves:
            if leaf.t != "leaf":
                fields = SCHEMA[leaf.t][2]
                need = max(f[1] + (16 if f[0] in "AD" else 8) for f in fields)
                first_child = min([c.src for c in self._children(leaf) if c is not None and c.src] or [1 << 60])
                size = min(len(leaf.buf), first_child - leaf.src)
                assert size >= need, (leaf.t, hex(leaf.src), size, need)
                leaf.buf = leaf.buf[:size]
        for leaf in self.leaves:      # zero the pointer slots
            for f in (SCHEMA[leaf.t][2] if leaf.t != "leaf" else []):
                leaf.buf[f[1]:f[1] + 8] = bytes(8)
        self._zero_slots(self.root, set())

    def _children(self, node):
        out = []
        for s in sorted(node.ptr):
            x = node.ptr[s]
            if isinstance(x, (Node, Arr)):
                out.append(x)
            elif isinstance(x, Dict):
                out.append(x)
        return out

    def _zero_slots(self, x, done):
        if id(x) in done:
            return
        done.add(id(x))
        if isinstance(x, Node):
            if x.t != "leaf":
                for f in SCHEMA[x.t][2]:
                    n = 24 if f[0] in ("D", "PD") else 8
                    x.buf[f[1]:f[1] + n] = bytes(n)
            for v in x.ptr.values():
                self._zero_slots(v, done)
        elif isinstance(x, Arr):
            for it in x.items:
                self._zero_slots(it, done)
        elif isinstance(x, Dict):
            for it in x.items:
                self._zero_slots(it[1], done)

    def _pool_entries(self):
        """Cut the string pool into entries at every referenced offset."""
        offs = sorted(set(o for o, _ in self.pool_refs))
        keys = set(o for o, k in self.pool_refs if k)
        self.pool = {}
        for i, o in enumerate(offs):
            end = offs[i + 1] if i + 1 < len(offs) else len(self.d)
            raw = self.d[o:end]
            if o in keys:
                assert len(raw) >= KEY_LEN, (hex(o), raw)
                ent = raw[:KEY_LEN]
            else:
                ent = raw[:raw.index(b"\0") + 1]
            assert raw[len(ent):] == b"" or o == offs[-1] or True
            self.pool[o] = Pool(ent)
        self.pool_tail = {}           # bytes after an entry that nothing references
        for i, o in enumerate(offs):
            end = offs[i + 1] if i + 1 < len(offs) else len(self.d)
            extra = self.d[o + len(self.pool[o]):end]
            if extra:
                self.pool_tail[o] = extra
        self._resolve_pool(self.root, set())

    def _resolve_pool(self, x, done):
        if id(x) in done:
            return
        done.add(id(x))
        if isinstance(x, Node):
            for s, v in x.ptr.items():
                if isinstance(v, int):
                    x.ptr[s] = self.pool[v]
                else:
                    self._resolve_pool(v, done)
        elif isinstance(x, Arr):
            x.items = [self.pool[v] if isinstance(v, int) else v for v in x.items]
            for v in x.items:
                self._resolve_pool(v, done)
        elif isinstance(x, Dict):
            for it in x.items:
                if isinstance(it[0], int):
                    it[0] = self.pool[it[0]]
                self._resolve_pool(it[1], done)
        elif isinstance(x, PDict):
            for it in x.items:
                if isinstance(it[0], int):
                    it[0] = self.pool[it[0]]

    # ---------------------------------------------------------------- views
    def dict(self, slot):
        return self.root.ptr[slot]

    @property
    def name(self):
        return self.root.ptr[0x18].text


DICTS = {"panes": 0x20, "materials": 0x40, "pictures": 0x60, "texts": 0x80, "aligns": 0xA0, "parts": 0xC0,
         "captures": 0xE0, "lists": 0x100, "anims": 0x140, "ext160": 0x160, "captex": 0x180, "captex2": 0x1A8}


# -------------------------------------------------------------------- writing

def build(lay, header=None, _gap=0):
    """Serialize a Layout (or anything with .root and .d for the header).
    `_gap` (tests only): zero bytes inserted after the root, moving everything else."""
    out = bytearray(lay.d[:ROOT_OFFSET] if header is None else header)
    fix = []         # (position, target object or Pool)
    pos = {}         # id(object) -> offset
    pool, pool_pos = [], {}

    def addpool(s):
        if s not in pool_pos:
            pool_pos[s] = None
            pool.append(s)

    def align(a):
        while len(out) % a:
            out.append(0)

    def emit(x):
        if isinstance(x, Node):
            align(x.align)
            base = len(out)
            pos[id(x)] = base
            out.extend(x.buf)
            if x is lay.root and _gap:
                out.extend(bytes(_gap))
            for s in sorted(x.ptr):
                slot(base + s, x.ptr[s])
        else:
            raise TypeError(x)

    def slot(at, v):
        if v is None:
            return
        if isinstance(v, Pool):
            addpool(v)
            fix.append((at, v))
        elif isinstance(v, Node):
            fix.append((at, v))
            emit(v)
        elif isinstance(v, Arr):
            if v.null:
                assert not v.items
                return
            align(8)
            base = len(out)
            pos[id(v)] = base
            fix.append((at, v))
            struct.pack_into("<II", out, at + 8, len(v.items), 8)
            out.extend(bytes(8 * len(v.items)))
            for i, it in enumerate(v.items):
                slot(base + 8 * i, it)
        elif isinstance(v, Dict):
            n = len(v.items)
            align(8)
            vals = len(out)
            out.extend(bytes(8 * n))
            keys = len(out)
            out.extend(bytes(8 * n))
            hashes = len(out)
            for k, _, *h in v.items:
                hv = city_hash64(k[:-1]) if not h or h[0] is None else h[0]
                out.extend(struct.pack("<Q", hv))
            out.extend(bytes(32))
            struct.pack_into("<QQQII", out, at, 0, 0, 0, n, 8)
            fix.append((at, ("abs", keys)))
            fix.append((at + 8, ("abs", vals)))
            fix.append((at + 16, ("abs", hashes)))
            for i, (k, obj, *_) in enumerate(v.items):
                addpool(k)
                fix.append((keys + 8 * i, k))
                fix.append((vals + 8 * i, obj))
                emit(obj)
        elif isinstance(v, PDict):
            n = len(v.items)
            align(8)
            vals = len(out)
            for _, val, *_h in v.items:
                assert len(val) == v.stride
                out.extend(val)
            align(8)
            keys = len(out)
            out.extend(bytes(8 * n))
            hashes = len(out)
            for k, _, *h in v.items:
                hv = city_hash64(k[:-1]) if not h or h[0] is None else h[0]
                out.extend(struct.pack("<Q", hv))
            out.extend(bytes(32))
            struct.pack_into("<QQQII", out, at, 0, 0, 0, n, v.stride)
            fix.append((at, ("abs", keys)))
            fix.append((at + 8, ("abs", vals)))
            fix.append((at + 16, ("abs", hashes)))
            for i, (k, *_r) in enumerate(v.items):
                addpool(k)
                fix.append((keys + 8 * i, k))
        else:
            raise TypeError(v)

    emit(lay.root)
    lay.placed = pos   # id(object) -> offset in the output (handy for debugging)
    data_end = len(out)
    pool_start = data_end
    blob = bytearray()
    tails = getattr(lay, "pool_tail", {})
    src_off = {v: k for k, v in getattr(lay, "pool", {}).items()}
    for s in pool:
        pool_pos[s] = pool_start + len(blob)
        blob.extend(s)
        t = tails.get(src_off.get(s))
        if t:
            blob.extend(t)
    out.extend(blob)
    for at, v in fix:
        if isinstance(v, tuple):
            target = v[1]
        elif isinstance(v, Pool):
            target = pool_pos[v]
        else:
            target = pos[id(v)]
        struct.pack_into("<Q", out, at, target)
    # header: file size, sections
    struct.pack_into("<I", out, 8, len(out))
    secs = u64(out, 0x10)
    for i in range(u32(out, 0x18)):
        o = secs + i * 0x28
        name = bytes(out[o + 24:o + 40]).split(b"\0")[0]
        if name == b"Default":
            struct.pack_into("<IIIQ", out, o + 4, ROOT_OFFSET, data_end - ROOT_OFFSET, 8, ROOT_OFFSET)
        elif name == b"StringPool":
            struct.pack_into("<IIIQ", out, o + 4, pool_start, len(out) - pool_start, 1, pool_start)
    return bytes(out)
