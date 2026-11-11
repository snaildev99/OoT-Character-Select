"""Client for the emulator's debug server (Lichii/Fritchii, EDEN_NX2_DEBUG_SERVER=127.0.0.1:6545).

One JSON line per request: {"id", "method", "params"} -> {"id", "result"} or {"error"}.
Memory only (status, read_memory, search_memory); no screenshots.

    venv\\Scripts\\python.exe tools\\dbg.py status
    venv\\Scripts\\python.exe tools\\dbg.py search 0000803f00000444 --all
    venv\\Scripts\\python.exe tools\\dbg.py read 0x8012345000 64 --as f32
"""
import json
import socket
import struct
import sys

ADDR = ("127.0.0.1", 6545)


class Debug:
    def __init__(self, addr=ADDR, timeout=60):
        self.sock = socket.create_connection(addr, timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        self.next_id = 1

    def call(self, method, **params):
        rid = self.next_id
        self.next_id += 1
        self.sock.sendall(json.dumps({"id": rid, "method": method, "params": params}).encode() + b"\n")
        while b"\n" not in self.buf:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("debug server closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        resp = json.loads(line)
        if resp.get("error"):
            raise RuntimeError(resp["error"])
        return resp.get("result")

    def search(self, data, regions="all", max_hits=64, start=None, end=None):
        """All hits of a byte pattern, following 'next' until the scan is done."""
        hits, nxt = [], start
        while True:
            params = {"pattern": data.hex(), "regions": regions, "max_hits": max_hits, "budget_ms": 10000}
            if nxt is not None:
                params["start"] = nxt
            if end is not None:
                params["end"] = end
            r = self.call("search_memory", **params)
            hits += [int(h, 16) if isinstance(h, str) else h for h in r.get("hits", [])]
            nxt = r.get("next")
            if not nxt:
                return hits

    def read(self, addr, size):
        out = b""
        while size > 0:
            n = min(size, 65536)
            r = self.call("read_memory", address=hex(addr), size=n, **{"as": "hex"})
            out += bytes.fromhex(r["hex"])
            addr += n
            size -= n
        return out


def f32s(*vals):
    return struct.pack("<%df" % len(vals), *vals)


if __name__ == "__main__":
    d = Debug()
    cmd = sys.argv[1]
    if cmd == "status":
        print(json.dumps(d.call("status"), indent=1))
    elif cmd == "search":
        print([hex(h) for h in d.search(bytes.fromhex(sys.argv[2]), "all" if "--all" in sys.argv else "heap")])
    elif cmd == "read":
        print(d.read(int(sys.argv[2], 16), int(sys.argv[3])).hex(" "))
