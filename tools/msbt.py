"""MSBT (message studio) reader/writer: labels + text, other sections kept as-is.

This game stores text as UTF-8 (encoding byte 0). Tags are 0x0E + u16 group,
u16 type, u16 size + payload (UTF-16 games use the same layout in code units).
Messages are kept as raw bytes, so a round trip reproduces the file exactly.
"""
import struct


class Msbt:
    def __init__(self, data):
        assert data[:8] == b"MsgStdBn", data[:8]
        self.e = "<" if data[8:10] == b"\xff\xfe" else ">"
        e = self.e
        self.header = bytearray(data[:0x20])
        self.encoding = data[0x0C]  # 0 utf8, 1 utf16, 2 utf32
        self.cw = {0: 1, 1: 2, 2: 4}[self.encoding]
        self.enc = {0: "utf-8", 1: "utf-16-le" if e == "<" else "utf-16-be", 2: "utf-32"}[self.encoding]
        nsec = struct.unpack_from(e + "H", data, 0x0E)[0]
        self.sections = []  # [magic, body bytes]
        o = 0x20
        for _ in range(nsec):
            magic = data[o:o + 4]
            size = struct.unpack_from(e + "I", data, o + 4)[0]
            self.sections.append([magic, data[o + 0x10:o + 0x10 + size]])
            o = (o + 0x10 + size + 15) & ~15
        self.labels = self._labels()
        self.raw = self._texts()

    def _sec(self, magic):
        for m, b in self.sections:
            if m == magic:
                return b
        return None

    def _labels(self):
        b = self._sec(b"LBL1")
        e = self.e
        n = struct.unpack_from(e + "I", b, 0)[0]
        out = {}
        self.buckets = []  # per hash bucket: [(name, index)] in file order
        for i in range(n):
            cnt, o = struct.unpack_from(e + "II", b, 4 + i * 8)
            bucket = []
            for _ in range(cnt):
                ln = b[o]
                name = b[o + 1:o + 1 + ln].decode("ascii")
                idx = struct.unpack_from(e + "I", b, o + 1 + ln)[0]
                out[name] = idx
                bucket.append((name, idx))
                o += 1 + ln + 4
            self.buckets.append(bucket)
        return out

    @staticmethod
    def label_hash(name, buckets):
        h = 0
        for c in name.encode("ascii"):
            h = (h * 0x492 + c) & 0xFFFFFFFF
        return h % buckets

    def add(self, label, text):
        """Add a new message (plain text) under a new label."""
        assert label not in self.labels, label
        idx = len(self.raw)
        self.raw.append(text.encode(self.enc))
        self.labels[label] = idx
        self.buckets[self.label_hash(label, len(self.buckets))].append((label, idx))
        for s in self.sections:
            if s[0] == b"LBL1":
                s[1] = self._lbl1_bytes()

    def _lbl1_bytes(self):
        e, n = self.e, len(self.buckets)
        head = bytearray(struct.pack(e + "I", n))
        body = bytearray()
        o = 4 + 8 * n
        for bucket in self.buckets:
            head += struct.pack(e + "II", len(bucket), o + len(body))
            for name, idx in bucket:
                nb = name.encode("ascii")
                body += bytes([len(nb)]) + nb + struct.pack(e + "I", idx)
        return bytes(head + body)

    def _texts(self):
        b = self._sec(b"TXT2")
        e = self.e
        n = struct.unpack_from(e + "I", b, 0)[0]
        offs = list(struct.unpack_from(e + "%dI" % n, b, 4)) + [len(b)]
        out = []
        for i in range(n):
            raw = bytes(b[offs[i]:offs[i + 1]])
            assert raw[-self.cw:] == b"\0" * self.cw, ("no terminator", i)
            out.append(raw[:-self.cw])
        return out

    def get(self, label):
        """Plain text of a message (tags dropped)."""
        return "".join(p for kind, p in self.parts(label) if kind == "text")

    def set(self, label, text):
        """Replace a message with plain text (no tags)."""
        self.raw[self.labels[label]] = text.encode(self.enc)

    def parts(self, label):
        raw, e, cw = self.raw[self.labels[label]], self.e, self.cw
        out, i, start = [], 0, 0
        while i < len(raw):
            u = int.from_bytes(raw[i:i + cw], "little" if e == "<" else "big")
            if u in (0x0E, 0x0F):
                if i > start:
                    out.append(("text", raw[start:i].decode(self.enc, "replace")))
                if u == 0x0E:
                    grp, typ, sz = struct.unpack_from(e + "HHH", raw, i + cw)
                    out.append(("tag", (grp, typ, raw[i + cw + 6:i + cw + 6 + sz].hex())))
                    i += cw + 6 + sz
                else:
                    grp, typ = struct.unpack_from(e + "HH", raw, i + cw)
                    out.append(("end", (grp, typ)))
                    i += cw + 4
                start = i
            else:
                i += cw
        if start < len(raw):
            out.append(("text", raw[start:].decode(self.enc, "replace")))
        return out

    def pretty(self, label):
        s = []
        for kind, p in self.parts(label):
            s.append(p if kind == "text" else "<%s %r>" % (kind, p))
        return "".join(s)

    def to_bytes(self):
        e, n = self.e, len(self.raw)
        blobs = [t + b"\0" * self.cw for t in self.raw]
        offs, o = [], 4 + 4 * n
        for bl in blobs:
            offs.append(o)
            o += len(bl)
        txt2 = struct.pack(e + "I", n) + struct.pack(e + "%dI" % n, *offs) + b"".join(blobs)
        out = bytearray()
        for magic, body in self.sections:
            if magic == b"TXT2":
                body = txt2
            out += magic + struct.pack(e + "I", len(body)) + b"\0" * 8 + body
            while len(out) % 16:
                out += b"\xab"
        hdr = bytearray(self.header)
        struct.pack_into(e + "I", hdr, 0x12, 0x20 + len(out))
        return bytes(hdr) + bytes(out)
