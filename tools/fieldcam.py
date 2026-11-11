"""Record the player camera's memory while snaildev does a field swap (field swap session).

    venv\\Scripts\\python.exe tools\\fieldcam.py [seconds]      wait for the game, record (default 900 s)
    venv\\Scripts\\python.exe tools\\fieldcam.py --read         summary of the last recording

Memory reads only through the debug server of the field swap emulator copy (tools\\dbg.py, port
6549); never screenshots, never input. As fast as the server answers it reads the camera system
object, the PlayerCamera actor's pose, its camera component (0x1200 bytes; the reset request
flags live at +0x5b8+0x7b0...), the view it outputs, our routines' counters (to mark the swap's
steps) and, once a swap has run, the player's state and pose. Every sample that differs from
the one before goes to build-field\\cam-trace.pkl (a stream of pickled dicts); what happened in
words goes to build-field\\cam-trace.txt. Don't run tools\\fieldlive.py at the same time.
"""
import os
import pickle
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fieldlive  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "build-field", "cam-trace.pkl")
TXT = os.path.join(os.path.dirname(HERE), "build-field", "cam-trace.txt")
CAMERA_SYSTEM = 0x84e40c08      # global: the object the camera message nodes use (0x80bb1190)
CAMERA_SYSTEM_GOT = 0x84c9e818  # the same through the table the "save camera" node uses
CAM_SIZE, SYS_SIZE, VIEW_SIZE = 0x1200, 0x260, 0xD0
LIMIT_BYTES = 400 << 20


def note(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    with open(TXT, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def linked_actor(lv, at):
    """The actor behind a handle stored as (.., link pointer at +8, id at +0x10), or None."""
    link, hid = struct.unpack("<QI", lv.d.read(at + 8, 12))
    if not link or hid == 0xFFFFFFFF:
        return None
    actor, lid = struct.unpack("<QI", lv.d.read(link + 0x40, 12))
    return actor if actor and lid == hid else None


def player_of(lv, mgr):
    head = lv.d.read(mgr + 0x28, 0x50)
    is_child = head[0x48]
    count, ptr = struct.unpack_from("<I4xQ", head, 0x10 if is_child else 0x20)
    if not count or not ptr:
        return None
    return linked_actor(lv, ptr)


def sample(lv):
    s = {"t": time.time()}
    s["stash"] = lv.d.read(lv.stash_at, fieldlive.STASH_SIZE)
    mgr = struct.unpack_from("<Q", s["stash"], 0x38)[0]
    sysobj = lv.q(CAMERA_SYSTEM + lv.delta)
    s["sys_at"] = sysobj
    if not sysobj:
        return s
    s["sys"] = lv.d.read(sysobj, SYS_SIZE)
    actor = linked_actor(lv, sysobj + 0x38)
    s["actor_at"] = actor
    if actor:
        s["actor"] = lv.d.read(actor + 0x2C, 4) + lv.d.read(actor + 0x2EC, 0x6C)
        comp = lv.q(actor + 0x270)
        cam = lv.q(comp + 0x360) if comp else 0
        s["cam_at"] = cam
        if cam:
            s["cam"] = lv.d.read(cam, CAM_SIZE)
            view = struct.unpack_from("<Q", s["cam"], 0x38)[0]
            s["view_at"] = view
            if view:
                s["view"] = lv.d.read(view, VIEW_SIZE)
    if mgr:
        player = player_of(lv, mgr)
        s["player_at"] = player
        if player:
            s["player"] = lv.d.read(player + 0x2C, 4) + lv.d.read(player + 0x2EC, 0x6C)
            comps = lv.q(player + 0x270)        # the player's camera part (PlayerCameraTarget component)
            target = lv.q(comps + 0x368) if comps else 0
            s["target_at"] = target
            if target:
                s["target"] = lv.d.read(target + 0x240, 0x10)       # +0x24c: the flags the camera reads
    return s


def record(seconds):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    lv, waited = None, time.time()
    while lv is None:
        try:
            lv = fieldlive.FieldLive()
        except (OSError, AssertionError, RuntimeError):
            if time.time() - waited > 8 * 3600:
                return
            time.sleep(3)
    open(TXT, "w").close()
    note("connected (main shifted by %#x); code patch %s" % (lv.delta, "in place" if lv.patched else "NOT there"))
    try:
        a, b = lv.q(CAMERA_SYSTEM + lv.delta), lv.q(lv.q(CAMERA_SYSTEM_GOT + lv.delta))
        note("camera system object %#x (through the other table %#x)" % (a, b))
    except Exception as e:  # noqa: BLE001
        note("camera system not readable yet: %r" % (e,))
    last, last_marks, n, kept, size, errors = None, None, 0, 0, 0, 0
    start = time.time()
    with open(OUT, "wb") as f:
        while time.time() - start < seconds and size < LIMIT_BYTES:
            try:
                s = sample(lv)
            except (OSError, ConnectionError) as e:
                note("the game is gone: %s" % repr(e)[:80])
                break
            except Exception as e:  # noqa: BLE001      a pointer that was not there yet
                errors += 1
                if errors in (1, 10, 100, 1000):
                    note("read failed (%d so far): %s" % (errors, repr(e)[:100]))
                time.sleep(0.05)
                continue
            n += 1
            key = {k: v for k, v in s.items() if k != "t"}
            if key != last:
                blob = pickle.dumps(s)
                f.write(blob)
                size += len(blob)
                kept += 1
                last = key
            valid, deletes = struct.unpack_from("<II", s["stash"], 0)
            wakes, swaps = struct.unpack_from("<II", s["stash"], 0x48)
            pst = struct.unpack_from("<I", s["player"], 0)[0] if s.get("player") else None
            marks = (valid, wakes, swaps, bool(s.get("actor_at")), bool(s.get("cam_at")), s.get("player_at"), pst)
            if marks != last_marks:
                note("sample %d: place remembered %d, wake calls %d, swaps done %d, camera actor %s, component %s, "
                     "player %s state %s (fs_delete calls %d)" % (
                         n, valid, wakes, swaps, "found" if marks[3] else "none", "found" if marks[4] else "none",
                         "%#x" % marks[5] if marks[5] else "unknown", pst, deletes))
                last_marks = marks
    note("done: %d samples in %.0f s, %d kept (%.1f MB), %d failed reads" % (n, time.time() - start, kept, size / 1e6, errors))


def load():
    out = []
    with open(OUT, "rb") as f:
        while True:
            try:
                out.append(pickle.load(f))
            except EOFError:
                return out


if __name__ == "__main__":
    if "--read" in sys.argv:
        ss = load()
        print(len(ss), "samples kept;", "%.1f s" % (ss[-1]["t"] - ss[0]["t"]) if ss else "")
    else:
        record(float(sys.argv[1]) if len(sys.argv) > 1 else 900.0)
