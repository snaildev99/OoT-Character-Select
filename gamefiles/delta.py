"""Byte patches of a file against the player's own copy of the game's files.

A patch rebuilds one output file from one or more source files (decompressed, laid end to
end as S). It is a list of steps, each "literal, then add":
    out += literal bytes                         (bytes that are ours: new names, new bones)
    out += (S[src:src + n] + diff) mod 256       (the game's bytes, with our changes as a
                                                  difference: zero where nothing changed)
So a patch holds no run of the game's bytes; without the player's copy of the source it
cannot make the file. `encode` runs on our side only (numpy); `decode` runs on the
player's (numpy). The serialised patch is three streams (steps as varints, the difference,
the literals) and is compressed by the caller.
"""
import hashlib
import struct

import numpy as np

MAGIC = b"OCSD1"
W = 16                      # a match is found from one aligned 16-byte block of S
K1 = np.uint64(0x9E3779B97F4A7C15)
K2 = np.uint64(0xC2B2AE3D27D4EB4F)
LOOK = 1 << 16              # how far a diagonal is scored at a time
PENALTY = 3                 # a changed byte costs three equal ones: an add run is at least 75 % the game's
                            # own bytes (else its difference would be the content itself)


def _u64_all(b):
    """A[p] = the 8 bytes at p as a little-endian number, for every p."""
    m = len(b) - 7
    if m <= 0:
        return np.zeros(0, np.uint64)
    a = np.empty(m, np.uint64)
    for r in range(8):
        cnt = (len(b) - r) // 8
        a[r::8] = np.frombuffer(b, "<u8", count=cnt, offset=r)
    return a


def _hashes(b):
    a = _u64_all(b)
    if len(a) <= 8:
        return np.zeros(0, np.uint64)
    with np.errstate(over="ignore"):
        return (a[:-8] * K1) ^ (a[8:] * K2)


def _varint(n, out):
    while True:
        x = n & 0x7F
        n >>= 7
        if n:
            out.append(x | 0x80)
        else:
            out.append(x)
            return


def _zig(n):
    return (n << 1) ^ (n >> 63) if n >= 0 else ((-n) << 1) - 1


def _unzig(n):
    return (n >> 1) if not n & 1 else -((n + 1) >> 1)


def encode(out, src):
    """The patch that makes `out` from `src` (bytes). -> (patch bytes, stats)"""
    O = np.frombuffer(out, np.uint8)
    S = np.frombuffer(src, np.uint8)
    steps = []                      # (literal start, literal end, src, add length)
    if len(src) >= 64 and len(out) >= 64:
        hs = _hashes(src)[::W]
        order = np.argsort(hs, kind="stable")
        hs_sorted = hs[order]
        pos_sorted = order.astype(np.int64) * W
        ho = _hashes(out)
        k = np.searchsorted(hs_sorted, ho)
        k[k >= len(hs_sorted)] = len(hs_sorted) - 1
        cand = np.nonzero(hs_sorted[k] == ho)[0]
        cand_k = k[cand]
    else:
        cand = np.zeros(0, np.int64)
    i = 0                           # out[:i] is covered
    ci = 0
    n_out, n_src = len(out), len(src)
    while True:
        ci = int(np.searchsorted(cand, i, "left")) if ci < len(cand) and cand[ci] < i else ci
        if ci >= len(cand):
            break
        c = int(cand[ci])
        s = int(pos_sorted[cand_k[ci]])
        ci += 1
        if out[c:c + W] != src[s:s + W]:
            continue
        while c > i and s > 0 and out[c - 1] == src[s - 1]:     # back while equal
            c -= 1
            s -= 1
        # forward along the diagonal: the length with the most (equal - different) bytes
        end = c
        base = 0
        best, best_len = 0, 0
        while True:
            n = min(LOOK, n_out - end, n_src - (s + (end - c)))
            if n <= 0:
                break
            eq = O[end:end + n] == S[s + (end - c):s + (end - c) + n]
            score = base + np.cumsum(np.where(eq, 1, -PENALTY).astype(np.int64))
            j = int(np.argmax(score))
            if score[j] > best:
                best, best_len = int(score[j]), end - c + j + 1
            base = int(score[-1])
            end += n
            if best_len < end - c - LOOK // 16:     # the best end is well behind: stop
                break
        if best_len < W:
            continue
        steps.append((i, c, s, best_len))
        i = c + best_len
    steps.append((i, n_out, 0, 0))
    ctrl, diff, lit = bytearray(), [], []
    last = 0
    add_bytes = changed = 0
    for a, b, s, n in steps:
        lit.append(out[a:b])
        _varint(b - a, ctrl)
        _varint(n, ctrl)
        if n:
            _varint(_zig(s - last), ctrl)
            last = s + n
            d = (O[b:b + n] - S[s:s + n]).astype(np.uint8)
            diff.append(d.tobytes())
            add_bytes += n
            changed += int(np.count_nonzero(d))
    lit_b, diff_b = b"".join(lit), b"".join(diff)
    head = MAGIC + struct.pack("<QQQQ", n_out, len(ctrl), len(diff_b), len(lit_b)) + hashlib.sha1(out).digest()
    stats = {"out": n_out, "literal": len(lit_b), "added": add_bytes, "changed": changed, "steps": len(steps)}
    return head + bytes(ctrl) + diff_b + lit_b, stats


def decode(patch, src):
    """The file the patch makes from `src`; raises if the result is not the one patched."""
    assert patch[:5] == MAGIC, "not a patch"
    n_out, n_ctrl, n_diff, n_lit = struct.unpack_from("<QQQQ", patch, 5)
    want = patch[37:57]
    p = 57
    ctrl = patch[p:p + n_ctrl]
    p += n_ctrl
    D = np.frombuffer(patch, np.uint8, count=n_diff, offset=p)
    p += n_diff
    lit = patch[p:p + n_lit]
    S = np.frombuffer(src, np.uint8)
    out = np.empty(n_out, np.uint8)
    q = o = d = li = last = 0

    def rd():
        nonlocal q
        v = sh = 0
        while True:
            x = ctrl[q]
            q += 1
            v |= (x & 0x7F) << sh
            sh += 7
            if not x & 0x80:
                return v
    while o < n_out or q < len(ctrl):
        ln = rd()
        n = rd()
        out[o:o + ln] = np.frombuffer(lit, np.uint8, count=ln, offset=li)
        o += ln
        li += ln
        if n:
            s = last + _unzig(rd())
            out[o:o + n] = S[s:s + n] + D[d:d + n]
            o += n
            d += n
            last = s + n
    data = out.tobytes()
    if hashlib.sha1(data).digest() != want:
        raise ValueError("the patched file is not the one we made (a different game version?)")
    return data
