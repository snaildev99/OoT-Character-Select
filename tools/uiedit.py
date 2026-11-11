"""Editing UI layouts (.buil) parsed with layout.py: pane views, cloning, inserting
panes with every cross reference renumbered, and authoring simple animations.

    lay = layout.Layout(data)
    ed = Editor(lay)
    ed.validate()                        # all vanilla layouts pass
    p = ed.pane("T_Name_00"); p.pos = (0, 200); p.visible = False
    new = ed.clone("T_Name_00", "T_Label_00")       # pane + material + passes + text record
    ed.insert("N_IsGuide_00", [new])                 # as last child, everything renumbered
    ed.add_anim("Focus_00", frames=1, consts=[(ed.key("P_Cursor_00", TRANSLATE), (100.0, 50.0))])
    data = layout.build(lay)

What the indices mean (all verified on the 164 vanilla layouts by validate()):
- Panes are in pre-order. Pane record (0x44): +0x10 scale x/y, +0x18 rotation (rad),
  +0x1c translate x/y (y up, screen 3840x2160 centred), +0x24 alpha table index
  (root +0x1e8), +0x28 parent, +0x2c size w/h, +0x34 end of subtree (exclusive),
  +0x38 flags (bit 0 visible; (flags >> 7) & 7 = type), +0x3c material index |
  window-extension index << 16 (0xffff = none), +0x40 low 0xffff | index into the
  type's dictionary << 16.
- Each type has its dictionary in pane order: pictures +0x60 (P_, W_), texts +0x80,
  alignments +0xa0, parts +0xc0, captures +0xe0, slide lists +0x100 (type 7). Materials +0x40 in pane order;
  each owns a run of passes in root +0x130 starting at material +0x14.
- Part record +0x40 = its own pane index; its property paths ("PartProp") start
  with that index as a string.
- Other records repeat indices too (100 % on vanilla): picture +0x10 = material;
  text +0x88 = material, +0x8c = pane; capture +0x0 = material, +0x4 = pane; slide
  list +0x28 = pane. insert() rewrites them all (_fix_embedded) and validate()
  checks them. (Before 2026-11-09 they were left stale: every pane after an insert
  point, and every clone, pointed at the wrong material.)
- Text record: +0x08 font size (x, y), +0x18 message reference (see README: only
  "*" or a null pointer gives the automatic label lookup), +0x70 / +0x78 horizontal
  alignment (0 left, 1 centre, 2 right), +0x74 vertical (0 top, 1 centre, 2 bottom).
- Material: +0x18..+0x24 four colour palette indices (corners), +0x28 white colour,
  +0x2c black colour (texture grey is mapped black -> white), all into root +0x1c8.
- Scissor panes (type 4, flags 0x0255522b) clip their children to their rectangle.
- Animation target key (6 bytes, 7 bits used per byte, fields packed across them as
  the game reads them at 0x81505fd8): sub element = b0 | (b1 & 7) << 7 (0x3ff = the
  pane itself), pane = ((b1 >> 3) & 15) | (b2 & 31) << 4 (b2 bit 5 is always set),
  property = (b2 >> 6 & 1) | b3 << 1 | (b4 & 1) << 8. So "group" b3 with b2 = 0x60
  and with b2 = 0x20 are different properties (b3 = 2: visibility / size).
  Groups: 0 scale, 1 translate, 2 visibility, 4 alpha, 27+ material, 57+ text,
  0x43/0x44 (b4 = 1) frame / enable of the part's animation number b0.
  For a part's animation, b0 indexes the layout's part-animation name list (root
  +0x1f8) and the animation needs an event (name, pane index, 1) per driven part;
  the frame value is normalised (0 start, 1 end of the part's animation).
  Target: u16 first curve, u16 first constant, u16 count (curves | constants << 8),
  u16 value type (0 bool, 12 alpha, 14 float/vec, 15 colour). Each curve and
  constant has a component byte (offset in the value: 0 x, 4 y, 8 z, 12 w).
"""
import copy
import struct

import layout
from layout import Arr, Dict, Node, Pool, cstr

TYPES = {0: "null", 1: "picture", 2: "text", 3: "align", 4: "scissor", 5: "capture", 6: "part", 7: "list"}
TYPE_DICT = {1: 0x60, 2: 0x80, 3: 0xA0, 5: 0xE0, 6: 0xC0, 7: 0x100}
SCALE, TRANSLATE, VISIBLE, ALPHA = 0, 1, 2, 4
PART_FRAME, PART_ENABLE = 0x43, 0x44
VALUE_TYPE = {SCALE: 14, TRANSLATE: 14, VISIBLE: 0, ALPHA: 12, PART_FRAME: 12, PART_ENABLE: 0}
# typed dictionary -> [(offset in the record, which index of its pane)]
EMBEDDED = {0x60: [(0x10, "material")], 0x80: [(0x88, "material"), (0x8C, "pane")],
            0xE0: [(0x0, "material"), (0x4, "pane")], 0x100: [(0x28, "pane")]}
SCISSOR_FLAGS = 0x0255522B     # S_ScissorsNote_00 (Ocarina_00): scissor with a scrolling child
CURVE_REPEAT = 0x0400          # curve type word of the game's looping curves


def key_pane(k):
    return ((k[1] >> 3) & 0xF) | ((k[2] & 0x1F) << 4)


def key_set_pane(k, i):
    assert 0 <= i < 512
    b = bytearray(k)
    b[1] = (b[1] & 0x87) | ((i & 0xF) << 3)
    b[2] = (b[2] & 0xE0) | (i >> 4)
    return Pool(bytes(b))


def deep(x):
    """Deep copy of a sub-tree (new Node / Arr objects; pool strings shared)."""
    if isinstance(x, Node):
        n = Node(x.t, bytes(x.buf), x.align)
        n.ptr = {s: deep(v) for s, v in x.ptr.items()}
        return n
    if isinstance(x, Arr):
        return Arr([deep(i) for i in x.items], x.null)
    if isinstance(x, Dict):
        return Dict(x.t, [[k, deep(v), None] for k, v, *_ in x.items])
    return x  # Pool or None


class PaneView:
    def __init__(self, ed, item):
        self.ed, self.item = ed, item

    node = property(lambda s: s.item[1])
    name = property(lambda s: s.item[0].text)
    index = property(lambda s: s.ed.panes.index(s.item))
    type = property(lambda s: (s.flags >> 7) & 7)

    def _f(fmt, off):
        def get(s):
            v = s.node.get(fmt, off)
            return v if len(v) > 1 else v[0]

        def put(s, v):
            s.node.set(fmt, off, *(v if isinstance(v, (tuple, list)) else (v,)))
        return property(get, put)

    scale = _f("<2f", 0x10)
    rotation = _f("<f", 0x18)
    pos = _f("<2f", 0x1C)
    alpha_index = _f("<I", 0x24)
    parent = _f("<i", 0x28)
    size = _f("<2f", 0x2C)
    end = _f("<I", 0x34)
    flags = _f("<I", 0x38)
    del _f

    @property
    def visible(self):
        return bool(self.flags & 1)

    @visible.setter
    def visible(self, v):
        self.flags = (self.flags & ~1) | int(bool(v))

    @property
    def material(self):
        return self.node.get("<I", 0x3C)[0] & 0xFFFF

    @property
    def ext(self):
        return self.node.get("<I", 0x3C)[0] >> 16

    @property
    def type_index(self):
        return self.node.get("<I", 0x40)[0] >> 16

    def set_indices(self, material=None, ext=None, type_index=None):
        m, e = self.material, self.ext
        m = m if material is None else material
        e = e if ext is None else ext
        self.node.set("<I", 0x3C, (e << 16) | m)
        if type_index is not None:
            lo = self.node.get("<I", 0x40)[0] & 0xFFFF
            self.node.set("<I", 0x40, (type_index << 16) | lo)

    def __repr__(self):
        return "<pane %d %s %s>" % (self.index, self.name, TYPES[self.type])


class NewPane:
    """A pane not yet in the layout, with what it owns (see Editor.clone)."""

    def __init__(self, name, node, parent=None, material=None, passes=(), arr208=b"\0" * 4, typed=None, ext=None):
        self.name, self.node, self.parent = name, node, parent   # parent: index in the block, None = insert target
        self.material, self.passes, self.arr208 = material, list(passes), arr208
        self.typed, self.ext = typed, ext

    @property
    def view(self):
        return PaneView(None, [cstr(self.name), self.node, None])


class Editor:
    def __init__(self, lay):
        self.lay = lay
        self.r = lay.root

    # ------------------------------------------------------------ access
    @property
    def panes(self):
        return self.r.ptr[0x20].items

    def pane(self, name_or_index):
        if isinstance(name_or_index, int):
            return PaneView(self, self.panes[name_or_index])
        for it in self.panes:
            if it[0].text == name_or_index:
                return PaneView(self, it)
        raise KeyError(name_or_index)

    def names(self):
        return [it[0].text for it in self.panes]

    def dict_items(self, slot):
        d = self.r.ptr[slot]
        return d.items

    def passes(self):
        a = self.r.ptr[0x130]
        return a.items

    def _pass_runs(self):
        """[(first pass, count)] per material, in material order."""
        mats = self.dict_items(0x40)
        firsts = [m[1].get("<I", 0x14)[0] for m in mats]
        total = len(self.passes())
        return [(f, (firsts[i + 1] if i + 1 < len(firsts) else total) - f) for i, f in enumerate(firsts)]

    def arr208(self):
        n = self.r.get("<I", 0x208 + 8)[0]
        return [bytes(self.r.ptr[0x208].buf[4 * i:4 * i + 4]) for i in range(n)]

    # ------------------------------------------------------------ checks
    def validate(self):
        """Every cross reference this editor maintains, recomputed and compared."""
        panes = self.panes
        n = len(panes)
        views = [PaneView(self, it) for it in panes]
        assert views[0].parent == -1 and views[0].end == n
        for i, v in enumerate(views[1:], 1):
            assert 0 <= v.parent < i, (v, v.parent)
            assert views[v.parent].end >= v.end > i, (v, v.end)
        for t, slot in TYPE_DICT.items():
            typed = [v for v in views if v.type == t]
            items = self.dict_items(slot)
            assert [v.name for v in typed] == [it[0].text for it in items], (TYPES[t], len(typed), len(items))
            assert [v.type_index for v in typed] == list(range(len(typed))), TYPES[t]
        with_mat = [v for v in views if v.material != 0xFFFF]
        mats = self.dict_items(0x40)
        assert [v.name for v in with_mat] == [it[0].text for it in mats]
        assert [v.material for v in with_mat] == list(range(len(mats)))
        runs = self._pass_runs()
        assert not runs or runs[0][0] == 0, runs
        assert all(c >= 1 for f, c in runs) or not runs, runs
        assert [f for f, c in runs] == sorted(f for f, c in runs)
        assert len(self.arr208()) == len(mats)
        with_ext = [v for v in views if v.ext != 0xFFFF]
        ext = self.dict_items(0x160)
        assert [v.name for v in with_ext] == [it[0].text for it in ext]
        assert [v.ext for v in with_ext] == list(range(len(ext)))
        for k, part, h in self.dict_items(0xC0):
            assert part.get("<I", 0x40)[0] == self.names().index(k.text), k.text
        index = {v.name: i for i, v in enumerate(views)}
        for slot, checks in EMBEDDED.items():
            for it in self.dict_items(slot):
                v = views[index[it[0].text]]
                for off, what in checks:
                    want = v.material if what == "material" else index[v.name]
                    assert it[1].get("<I", off)[0] == want, (it[0].text, hex(slot), hex(off), what)
        for _, anim, _h in self.dict_items(0x140):
            for t in anim.ptr[0].ptr[8].items:
                assert key_pane(t.ptr[0]) < n
            for e in anim.ptr[0x18].items:
                pi, one = e.get("<II", 0x18)
                assert pi < n and views[pi].type == 6 and one == 1, (pi, one)
        return True

    # ------------------------------------------------------------ cloning
    def clone(self, src, new_name, parent=None):
        """Copy of pane `src` of this layout (not its children) with everything it
        owns: material + passes + arr208 entry, type record, window extension."""
        v = self.pane(src)
        node = deep(v.node)
        mat = passes = typed = ext = None
        a208 = b"\0" * 4
        if v.material != 0xFFFF:
            mat = deep(self.dict_items(0x40)[v.material][1])
            f, c = self._pass_runs()[v.material]
            passes = [deep(p) for p in self.passes()[f:f + c]]
            a208 = self.arr208()[v.material]
        if v.type in TYPE_DICT:
            typed = deep(self.dict_items(TYPE_DICT[v.type])[v.type_index][1])
        if v.ext != 0xFFFF:
            ext = deep(self.dict_items(0x160)[v.ext][1])
        return NewPane(new_name, node, parent, mat, passes or (), a208, typed, ext)

    def new_part(self, template, new_name, part_layout, parent=None, anims=(), size=None):
        """A part pane from an existing part pane of this layout (its record lists
        are dropped), showing layout `part_layout`; `anims` = [(anim name, frame)]
        initial frames of the part's own animations."""
        np_ = self.clone(template, new_name, parent)
        rec = np_.typed
        rec.ptr[0x0] = cstr(part_layout)
        rec.ptr[0x8] = Arr([], null=True)
        rec.ptr[0x18] = Arr([], null=True)
        rec.ptr[0x28] = Arr([part_anim(a, f) for a, f in anims], null=not anims)
        x = rec.ptr[0x38]
        x.ptr[0x0] = Arr([], null=True)
        for off in (0x8, 0x18, 0x28):   # count / stride of the emptied arrays
            rec.set("<II", off + 8, 0, 0)
        x.set("<II", 0x8, 0, 0)
        if anims:
            rec.set("<II", 0x28 + 8, len(anims), 8)
        if size:
            np_.node.set("<2f", 0x2C, *size)
        return np_

    # ------------------------------------------------------------ inserting
    def insert(self, parent, block, at=None):
        """Insert NewPanes (a pre-order block: each one's .parent is an index into
        the block, or None for `parent`) as the last children of `parent` (or at
        pane index `at`, which must be inside parent's subtree end)."""
        names = self.names()
        P = names.index(parent) if isinstance(parent, str) else parent
        views = [PaneView(self, it) for it in self.panes]
        pos = views[P].end if at is None else at
        assert P < pos <= views[P].end
        n = len(block)
        for b in block:
            assert b.name not in names, b.name
        old_to_new = {i: (i if i < pos else i + n) for i in range(len(views))}
        ancestors = set()
        a = P
        while a != -1:
            ancestors.add(a)
            a = views[a].parent
        for i, v in enumerate(views):
            if v.parent >= pos:
                v.parent = v.parent + n
            if i >= pos or i in ancestors:
                v.end = v.end + n
        # block pane parents / ends
        ends = list(range(1, n + 1))
        for j in range(n - 1, -1, -1):
            pj = block[j].parent
            if pj is not None:
                assert pj < j
                ends[pj] = max(ends[pj], ends[j])
        for j, b in enumerate(block):
            bv = PaneView(self, [cstr(b.name), b.node, None])
            bv.parent = P if b.parent is None else pos + b.parent
            bv.end = pos + ends[j]
        new_items = [[cstr(b.name), b.node, None] for b in block]
        self.panes[pos:pos] = new_items
        # typed dictionaries, materials (+ passes, arr208), window extensions
        owned = {id(b.node): b for b in block}
        self._rebuild_typed(owned)
        self._rebuild_materials(owned)
        self._rebuild_ext(owned)
        # part self indices and their property paths (which start with that index)
        block_recs = set(id(b.typed) for b in block)
        for k, part, h in self.dict_items(0xC0):
            old = part.get("<I", 0x40)[0]
            idx = self.names().index(k.text)
            remap = {old: idx} if id(part) in block_recs else old_to_new
            if id(part) not in block_recs:
                assert old_to_new.get(old) == idx, (k.text, old, idx)
            part.set("<I", 0x40, idx)
            for prop in part.ptr[0x18].items:
                for i, s in enumerate(prop.ptr[0].items):
                    if isinstance(s, Pool) and s.text and s.text.isdigit():
                        prop.ptr[0].items[i] = cstr(str(remap.get(int(s.text), int(s.text))))
        # animation targets and part-animation events
        for _, anim, _h in self.dict_items(0x140):
            for t in anim.ptr[0].ptr[8].items:
                k = t.ptr[0]
                t.ptr[0] = key_set_pane(k, old_to_new[key_pane(k)])
            for e in anim.ptr[0x18].items:
                e.set("<I", 0x18, old_to_new[e.get("<I", 0x18)[0]])
        self._fix_embedded()
        self.validate()
        return [self.pane(b.name) for b in block]

    def _fix_embedded(self):
        """Material / pane indices repeated inside the typed records (EMBEDDED)."""
        views = [PaneView(self, it) for it in self.panes]
        index = {v.name: i for i, v in enumerate(views)}
        for slot, checks in EMBEDDED.items():
            for it in self.dict_items(slot):
                v = views[index[it[0].text]]
                for off, what in checks:
                    it[1].set("<I", off, v.material if what == "material" else index[v.name])

    def _rebuild_typed(self, owned):
        views = [PaneView(self, it) for it in self.panes]
        for t, slot in TYPE_DICT.items():
            old = {it[0].text: it for it in self.dict_items(slot)}
            items = []
            for v in views:
                if v.type != t:
                    continue
                if v.name in old:
                    items.append(old[v.name])
                else:
                    rec = owned[id(v.node)].typed
                    assert rec is not None, v.name
                    items.append([cstr(v.name), rec, None])
                v.set_indices(type_index=len(items) - 1)
            self.r.ptr[slot].items = items

    def _rebuild_materials(self, owned):
        views = [PaneView(self, it) for it in self.panes]
        old_mats = self.dict_items(0x40)
        runs = self._pass_runs()
        a208 = self.arr208()
        passes = self.passes()
        by_name = {it[0].text: (it, passes[f:f + c], a208[i]) for i, (it, (f, c)) in enumerate(zip(old_mats, runs))}
        mats, new_passes, new_208 = [], [], []
        for v in views:
            if v.material == 0xFFFF:
                continue
            if v.name in by_name and id(v.node) not in owned:
                it, ps, a = by_name[v.name]
            else:
                b = owned[id(v.node)]
                it, ps, a = [cstr(v.name), b.material, None], b.passes, b.arr208
            it[1].set("<I", 0x14, len(new_passes))
            v.set_indices(material=len(mats))
            mats.append(it)
            new_passes += ps
            new_208.append(a)
        self.r.ptr[0x40].items = mats
        self.r.ptr[0x130].items = new_passes
        self.r.ptr[0x130].null = not new_passes
        leaf = self.r.ptr[0x208]
        leaf.buf = bytearray(b"".join(new_208))
        while len(leaf.buf) % 8:
            leaf.buf.append(0)
        self.r.set("<II", 0x208 + 8, len(new_208), 4)

    def _rebuild_ext(self, owned):
        views = [PaneView(self, it) for it in self.panes]
        old = {it[0].text: it for it in self.dict_items(0x160)}
        items = []
        for v in views:
            if v.ext == 0xFFFF:
                continue
            if v.name in old and id(v.node) not in owned:
                items.append(old[v.name])
            else:
                items.append([cstr(v.name), owned[id(v.node)].ext, None])
            v.set_indices(ext=len(items) - 1)
        self.r.ptr[0x160].items = items

    # ------------------------------------------------------------ animations
    def key(self, pane, group, sub=None, part_anim=None):
        """Target key for a pane property (group TRANSLATE etc.), or for frame /
        enable (PART_FRAME / PART_ENABLE) of animation number `part_anim` of a part."""
        i = self.names().index(pane) if isinstance(pane, str) else pane
        if part_anim is not None:
            return key_set_pane(bytes([part_anim, 0, 0x20, group, 1, 0]), i)
        return key_set_pane(bytes([0x7F if sub is None else sub, 7 if sub is None else 0, 0x60, group, 0, 0]), i)

    def part_anim_slot(self, name):
        """Index of a part animation name in the layout's list (root +0x1f8), added if new."""
        lst = self.r.ptr[0x1F8].ptr[0x0]
        names = [x.text for x in lst.items]
        if name not in names:
            lst.items.append(cstr(name))
            lst.null = False
            names.append(name)
        return names.index(name)

    def part_key(self, pane, anim_name, group=PART_FRAME):
        return self.key(pane, group, part_anim=self.part_anim_slot(anim_name))

    def add_anim(self, name, frames, consts=(), curves=(), template="DetailIn", events=(), loop=False):
        """New animation from a vanilla one of this layout (its targets dropped).
        consts: [(key, value or tuple of values)]: constant for the whole animation.
        curves: [(key, curve)] with curve = a Curve node (hermite / state_curve /
            linear_curve), a list of them (one per component: x, y, ...), or a list of
            (frame, value) points (linear). One target per key.
        Components are x, y, ... in order (byte offsets 0, 4, ...).
        events: [(part animation name, part pane)]: one per part whose own animation
            the targets drive (PART_FRAME / PART_ENABLE keys).
        loop: the animation repeats when played (give its curves CURVE_REPEAT)."""
        anim = deep(self.r.ptr[0x140].get(template))
        ad = anim.ptr[0]
        ad.ptr[0x0] = cstr(name)
        targets, curve_nodes, curve_comp, const_vals, const_comp = [], [], [], [], []
        seen = set()
        for key, value in consts:
            assert bytes(key) not in seen, "two targets for one key in " + name
            seen.add(bytes(key))
            vals = value if isinstance(value, (tuple, list)) else (value,)
            t = Node("Target", bytes(0x20))
            t.ptr = {0x0: Pool(key), 0x10: Arr([], null=True)}
            t.set("<HHHH", 8, 0xFFFF, len(const_vals), len(vals) << 8, VALUE_TYPE[key[3]])
            const_vals += vals
            const_comp += [4 * i for i in range(len(vals))]
            targets.append(t)
        for key, cv in curves:
            assert bytes(key) not in seen, "two targets for one key in " + name
            seen.add(bytes(key))
            if isinstance(cv, Node):
                cvs = [cv]
            elif cv and isinstance(cv[0], Node):
                cvs = list(cv)
            else:
                cvs = [linear_curve(cv)]
            t = Node("Target", bytes(0x20))
            t.ptr = {0x0: Pool(key), 0x10: Arr([], null=True)}
            t.set("<HHHH", 8, len(curve_nodes), 0xFFFF, len(cvs), VALUE_TYPE[key[3]])
            curve_nodes += cvs
            curve_comp += [4 * i for i in range(len(cvs))]
            targets.append(t)
        ad.ptr[0x8] = Arr(targets)
        ad.ptr[0x18] = Arr(curve_nodes, null=not curve_nodes)
        ad.set("<II", 0x10, len(targets), 8)
        ad.set("<II", 0x20, len(curve_nodes), 8 if curve_nodes else 0)
        ad.ptr[0x28] = leaf(bytes(curve_comp), 1) if curve_comp else None
        ad.set("<II", 0x30, len(curve_comp), 1 if curve_comp else 0)
        ad.ptr[0x38] = leaf(struct.pack("<%df" % len(const_vals), *const_vals), 4) if const_vals else None
        ad.set("<II", 0x40, len(const_vals), 4 if const_vals else 0)
        ad.ptr[0x48] = leaf(bytes(const_comp), 1) if const_comp else None
        ad.set("<II", 0x50, len(const_comp), 1 if const_comp else 0)
        ad.set("<II", 0x58, int(loop), frames)
        evs = []
        for an, pane in events:
            e = Node("AnimEv", bytes(0x20))
            e.ptr = {0x0: cstr(an), 0x8: None}
            e.set("<II", 0x18, self.names().index(pane) if isinstance(pane, str) else pane, 1)
            evs.append(e)
        anim.ptr[0x18] = Arr(evs, null=not evs)
        anim.set("<II", 0x20, len(evs), 8 if evs else 0)
        anim.set("<I", 0x28, frames)
        self.r.ptr[0x140].items.append([cstr(name), anim, None])
        return anim

    def drive(self, pane, anim_name, values=None, value=None):
        """Targets + event that make a screen animation set a part's own animation:
        returns (consts, curves, events) to add to add_anim's lists. `values` = the
        normalised frame (0 = start, 1 = end of the part's animation) at each integer
        frame of the screen animation; `value` = one constant instead."""
        slot = self.part_anim_slot(anim_name)
        consts = [(self.key(pane, PART_ENABLE, part_anim=slot), 1.0)]
        curves = []
        if values is None:
            consts.append((self.key(pane, PART_FRAME, part_anim=slot), float(value)))
        else:
            curves.append((self.key(pane, PART_FRAME, part_anim=slot), state_curve(values)))
        return consts, curves, [(anim_name, pane)]

    def add_targets(self, anim_name, curves):
        """Append linear curve targets to an existing animation (e.g. make a new
        container slide with the page's own in/out animation)."""
        anim = self.r.ptr[0x140].get(anim_name)
        ad = anim.ptr[0]
        tg, cv = ad.ptr[0x8], ad.ptr[0x18]
        comps = bytearray(ad.ptr[0x28].buf[:ad.get("<I", 0x30)[0]]) if ad.ptr.get(0x28) is not None else bytearray()
        for key, points, comp in curves:
            t = Node("Target", bytes(0x20))
            t.ptr = {0x0: Pool(key), 0x10: Arr([], null=True)}
            t.set("<HHHH", 8, len(cv.items), 0xFFFF, 1, VALUE_TYPE[key[3]])
            tg.items.append(t)
            cv.items.append(linear_curve(points))
            cv.null = False
            comps.append(comp)
        ad.set("<II", 0x10, len(tg.items), 8)
        ad.set("<II", 0x20, len(cv.items), 8)
        ad.ptr[0x28] = leaf(bytes(comps), 1)
        ad.set("<II", 0x30, len(comps), 1)

    def anim_curve_points(self, anim_name, pane, group):
        """[(frame, value)] keys of the first curve driving (pane, group) in an animation."""
        anim = self.r.ptr[0x140].get(anim_name)
        ad = anim.ptr[0]
        i = self.names().index(pane)
        for t in ad.ptr[8].items:
            k = t.ptr[0]
            if key_pane(k) == i and k[3] == group and k[4] == 0:
                c = t.get("<H", 8)[0]
                cvn = ad.ptr[0x18].items[c]
                A, B = cvn.ptr[0], cvn.ptr[8]
                n = A.get("<I", 0x10)[0]
                times = struct.unpack_from("<%df" % n, A.ptr[8].buf)
                vals = struct.unpack_from("<%df" % (2 * n), B.ptr[8].buf)[0::2]
                return list(zip(times, vals))
        raise KeyError((anim_name, pane, group))


def leaf(data, align):
    n = Node("leaf", data, 8)
    while len(n.buf) % 8:
        n.buf.append(0)
    return n


def part_anim(name, frame):
    n = Node("PartAnim", bytes(0x10))
    n.ptr = {0x0: cstr(name)}
    n.set("<f", 8, float(frame))
    return n


def hermite(keys, wrap=0):
    """Curve from Hermite keys [(frame, value, slope)], in the game's layout: keys A
    = {first frame, last frame, times}, keys B = {last value - first value, 0,
    (value, slope) pairs}. Two keys may share a frame (a step, or a change of slope).
    wrap: the curve's type word (CURVE_REPEAT for looping animations)."""
    assert len(keys) >= 2 and all(keys[i][0] <= keys[i + 1][0] for i in range(len(keys) - 1)), keys
    times = [float(k[0]) for k in keys]
    vals = [float(x) for k in keys for x in (k[1], k[2])]
    a = Node("CurveKeys", bytes(0x18))
    a.set("<ff", 0, times[0], times[-1])
    a.set("<II", 0x10, len(times), 4)
    a.ptr = {0x8: leaf(struct.pack("<%df" % len(times), *times), 4)}
    b = Node("CurveKeys", bytes(0x18))
    b.set("<f", 0, vals[-2] - vals[0])
    b.set("<II", 0x10, len(vals), 4)
    b.ptr = {0x8: leaf(struct.pack("<%df" % len(vals), *vals), 4)}
    c = Node("Curve", bytes(0x18))
    c.set("<I", 0x10, wrap)
    c.ptr = {0x0: a, 0x8: b}
    return c


def state_curve(values):
    """A value per integer frame (for animations that are stopped at a frame, the
    way the game picks states): one key per frame with slope 0, so the value at
    frame i is exactly values[i] however the game treats the ends of a segment."""
    values = list(values)
    if len(values) == 1:
        values = values * 2
    return hermite([(i, v, 0.0) for i, v in enumerate(values)])


def ease_curve(t0, v0, t1, v1):
    """v0 -> v1 over [t0, t1], fast at first and settling (quadratic ease-out)."""
    return hermite([(t0, v0, 2.0 * (v1 - v0) / (t1 - t0)), (t1, v1, 0.0)])


def linear_curve(points):
    """Curve through [(frame, value)], linear in between and flat outside, keyed the
    way the game's own linear curves are: every key doubled for its incoming and
    outgoing slope."""
    keys = []
    for i, (t, v) in enumerate(points):
        s_in = (v - points[i - 1][1]) / (t - points[i - 1][0]) if i > 0 else 0.0
        s_out = (points[i + 1][1] - v) / (points[i + 1][0] - t) if i + 1 < len(points) else 0.0
        keys += [(t, v, s_in), (t, v, s_out)]
    return hermite(keys)


def curve_keys(cv):
    """[(frame, value, slope)] of a Curve node."""
    A, B = cv.ptr[0x0], cv.ptr[0x8]
    n = A.get("<I", 0x10)[0]
    times = struct.unpack_from("<%df" % n, A.ptr[0x8].buf)
    vals = struct.unpack_from("<%df" % (2 * n), B.ptr[0x8].buf)
    return [(times[i], vals[2 * i], vals[2 * i + 1]) for i in range(n)]


def curve_value(cv, t):
    """A Curve node evaluated at frame t (cubic Hermite, clamped outside its keys;
    at a doubled key the later one counts). For offline checks of what we author."""
    keys = curve_keys(cv)
    if t >= keys[-1][0]:
        return keys[-1][1]
    if t < keys[0][0]:
        return keys[0][1]
    at = [k for k in keys if k[0] == t]
    if at:
        return at[-1][1]
    for (t0, v0, m0), (t1, v1, m1) in zip(keys, keys[1:]):
        if t0 < t < t1:
            d = t1 - t0
            x = (t - t0) / d
            return ((2 * x ** 3 - 3 * x ** 2 + 1) * v0 + (x ** 3 - 2 * x ** 2 + x) * d * m0 +
                    (-2 * x ** 3 + 3 * x ** 2) * v1 + (x ** 3 - x ** 2) * d * m1)
    return keys[-1][1]


def anim_values(lay, anim_name, t):
    """{(pane index, property group, part-animation slot or None): [values]} of a
    layout animation at frame t (constants and curves, per component)."""
    anim = lay.root.ptr[0x140].get(anim_name)
    ad = anim.ptr[0]
    curves = ad.ptr[0x18].items
    consts = struct.unpack_from("<%df" % ad.get("<I", 0x40)[0], ad.ptr[0x38].buf) if ad.ptr.get(0x38) else ()
    out = {}
    for tg in ad.ptr[0x8].items:
        k = tg.ptr[0x0]
        c, co, fl, _ = tg.get("<HHHH", 8)
        vals = [curve_value(curves[c + i], t) for i in range(fl & 0xFF)] + [consts[co + i] for i in range(fl >> 8)]
        out[(key_pane(k), k[3], k[0] if k[4] == 1 else None)] = vals
    return out


def pane_rects(lay, hidden=False):
    """{pane name: (x0, y0, x1, y1, visible)} in the layout's own coordinates, from the
    pane records alone (no animation): translate, scale, size, base position (flag bits
    12-13 horizontal: 0 left, 1 centre, 2 right; 14-15 vertical: 0 top, 1 centre, 2
    bottom) and the edge of the parent the position is measured from (bits 18-19
    horizontal, 20-21 vertical, same values). Rotation is ignored. `visible` is false
    under a hidden pane; hidden=True lists those too. An estimate for placing things:
    the anchor bits were read off vanilla data, not confirmed in game."""
    ed = Editor(lay)
    out = {}
    frames = {}      # pane index -> (origin x, origin y, scale x, scale y, rect, visible)
    for i in range(len(ed.panes)):
        v = ed.pane(i)
        fl = v.flags
        bh, bv, ph, pv = (fl >> 12) & 3, (fl >> 14) & 3, (fl >> 18) & 3, (fl >> 20) & 3
        if v.parent < 0:
            ox, oy, sx, sy, vis = 0.0, 0.0, 1.0, 1.0, True
        else:
            px, py, psx, psy, prect, pvis = frames[v.parent]
            ax = {0: prect[0], 1: px, 2: prect[2]}[ph] if ph != 1 else px
            ay = {0: prect[3], 1: py, 2: prect[1]}[pv] if pv != 1 else py
            ox, oy = ax + v.pos[0] * psx, ay + v.pos[1] * psy
            sx, sy = psx * v.scale[0], psy * v.scale[1]
            vis = pvis and v.visible
        w, h = v.size[0] * abs(sx), v.size[1] * abs(sy)
        x0 = {0: ox, 1: ox - w / 2, 2: ox - w}[bh if bh != 3 else 1]
        y1 = {0: oy, 1: oy + h / 2, 2: oy + h}[bv if bv != 3 else 1]
        rect = (x0, y1 - h, x0 + w, y1)
        frames[i] = (ox, oy, sx, sy, rect, vis)
        if vis or hidden:
            out[v.name] = rect + (vis,)
    return out


def part_bounds(lay):
    """Footprint of a part relative to its origin: its hit box B_Hit_00 when it has
    one (the buttons), else the union of its visible pictures and texts, leaving out
    cursor and glow effects."""
    ed = Editor(lay)
    rects = pane_rects(lay, hidden=True)
    if "B_Hit_00" in rects:
        return rects["B_Hit_00"][:4]
    box = None
    for i in range(len(ed.panes)):
        v = ed.pane(i)
        r = rects.get(v.name)
        if r is None or not r[4] or v.type not in (1, 2) or any(w in v.name for w in ("Cursor", "Glow", "Eff")):
            continue
        box = r[:4] if box is None else (min(box[0], r[0]), min(box[1], r[1]), max(box[2], r[2]), max(box[3], r[3]))
    return box or (-100.0, -100.0, 100.0, 100.0)


def set_text(new_pane, ref="*", font_size=None, h_align=None, v_align=None, font=None):
    """Text settings of a cloned text pane (NewPane) or a Text record. ref "*" = the
    message labelled with the pane's path in LayoutMsg/<screen layout>.msbt."""
    rec = new_pane.typed if isinstance(new_pane, NewPane) else new_pane
    assert rec.t == "Text"
    rec.ptr[0x18] = cstr(ref)
    if font is not None:
        rec.ptr[0x0] = cstr(font)
    if font_size is not None:
        rec.set("<2f", 0x08, font_size, font_size)
    if h_align is not None:
        rec.set("<I", 0x70, h_align)
        rec.set("<I", 0x78, h_align)
    if v_align is not None:
        rec.set("<I", 0x74, v_align)


def set_palette(lay, index, rgba):
    """Colour palette entry (root +0x1c8; materials and text records index it)."""
    r = lay.root
    assert index < r.get("<I", 0x1C8 + 8)[0]
    struct.pack_into("<4f", r.ptr[0x1C8].buf, 16 * index, *rgba)


def palette(lay):
    r = lay.root
    n = r.get("<I", 0x1C8 + 8)[0]
    return [struct.unpack_from("<4f", r.ptr[0x1C8].buf, 16 * i) for i in range(n)]


def rename(lay, name):
    lay.root.ptr[0x18] = cstr(name)
