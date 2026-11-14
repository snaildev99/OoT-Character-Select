"""Change the label test's text in a RUNNING game (snaildev's word, 2026-11-14: a memory hack
from outside the game, no emulator change).

The label routine (mods\\label_test.py) reads its text and length from a buffer in the
game's memory that starts with label_test.MARKER. The emulator keeps the game's memory
inside its own Windows process, so this script opens lichii.exe, finds the marker in that
process's memory (ReadProcessMemory) and writes the new length and text after it
(WriteProcessMemory), as a memory editor would. The page shows it the next time it opens
(the routine runs as the page opens; the text box lays its letters out only then).

    venv\\Scripts\\python.exe -I tools\\labelset.py "hello world!!!"
    venv\\Scripts\\python.exe -I tools\\labelset.py --show

With the debug server running (PLAY-debug.bat) the write is also read back at the game's
own address of the buffer, as a check that the copy written is the one the game reads.
"""
import ctypes
import ctypes.wintypes as W
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))

import label_test as L  # noqa: E402

PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION, PROCESS_QUERY_INFORMATION = 0x10, 0x20, 0x8, 0x400
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}         # PAGE_READONLY .. PAGE_EXECUTE_WRITECOPY
WRITABLE = {0x04, 0x08, 0x40, 0x80}
PAGE_GUARD, PAGE_NOACCESS = 0x100, 0x01
CHUNK = 64 << 20


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", W.DWORD),
                ("PartitionId", W.WORD), ("RegionSize", ctypes.c_size_t), ("State", W.DWORD), ("Protect", W.DWORD),
                ("Type", W.DWORD)]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = W.HANDLE
k32.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualProtectEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_size_t, W.DWORD, ctypes.POINTER(W.DWORD)]


def lichii_pid():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq lichii.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    pids = [int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"lichii.exe"')]
    assert len(pids) == 1, "want one lichii.exe running, found %d" % len(pids)
    return pids[0]


def read(h, addr, n):
    buf = ctypes.create_string_buffer(n)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)):
        return b""
    return buf.raw[:got.value]


def find(h):
    """Every place in the process where the buffer is: the marker, a sane length and a 0
    after the text. -> [(host address, protection)]"""
    hits, addr, mbi = [], 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Protect & 0xFF in READABLE and not mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS):
            for at in range(base, base + size, CHUNK):
                data = read(h, at, min(CHUNK + 256, base + size - at))
                i = data.find(L.MARKER)
                while i >= 0:
                    if i + L.BUF_END - L.BUF <= len(data):
                        n = struct.unpack_from("<I", data, i + 16)[0]
                        if n <= L.TEXT_ROOM and data[i + L.TEXT_AT + n] == 0:
                            hits.append((at + i, mbi.Protect))
                    i = data.find(L.MARKER, i + 1)
        addr = base + size
    return sorted(set(hits))


def write(h, addr, prot, blob):
    old = W.DWORD()
    changed = prot & 0xFF not in WRITABLE and k32.VirtualProtectEx(h, ctypes.c_void_p(addr), len(blob), 0x04, ctypes.byref(old))
    done = ctypes.c_size_t()
    ok = k32.WriteProcessMemory(h, ctypes.c_void_p(addr), blob, len(blob), ctypes.byref(done))
    if changed:
        k32.VirtualProtectEx(h, ctypes.c_void_p(addr), len(blob), old.value, ctypes.byref(W.DWORD()))
    return ok and done.value == len(blob)


def guest_check(expect):
    """Read the buffer at the game's own address through the debug server, if it runs."""
    try:
        from dbg import Debug
        from exepatch import Image
        d = Debug(timeout=150)
    except Exception as e:                  # noqa: BLE001
        return "debug server not reachable (%s): not checked at the game's address" % e.__class__.__name__
    img = Image()
    probe = 0x80C38510
    hits = d.search(img.data[probe - img.base:probe - img.base + 32], max_hits=4)
    if len(hits) != 1:
        return "game's base not found: not checked"
    at = L.BUF + hits[0] - probe
    got = d.read(at, L.BUF_END - L.BUF)
    return ("the game reads it: %r at %#x" % (got[L.TEXT_AT:L.TEXT_AT + struct.unpack_from("<I", got, 16)[0]].decode("utf-8"), at)
            if got == expect else "MISMATCH at the game's address %#x: %r" % (at, got[:48]))


def main():
    args = sys.argv[1:]
    assert len(args) == 1, __doc__
    pid = lichii_pid()
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION, False, pid)
    assert h, "cannot open lichii.exe (error %d)" % ctypes.get_last_error()
    hits = find(h)
    print("lichii.exe %d: the buffer found at %d place(s): %s" % (pid, len(hits), ", ".join("%#x" % a for a, _ in hits)))
    assert hits, "not found: is the label test installed and the game started?"
    for a, _ in hits:
        n = struct.unpack("<I", read(h, a + 16, 4))[0]
        print("  %#x: %r" % (a, read(h, a + L.TEXT_AT, n).decode("utf-8", "replace")))
    if args[0] == "--show":
        return
    blob = L.buffer(args[0])
    for a, prot in hits:
        print("  %#x: %s" % (a, "written" if write(h, a, prot, blob) else "WRITE FAILED (error %d)" % ctypes.get_last_error()))
    print("  " + guest_check(blob))
    print("Open the Characters page again (leave it and come back) to see it.")


if __name__ == "__main__":
    main()
