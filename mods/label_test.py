"""Label test (session of 2026-11-14): set a text pane's string from code at run time.

The Characters page's title "Character Select" is replaced by TEXT, written by a routine
of ours that the page's graph runs once when the page opens. Nothing else changes.

How the game does it (read in the stock lift, build-hookshot/work-stock/sa/gen; README,
layouts section, "Setting a text pane's string from code"):
  screen id  = 0x801939e0(name, 0)                    murmur3 of the screen's name
  screen     = 0x83be9670(UI manager, id, 0)           UI manager = [[GOT 0x84ca0968] + 0x38]
  holder     = screen - 0xe8                           the body layout's holder, INSIDE the screen
             (0x8045b970 reads its layout at [holder + 0x80]: it adds 0x40, then 0x8045c040
             reads [+0x40]; live, the layout's pointer was at screen - 0x68; the second try
             passed screen - 0xa8 and crashed reading a null layout's count)
             object (read from the running game, 2026-11-14, tools\labelprobe.py; the
             header's class keeps a pointer at +0x20 instead: the first try read that and crashed)
  0x8045b970(holder, key, &{utf8 *text, i32 bytes, u64 -1 (as the header's)}, 0, 0)   (texts are UTF-8 in this build)
             sets every text pane of that layout whose reference string is "@<key>";
             the TextBox keeps the pointer (0x80459680 copies nothing), so the text
             lives in our code region for good.
So our layout copy gives the title the reference "@T_Title_00" (as the game's own
code-filled titles have), and a node of the unused class CODE_NODE in the page's entry
runs the routine (its one-shot body, CODE, replaced; its input binder is left as it is).
Like every stock one-shot body it must end with [node + 24] = 0x80000000 | (low 30 bits):
the node's "done"; without it the page's entry waits for ever (third build: TEST shown,
game hung).

A test folder that sorts first, laid over what is installed (as mods\\customize.py does):
it ships the installed page layout archive and page graph with the change, the installed
size tables merged, and a code patch. It is NOT in build.RETIRED (build.py is not ours):
run `--remove` after the round, before the next normal install is looked at.

  venv\\Scripts\\python.exe -I mods\\label_test.py              build into build-label\\, check
  venv\\Scripts\\python.exe -I mods\\label_test.py --install    and install our one folder
  venv\\Scripts\\python.exe -I mods\\label_test.py --remove     take it out
"""
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build  # noqa: E402  (a library here; not edited)
import ui_sandbox as ui  # noqa: E402
import ainbx  # noqa: E402
import exepatch  # noqa: E402
import gamedata  # noqa: E402
import layout  # noqa: E402
import modstate  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402
import rstb  # noqa: E402
import sarcedit  # noqa: E402
import uiedit  # noqa: E402

BUILD_LABEL = os.path.join(ROOT, "build-label")
MOD = "Character (a label test)"            # sorts before every folder, the customize test's too ("(a" < "(c")
OLD_NAMES = ["Character (label test)"]      # this script's earlier name: removed by --install and --remove
SESSION = os.environ.get("OOT_SESSION", "label")
GRAPH = "Sequence/CharacterMenu.module.ainb"
SCREEN = build.PAGE_SCREEN                  # "CharacterMenu_00"
PANE = "T_Title_00"
KEY = PANE                                  # the reference becomes "@T_Title_00"
TEXT = "TEST"                               # the text the build ships; tools\labelset.py changes it in a running game

# The text lives in a buffer of its own at the end of the padding after .rodata (the
# Character models patch uses its start, up to 0x8486c3ec): MARKER (16 bytes, for
# tools\labelset.py to find it in the emulator's memory), the length (u32), 4 bytes, then
# the text (UTF-8, room for TEXT_ROOM bytes and a 0). The routine reads both on every run,
# so a new text written there shows the next time the page opens.
BUF = 0x8486CF00
BUF_END = 0x8486D000
MARKER = b"OoT-label-13377"
TEXT_AT = 24
TEXT_ROOM = BUF_END - BUF - TEXT_AT - 1

# the routine's home: the one-shot body of an unused node class (README's list of 2026-11-13,
# taken by nobody); keep other patches off it
CODE_NODE = "SeqOneShotClearLODJudgeLayer"
CODE = (0x832EDB10, 0x832EDBD4)
CODE_SHA1 = "008c2c5d41fc7baef309d13cc2cdd08f04931cf6"   # the stock body, checked before it is replaced
UI_GOT = 0x84CA0968                         # -> the global whose +0x38 is the UI screen manager
SCREEN_ID = 0x801939E0
SCREEN_GET = 0x83BE9670
SET_BY_KEY = 0x8045B970

ROUTINE = """
    lt_body:
        hint #27
        stp x29, x30, [sp, #-64]!
        mov x29, sp
        stp x19, x20, [sp, #16]
        mov x20, x0
        adrp x8, #{got_page:#x}
        ldr x8, [x8, #{got_off:#x}]
        ldr x8, [x8]
        cbz x8, lt_out
        ldr x19, [x8, #0x38]
        cbz x19, lt_out
        adr x0, lt_screen
        mov w1, #0
        bl #{screen_id:#x}
        mov w1, w0
        mov x0, x19
        mov w2, #0
        bl #{screen_get:#x}
        cbz x0, lt_out
        sub x0, x0, #0xe8
        ldr x8, [x0, #0x80]
        cbz x8, lt_out
        adrp x8, #{buf_page:#x}
        add x8, x8, #{text_off:#x}
        str x8, [sp, #32]
        ldr w9, [x8, #-{text_at_len}]
        str x9, [sp, #40]
        mov x8, #-1
        str x8, [sp, #48]
        adr x1, lt_key
        add x2, sp, #32
        mov w3, #0
        mov x4, #0
        bl #{set_by_key:#x}
    lt_out:
        ldr w8, [x20, #24]
        and w8, w8, #0x3fffffff
        orr w8, w8, #0x80000000
        str w8, [x20, #24]
        ldp x19, x20, [sp, #16]
        ldp x29, x30, [sp], #64
        retab
    lt_screen:
        .byte {screen}
    lt_key:
        .byte {key}
"""


def buffer(text):
    raw = text.encode("utf-8")
    assert len(raw) <= TEXT_ROOM, "the text is longer than the buffer (%d bytes)" % TEXT_ROOM
    out = MARKER + struct.pack("<II", len(raw), 0) + raw
    assert len(MARKER) == 16 and out.index(raw) == TEXT_AT
    return out + bytes(BUF_END - BUF - len(out))


def blob(s, enc="ascii"):
    return ", ".join("%d" % b for b in s.encode(enc) + (b"\0\0" if enc == "utf-16-le" else b"\0"))


def installed():
    load = build.EDEN_LOAD
    out = sorted(f for f in os.listdir(load) if os.path.isdir(os.path.join(load, f)) and f != MOD and f not in OLD_NAMES)
    assert out == sorted(out, key=str.lower)
    assert all(MOD < f for f in out), "a folder sorts before ours: " + ", ".join(f for f in out if f < MOD)
    return out


def resolve(rel):
    for folder in installed():
        path = os.path.join(build.EDEN_LOAD, folder, "romfs", *rel.split("/"))
        if os.path.exists(path):
            return folder, path
    raise AssertionError("no installed folder ships " + rel)


def read_installed(rel):
    return open(resolve(rel)[1], "rb").read()


# ---------------------------------------------------------------- the layout
def title_layout(data):
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    ed.validate()
    texts = lay.root.ptr[0x80]
    rec = texts.get(PANE)
    assert rec.ptr[0x18].text == "*", rec.ptr[0x18].text        # the picker's title: its message by label
    assert sum(1 for k, _, _ in texts.items if k.text.startswith("@") or False) == 0
    refs = [r.ptr[0x18].text for _, r, _ in texts.items if r.ptr[0x18] is not None]
    assert "@" + KEY not in refs, "another pane is filled by the same key"
    uiedit.set_text(rec, ref="@" + KEY)
    out = layout.build(lay)
    back = layout.Layout(out)
    uiedit.Editor(back).validate()
    assert back.root.ptr[0x80].get(PANE).ptr[0x18].text == "@" + KEY
    assert uiedit.Editor(back).names() == ed.names()
    return out


def write_layout():
    rel = ui.COMMON
    raw = read_installed(rel)
    entries, meta = sarcedit.parse(nxfmt.unzs(raw, rel))
    name = ui.LAYOUT + ".buil"
    assert [n for n, _ in entries].count(name) == ui.LAYOUT_COPIES
    assert len(set(d for n, d in entries if n == name)) == 1
    old = [d for n, d in entries if n == name][0]
    new_layout = title_layout(old)
    new = sarcedit.build([(n, new_layout if n == name else d) for n, d in entries], meta)
    files = nxfmt.sarc_files(new)
    assert files[name] == new_layout
    assert all(files[n] == d for n, d in entries if n != name)
    build.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), build.SIZES, size=ui.align32(len(new)) + 4144)
    return len(old), len(new_layout)


# ---------------------------------------------------------------- the graph
def kids(nodes, i):
    return [c["Node Index"] for c in nodes[i]["Plugs"].get("Child", [])]


def t_label():
    """A node of CODE_NODE: SeqOneShotSetLayerVisible's (its one input, LayerTag, is the
    one CODE_NODE binds; the value is not read by our body)."""
    n = build.vanilla("BeforeTitle", 5, "SeqOneShotSetLayerVisible")
    n["Name"] = CODE_NODE
    ins = n["Parameters"]["Inputs"]
    assert [p["Name"] for p in ins["Int"]] == ["LayerTag"] and [p["Name"] for p in ins["Bool"]] == ["IsVisible"]
    del ins["Bool"]
    return n


def write_graph():
    owner, path = resolve(GRAPH)
    g = build.Graph(path)
    assert g.build() == g.src, "the installed graph does not rebuild exactly"
    nodes = g.nodes
    assert not any(n.get("Name") == CODE_NODE for n in nodes)
    root = g.d["Commands"][0]["Root Node Index"]
    enter = kids(nodes, root)[0]
    assert nodes[enter]["Node Type"] == "Element_Simultaneous"
    opens = [c for c in kids(nodes, enter) if nodes[c].get("Name") == "SeqOneShotOpenScreen"]
    assert len(opens) == 1
    # one frame after the page's entry (the screen is open and its texts resolved), then ours
    seq = g.add(build.t_sequential([g.add(build.t_wait_frames(1)), g.add(t_label())], root=False, same_frame=False))
    nodes[enter]["Plugs"]["Child"].append({"Node Index": seq, "Name": ""})
    before = len(ainbx.load(g.src).as_dict()["Nodes"])
    data = g.build()
    back = ainbx.load(data).as_dict()["Nodes"]
    assert len(back) == before + 3
    assert [n.get("Name") for n in back[:before]] == [n.get("Name") for n in ainbx.load(g.src).as_dict()["Nodes"]]
    assert back[before + 1]["Name"] == CODE_NODE and seq in [c["Node Index"] for c in back[enter]["Plugs"]["Child"]]
    build.write(MOD, GRAPH, data, len(data), build.SIZES)
    return owner, before


# ---------------------------------------------------------------- the code
def make_patch():
    import hashlib
    import npc_characters as npc
    img = exepatch.Image()
    sha1 = hashlib.sha1(img.data[CODE[0] - img.base:CODE[1] - img.base]).hexdigest()
    import glob
    real = glob.glob
    glob.glob = lambda *a, **k: [x for x in real(*a, **k) if MOD not in x]     # our own graph names the class
    try:
        npc.check_unused_node(img, CODE_NODE, CODE, CODE_SHA1 or sha1)
    finally:
        glob.glob = real
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = CODE
    try:
        p = exepatch.Patcher(image=img, cave=CODE)
    finally:
        exepatch.CAVE_REGION = region
    p.patches.clear()                       # no signature words: the vtable enters at CODE[0]
    text = ROUTINE.format(got_page=UI_GOT & ~0xFFF, got_off=UI_GOT & 0xFFF, screen_id=SCREEN_ID,
                          screen_get=SCREEN_GET, set_by_key=SET_BY_KEY, buf_page=BUF & ~0xFFF,
                          text_off=(BUF & 0xFFF) + TEXT_AT, text_at_len=TEXT_AT - 16,
                          screen=blob(SCREEN), key=blob(KEY + "\0" * ((len(SCREEN) + len(KEY)) % 2)))
    code = exepatch.assemble(text, CODE[0])
    assert CODE[0] + len(code) <= CODE[1], "the routine does not fit (%d bytes)" % len(code)
    p.raw(CODE[0], code, expect=img.data[CODE[0] - img.base:CODE[0] - img.base + 4])
    p.symbols["lt_body"] = CODE[0]
    p.raw(BUF, buffer(TEXT), expect=bytes(BUF_END - BUF))           # the padding is empty there
    assert BUF >= exepatch.RODATA_CAVE[0] and BUF_END <= exepatch.RODATA_CAVE[1]
    p.cave_ptr = CODE[0] + len(code)
    return p, code, sha1


def check_patch(code, texts=(TEXT, "hello world!!!")):
    """The routine in an ARM64 emulator, the game's own murmur3 routine run for the id, the
    screen lookup and the setter stubbed: every null on the way returns cleanly, and the
    whole way gives the setter the holder, the key and {TEXT, len}. -> runs."""
    import unicorn as uc
    from unicorn import arm64_const as A
    img = exepatch.Image()
    base, size = img.base, (len(img.data) + 0xFFFF) & ~0xFFFF
    STACK, HEAP, DONE = 0x10000000, 0x20000000, 0x30000000
    runs = 0

    def run(text, globals_ok, mgr_ok, screen_ok, holder_ok, layout_ok):
        m = uc.Uc(uc.UC_ARCH_ARM64, uc.UC_MODE_ARM)
        m.mem_map(base, size)
        m.mem_write(base, bytes(img.data))
        m.mem_write(CODE[0], code)
        m.mem_write(BUF, buffer(text))                           # as tools\labelset.py writes it
        for a in (STACK, HEAP, DONE):
            m.mem_map(a, 0x10000)
        g, mgr, scr, lay = HEAP, HEAP + 0x100, HEAP + 0x300, HEAP + 0x400
        hold = scr - 0xE8                                           # the holder lies inside the screen object
        m.mem_write(UI_GOT, struct.pack("<Q", HEAP + 0x900))            # the GOT slot -> the global
        m.mem_write(HEAP + 0x900, struct.pack("<Q", g if globals_ok else 0))
        m.mem_write(g + 0x38, struct.pack("<Q", mgr if mgr_ok else 0))
        m.mem_write(hold + 0x80, struct.pack("<Q", lay if layout_ok else 0))    # 0x8045b970: [[x0 + 0x40] via 0x8045c040 = [x0 + 0x80]
        seen = {}

        def hook(mu, addr, n, _):
            if addr == SCREEN_GET:
                seen["get"] = (mu.reg_read(A.UC_ARM64_REG_X0), mu.reg_read(A.UC_ARM64_REG_W1), mu.reg_read(A.UC_ARM64_REG_W2))
                mu.reg_write(A.UC_ARM64_REG_X0, scr if screen_ok else 0)
            elif addr == SET_BY_KEY:
                x = [mu.reg_read(getattr(A, "UC_ARM64_REG_X%d" % i)) for i in range(5)]
                key = bytes(mu.mem_read(x[1], 32)).split(b"\0")[0].decode()
                ptr, ln, extra = struct.unpack("<QIxxxxQ", bytes(mu.mem_read(x[2], 24)))
                text = bytes(mu.mem_read(ptr, ln + 1)).decode("utf-8")
                seen["set"] = (x[0], key, text, ln, extra, x[3], x[4], BUF + TEXT_AT == ptr)
                # what the setter itself reads: x0 + 0x40, then 0x8045c040 loads [that + 0x40]
                seen["layout"] = struct.unpack("<Q", bytes(mu.mem_read(x[0] + 0x40 + 0x40, 8)))[0]
            else:
                return
            mu.reg_write(A.UC_ARM64_REG_PC, mu.reg_read(A.UC_ARM64_REG_X30))
        retab = CODE[0] + code.index(struct.pack("<I", 0xD65F0FFF))      # the emulator has no PAC: a plain ret
        assert retab % 4 == 0
        m.hook_add(uc.UC_HOOK_CODE, lambda mu, a, n, _: mu.reg_write(A.UC_ARM64_REG_PC, mu.reg_read(A.UC_ARM64_REG_X30)),
                   begin=retab, end=retab)
        m.hook_add(uc.UC_HOOK_CODE, hook, begin=SCREEN_GET, end=SCREEN_GET)
        m.hook_add(uc.UC_HOOK_CODE, hook, begin=SET_BY_KEY, end=SET_BY_KEY)
        sp = STACK + 0x8000
        m.reg_write(A.UC_ARM64_REG_SP, sp)
        m.reg_write(A.UC_ARM64_REG_X30, DONE)
        m.reg_write(A.UC_ARM64_REG_X0, HEAP + 0x800)        # the node
        m.reg_write(A.UC_ARM64_REG_X19, 0x1919)
        m.reg_write(A.UC_ARM64_REG_X20, 0x2020)
        m.mem_write(HEAP + 0x800 + 24, struct.pack("<I", 0x4ABCDEF1))       # the node's state word
        m.emu_start(CODE[0], DONE, count=20000)
        assert m.reg_read(A.UC_ARM64_REG_SP) == sp and m.reg_read(A.UC_ARM64_REG_X19) == 0x1919
        assert m.reg_read(A.UC_ARM64_REG_X20) == 0x2020
        # done, as the stock one-shot bodies end (the first build in game left it out: the
        # page's entry waited for the node forever and the game hung with the title set)
        assert struct.unpack("<I", bytes(m.mem_read(HEAP + 0x800 + 24, 4)))[0] == 0x80000000 | (0x4ABCDEF1 & 0x3FFFFFFF)
        return seen, mgr

    for text, case in [(t, c) for t in texts for c in range(32)]:
        flags = [bool(case >> k & 1) for k in range(5)]
        seen, mgr = run(text, *flags)
        runs += 1
        reach_get = flags[0] and flags[1]
        assert ("get" in seen) == reach_get, (flags, seen)
        if reach_get:
            assert seen["get"] == (mgr, gamedata.murmur3_32(SCREEN), 0), (seen["get"], hex(gamedata.murmur3_32(SCREEN)))
        reach_set = flags[0] and flags[1] and flags[2] and flags[4]      # (flags[3] no longer used: no holder pointer)
        assert ("set" in seen) == reach_set, (flags, seen)
        if reach_set:
            assert seen["layout"] == 0x20000400, hex(seen["layout"])
            assert seen["set"] == (0x20000300 - 0xE8, KEY, text + "\0", len(text.encode()), (1 << 64) - 1, 0, 0, True), seen["set"]
    return runs


def ips_ranges(data):
    assert data[:5] == b"IPS32"
    o, out = 5, []
    while data[o:o + 4] != b"EEOF":
        at, n = struct.unpack_from(">IH", data, o)
        o += 6
        if n == 0:
            n = struct.unpack_from(">H", data, o)[0]
            o += 3
        else:
            o += n
        out.append((at, at + n))
    return out


def check_installed_patches():
    mine = ips_ranges(open(os.path.join(build.BUILD, MOD, "exefs", exepatch.BUILD_ID + ".ips"), "rb").read())
    count = 0
    for folder in installed():
        path = os.path.join(build.EDEN_LOAD, folder, "exefs", exepatch.BUILD_ID + ".ips")
        if os.path.exists(path):
            count += 1
            for a0, a1 in ips_ranges(open(path, "rb").read()):
                for b0, b1 in mine:
                    assert a1 <= b0 or b1 <= a0, "%s patches our bytes" % folder
    return count


# ---------------------------------------------------------------- the size table
def write_table():
    tables = []
    for folder in installed():
        path = os.path.join(build.EDEN_LOAD, folder, "romfs", *rstb.PATH.split("/"))
        if os.path.exists(path):
            tables.append(rstb.Rstb(nxfmt.read(path)))
    assert tables
    t = rstb.Rstb(tables[0].to_bytes())
    for other in tables[1:]:
        for h, v in other.crc.items():
            t.crc[h] = max(t.crc.get(h, 0), v)
        for k, v in other.names.items():
            t.names[k] = max(t.names.get(k, 0), v)
    for path, size in sorted(build.SIZES.items()):
        assert t.get(path) is not None, path
        t.ensure(path, size)
    stock = open(os.path.join(build.ROMFS, rstb.PATH), "rb").read()
    build._write_raw(MOD, rstb.PATH, nxpack.zs_compress_like(t.to_bytes(), stock))
    back = rstb.Rstb(nxfmt.read(os.path.join(build.BUILD, MOD, "romfs", *rstb.PATH.split("/"))))
    for other in tables:
        assert all(back.crc.get(h, 0) >= v for h, v in other.crc.items())
        assert all(back.names.get(k, 0) >= v for k, v in other.names.items())
    assert all(back.get(path) >= size for path, size in build.SIZES.items())
    return len(tables)


def build_folder():
    B = build
    B.BUILD, B.SIZES, B.EXE_PATCHES = BUILD_LABEL, {}, {}
    shutil.rmtree(os.path.join(BUILD_LABEL, MOD), ignore_errors=True)
    os.makedirs(BUILD_LABEL, exist_ok=True)
    folders = installed()
    print("%s, over the %d installed folders (%s)" % (MOD, len(folders), ", ".join(folders)))
    old, new = write_layout()
    owner, nodes = write_graph()
    p, code, sha1 = make_patch()
    B.write_ips(MOD, p)
    runs = check_patch(code)
    others = check_installed_patches()
    tables = write_table()
    return [
        "  layout %s: %s's reference '*' -> '@%s' (%d -> %d bytes)" % (ui.LAYOUT, PANE, KEY, old, new),
        "  graph: from '%s', %d nodes + a sequence (one frame, then %s) in the page's entry" % (owner, nodes, CODE_NODE),
        "  code: %d bytes in the one-shot body of the unused %s (%x-%x, %d left; sha1 %s); %d runs emulated;"
        " no byte shared with the %d installed patches" % (len(code), CODE_NODE, CODE[0], CODE[1], CODE[1] - CODE[0] - len(code),
                                                          sha1, runs, others),
        "  size table: the %d installed tables merged, ours raised: %s" % (tables, ", ".join(sorted(B.SIZES))),
        "  text: %r on %s of screen %s" % (TEXT, PANE, SCREEN),
    ]


def main():
    B = build
    install, remove = "--install" in sys.argv, "--remove" in sys.argv
    if (install or remove) and B.emulator_running():
        print("The emulator is running (%s). Close it first." % ", ".join(B.emulator_running()))
        sys.exit(1)
    with modstate.building(SESSION):
        dst = os.path.join(B.EDEN_LOAD, MOD)
        old = [n for n in OLD_NAMES if os.path.isdir(os.path.join(B.EDEN_LOAD, n))]
        for n in old:
            shutil.rmtree(os.path.join(B.EDEN_LOAD, n))
        if remove:
            there = os.path.isdir(dst)
            shutil.rmtree(dst, ignore_errors=True)
            if there or old:
                modstate.stamp(SESSION, B.EDEN_LOAD, [], removed=[MOD] * there + old, note="the label test taken out")
            print("%s: %s" % (MOD, "removed" if there else "was not installed"))
            return
        print("\n".join(build_folder()))
        if install:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(os.path.join(BUILD_LABEL, MOD), dst)
            modstate.stamp(SESSION, B.EDEN_LOAD, [MOD], removed=old, note="laid over the %d folders installed then" % len(installed()))
            print("  installed ->", dst)


if __name__ == "__main__":
    main()
