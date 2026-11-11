"""CityHash64 (v1.1), pure Python. The UI layout files (.buil) key their name
dictionaries with CityHash64 of the UTF-8 name (e.g. "RootPane" -> 0x55a7ab4a9d264968)."""
import struct

M = (1 << 64) - 1
K0 = 0xC3A5C85C97CB3127
K1 = 0xB492B66FBE98F273
K2 = 0x9AE16A3B2F90404F
KMUL = 0x9DDFEA08EB382D69


def _f64(s, i):
    return struct.unpack_from("<Q", s, i)[0]


def _f32(s, i):
    return struct.unpack_from("<I", s, i)[0]


def _rot(v, r):
    return v if r == 0 else ((v >> r) | (v << (64 - r))) & M


def _smix(v):
    return v ^ (v >> 47)


def _bswap(v):
    return int.from_bytes(v.to_bytes(8, "little"), "big")


def _h16(u, v, mul=KMUL):
    a = ((u ^ v) * mul) & M
    a ^= a >> 47
    b = ((v ^ a) * mul) & M
    b ^= b >> 47
    return (b * mul) & M


def _len0to16(s):
    n = len(s)
    if n >= 8:
        mul = (K2 + n * 2) & M
        a = (_f64(s, 0) + K2) & M
        b = _f64(s, n - 8)
        c = (_rot(b, 37) * mul + a) & M
        d = ((_rot(a, 25) + b) * mul) & M
        return _h16(c, d, mul)
    if n >= 4:
        mul = (K2 + n * 2) & M
        a = _f32(s, 0)
        return _h16((n + (a << 3)) & M, _f32(s, n - 4), mul)
    if n > 0:
        a, b, c = s[0], s[n >> 1], s[n - 1]
        y = a + (b << 8)
        z = n + (c << 2)
        return (_smix(((y * K2) ^ (z * K0)) & M) * K2) & M
    return K2


def _len17to32(s):
    n = len(s)
    mul = (K2 + n * 2) & M
    a = (_f64(s, 0) * K1) & M
    b = _f64(s, 8)
    c = (_f64(s, n - 8) * mul) & M
    d = (_f64(s, n - 16) * K2) & M
    return _h16((_rot((a + b) & M, 43) + _rot(c, 30) + d) & M, (a + _rot((b + K2) & M, 18) + c) & M, mul)


def _len33to64(s):
    n = len(s)
    mul = (K2 + n * 2) & M
    a = (_f64(s, 0) * K2) & M
    b = _f64(s, 8)
    c = _f64(s, n - 24)
    d = _f64(s, n - 32)
    e = (_f64(s, 16) * K2) & M
    f = (_f64(s, 24) * 9) & M
    g = _f64(s, n - 8)
    h = (_f64(s, n - 16) * mul) & M
    u = (_rot((a + g) & M, 43) + (_rot(b, 30) + c) * 9) & M
    v = (((a + g) & M) ^ d) + f + 1 & M
    w = (_bswap(((u + v) * mul) & M) + h) & M
    x = (_rot((e + f) & M, 42) + c) & M
    y = ((_bswap(((v + w) * mul) & M) + g) * mul) & M
    z = (e + f + c) & M
    a = (_bswap(((x + z) * mul + y) & M) + b) & M
    b = (_smix(((z + a) * mul + d + h) & M) * mul) & M
    return (b + x) & M


def _weak(s, i, a, b):
    w, x, y, z = _f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24)
    a = (a + w) & M
    b = _rot((b + a + z) & M, 21)
    c = a
    a = (a + x + y) & M
    b = (b + _rot(a, 44)) & M
    return (a + z) & M, (b + c) & M


def city_hash64(s):
    if isinstance(s, str):
        s = s.encode()
    n = len(s)
    if n <= 16:
        return _len0to16(s)
    if n <= 32:
        return _len17to32(s)
    if n <= 64:
        return _len33to64(s)
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & M
    z = _h16((_f64(s, n - 48) + n) & M, _f64(s, n - 24))
    v = _weak(s, n - 64, n, z)
    w = _weak(s, n - 32, (y + K1) & M, x)
    x = (x * K1 + _f64(s, 0)) & M
    left = (n - 1) & ~63
    i = 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, i + 8)) & M, 37) * K1) & M
        y = (_rot((y + v[1] + _f64(s, i + 48)) & M, 42) * K1) & M
        x ^= w[1]
        y = (y + v[0] + _f64(s, i + 40)) & M
        z = (_rot((z + w[0]) & M, 33) * K1) & M
        v = _weak(s, i, (v[1] * K1) & M, (x + w[0]) & M)
        w = _weak(s, i + 32, (z + w[1]) & M, (y + _f64(s, i + 16)) & M)
        z, x = x, z
        i += 64
        left -= 64
        if left == 0:
            break
    return _h16((_h16(v[0], w[0]) + _smix(y) * K1 + z) & M, (_h16(v[1], w[1]) + x) & M)
