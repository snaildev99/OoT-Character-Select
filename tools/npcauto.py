"""Smoke tests without a person: an isolated emulator copy driven through the debug server.

NOT IN USE: snaildev tests in the game himself (asked 2026-11-09, his answer: "No, I'll test it
myself"). Nothing here has ever been run against a game: the copy was set up once and removed
again. Do not run it, or start any emulator, unless snaildev asks for exactly that.

    venv\\Scripts\\python.exe tools\\npcauto.py setup            create / repair the copy (safe to rerun)
    venv\\Scripts\\python.exe tools\\npcauto.py ring [first [count]]   swap through the character ring on the
                                                              Characters page, checking after every swap
    venv\\Scripts\\python.exe tools\\npcauto.py field <number> ...     boot a save with that character stored and
                                                              run, roll, swing and guard as the field Link
    venv\\Scripts\\python.exe tools\\npcauto.py stop             close this copy's emulator

The debug server of Fritchii (EDEN_NX2_DEBUG_SERVER) can also press buttons: method
"input_sequence", steps {"buttons": [...], "left_stick": [x, y], "frames": n} on player 1,
counted in presented frames (buttons A B X Y L R ZL ZR Plus Minus Up Down Left Right StickL
StickR). Together with memory reads that is enough to walk the menus blind: this tool never
takes screenshots or frame captures. What it can tell: the game keeps running, the menu's 3D
character is re-created, what the log says. What it cannot: how anything looks.

The copy (C:\\Repos\\Lichii\\dist\\Fritchii-npc-auto) shares nothing a person's emulator
writes: its own saves (a copy of %APPDATA%\\eden\\nand, taken at setup), its own empty shader
cache, its own log; sound off, no physical controller, window minimized, debug server on
127.0.0.1:6548. Mods: the NPC emulator copy's folder (tools\\npcenv.py), read only.
It never starts while another lichii.exe runs, and closes itself when one appears, unless
called with --alongside (snaildev said it may share the machine with what he is running).
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "mods"))

import dbg  # noqa: E402
import gamedata  # noqa: E402
import live  # noqa: E402
import npcenv  # noqa: E402
from a64 import Image  # noqa: E402

SRC = npcenv.SRC
DST = r"C:\Repos\Lichii\dist\Fritchii-npc-auto"
EDEN = npcenv.EDEN
USER = os.path.join(DST, "user")
EXE = os.path.join(DST, "lichii.exe")
GAME = r"C:\Repos\Lichii\Games\OoT\The Legend of Zelda Ocarina of Time [0400486024DEC000][v131072].nup"
PORT = 6548
OUT = os.path.join(ROOT, "build-npc", "auto")
PID_FILE = os.path.join(OUT, "emulator.pid")
SAVES = os.path.join(USER, "nand", "user", "save")
FLAG = "CharacterMenu_SelectedCharacter"
PAGE_FLAG = "CharacterMenu_IsPageOpen"
DISPLAY_TYPE = 0x228        # menu actor manager: what the menu's 3D view shows (5 = menu closed)
POUCH = 0x28                # menu actor manager: handle of the menu's 3D character


# ---------------------------------------------------------------- the copy

def setup(fresh_saves=False):
    assert os.path.isfile(os.path.join(SRC, "lichii.exe")), "Fritchii not found at " + SRC
    npcenv._link_tree(SRC, DST)
    for d in ("cache", "sdmc", "dump", "tas", "log"):
        os.makedirs(os.path.join(USER, d), exist_ok=True)
    npcenv._junction(os.path.join(USER, "keys"), os.path.join(EDEN, "keys"))
    nand = os.path.join(USER, "nand")
    if fresh_saves and os.path.isdir(nand):
        shutil.rmtree(nand)
    if not os.path.isdir(nand):
        shutil.copytree(os.path.join(EDEN, "nand"), nand)
    src, dst = os.path.join(EDEN, "config"), os.path.join(USER, "config")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.bak*"))
    ini = os.path.join(dst, "qt-config.ini")
    text = open(ini, encoding="utf-8").read()

    def put(key, value):
        nonlocal text
        text, n = re.subn(r"(?m)^%s=.*$" % re.escape(key), lambda m: "%s=%s" % (key, value), text)
        assert n == 1, key
        text, n = re.subn(r"(?m)^%s\\default=.*$" % re.escape(key), lambda m: key + "\\default=false", text)
        assert n == 1, key
    put("load_directory", npcenv.LOAD_ROOT.replace("\\", "/"))
    for key in ("nand_directory", "sdmc_directory", "dump_directory", "tas_directory"):
        put(key, os.path.join(USER, key.split("_")[0]).replace("\\", "/"))
    put("audio_muted", "true")
    put("check_for_updates", "false")
    put("fullscreen", "false")
    # no physical controller: only injected input reaches player 1 (it stays connected)
    text, n = re.subn(r'(?m)^(player_\d+_(?:button|lstick|rstick|motionleft|motionright)[^=\\\n]*)=.*$',
                      lambda m: m.group(1) + "=[empty]", text)
    assert n > 20 and re.search(r"(?m)^player_0_connected=true$", text)
    open(ini, "w", encoding="utf-8").write(text)
    assert os.path.normcase(os.path.realpath(SAVES)).startswith(os.path.normcase(DST)), "saves must be the copy's own"
    print("automation emulator ready:", EXE)


def lichii_processes():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*lichii.exe' } | "
                          "ForEach-Object { \"$($_.Id)|$($_.Path)\" }"], capture_output=True, text=True).stdout
    return [(int(l.split("|")[0]), l.split("|", 1)[1]) for l in out.splitlines() if "|" in l]


ALONGSIDE = "--alongside" in sys.argv


def foreign():
    """Emulators that are not this copy (a person's, or another session's); none when we
    were told to run alongside them."""
    if ALONGSIDE:
        return []
    return [p for _pid, p in lichii_processes() if not os.path.normcase(p).startswith(os.path.normcase(DST))]


def ours():
    return [pid for pid, p in lichii_processes() if os.path.normcase(p).startswith(os.path.normcase(DST))]


def start():
    assert not foreign(), "another emulator is running: " + ", ".join(foreign())
    assert not ours(), "this copy's emulator is already running"
    os.makedirs(OUT, exist_ok=True)
    env = dict(os.environ, EDEN_NX2_DEBUG_SERVER="127.0.0.1:%d" % PORT)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7      # SW_SHOWMINNOACTIVE: minimized, and the person's window keeps the focus
    proc = subprocess.Popen([EXE, GAME], env=env, startupinfo=si, cwd=DST,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    open(PID_FILE, "w").write(str(proc.pid))
    return proc.pid


def stop():
    for pid in ours():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


# ---------------------------------------------------------------- saves

def set_saved_character(value):
    """Store `value` as the selected character in every save slot of the copy that has the
    flag (saves made with the Character models mod). Returns the slots changed."""
    h = struct.pack("<I", gamedata.murmur3_32(FLAG))
    done = []
    for root, _dirs, files in os.walk(SAVES):
        if "progress.sav" in files:
            path = os.path.join(root, "progress.sav")
            d = bytearray(open(path, "rb").read())
            at = [i for i in range(0x20, len(d) - 8, 4) if d[i:i + 4] == h]
            if len(at) == 1:
                struct.pack_into("<i", d, at[0] + 4, value)
                open(path, "wb").write(d)
                done.append(os.path.basename(root))
    return sorted(done)


# ---------------------------------------------------------------- the running game

class Yield(Exception):
    """Another emulator appeared: we got out of its way."""


class Dead(Exception):
    """The game stopped (crash, hang or the emulator was closed)."""


class Conn(dbg.Debug):
    """The debug server connection; a lost connection is a stopped game."""

    def call(self, method, **params):
        try:
            return dbg.Debug.call(self, method, **params)
        except (OSError, ConnectionError) as e:
            raise Dead("debug server gone during %s: %r" % (method, e))


class Game(live.Live):
    def __init__(self, log, boot_timeout=240):
        self.log = log
        self.d = None
        self.delta = None
        self.log_pos = 0
        self.last_foreign_check = 0.0
        img = Image()
        pat = img.data[live.PROBE - img.base:live.PROBE - img.base + 32]
        t0 = time.time()
        while time.time() - t0 < boot_timeout:
            self.check_foreign(force=True)
            if not ours():
                raise Dead("the emulator closed while booting")
            try:
                if self.d is None:
                    self.d = Conn(("127.0.0.1", PORT), timeout=180)
                hits = self.d.search(pat, max_hits=4)
                if len(hits) == 1:
                    self.delta = hits[0] - live.PROBE
                    return
            except (OSError, RuntimeError, ConnectionError, Dead):
                self.d = None
            time.sleep(2)
        raise Dead("no game code in memory after %d s" % boot_timeout)

    def check_foreign(self, force=False):
        if force or time.time() - self.last_foreign_check > 8:
            self.last_foreign_check = time.time()
            if foreign():
                stop()
                raise Yield()

    def call(self, method, **params):
        self.check_foreign()
        return self.d.call(method, **params)

    def status(self):
        return self.call("status", fps_window_ms=300)

    def steps(self, *steps, timeout_s=None):
        params = {"steps": list(steps)}
        if timeout_s:
            params["timeout_s"] = timeout_s
        try:
            return self.call("input_sequence", **params)
        except RuntimeError as e:      # frames were not presented in time: the game hangs or loads
            raise Dead("input not consumed: %s" % e)

    def press(self, *buttons, hold=3, then=20):
        self.steps({"buttons": list(buttons), "frames": hold}, {"frames": then})

    def wait(self, frames):
        self.steps({"frames": frames})

    def new_log(self, limit=400):
        r = self.call("log_tail", after=self.log_pos, limit=limit)
        self.log_pos = r.get("next", self.log_pos)
        return r.get("lines", [])

    # memory (all reads go through the debug server; a failed read means "not there yet")
    def manager(self):
        return self.q(self.q(live.MENU_GLOBAL + self.delta) + 0x4E8)

    def gd_bool(self, name):
        gm = self.q(live.GAMEDATA_GLOBAL + self.delta)
        h = gamedata.murmur3_32(name)
        m, cap = self.q(gm + 0x20), self.u(gm + 0x2C)
        i = h % cap
        for _ in range(cap):
            eh, idx = struct.unpack("<II", self.d.read(m + 8 * i, 8))
            if eh == h:
                return self.d.read(self.q(gm + 0x18) + 0x18 * idx + 0x16, 1)[0]
            if eh == 0:
                return None
            i = (i + 1) % cap
        return None

    def view(self):
        """(display type, pouch handle id or None, pouch actor name or None); None if no menu manager."""
        try:
            mm = self.manager()
            if not mm:
                return None
            display = self.u(mm + DISPLAY_TYPE)
            link, hid = self.q(mm + POUCH + 0x10), self.u(mm + POUCH + 0x18)
            if hid == 0xFFFFFFFF or not link:
                return display, None, None
            actor = self.q(link + 0x40)
            name = self.d.read(self.q(actor + 0x20), 40).split(b"\0")[0].decode("latin-1")
            return display, hid, name
        except (RuntimeError, struct.error, UnicodeDecodeError):
            return None

    def flag(self):
        try:
            return self.gd_int(FLAG)
        except (RuntimeError, struct.error, ZeroDivisionError):
            return None

    def page_open(self):
        try:
            return bool(self.gd_bool(PAGE_FLAG))
        except (RuntimeError, struct.error, ZeroDivisionError):
            return False

    def alive(self):
        s = self.status()
        return s.get("powered_on") and not s.get("paused") and s.get("fps", 0) > 1

    # navigation
    def boot_to_field(self, timeout=300):
        """From the emulator start to a loaded save: press A until the menu's 3D character
        exists (the area load creates it), then let the fade finish."""
        t0 = time.time()
        presses = 0
        while time.time() - t0 < timeout:
            v = self.view()
            if v and v[1] is not None and (v[2] or "").startswith("PouchPlayer"):
                self.wait(150)
                self.log("in the field after %.0f s and %d presses of A: %s, stored character %s"
                         % (time.time() - t0, presses, v[2], self.flag()))
                return v
            try:
                self.press("A", hold=3, then=45)
                presses += 1
            except Dead as e:
                if "input not consumed" not in str(e) or not ours():
                    raise
                time.sleep(2)       # loading: no frames for a while
        raise Dead("no loaded save after %d s (%d presses of A)" % (timeout, presses))

    def open_characters_page(self):
        self.press("Minus", then=60)
        for _ in range(6):
            if self.page_open():
                self.wait(60)
                return True
            self.press("L", then=60)
        return self.page_open()


BAD_LOG = re.compile(r"PANIC|panic|Unhandled|abort|Abort|assert|ASSERT|fatal|Fatal|exception|Exception|"
                     r"SvcBreak|Break\(|out of memory|OutOfMemory|failed to|Failed to|not found|NotFound|PathNotFound")


def bad_lines(lines):
    return [l[-300:] for l in lines if BAD_LOG.search(l)]


def run(name, body):
    """Start the copy, run body(game, log), always close it again. Returns a result dict."""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "%s-%s.txt" % (name, time.strftime("%Y%m%d-%H%M%S")))
    result = {"name": name, "log": path, "outcome": "?"}

    def log(text):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), text)
        print(line, flush=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if foreign():
        log("another emulator is running (%s): not starting" % ", ".join(foreign()))
        result["outcome"] = "yield"
        return result
    start()
    game = None
    try:
        game = Game(log)
        log("game code found, main shifted by %#x" % game.delta)
        body(game, log, result)
        result.setdefault("outcome", "done")
        if result["outcome"] == "?":
            result["outcome"] = "done"
    except Yield:
        log("another emulator started: closed ours and stopped")
        result["outcome"] = "yield"
    except Dead as e:
        log("GAME STOPPED: %s" % e)
        result["outcome"] = "dead"
        result["error"] = str(e)
    finally:
        tail = []
        if game is not None and game.d is not None and ours():
            try:
                tail = game.d.call("log_tail", after=game.log_pos, limit=400).get("lines", [])
            except Exception:
                pass
        for l in bad_lines(tail)[-15:]:
            log("log: " + l)
        stop()
        emu_log = os.path.join(USER, "log", "eden_log.txt")
        if os.path.exists(emu_log):
            shutil.copy2(emu_log, path[:-4] + ".emulator-log.txt")
    with open(path[:-4] + ".json", "w") as f:
        json.dump(result, f, indent=1)
    return result


def ring(first=0, count=None):
    """Boot the save, open the Characters page, then D-pad right `count` times (from stored
    number `first`, which is written into the save first): after each press wait until the
    stored number moved on and a new menu character exists and is shown again."""
    import npc_characters as n
    names = n.ring()
    total = len(names)
    count = total if count is None else count
    slots = set_saved_character(first)

    def body(game, log, result):
        log("saved character set to %d (%s) in %s" % (first, names[first % total], ", ".join(slots)))
        game.boot_to_field()
        assert game.flag() == first, ("the loaded save has another character", game.flag())
        for l in bad_lines(game.new_log()):
            log("log (boot): " + l)
        if not game.open_characters_page():
            raise Dead("could not reach the Characters page (page flag never set)")
        log("Characters page open: %s" % (game.view(),))
        result["swaps"] = []
        for k in range(1, count + 1):
            want = first + k
            before = game.view()
            t0 = time.time()
            game.press("Right", then=10)
            ok, again = False, True
            while time.time() - t0 < 40:
                v = game.view()
                if game.flag() == want and v and v[0] == 0 and v[1] is not None and v[1] != before[1]:
                    ok = True
                    break
                if again and time.time() - t0 > 4 and game.flag() == want - 1:
                    game.press("Right", then=10)    # the press fell into an animation: once more
                    again = False
                game.wait(6)
            dt = time.time() - t0
            game.wait(45)       # let it draw for a moment
            alive = game.alive()
            bad = bad_lines(game.new_log())
            entry = {"number": want, "name": names[want % total], "ok": ok and alive, "seconds": round(dt, 1),
                     "view": game.view(), "log": bad[:6]}
            result["swaps"].append(entry)
            log("%3d %-24s %s in %.1f s  %s%s" % (want % total, names[want % total], "ok" if ok and alive else "FAILED",
                                                   dt, game.view(), "  log: " + " | ".join(bad[:3]) if bad else ""))
            if not (ok and alive):
                raise Dead("swap to %d (%s) did not complete" % (want, names[want % total]))
        game.press("B", then=90)
        log("menu closed, still running: %s" % game.alive())
    return run("ring-%d" % first, body)


FIELD_MOVES = [
    ("stand", [{"frames": 90}]),
    ("run forward", [{"left_stick": [0.0, 1.0], "frames": 70}]),
    ("roll", [{"left_stick": [0.0, 1.0], "frames": 20}, {"left_stick": [0.0, 1.0], "buttons": ["A"], "frames": 4},
              {"left_stick": [0.0, 1.0], "frames": 50}]),
    ("run in a circle", [{"left_stick": [1.0, 0.0], "frames": 25}, {"left_stick": [0.0, -1.0], "frames": 25},
                         {"left_stick": [-1.0, 0.0], "frames": 25}, {"left_stick": [0.0, 1.0], "frames": 25}]),
    ("sword", [{"buttons": ["B"], "frames": 4}, {"frames": 25}, {"buttons": ["B"], "frames": 4}, {"frames": 25},
               {"buttons": ["B"], "frames": 4}, {"frames": 45}]),
    ("target and guard", [{"buttons": ["ZL"], "frames": 40}, {"buttons": ["ZL", "R"], "frames": 50}, {"frames": 30}]),
    ("target, backflip, side hop", [{"buttons": ["ZL"], "frames": 15},
                                    {"buttons": ["ZL", "A"], "left_stick": [0.0, -1.0], "frames": 4},
                                    {"buttons": ["ZL"], "frames": 50},
                                    {"buttons": ["ZL", "A"], "left_stick": [1.0, 0.0], "frames": 4},
                                    {"buttons": ["ZL"], "frames": 50}, {"frames": 20}]),
    ("items", [{"buttons": ["X"], "frames": 4}, {"frames": 40}, {"buttons": ["Y"], "frames": 4}, {"frames": 40},
               {"buttons": ["B"], "frames": 4}, {"frames": 40}]),
    ("pause menu and back", [{"buttons": ["Plus"], "frames": 4}, {"frames": 90}, {"buttons": ["B"], "frames": 4},
                             {"frames": 90}]),
]


def field(number):
    """Boot a save that has `number` stored: the field Link (and the menu's) is created with
    that character's model. Then move as a player would."""
    import npc_characters as n
    names = n.ring()
    label = dict((n.EXPERIMENT_BASE + i, "experiment " + m) for i, (m, _s) in enumerate(n.EXPERIMENTS)).get(
        number, names[number % len(names)])
    slots = set_saved_character(number)

    def body(game, log, result):
        log("saved character set to %d (%s) in %s" % (number, label, ", ".join(slots)))
        game.boot_to_field()
        assert game.flag() == number, ("the loaded save has another character", game.flag())
        result["moves"] = []
        for l in bad_lines(game.new_log()):
            log("log (boot): " + l)
        for name, steps in FIELD_MOVES:
            game.steps(*steps, timeout_s=60)
            alive = game.alive()
            bad = bad_lines(game.new_log())
            result["moves"].append({"move": name, "ok": bool(alive), "log": bad[:6]})
            log("%-28s %s%s" % (name, "ok" if alive else "STOPPED", "  log: " + " | ".join(bad[:3]) if bad else ""))
            if not alive:
                raise Dead("the game stopped during: " + name)
    return run("field-%d" % number, body)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if cmd == "setup":
        setup(fresh_saves="--fresh-saves" in sys.argv)
    elif cmd == "stop":
        stop()
    elif cmd == "ring":
        args = [int(a) for a in sys.argv[2:] if not a.startswith("--")]
        r = ring(*args)
        print(r["outcome"])
    elif cmd == "field":
        for a in [x for x in sys.argv[2:] if not x.startswith("--")]:
            r = field(int(a))
            print(a, r["outcome"])
            if r["outcome"] == "yield":
                break
    else:
        raise SystemExit("unknown command " + cmd)
