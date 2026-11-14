"""Who is building, and what is installed (main session, 2026-11-13: several sessions build and install into one
emulator's mods folder, and nothing said whose build was running or which install was the active one).

    venv\\Scripts\\python.exe -I tools\\modstate.py            what is installed now, and who is building

- building(owner): one build at a time. A lock file in the project folder (`.build.lock`: who, when, the process);
  a second build waits for it up to WAIT seconds, then stops and says whose build it is. A lock whose process is
  gone is taken over. `mods\\build.py` holds it for its whole run; a session's own build script can hold it too
  (`with modstate.building("my session"):`).
- stamp(...): after an install, `installed.json` in the project folder says who installed, when, with which
  options, and a sum of every folder that install wrote; `installed.log` keeps one line an install.
- status(): the last stamp, then every folder in the mods folder now: as stamped, changed since (another tool wrote
  it without a stamp), or not in the stamp at all. An installer that is not `mods\\build.py` should call
  stamp(owner, load, folders) after it copies, so that its folders show as its own.

Standard library only."""
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join(ROOT, ".build.lock")
STAMP = os.path.join(ROOT, "installed.json")
LOG = os.path.join(ROOT, "installed.log")
WAIT = 180     # seconds a build waits for another one (a build takes about half a minute)


def _alive(pid):
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return '"%d"' % pid in out


def _holder():
    try:
        return json.load(open(LOCK, encoding="utf-8"))
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def building(owner):
    """Hold the build lock for the block. Nested use in one process (a session's script that calls build.main) is fine."""
    mine = os.getpid()
    held = _holder()
    if held and held.get("pid") == mine:
        yield
        return
    start, said = time.time(), False
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            held = _holder()
            if not held or not _alive(held.get("pid", -1)):
                with contextlib.suppress(OSError):
                    os.remove(LOCK)     # (its process is gone: a build that was stopped)
                continue
            if not said:
                print("another build is running (%s, since %s): waiting for it" % (held.get("owner"), held.get("since")), flush=True)
                said = True
            if time.time() - start > WAIT:
                print("Still running after %d s: %s (process %s, since %s). Not started; run this again when it is done."
                      % (WAIT, held.get("owner"), held.get("pid"), held.get("since")))
                sys.exit(1)
            time.sleep(2)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"owner": owner, "pid": mine, "since": time.strftime("%Y-%m-%d %H:%M:%S"), "args": sys.argv[1:]}, f)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK)


def folder_sum(path):
    """A sum of a folder's files: their paths, sizes and bytes"""
    h = hashlib.sha1()
    for root, dirs, names in os.walk(path):
        dirs.sort()
        for n in sorted(names):
            p = os.path.join(root, n)
            h.update(os.path.relpath(p, path).replace("\\", "/").encode())
            with open(p, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
    return h.hexdigest()[:16]


def source():
    """The sources an install was built from: the local repository's commit, and how many files differ from it"""
    def git(*a):
        r = subprocess.run(["git", "-C", ROOT] + list(a), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    head = git("rev-parse", "--short", "HEAD")
    if not head:
        return ""
    changed = [line[3:] for line in git("status", "--porcelain").splitlines()]
    return "commit %s" % head + (", %d files changed since: %s" % (len(changed), ", ".join(changed[:8]) + (" ..." if len(changed) > 8 else ""))
                                 if changed else ", no changes since")


def stamp(owner, load, folders, removed=(), note=""):
    """Record an install: `folders` (names in `load`) were just written by `owner`. Folders of earlier stamps that
    are still there and were not written now stay in the record under the name of who wrote them."""
    try:
        old = json.load(open(STAMP, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    when = time.strftime("%Y-%m-%d %H:%M:%S")
    kept = {name: f for name, f in old.get("folders", {}).items()
            if old.get("load") == load and name not in folders and name not in removed and os.path.isdir(os.path.join(load, name))}
    for name in folders:
        kept[name] = {"sum": folder_sum(os.path.join(load, name)), "by": owner, "when": when}
    data = {"last": {"by": owner, "when": when, "args": sys.argv[1:], "note": note, "wrote": list(folders), "removed": list(removed), "source": source()},
            "load": load, "folders": kept}
    with open(STAMP, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s  %s  %s  wrote %d folders%s%s\n" % (when, owner, " ".join(sys.argv[1:]) or "-", len(folders),
                                                      ", removed " + ", ".join(removed) if removed else "", "  " + note if note else ""))


def status():
    held = _holder()
    if held and _alive(held.get("pid", -1)):
        print("BUILDING NOW: %s (since %s, %s)" % (held.get("owner"), held.get("since"), " ".join(held.get("args", [])) or "no options"))
    else:
        print("no build is running")
    try:
        data = json.load(open(STAMP, encoding="utf-8"))
    except (OSError, ValueError):
        print("no install is recorded yet (installed.json): run an install to start the record")
        return
    last = data["last"]
    print("last install: %s, by %s (%s)%s" % (last["when"], last["by"], " ".join(last["args"]) or "no options",
                                              ", " + last["note"] if last.get("note") else ""))
    if last.get("source"):
        print("  built from: %s" % last["source"])
    now_source = source()
    if now_source:
        print("the sources now: %s" % now_source)
    load = data["load"]
    print("mods folder: %s" % load)
    now = sorted(n for n in os.listdir(load) if os.path.isdir(os.path.join(load, n))) if os.path.isdir(load) else []
    for name in now:
        rec = data["folders"].get(name)
        if not rec:
            state = "NOT IN THE RECORD (installed by a tool that does not stamp)"
        elif folder_sum(os.path.join(load, name)) == rec["sum"]:
            state = "as installed by %s, %s" % (rec["by"], rec["when"])
        else:
            changed = time.strftime("%Y-%m-%d %H:%M", time.localtime(max(
                os.path.getmtime(os.path.join(r, n)) for r, _d, ns in os.walk(os.path.join(load, name)) for n in ns)))
            state = "CHANGED since %s's install of %s (newest file %s)" % (rec["by"], rec["when"], changed)
        print("  %-28s %s" % (name, state))
    for name in sorted(set(data["folders"]) - set(now)):
        print("  %-28s GONE (was installed by %s, %s)" % (name, data["folders"][name]["by"], data["folders"][name]["when"]))
    if os.path.exists(LOG):
        lines = open(LOG, encoding="utf-8").read().splitlines()[-5:]
        print("the last installs:")
        for line in lines:
            print("  " + line)


if __name__ == "__main__":
    status()
