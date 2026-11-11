"""Log the menu backdrop actor's scale while snaildev looks at the page (NPC emulator copy).

    venv\\Scripts\\python.exe tools\\npcbackdrop.py

Memory reads only (the debug server of PLAY-debug-npc.bat, 127.0.0.1:6547): nothing is
written, no picture is taken, nothing is pressed. For the test build's --display experiment
(mods\\npc_characters.py: backdrop_scale_hook): whatever the picture shows, this says whether
the hook's numbers arrived. One line whenever something changes, also in
build-npc\\backdrop-log.txt: the stored character, the display type, the backdrop actor's
state and scale (+0x34c), and its model (what the actor's update gets from the part at
[actor +0x270] +8 through that part's virtual function +0xf8; the function is read from the
game's memory and worked out from the image here, nothing is called): the two scale vectors
(+0x228, +0x234; what is drawn is their product) and the "matrix is new" byte (+0x240).
Waits for the game; ends when the game closes.
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))

import re  # noqa: E402

import a64  # noqa: E402
import live  # noqa: E402
import npclive  # noqa: E402

_img = None
_getters = {}


def model_of(lv, part):
    """The model a part's virtual function +0xf8 returns, for the shapes a getter has
    (a member, an embedded object, the part itself); None and a note otherwise."""
    global _img
    fn = lv.q(lv.q(part) + 0xF8) - lv.delta
    if fn not in _getters:
        _img = _img or a64.Image()
        lines = [l.split(":", 1)[1].strip() for l in _img.dis(fn, 3)]
        text = " ; ".join(re.sub(r"\s+", " ", l) for l in lines)
        m = re.match(r"ldr x0, \[x0, #(0x[0-9a-f]+)\] ; ret", text)
        e = re.match(r"add x0, x0, #(0x[0-9a-f]+) ; ret", text)
        _getters[fn] = ("member", int(m.group(1), 16)) if m else ("inside", int(e.group(1), 16)) if e else             ("self", 0) if text.startswith("ret") else ("unknown", text)
        say("the part's getter is %#x: %s" % (fn, text))
    kind, off = _getters[fn]
    return lv.q(part + off) if kind == "member" else part + off if kind in ("inside", "self") else None

LOG = os.path.join(os.path.dirname(HERE), "build-npc", "backdrop-log.txt")
BACKDROP = 0x208


def say(text):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sample(lv):
    try:
        number = lv.gd_int(npclive.FLAG)
        mm = lv.q(lv.q(live.MENU_GLOBAL + lv.delta) + 0x4E8)
        raw = lv.d.read(mm, 0x380)
    except (RuntimeError, struct.error, ZeroDivisionError):
        return "no menu actor manager (title screen or loading)"
    display = struct.unpack_from("<I", raw, 0x228)[0]
    z = struct.unpack_from("<f", raw, 0x234)[0]
    link, hid = struct.unpack_from("<QI", raw, BACKDROP + 0x10)
    text = "character %s | display %d | camera block z %.3f | " % (number, display, z)
    if hid == 0xFFFFFFFF or not link:
        return text + "no backdrop"
    try:
        actor, lid = struct.unpack("<QI", lv.d.read(link + 0x40, 12))
        if lid != hid or not actor:
            return text + "backdrop handle stale"
        state = lv.u(actor + 0x2C)
        flags = struct.unpack("<II", lv.d.read(actor + 0x4A8, 8))
        scale = struct.unpack("<3f", lv.d.read(actor + 0x34C, 12))
        text += "backdrop state %d flags %08x %08x scale %.3f %.3f %.3f | " % ((state,) + flags + scale)
        parts = lv.q(actor + 0x270)
        part = lv.q(parts + 8) if parts else 0
        if not part:
            return text + "no part at +8"
        model = model_of(lv, part)
        if not model:
            return text + "no model"
        a = struct.unpack("<3f", lv.d.read(model + 0x228, 12))
        b = struct.unpack("<3f", lv.d.read(model + 0x234, 12))
        new = lv.d.read(model + 0x240, 1)[0]
        return text + "model scale %.3f %.3f %.3f x %.3f %.3f %.3f, new byte %02x" % (a + b + (new,))
    except (RuntimeError, struct.error):
        return text + "backdrop unreadable"


MENU_SCENES = 0x84eb50a8    # build.MENU_SCENES: [this] scenes, count +0x50, array +0x58 (0x60 each); [1] = the menu's
_scene = [0, 0, 0, 0]       # samples until the next look-up, scene, floats, cascades


def shadow_sample(lv):
    """--shadow: as fast as the server answers (three reads a sample): the display type and
    the menu scene's depth shadow range: flag word +0xd14 (bit value 2 = range overridden),
    near, far. A frame drawn with the wrong range shows as a line between two lines of the
    right one (during a slide the range changes every frame with the zoom)."""
    try:
        if _scene[0] <= 0:
            scenes = lv.q(MENU_SCENES + lv.delta)
            scene = lv.q(lv.q(scenes + 0x58) + 0x60) if scenes and lv.u(scenes + 0x50) > 1 else 0
            shadow = lv.q(scene + 0xD60) if scene else 0
            _scene[:] = [300, scene, lv.q(shadow + 0x330) if shadow else 0,
                         struct.unpack("<i", lv.d.read(shadow + 0x250, 4))[0] if shadow else 0]
            _scene.append(lv.q(lv.q(live.MENU_GLOBAL + lv.delta) + 0x4E8))
            del _scene[5:]
        _scene[0] -= 1
        _n, scene, floats, cascades, mm = _scene
        if not (scene and floats and mm and 0 < cascades < 8):
            _scene[0] = 0
            return "no menu scene"
        raw = lv.d.read(mm + 0x208, 0x28)
        display = struct.unpack_from("<I", raw, 0x20)[0]
        flags = struct.unpack("<H", lv.d.read(scene + 0xD14, 2))[0]
        fl = struct.unpack("<%df" % (cascades + 1), lv.d.read(floats, 4 * cascades + 4))
        return "display %d | shadow flags %04x (%s) near %.3f far %.3f" % (
            display, flags, "overridden" if flags & 2 else "config", fl[0], fl[-1])
    except (RuntimeError, struct.error):
        _scene[0] = 0
        return "unreadable"


def main():
    fast = "--shadow" in sys.argv
    lv = None
    while lv is None:
        try:
            lv = npclive.NpcLive()
        except (OSError, AssertionError, RuntimeError):
            time.sleep(3)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    say("connected" + (" (shadow range, fast)" if fast else ""))
    last, count, since = None, 0, time.time()
    while True:
        try:
            text = shadow_sample(lv) if fast else sample(lv)
        except (OSError, ConnectionError) as e:
            say("the game is gone: %s" % repr(e)[:80])
            return
        count += 1
        if text != last:
            now = time.time()
            say(("%03d ms  " % (now * 1000 % 1000) if fast else "") + text)
            last = text
        if fast and count % 2000 == 0:
            say("(%d samples a second)" % (2000 / max(time.time() - since, 1e-6)))
            since = time.time()
        if not fast:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
