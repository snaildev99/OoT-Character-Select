"""Partial reader for UI layout files (.buil, "BFFH" / Nintendo.Ui.ResModels.LayoutResData).

Only what in-place edits need so far: animations, their targets and their
constant values. Pointers are absolute u64 file offsets; strings live in the
"StringPool" section.

Animation record (at the u64 that points to its name):
  +0x00 name ptr
  +0x08 targets ptr, +0x10 u32 count (u64 each -> target record)
  +0x18 curves ptr,  +0x20 u32 count
  +0x28 key bytes ptr, +0x30 u32 count
  +0x38 constants ptr, +0x40 u32 count (f32 each)
Target record: +0x00 ptr to a 6-byte key (byte 1 >> 3 = pane index, byte 3 =
property group: 1 = transform, 2 = visibility, ...), then u16 curve index
(0xffff = none), u16 constant index (0xffff = none), u16 flags, u16 property
(transform: 0xe = translate X).
"""
import re
import struct


class Buil:
    def __init__(self, data):
        assert data[:4] == b"BFFH"
        self.d = bytes(data)

    def cstr(self, o):
        return self.d[o:self.d.index(b"\0", o)].decode("latin-1")

    def u64(self, o):
        return struct.unpack_from("<Q", self.d, o)[0]

    def string_offset(self, s):
        hits = [m.start() + 1 for m in re.finditer(b"\0" + re.escape(s.encode()) + b"\0", self.d)]
        assert len(hits) == 1, (s, hits)
        return hits[0]

    def anim(self, name):
        """(record offset, targets, constants offset, constants)"""
        so = self.string_offset(name)
        refs = [m.start() for m in re.finditer(re.escape(struct.pack("<Q", so)), self.d) if m.start() % 8 == 0]
        recs = []
        for r in refs:
            tp, tn = self.u64(r + 8), struct.unpack_from("<I", self.d, r + 0x10)[0]
            vp, vn = self.u64(r + 0x38), struct.unpack_from("<I", self.d, r + 0x40)[0]
            if 0 < tp < len(self.d) and 0 < tn < 500 and vn < 500 and (vn == 0 or 0 < vp < len(self.d)):
                recs.append((r, tp, tn, vp, vn))
        assert len(recs) == 1, (name, recs)
        r, tp, tn, vp, vn = recs[0]
        targets = []
        for i in range(tn):
            p = self.u64(tp + i * 8)
            key = self.d[self.u64(p):self.u64(p) + 6]
            curve, const, flags, prop = struct.unpack_from("<HHHH", self.d, p + 8)
            targets.append({"pane": key[1] >> 3, "group": key[3], "prop": prop,
                            "curve": None if curve == 0xFFFF else curve,
                            "const": None if const == 0xFFFF else const})
        consts = list(struct.unpack_from("<%df" % vn, self.d, vp)) if vn else []
        return r, targets, vp, consts
