"""BFRES model files (FRES v0.10.x, little-endian): reader and skeleton editor.

    f = bfres.Fres(nxfmt.read(path))
    m = f.model()                      # the file's one model (FMDL)
    m.name, m.bones, m.shapes          # bones: [Bone]; shapes: [Shape]
    m.set_skeleton(new_bones, remap)   # new bone list (may be longer / reordered)
    m.rename_material("Mt_Hair", "Mt_Hairs")
    f.rename_model("Player_Child")
    data = f.build()

What the format looks like (checked on every Npc_* / Player_* model of the game):
- Header: "FRES    ", version, BOM, alignment (log2, 12 for the buffer section), u32 offset
  of the file name's characters (+0x10), u32 relocation table offset (+0x18), u32 file size
  (+0x1c), pointers to the name (+0x20), model array (+0x28), model dictionary (+0x30),
  ... string pool (+0xd0), counts (+0xdc).
- Pointers are u64 file offsets. The relocation table ("_RLT", last block of the file) lists
  every pointer: sections {u64 ptr, u32 position, u32 size, i32 first entry, i32 entry count}
  and entries {u32 position, u16 structs, u8 pointers per struct, u8 words to skip}; the
  entries of a section are the pointers that point INTO that section (section 0 = the
  structures and strings, 1 and 2 = index / vertex buffers, 4 = the memory pool object).
  The loader only relocates what is listed, so new pointers need new entries.
- Strings: u16 length, characters, NUL; name pointers point at the length.
- Dictionaries (ResDic): {u32 0, i32 count} + (count + 1) nodes {i32 reference bit,
  u16 left, u16 right, u64 key}; a Patricia trie over the key read as a big-endian number
  (bit 0 = lowest bit of the LAST character). resdic_build() reproduces all 2,158 stock
  dictionaries of the character models byte for byte; resdic_find() is the game's lookup.
- FMDL (0x78): name +0x08, skeleton +0x18, vertex array +0x20, shape array +0x28,
  shape dict +0x30, material array +0x38, material dict +0x40, user data +0x50/+0x58,
  counts +0x68 (vertex, shape, material, ?, user data).
- FSKL: flags +0x04 (0x1200 = Euler XYZ rotations), bone dict +0x08, bone array +0x10,
  matrix-to-bone table +0x18 (u16 bone index per smooth, then per rigid matrix), inverse
  bind matrices +0x20 (3x4 floats per SMOOTH matrix), counts +0x38 (bones, smooth, rigid).
- Bone (0x58): name, user data array, user data dict, 8 reserved, u16 index, i16 parent,
  i16 smooth matrix, i16 rigid matrix, i16 billboard, u16 user data count, u32 flags,
  scale[3], rotation[4] (Euler XYZ radians, w = 1), translation[3].
  Flags: bit 0 visible, 0x1000 Euler, bit 23 segment scale compensate, bits 24/25 scale
  is one, bit 26 rotation is zero, bit 27 translation is zero (hints: only ever set when
  true, sometimes clear although true).
  World rest matrix = parent world x T x Rz x Ry x Rx x S; stored inverse bind matrices are
  its inverse (worst error 1.1e-5 over 129 models).
- FSHP (0x60): name +0x08, skin bone index array +0x20 (u16 bone indices), +0x50: u16
  index, material, bone index, vertex index, skin bone count, u8 weights per vertex
  (0 = rigid to `bone index`, 1 = rigid matrices, 2+ = smooth), u8 mesh count.
- FMAT (0xB0): name +0x08, shader info +0x10 (its first pointer: the shader reflection,
  shared by the file's materials: archive name, shader name, render info array / dict,
  shader parameter array +0x20 (records 0x18: u64 key at +0x08, u16 offset into the
  material's parameter data at +0x10), parameter dict +0x28, counts +0x48 (render infos,
  parameters, parameter data size: 0x390 for the character shader)), sampler dict +0x38,
  parameter data +0x58. Shader parameter names are stored as 64-bit keys, not strings;
  the game looks materials up by NAME through the model's material dict (FMDL +0x40).

Edits never move existing data: new arrays and strings are written where the relocation
table was, the table (with entries for the new pointers) follows them, and the header's
table offset and file size are updated. Values that keep their size are changed in place.
"""
import math
import struct

BONE_SIZE = 0x58
SHAPE_SIZE = 0x60
MATERIAL_SIZE = 0xB0
MODEL_SIZE = 0x78
FLAG_ROTATE_ZERO = 1 << 26
FLAG_TRANSLATE_ZERO = 1 << 27


# ---------------------------------------------------------------- dictionaries

def _key(b):
    return int.from_bytes(b, "big")


def _mismatch(a, b):
    x = a ^ b
    return (x & -x).bit_length() - 1 if x else -1


class _Node:
    __slots__ = ("data", "ref", "child", "parent", "index")

    def __init__(self, data, ref):
        self.data, self.ref, self.child, self.parent, self.index = data, ref, [self, self], None, 0


def resdic_build(names):
    """[(reference bit, left, right)] for the root and each name (bytes), in order."""
    root = _Node(0, -1)
    root.parent = root
    nodes = [root]
    assert len(set(names)) == len(names), "duplicate dictionary keys"
    for name in names:
        data = _key(name)
        assert data, "empty dictionary key"
        if root.child[0] is root:
            cur = root
        else:
            entry = root.child[0]
            while True:
                prev = entry
                entry = entry.child[(data >> entry.ref) & 1]
                if entry.ref <= prev.ref:
                    break
            cur = prev
        b = _mismatch(cur.data, data)
        while b < cur.parent.ref:
            cur = cur.parent
        side = (data >> b) & 1
        if b < cur.ref:
            new = _Node(data, b)
            new.parent = cur.parent
            new.child[side ^ 1] = cur
            cur.parent.child[(data >> cur.parent.ref) & 1 if cur.parent.ref >= 0 else 0] = new
            cur.parent = new
        elif b > cur.ref:
            new = _Node(data, b)
            new.child[side ^ 1] = cur if ((cur.data >> b) & 1) == (side ^ 1) else root
            cur.child[(data >> cur.ref) & 1 if cur.ref >= 0 else 0] = new
            new.parent = cur
        else:
            at = (data >> b) & 1 if b >= 0 else 0
            nb = (data & -data).bit_length() - 1
            if cur.child[at] is not root:
                nb = _mismatch(cur.child[at].data, data)
            new = _Node(data, nb)
            new.child[((data >> nb) & 1) ^ 1] = cur.child[at]
            cur.child[at] = new
            new.parent = cur
        new.index = len(nodes)
        nodes.append(new)
    return [(n.ref, n.child[0].index, n.child[1].index) for n in nodes]


def resdic_find(nodes, names, key):
    """The game's lookup (nn::util::ResDic::FindImpl): node index of `key`, 0 if absent.
    nodes as from resdic_build, names the keys in order (bytes)."""
    def refbit(ref):
        ci = ref >> 3
        return (key[len(key) - ci - 1] >> (ref & 7)) & 1 if ci < len(key) else 0
    parent, child = 0, nodes[0][1]
    while nodes[parent][0] < nodes[child][0]:
        parent, child = child, nodes[child][1 + refbit(nodes[child][0])]
    return child if child and names[child - 1] == key else 0


# ---------------------------------------------------------------- transforms

def euler_matrix(r):
    """3x3 rotation of Euler XYZ angles (radians): Rz * Ry * Rx, as nested lists."""
    cx, sx, cy, sy, cz, sz = math.cos(r[0]), math.sin(r[0]), math.cos(r[1]), math.sin(r[1]), \
        math.cos(r[2]), math.sin(r[2])
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def matrix_euler(m):
    """Euler XYZ angles of a 3x3 rotation matrix (inverse of euler_matrix)."""
    sy = -m[2][0]
    if abs(sy) < 0.9999999:
        return (math.atan2(m[2][1], m[2][2]), math.asin(sy), math.atan2(m[1][0], m[0][0]))
    # gimbal lock: z folded into x
    return (math.atan2(-m[1][2], m[1][1]), math.copysign(math.pi / 2, sy), 0.0)


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mat_vec(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def mat_t(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def rotation_angle(a, b):
    """Angle in degrees between two rotation matrices."""
    m = mat_mul(mat_t(a), b)
    c = (m[0][0] + m[1][1] + m[2][2] - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


class Xf:
    """Rigid transform: rotation matrix + translation (bones with scale 1)."""
    __slots__ = ("r", "t")

    def __init__(self, r, t):
        self.r, self.t = r, list(t)

    def __mul__(self, o):
        return Xf(mat_mul(self.r, o.r), [a + b for a, b in zip(self.t, mat_vec(self.r, o.t))])

    def inv(self):
        rt = mat_t(self.r)
        return Xf(rt, [-x for x in mat_vec(rt, self.t)])

    def point(self, v):
        return [a + b for a, b in zip(self.t, mat_vec(self.r, v))]


IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


# ---------------------------------------------------------------- file

class Bone:
    FIELDS = ("name", "parent", "smooth", "rigid", "billboard", "flags", "scale", "rotate", "translate")

    def __init__(self, name, parent=-1, smooth=-1, rigid=-1, billboard=-1, flags=0x03801001,
                 scale=(1.0, 1.0, 1.0), rotate=(0.0, 0.0, 0.0, 1.0), translate=(0.0, 0.0, 0.0)):
        self.name, self.parent, self.smooth, self.rigid, self.billboard = name, parent, smooth, rigid, billboard
        self.flags, self.scale, self.rotate, self.translate = flags, tuple(scale), tuple(rotate), tuple(translate)

    def copy(self, **changes):
        b = Bone(*(getattr(self, k) for k in self.FIELDS))
        for k, v in changes.items():
            assert k in self.FIELDS, k
            setattr(b, k, v)
        return b

    def local(self):
        assert self.scale == (1.0, 1.0, 1.0), ("scaled bone", self.name)
        return Xf(euler_matrix(self.rotate), self.translate)

    def set_local(self, xf=None, rotate=None, translate=None):
        """Change the rest transform; the "is zero" hint flags are cleared (always safe)."""
        if xf is not None:
            rotate, translate = matrix_euler(xf.r), xf.t
        if rotate is not None:
            self.rotate = (float(rotate[0]), float(rotate[1]), float(rotate[2]), 1.0)
            self.flags &= ~FLAG_ROTATE_ZERO
        if translate is not None:
            self.translate = tuple(float(x) for x in translate)
            self.flags &= ~FLAG_TRANSLATE_ZERO

    def pack(self, index, name_ptr):
        return struct.pack("<QQQQHhhhhHI3f4f3f", name_ptr, 0, 0, 0, index, self.parent, self.smooth, self.rigid,
                           self.billboard, 0, self.flags, *self.scale, *self.rotate, *self.translate)

    def __repr__(self):
        return "Bone(%r, parent=%d)" % (self.name, self.parent)


def world_matrices(bones):
    """Rest world transform (Xf) of every bone; parents must come first."""
    out = []
    for i, b in enumerate(bones):
        assert b.parent < i, ("parent after child", b.name)
        out.append(b.local() if b.parent < 0 else out[b.parent] * b.local())
    return out


class Shape:
    def __init__(self, f, off):
        self.off = off
        assert f.data[off:off + 4] == b"FSHP", hex(off)
        self.name = f.string(f.u64(off + 8))
        self.skin_ptr = f.u64(off + 0x20)
        self.index, self.material, self.bone, self.vertex, self.skin_count, self.weights, self.meshes = \
            struct.unpack_from("<HHHHHBB", f.data, off + 0x50)
        self.skin_bones = list(struct.unpack_from("<%dH" % self.skin_count, f.data, self.skin_ptr)) \
            if self.skin_count else []


class Model:
    def __init__(self, f, off):
        self.f, self.off = f, off
        d = f.data
        assert d[off:off + 4] == b"FMDL", hex(off)
        self.name = f.string(f.u64(off + 8))
        self.skl = f.u64(off + 0x18)
        assert d[self.skl:self.skl + 4] == b"FSKL"
        self.skl_flags = f.u32(self.skl + 4)
        self.dict_off, self.bones_off, self.m2b_off, self.inv_off = struct.unpack_from("<4Q", d, self.skl + 8)
        assert f.u64(self.skl + 0x28) == 0 and f.u64(self.skl + 0x30) == 0, "unknown skeleton arrays"
        nb, self.smooth_count, self.rigid_count = struct.unpack_from("<3H", d, self.skl + 0x38)
        self.vertex_count, self.shape_count, self.material_count = struct.unpack_from("<3H", d, off + 0x68)
        self.bones = []
        self.name_ptr = {}  # name -> string offset (reused when the bone array is rewritten)
        for i in range(nb):
            o = self.bones_off + i * BONE_SIZE
            name_ptr, ud, udd, res, index, parent, smooth, rigid, bill, nud, flags = \
                struct.unpack_from("<QQQQHhhhhHI", d, o)
            assert index == i and ud == udd == res == 0 and nud == 0, ("unsupported bone record", i)
            b = Bone(f.string(name_ptr), parent, smooth, rigid, bill, flags, struct.unpack_from("<3f", d, o + 0x30),
                     struct.unpack_from("<4f", d, o + 0x3C), struct.unpack_from("<3f", d, o + 0x4C))
            assert b.pack(i, name_ptr) == d[o:o + BONE_SIZE], ("bone record does not round trip", b.name)
            self.bones.append(b)
            self.name_ptr[b.name] = name_ptr
        names, nodes = f.read_dict(self.dict_off)
        assert names == [b.name for b in self.bones], "bone dictionary order differs from the bone array"
        assert nodes == resdic_build([n.encode() for n in names]), "bone dictionary does not rebuild"
        self.m2b = list(struct.unpack_from("<%dH" % (self.smooth_count + self.rigid_count), d, self.m2b_off))
        for i, bi in enumerate(self.m2b):
            b = self.bones[bi]
            assert (b.smooth if i < self.smooth_count else b.rigid) == i, ("matrix-to-bone table", i, b.name)
        # (the "<animation project>.Skeleton" files are models without shapes and materials)
        shapes_off = f.u64(off + 0x28)
        self.shapes = [Shape(f, shapes_off + i * SHAPE_SIZE) for i in range(self.shape_count)]
        shape_names = f.read_dict(f.u64(off + 0x30))[0] if self.shape_count else []
        assert shape_names == [s.name for s in self.shapes]
        self.material_names = f.read_dict(f.u64(off + 0x40))[0] if self.material_count else []
        assert len(self.material_names) == self.material_count
        for i, name in enumerate(self.material_names):
            o = f.u64(off + 0x38) + i * MATERIAL_SIZE
            assert d[o:o + 4] == b"FMAT" and f.string(f.u64(o + 8)) == name, ("material record", i, name)

    def inverse_bind_raw(self, smooth):
        """The 12 floats (3 rows of 4) of a smooth matrix index's inverse bind matrix."""
        return struct.unpack_from("<12f", self.f.data, self.inv_off + 48 * smooth)

    def inverse_bind(self, smooth):
        """Stored inverse bind matrix as an Xf (meaningful for bones without scale)."""
        v = self.inverse_bind_raw(smooth)
        return Xf([list(v[0:3]), list(v[4:7]), list(v[8:11])], [v[3], v[7], v[11]])

    def set_inverse_bind(self, smooth, xf):
        rows = [xf.r[i] + [xf.t[i]] for i in range(3)]
        struct.pack_into("<12f", self.f.data, self.inv_off + 48 * smooth, *(x for row in rows for x in row))

    def rigid_bones(self):
        """Indices of bones whose own frame positions vertices (must keep their world rest
        transform): bones with a rigid matrix, and bones a weightless shape hangs on."""
        out = {i for i, b in enumerate(self.bones) if b.rigid >= 0}
        out |= {s.bone for s in self.shapes if s.weights == 0}
        return out

    def rename_material(self, old, new):
        """Rename a material: its record's name and its key in the model's material
        dictionary (the game finds materials by name through that dictionary). Shapes refer
        to materials by index, so nothing else changes."""
        f, d = self.f, self.f.data
        names = list(self.material_names)
        assert new not in names, ("material name is taken", new)
        i = names.index(old)
        names[i] = new
        dic = f.u64(self.off + 0x40)
        stock = f.read_dict(dic)[1]
        assert stock == resdic_build([n.encode() for n in self.material_names]), "material dictionary does not rebuild"
        ptr = f.add_string(new)
        struct.pack_into("<Q", d, f.u64(self.off + 0x38) + i * MATERIAL_SIZE + 8, ptr)
        for k, (ref, left, right) in enumerate(resdic_build([n.encode() for n in names])):
            struct.pack_into("<iHH", d, dic + 8 + k * 16, ref, left, right)
        struct.pack_into("<Q", d, dic + 8 + (i + 1) * 16 + 8, ptr)
        self.material_names = names

    def shader_param(self, material, key, fmt):
        """Default value of a shader parameter of a material (by the parameter's 64-bit
        key), unpacked with `fmt` from the material's parameter data; None if the
        material's shader has no such parameter."""
        f = self.f
        fmat = f.u64(self.off + 0x38) + self.material_names.index(material) * MATERIAL_SIZE
        refl = f.u64(f.u64(fmat + 0x10))
        count, size = struct.unpack_from("<HH", f.data, refl + 0x4A)
        arr = f.u64(refl + 0x20)
        for k in range(count):
            if f.u64(arr + k * 0x18 + 8) == key:
                off = struct.unpack_from("<H", f.data, arr + k * 0x18 + 0x10)[0]
                assert off + struct.calcsize(fmt) <= size
                return struct.unpack_from(fmt, f.data, f.u64(fmat + 0x58) + off)
        return None

    def set_skeleton(self, bones, remap):
        """Replace the bone list. `bones`: the new [Bone] (parents by NEW index, parents
        first). `remap[old index] = new index` for every old bone (all must survive, with
        their smooth / rigid matrix indices). Rewrites the bone array and dictionary (new
        data), and in place the matrix-to-bone table and the shapes' bone indices."""
        f, d = self.f, self.f.data
        assert len(remap) == len(self.bones) and len(set(remap)) == len(remap)
        names = [b.name for b in bones]
        assert len(set(names)) == len(names), "duplicate bone names"
        for old, new in enumerate(remap):
            o, n = self.bones[old], bones[new]
            assert (o.smooth, o.rigid) == (n.smooth, n.rigid), ("matrix index changed", o.name)
        kept = set(remap)
        for i, b in enumerate(bones):
            assert -1 <= b.parent < i, ("parent must come first", b.name)
            assert i in kept or (b.smooth == -1 and b.rigid == -1), ("new bone with a matrix", b.name)
        ptrs = [self.name_ptr[n] if n in self.name_ptr else f.add_string(n) for n in names]
        for n, p in zip(names, ptrs):
            self.name_ptr.setdefault(n, p)
        arr = f.alloc(b"".join(b.pack(i, ptrs[i]) for i, b in enumerate(bones)))
        f.add_reloc(arr, len(bones), 3, 8)
        root_key = f.u64(self.dict_off + 8 + 8)
        nodes = resdic_build([n.encode() for n in names])
        blob = struct.pack("<Ii", 0, len(bones))
        for (ref, left, right), key in zip(nodes, [root_key] + ptrs):
            blob += struct.pack("<iHHQ", ref, left, right, key)
        dic = f.alloc(blob)
        f.add_reloc(dic + 16, len(bones) + 1, 1, 1)
        struct.pack_into("<QQ", d, self.skl + 8, dic, arr)
        struct.pack_into("<H", d, self.skl + 0x38, len(bones))
        self.m2b = [remap[i] for i in self.m2b]
        struct.pack_into("<%dH" % len(self.m2b), d, self.m2b_off, *self.m2b)
        for s in self.shapes:
            s.bone = remap[s.bone]
            struct.pack_into("<H", d, s.off + 0x54, s.bone)
            s.skin_bones = [remap[i] for i in s.skin_bones]
            if s.skin_bones:
                struct.pack_into("<%dH" % len(s.skin_bones), d, s.skin_ptr, *s.skin_bones)
        self.bones, self.dict_off, self.bones_off = list(bones), dic, arr


class Fres:
    def __init__(self, data):
        self.data = bytearray(data)
        d = self.data
        assert d[:8] == b"FRES    ", d[:8]
        self.version = self.u32(8)
        assert self.version >> 16 == 0x000A and d[0x0C:0x0E] == b"\xff\xfe", hex(self.version)
        self.rlt_off, size = struct.unpack_from("<II", d, 0x18)
        assert size == len(d), "file size field"
        # relocation table
        sig, pos, nsec = struct.unpack_from("<4sII", d, self.rlt_off)
        assert sig == b"_RLT" and pos == self.rlt_off
        self.sections = []
        entries_off = self.rlt_off + 0x10 + nsec * 0x18
        total = 0
        for i in range(nsec):
            ptr, spos, ssize, first, count = struct.unpack_from("<QIIii", d, self.rlt_off + 0x10 + i * 0x18)
            assert ptr == 0 and first == total, "relocation sections"
            entries = [list(struct.unpack_from("<IHBB", d, entries_off + (first + k) * 8)) for k in range(count)]
            self.sections.append({"pos": spos, "size": ssize, "entries": entries})
            total += count
        assert entries_off + total * 8 == len(d), "relocation table is not the end of the file"
        # string pool block ("_STR", its size field reaches up to the relocation table)
        pool = self.u64(0xD0)
        self.str_block = d.rfind(b"_STR", 0, pool)
        assert self.str_block >= 0 and self.str_block + self.u32(self.str_block + 8) == self.rlt_off
        self.new = bytearray()      # data that replaces / follows the old relocation table
        self._strings = {}
        self._models = {}
        self.check()

    def u32(self, o):
        return struct.unpack_from("<I", self.data, o)[0]

    def u64(self, o):
        return struct.unpack_from("<Q", self.data, o)[0]

    def string(self, off):
        n = struct.unpack_from("<H", self.data, off)[0]
        s = self.data[off + 2:off + 2 + n]
        assert self.data[off + 2 + n] == 0
        return s.decode("utf-8")

    def read_dict(self, off):
        sig, count = struct.unpack_from("<Ii", self.data, off)
        assert sig == 0, "dictionary signature"
        names, nodes = [], []
        for i in range(count + 1):
            ref, left, right, key = struct.unpack_from("<iHHQ", self.data, off + 8 + i * 16)
            nodes.append((ref, left, right))
            if i:
                names.append(self.string(key))
        return names, nodes

    def pointers(self):
        """(section index, location) of every pointer the relocation table lists."""
        for si, sec in enumerate(self.sections):
            for pos, structs, offsets, padding in sec["entries"]:
                for k in range(structs):
                    for j in range(offsets):
                        yield si, pos + (k * (offsets + padding) + j) * 8

    def check(self):
        """Every listed pointer is null or points into its section (section 0: anywhere in
        the file before the relocation table); no location is listed twice."""
        seen = set()
        end = self.rlt_off + len(self.new)
        for si, loc in self.pointers():
            assert loc not in seen and loc + 8 <= end, ("pointer listed twice / out of range", hex(loc))
            seen.add(loc)
            v = struct.unpack_from("<Q", self._view(loc, 8))[0]
            sec = self.sections[si]
            if v == 0:
                continue
            if si == 0:
                assert v < end, (hex(loc), hex(v))
            else:
                assert sec["pos"] <= v <= sec["pos"] + sec["size"], (si, hex(loc), hex(v))
        return len(seen)

    def _view(self, off, n):
        if off >= self.rlt_off:
            return bytes(self.new[off - self.rlt_off:off - self.rlt_off + n])
        return bytes(self.data[off:off + n])

    def model(self, index=0):
        if index not in self._models:
            assert index < struct.unpack_from("<H", self.data, 0xDC)[0]
            self._models[index] = Model(self, self.u64(0x28) + index * MODEL_SIZE)
        return self._models[index]

    # ---- new data

    def alloc(self, blob, align=8):
        """Add new data; returns its file offset (final: nothing else moves)."""
        while len(self.new) % align:
            self.new.append(0)
        off = self.rlt_off + len(self.new)
        self.new += blob
        return off

    def add_string(self, text):
        if text not in self._strings:
            raw = text.encode("utf-8")
            self._strings[text] = self.alloc(struct.pack("<H", len(raw)) + raw + b"\0", align=4)
        return self._strings[text]

    def add_reloc(self, pos, structs, offsets, padding):
        """List `structs` records of `offsets` pointers followed by `padding` u64 words."""
        assert structs < 0x10000 and offsets < 0x100 and padding < 0x100
        self.sections[0]["entries"].append([pos, structs, offsets, padding])

    def rename_model(self, name, index=0):
        """Rename the model (its name, its dictionary key) and the file's own name
        ("<old project>.<old name>" keeps its project part)."""
        d = self.data
        m = self.model(index)
        names, _ = self.read_dict(self.u64(0x30))
        assert names[index] == m.name
        names[index] = name
        ptr = self.add_string(name)
        struct.pack_into("<Q", d, m.off + 8, ptr)
        dic = self.u64(0x30)
        for i, (ref, left, right) in enumerate(resdic_build([n.encode() for n in names])):
            struct.pack_into("<iHH", d, dic + 8 + i * 16, ref, left, right)
        struct.pack_into("<Q", d, dic + 8 + (index + 1) * 16 + 8, ptr)
        m.name = name
        if index == 0:
            old = self.string(self.u64(0x20))
            fptr = self.add_string(old.rsplit(".", 1)[0] + "." + name if "." in old else name)
            struct.pack_into("<Q", d, 0x20, fptr)
            struct.pack_into("<I", d, 0x10, fptr + 2)

    def build(self):
        self.check()
        if not self.new:
            return bytes(self.data)
        d = bytearray(self.data[:self.rlt_off])
        new = bytearray(self.new)
        while len(new) % 16:
            new.append(0)
        rlt = self.rlt_off + len(new)
        nsec = len(self.sections)
        out = bytearray(struct.pack("<4sIII", b"_RLT", rlt, nsec, 0))
        first = 0
        for sec in self.sections:
            # (model files end with an empty section at the table itself; it moves along)
            pos = rlt if sec["pos"] == self.rlt_off else sec["pos"]
            out += struct.pack("<QIIii", 0, pos, sec["size"], first, len(sec["entries"]))
            first += len(sec["entries"])
        for sec in self.sections:
            for e in sec["entries"]:
                out += struct.pack("<IHBB", *e)
        d += new + out
        # a section that sat at the table and moved with it: pointers to its start move too
        # (files made with Switch Toolbox keep one such pointer, header +0x188)
        for sec in self.sections:
            if sec["pos"] != self.rlt_off:
                continue
            for pos, structs, offsets, padding in sec["entries"]:
                for k in range(structs):
                    for j in range(offsets):
                        loc = pos + (k * (offsets + padding) + j) * 8
                        if struct.unpack_from("<Q", d, loc)[0] == self.rlt_off:
                            struct.pack_into("<Q", d, loc, rlt)
        struct.pack_into("<II", d, 0x18, rlt, len(d))
        struct.pack_into("<I", d, self.str_block + 8, rlt - self.str_block)
        return bytes(d)
