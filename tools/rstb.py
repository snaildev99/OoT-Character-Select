"""Resource size table (RESTBL v1): read, change entries, write.

Layout: "RESTBL", u32 version, u32 name length (160), u32 crc count,
u32 name count; crc entries (u32 crc32(path), u32 size) sorted by crc;
name entries (160-byte path, u32 size) for paths whose crc collides.
Sizes are what the loader reserves for the decompressed file; for AINB the
game uses align32(size) + 376 (107 of 108 Sequence graphs).
"""
import bisect
import struct
import zlib

PATH = "System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs"


def ainb_size(n):
    return ((n + 31) & ~31) + 376


class Rstb:
    def __init__(self, data):
        assert data[:6] == b"RESTBL"
        self.version, self.name_len, ncrc, nname = struct.unpack_from("<IIII", data, 6)
        o = 0x16
        self.crc = dict(struct.unpack_from("<II", data, o + i * 8) for i in range(ncrc))
        o += ncrc * 8
        self.names = {}
        for _ in range(nname):
            name = data[o:o + self.name_len].split(b"\0")[0].decode()
            self.names[name] = struct.unpack_from("<I", data, o + self.name_len)[0]
            o += self.name_len + 4
        assert o == len(data)

    def get(self, path):
        if path in self.names:
            return self.names[path]
        return self.crc.get(zlib.crc32(path.encode()))

    def set(self, path, size):
        if path in self.names:
            self.names[path] = size
        else:
            self.crc[zlib.crc32(path.encode())] = size

    def ensure(self, path, size):
        """Make the entry at least `size`; returns (old, new)."""
        old = self.get(path)
        if old is None or old < size:
            self.set(path, size)
        return old, self.get(path)

    def to_bytes(self):
        out = bytearray(b"RESTBL")
        out += struct.pack("<IIII", self.version, self.name_len, len(self.crc), len(self.names))
        for h in sorted(self.crc):
            out += struct.pack("<II", h, self.crc[h])
        for name, size in self.names.items():
            out += name.encode().ljust(self.name_len, b"\0") + struct.pack("<I", size)
        return bytes(out)
