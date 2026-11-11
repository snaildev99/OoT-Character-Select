"""Code patches for the game's main executable, written as an IPS32 file.

The emulator (yuzu/Eden/Lichii) loads mods\\<mod>\\exefs\\<BUILD_ID>.ips and
applies it to [0x100-byte NSO header][main's program image], so a patch at
address A (as in image.bin / a64.Image) lands at file offset 0x100 + (A - MAIN).

    p = Patcher()
    p.asm(0x80a8ab8c, "adrp x8, #0x83e34000; add x8, x8, #0x370",
          expect="adrp x8, #0x83cfd000; add x8, x8, #0xd43")
    p.cave("name", "...asm...")        # code placed in the zero padding after .text
    open(path, "wb").write(p.ips32())

Every patch states the instructions it replaces (`expect`); the build fails if
image.bin differs there.
"""
import struct

import keystone

from a64 import Image

MAIN = 0x80020000           # main's load address in image.bin (text start; +4 holds the MOD0 offset)
BUILD_ID = "E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348"  # game v1.1.1 main, trailing zeros trimmed
# Zero padding after the last PLT stub (text ends 0x83c661d0) up to .rodata (0x83c67000), still
# inside the page-aligned executable text segment. The emulator's built-in "Dynamic FPS by Dark"
# patch puts a routine at main+0x3C46200 (0x83c66200-0x83c66278) and is skipped if a mod touches
# those bytes, so we use the far end. Each mod's part starts with a signature (u32 1337, u32 69420).
CAVE_REGION = (0x83c66800, 0x83c67000)  # what we allow ourselves; split between mods:
CAVE_START = 0x83c66c00     # main+0x3C46C00 .. CAVE_END: Header four tabs (the default part)
CAVE_END = 0x83c67000
CAVE_MODELS = (0x83c66800, 0x83c66c00)  # Character models
CAVE_EXTRA = (0x83c66280, 0x83c66400)   # after the emulator's Dynamic FPS routine: shared routines + the menu delete patches (Character models)
CAVE_EXTRA2 = (0x83c66ea0, 0x83c67000)  # the unused end of Header four tabs' part: the page view's two routines
# The same kind of padding after .rodata (main's NSO header: rodata 0x83c67000 + 0xc05080,
# data from 0x8486d000): zeros in a read-only page, for tables that do not have to sit in
# the code cave, which is nearly full. Patcher.rodata() places them. One mod only.
RODATA_CAVE = (0x8486c100, 0x8486d000)
SIGNATURE = (1337, 69420)
BUILTIN_RANGES = [(0x83c66200, 0x83c66278)]  # Dynamic FPS routine (Fritchii 4.1-5.5)

_ks = keystone.Ks(keystone.KS_ARCH_ARM64, keystone.KS_MODE_LITTLE_ENDIAN)


# Instructions keystone does not know (it predates ARMv8.1): assembled as an unused
# hint and swapped for the real encoding afterwards. Not as ".long": keystone then
# computes every later absolute branch target 4 bytes off per ".long" (seen 2026-11-10:
# "bl #0x80964e70" after two of them went to 0x80964e78).
PSEUDO = {
    "retab": ("hint #0x7d", 0xD65F0FFF),
    "ldset w13, w14, [x12]": ("hint #0x7e", 0xB82D318E),    # atomic [x12] |= w13, w14 = old value
    "ldclr w13, w14, [x12]": ("hint #0x7f", 0xB82D118E),    # atomic [x12] &= ~w13, w14 = old value
}


def assemble(text, addr):
    swaps = {}
    for name, (hint, word) in PSEUDO.items():
        if name in text:
            text = text.replace(name, hint)
            swaps[bytes(_ks.asm(hint, 0)[0])] = struct.pack("<I", word)
    enc, _ = _ks.asm(text, addr)
    enc = bytearray(enc)
    for i in range(0, len(enc), 4):
        if bytes(enc[i:i + 4]) in swaps:
            enc[i:i + 4] = swaps[bytes(enc[i:i + 4])]
    return bytes(enc)


class Patcher:
    def __init__(self, image=None, cave=(CAVE_START, CAVE_END)):
        """`cave`: this patch set's part of the padding. Mods that ship their own
        .ips must use parts that do not overlap (the emulator applies all)."""
        self.img = image or Image()
        self.patches = []  # (addr, bytes)
        self.data_at = set()  # addresses of data() blobs (listed as bytes, not code)
        self.cave_start, self.cave_end = cave
        assert CAVE_REGION[0] <= self.cave_start < self.cave_end <= CAVE_REGION[1], cave
        self.symbols = {}
        self.raw(self.cave_start, struct.pack("<II", *SIGNATURE))
        self.cave_ptr = self.cave_start + 8
        self.rodata_ptr = RODATA_CAVE[0]

    def _check_free(self, addr, n):
        for a, b in self.patches:
            assert addr + n <= a or a + len(b) <= addr, "overlapping patches at %x" % addr
        for lo, hi in BUILTIN_RANGES:
            assert addr + n <= lo or hi <= addr, "patch at %x overlaps an emulator built-in patch" % addr

    def raw(self, addr, data, expect=None):
        if expect is not None:
            exp = assemble(expect, addr) if isinstance(expect, str) else expect
            have = self.img.data[addr - self.img.base:addr - self.img.base + len(exp)]
            assert have == exp, "unexpected code at %x: %s, expected %s" % (addr, have.hex(), exp.hex())
        self._check_free(addr, len(data))
        self.patches.append((addr, bytes(data)))

    def asm(self, addr, text, expect):
        code = assemble(text.format(**self.symbols), addr)
        self.raw(addr, code, expect)
        return code

    def cave(self, name, text):
        """Place code in the padding after .text; returns its address."""
        addr = (self.cave_ptr + 15) & ~15
        return self._place(name, addr, assemble(text.format(**self.symbols), addr))

    def data(self, name, blob):
        """Place read-only data (strings, tables) in the cave; returns its address."""
        addr = self._place(name, (self.cave_ptr + 15) & ~15, bytes(blob))
        self.data_at.add(addr)
        return addr

    def rodata(self, name, blob):
        """Place read-only data in the padding after .rodata; returns its address."""
        addr, blob = (self.rodata_ptr + 15) & ~15, bytes(blob)
        assert addr + len(blob) <= RODATA_CAVE[1], "rodata padding full"
        have = self.img.data[addr - self.img.base:addr - self.img.base + len(blob)]
        assert have == b"\0" * len(blob), "rodata padding not empty at %x" % addr
        self.raw(addr, blob)
        self.symbols[name] = addr
        self.rodata_ptr = addr + len(blob)
        self.data_at.add(addr)
        return addr

    def _place(self, name, addr, blob):
        assert addr + len(blob) <= self.cave_end, "code cave full"
        have = self.img.data[addr - self.img.base:addr - self.img.base + len(blob)]
        assert have == b"\0" * len(blob), "cave not empty at %x" % addr
        self.raw(addr, blob)
        self.symbols[name] = addr
        self.cave_ptr = addr + len(blob)
        return addr

    def extra(self, area=None):
        """`with p.extra():` places caves / data in CAVE_EXTRA (or another spare
        `area`) instead of this patch's own part."""
        patcher = self
        area = area or CAVE_EXTRA
        if not hasattr(self, "extra_ptr"):
            self.extra_ptr = {}

        class Area:
            def __enter__(self):
                self.keep = (patcher.cave_ptr, patcher.cave_end)
                patcher.cave_ptr = patcher.extra_ptr.get(area, area[0])
                patcher.cave_end = area[1]

            def __exit__(self, *exc):
                patcher.extra_ptr[area] = patcher.cave_ptr
                patcher.cave_ptr, patcher.cave_end = self.keep
        return Area()

    def ranges(self):
        return [(a, a + len(b)) for a, b in self.patches]

    def ips32(self):
        out = bytearray(b"IPS32")
        for addr, data in sorted(self.patches):
            off = 0x100 + addr - MAIN
            for i in range(0, len(data), 0xFFFF):
                chunk = data[i:i + 0xFFFF]
                out += struct.pack(">IH", off + i, len(chunk)) + chunk
        out += b"EEOF"
        return bytes(out)

    def listing(self):
        lines = []
        for addr, data in sorted(self.patches):
            if addr in self.data_at or addr == self.cave_start:
                for i in range(0, len(data), 16):
                    chunk = data[i:i + 16]
                    lines.append("%x: data     %s  %s" % (addr + i, chunk.hex(" "), "".join(
                        chr(c) if 32 <= c < 127 else "." for c in chunk)))
            else:
                for ins in self.img.cs.disasm(data, addr):
                    lines.append("%x: %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
            lines.append("")
        return "\n".join(lines)
