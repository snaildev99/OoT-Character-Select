"""Install ONLY the model fixes session's test folders (the cloth folders of mods\\npc_characters.py) into the mods
folder the normal build installs into.

    venv\\Scripts\\python.exe -I tools\\models_install.py

Why (CLAUDE.md, "Who is building and what is installed", 2026-11-13): only the main build installs the whole set;
a session installs its own named test folders and nothing else. This session's folders are made by the normal
build (npc_characters.EXTRA_MODS), so this script
  1. runs the normal full build into build\\ WITHOUT installing (it holds the build lock for that),
  2. checks that every folder of that build which is NOT this session's is byte for byte what is installed.
     If one is not (this session's work changed the size table, or somebody else's work is built but not
     installed), these folders alone would be out of step with the rest: it stops, names the files, and
     installs nothing. Then the main build has to install (install-mods.bat), which is not this session's to run.
  3. copies this session's folders, removes this session's retired ones (npc_characters.RETIRED) and stamps
     the record (tools\\modstate.py) as "models".
It does not touch the voices folder or any other session's folder. Refuses while the emulator runs.
"""
import filecmp
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))
sys.path.insert(0, HERE)
os.environ.setdefault("OOT_SESSION", "models")

import build  # noqa: E402
import modstate  # noqa: E402
import npc_characters as npc  # noqa: E402


def tree(base):
    out = {}
    for root, _dirs, names in os.walk(base):
        for n in names:
            p = os.path.join(root, n)
            out[os.path.relpath(p, base)] = p
    return out


def differing(a, b):
    """Files that are not the same in the two folders (missing on either side too)."""
    fa, fb = tree(a), tree(b)
    return sorted(k for k in set(fa) | set(fb) if k not in fa or k not in fb or not filecmp.cmp(fa[k], fb[k], shallow=False))


def main():
    if build.emulator_running():
        print("The emulator is running (%s). Close it first: it keeps the mod files open." % ", ".join(build.emulator_running()))
        sys.exit(1)
    mine = list(npc.EXTRA_MODS)
    sys.argv = [sys.argv[0], "--full"]
    build.main()                    # the normal build, into build\, no install; it holds the build lock
    load = build.EDEN_LOAD
    assert all(os.path.isdir(os.path.join(build.BUILD, m)) for m in mine), mine
    # everything else of that build against what is installed
    import field_swap
    others = [m for m in list(build.MODS) + [npc.MOD, field_swap.MOD] if os.path.isdir(os.path.join(build.BUILD, m))]
    out_of_step = {}
    for mod in others:
        if not os.path.isdir(os.path.join(load, mod)):
            out_of_step[mod] = ["(the folder is not installed)"]
            continue
        d = differing(os.path.join(build.BUILD, mod), os.path.join(load, mod))
        if d:
            out_of_step[mod] = d
    if out_of_step:
        print("\nNOT INSTALLED. The normal build's own folders differ from what is installed:")
        for mod, files in out_of_step.items():
            print("  %s: %s%s" % (mod, ", ".join(files[:4]), " ..." if len(files) > 4 else ""))
        print("The cloth folders alone would be out of step with them (the size table is one of those files).")
        print("The main build has to install first (install-mods.bat), then this again.")
        sys.exit(2)
    for mod in mine:
        dst = os.path.join(load, mod)
        same = os.path.isdir(dst) and not differing(os.path.join(build.BUILD, mod), dst)
        if not same:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(os.path.join(build.BUILD, mod), dst)
        print("  %s -> %s" % ("as installed" if same else "installed", dst))
    gone = []
    for mod in npc.RETIRED:
        dst = os.path.join(load, mod)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
            gone.append(mod)
            print("  removed (retired) -> %s" % dst)
    modstate.stamp(os.environ["OOT_SESSION"], load, mine, removed=gone,
                   note="the cloth folders only; the rest is the installed build, checked the same as this build's")
    print("models: %d folders of this session installed; the other %d folders of the build are the installed ones" % (len(mine), len(others)))


if __name__ == "__main__":
    main()
