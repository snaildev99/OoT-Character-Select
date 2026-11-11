"""Field swap (its own session, 2026-11-10): the character picked on the Characters page
becomes the field Link when the menu closes, without save + return to title + Continue.

    venv\\Scripts\\python.exe mods\\field_swap.py             build into build-field\\ and check
    venv\\Scripts\\python.exe mods\\field_swap.py --install   also install into the field swap
        emulator copy only (tools\\fieldenv.py, PLAY-debug-field.bat)

The normal build (mods\\build.py, the full build: picker, 100 characters, slide) is used as
a library and built into build-field\\ exactly as it builds itself; build\\ and the normal
mods folder are not touched. On top of it:

1. How the game itself changes the player (README, field swap section). The field Link is a
   "resident actor". The resident actor manager has three lists (Scene/Component/
   ResidentActorMgrParam/Default): Common (cameras, Navi, ...), Child (Player_Child) and
   Adult (Player_Adult). "Create resident actors" (0x80965610, the node
   SeqExecuteSceneRequestCreateResidentActors) first deletes the other age's list, then
   creates whatever is missing: that is how an age change swaps the player while every
   other resident actor stays, asleep, during an area load. "Cancel sleep" (0x80963550,
   SeqExecuteSceneRequestCancelSleepResidentActors) then wakes them at a position and
   rotation.
2. The swap here is that path without the area load: sleep the residents, delete the
   player of the current age, run the game's create node (it finds the player missing and
   makes a new one, whose model path goes through the model hook = the stored character),
   wake everybody where the old player stood.
3. Two things the game has no node for are code: "delete the player of the current age"
   and "wake at the place he stood" (no node reads a rotation). All of it lives in code no
   stock graph can reach, so no live game code changes: the sequence node class NODE, which
   no graph uses, gets new enter and update routines, and its one Bool input says what an
   instance does:
     false  remember the player's position and rotation (actor + 0x31c, 48 bytes) and ask
            both age lists to go (the game's own two routines); finished when they are gone
     true   the game's cancel-sleep routine with the remembered position and rotation;
            finished when it says all are awake. If nobody was remembered (no delete ran),
            whoever is the player now is woken where he is: nothing can be left asleep.
   The routines sit in the body of another unused node's function (DEAD), the remembered
   transform in the unused end of main's last .bss page (stash_address).
4. Sequence/MinusMenuToMain.module (the menu closing): while PENDING_FLAG is set (the
   picker's A sets it), fade to black over the menu before it closes, do the swap after
   the menu's pause is released (under the area-change pause, as the game does), fade in.
   When nothing is pending the file runs frame for frame as the normal build's.

Not handled yet: a swap while riding (child saves cannot ride), a pending swap is also run
when A picked the character the field Link already is.
"""
import hashlib
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build  # noqa: E402  (the normal mods, used as a library)
import ainbx  # noqa: E402
import byml  # noqa: E402
import exepatch  # noqa: E402
import fieldenv  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402

BUILD_FIELD = os.path.join(ROOT, "build-field")
# Our folder: the code patch and the one graph that uses it, together, so that they can only
# be on or off together (with the patch alone off, the node class we take over would do its
# own job in our graph). Mod folders are layered in name order and the first folder that has
# a file wins (README, `Character layout (test)`): this name sorts before the normal build's
# folders, whose MinusMenuToMain.module stays the normal one. Switched off in the emulator's
# Add-Ons list, everything is the normal build (the picker still sets PENDING_FLAG, which
# nothing reads then; the fade screen's row is never opened).
MOD = "Character field swap"
PENDING_FLAG = "CharacterMenu_FieldSwapPending"     # Bool, not saved: the picker stored a character since the last field swap
MENU_TO_MAIN = "Sequence/MinusMenuToMain.module.ainb"

# The fade: a screen of our own with the game's fade layout (the NPC session's recipe:
# no class, draw unit 1; LoadingFade_00 is 9000 there, the header 8749).
FADE_SCREEN = "CharacterFade_00"
FADE_LAYOUT = "EventFade_00"
FADE_ROW = {"DrawUnitId": 1, "SortKey": 9100}
FADE_IN, FADE_IN_FRAMES = "VeryShortIn", 15         # to black, over the open menu
FADE_OUT, FADE_OUT_FRAMES = "ShortOut", 30          # back to the game

# ---------------------------------------------------------------- the game's routines
RESIDENT_HOLDER = 0x80965110        # (node) -> the scene's component holder
RESIDENT_MGR = 0x80965170           # (holder) -> the resident actor manager, 0 if the scene has none
DELETE_ADULT_LIST = 0x809654c0      # (manager): ask the adult list's actors to go; 1 = all gone
DELETE_CHILD_LIST = 0x832cb8d0      # (manager): the same for the child list
CANCEL_SLEEP = 0x80963550           # (manager, &position, &rotation 3x3): 1 = everybody is awake
CREATE_RESIDENTS = 0x80965610       # (manager, is child): what the stock create node runs
# manager: +0x28 / +0x30 common list (count, entries), +0x38 / +0x40 child, +0x48 / +0x50 adult,
# +0x70 "the player is the child one" (set by create). Entry (0x70): +0x8 link, +0x10 handle id
# (-1 none); the actor is [link + 0x40] while [link + 0x48] == id; actor +0x2c state (7..11 =
# going away), +0x31c position, +0x328 rotation 3x3.
ACTOR_TRANSFORM = 0x31C
ACTOR_STATE = 0x2C
NODE_STATE = 0x18                   # bits 30-31: 2 = finished (what every Execute node sets)
NODE_INPUT = 0x20                   # -> the node's first input value (as the stock create node reads its Bool)

# The node class we take over. What made it the one (2026-11-10, all checked at build):
# no stock graph uses it; it can be created (a creator routine sets its vtable and the node
# factory takes that routine's address: classes of the 0x83bd.... family, such as
# SeqExecuteWaitGlobalResidentEventLoad, have vtables but no creator at all); its enter and
# update routines are its own (nothing else points at them); one Bool input, registered by
# its own parameter routine with the same call the stock create node uses for its Bool.
NODE = "SeqExecuteWaitInitializeEventMgr"
NODE_INPUT_NAME = "IsNeedWaitResidentEvent"
NODE_ENTER, NODE_UPDATE, NODE_PARAMS, NODE_CREATOR = 0x832e74c0, 0x832e74d0, 0x832e7520, 0x832e7470
REGISTER_INPUT = 0x803f94c0         # (node, &name, &value pointer, 1, 0): how both register a Bool
STOCK_BOOL_PARAMS = 0x80d39ce0      # the stock create node's parameter routine (its vtable + 0xe0): one Bool, IsCreateChildPlayer
MODE_DELETE, MODE_WAKE = False, True
# Where the routines go: the one-shot body of another unused node (0x300 bytes, one
# function, referenced by its vtable only).
DEAD_NODE = "SeqOneShotSetGameDataAppearScarecrowFalseAll"
DEAD = (0x83302000, 0x83302300)
DEAD_SHA1 = "ffbcf0221b94fec67baf0da890948d53007be589"     # its bytes in game v1.1.1 (checked at build)
VT_NAME, VT_PARAMS, VT_ENTER, VT_UPDATE, VT_ONESHOT = 0x98, 0xE0, 0xE8, 0xF0, 0x138
RET, RETAB = 0xD65F03C0, 0xD65F0FFF


# ---------------------------------------------------------------- the code patch

def _image_facts(img):
    """main's .bss end (MOD0) and the unused rest of its last page: mapped, writable, zero."""
    mod0 = exepatch.MAIN + struct.unpack_from("<I", img.data, exepatch.MAIN + 4 - img.base)[0]
    magic, _dyn, _bss0, bss1 = struct.unpack_from("<4s3i", img.data, mod0 - img.base)
    assert magic == b"MOD0"
    bss_end = mod0 + bss1
    return bss_end, (bss_end + 0xFFF) & ~0xFFF


STASH_SIZE = 0x80
ST_VALID, ST_DELETE_CALLS, ST_POS, ST_ROT, ST_MGR, ST_ACTOR, ST_WAKE_CALLS, ST_SWAPS = \
    0x00, 0x04, 0x08, 0x14, 0x38, 0x40, 0x48, 0x4C


def stash_address(img):
    """STASH_SIZE bytes at the very end of main's last .bss page. What the swap needs:
    +0 valid (u32), +8 position (3 floats), +0x14 rotation (9 floats). For reading through
    the debug server (tools\\fieldlive.py), written and never read by the game:
    +4 calls in delete mode, +0x38 the resident actor manager, +0x40 the player actor that
    was remembered last, +0x48 calls in wake mode, +0x4c wakes that finished."""
    bss_end, page_end = _image_facts(img)
    stash = page_end - STASH_SIZE
    assert bss_end + 0x100 <= stash, "no spare room after .bss"
    assert img.data[stash - img.base:page_end - img.base] == b"\0" * STASH_SIZE
    return stash


def _vtable(img, name):
    """The vtable of the sequence node class `name` (its name getter is slot VT_NAME)."""
    import numpy as np
    q = np.frombuffer(img.data[:len(img.data) // 8 * 8], dtype="<u8")
    found = []
    for s in img.strings(name):
        for x in img.xrefs(s):
            if img.u32(x + 8) == RET:                   # adrp, add, ret: the getter
                found += [img.base + int(i) * 8 - VT_NAME for i in np.nonzero(q == x)[0]]
    assert len(found) == 1, (name, found)
    return found[0]


def _slot(img, vt, off):
    return struct.unpack_from("<Q", img.data, vt + off - img.base)[0]


def check_dead_code(img):
    """The code we overwrite is what we developed against, belongs to the node classes we
    name, and nothing but those classes' vtables refers to it; our node class can be
    created and has the one Bool input we use. That no graph uses the two classes is
    checked by check_graphs. Returns the address ranges we may write to."""
    import numpy as np
    assert hashlib.sha1(img.data[DEAD[0] - img.base:DEAD[1] - img.base]).hexdigest() == DEAD_SHA1, \
        "the unused function is not the one this patch was written for"
    vt = _vtable(img, NODE)
    assert [_slot(img, vt, o) for o in (VT_PARAMS, VT_ENTER, VT_UPDATE, VT_ONESHOT)] == \
        [NODE_PARAMS, NODE_ENTER, NODE_UPDATE, 0]
    # it can be created: the creator stores this vtable, and the factory takes the creator's address
    assert [img.func_start(r) for r in img.xrefs(vt)] == [NODE_CREATOR] and len(img.xrefs(NODE_CREATOR)) == 1
    # its parameter routine registers one Bool by name, into node + NODE_INPUT, exactly as the
    # routine of the create node (whose Bool the game reads as [[node + 0x20]]) does
    def params(f):
        strs, calls = img.summary(f, f + 0x40)
        body = [l.split(": ")[1] for l in img.dis(f, 15)]
        return [s for _a, s in strs], [c for _a, c in calls], [l for l in body if "adrp" not in l and not l.startswith("add      x8")]
    assert _slot(img, _vtable(img, "SeqExecuteSceneRequestCreateResidentActors"), VT_PARAMS) == STOCK_BOOL_PARAMS
    ours, stock = params(NODE_PARAMS), params(STOCK_BOOL_PARAMS)
    assert ours[0] == [NODE_INPUT_NAME] and stock[0] == ["IsCreateChildPlayer"], (ours[0], stock[0])
    assert ours[1] == stock[1] == [REGISTER_INPUT] and ours[2] == stock[2], "the Bool is registered another way"
    assert "add      x2, x0, #0x%x" % NODE_INPUT in ours[2]
    assert img.u32(NODE_ENTER) == RET, "the node's enter is not the empty one"
    owners = {NODE_ENTER: vt + VT_ENTER, NODE_UPDATE: vt + VT_UPDATE}
    dead_vt = _vtable(img, DEAD_NODE)
    assert _slot(img, dead_vt, VT_ONESHOT) == DEAD[0]
    assert img.u32(DEAD[0]) == img.PACIBSP and img.u32(DEAD[1] - 4) == RETAB       # one function: pacibsp .. retab
    assert not any(img.u32(a) == img.PACIBSP for a in range(DEAD[0] + 4, DEAD[1], 4))
    regions = [(DEAD[0], DEAD[1], {dead_vt + VT_ONESHOT: DEAD[0]})]
    for f in (NODE_ENTER, NODE_UPDATE):     # each up to its own return
        end = f
        while img.u32(end) not in (RET, RETAB):
            end += 4
        regions.append((f, end + 4, {owners[f]: f}))
    w = img.words.astype(np.int64)
    pc = img.base + np.arange(len(w), dtype=np.int64) * 4
    jumps = []
    for mask, value, shift, bits in ((0x7C000000, 0x14000000, 0, 26), (0xFF000010, 0x54000000, 5, 19),
                                     (0x7E000000, 0x34000000, 5, 19), (0x7E000000, 0x36000000, 5, 14)):
        m = (w & mask) == value
        imm = (w >> shift) & ((1 << bits) - 1)
        imm = np.where(imm & (1 << (bits - 1)), imm - (1 << bits), imm)
        jumps.append((pc[m], (pc + imm * 4)[m]))
    q = np.frombuffer(img.data[:len(img.data) // 8 * 8], dtype="<u8")
    img._build_refs()
    rt, _rs = img._refs
    for start, end, pointers in regions:
        for src, dst in jumps:
            into = (dst >= start) & (dst < end) & ((src < start) | (src >= end))
            assert not into.any(), "code outside jumps into %x-%x: %s" % (start, end, [hex(int(s)) for s in src[into]])
        sites = {img.base + int(i) * 8: int(q[i]) for i in np.nonzero((q >= start) & (q < end))[0]}
        assert sites == pointers, "unexpected pointers into %x-%x: %s" % (start, end, sites)
        assert not ((rt >= start) & (rt < end)).any(), "address of %x-%x taken in code" % (start, end)
    return [(s, e) for s, e, _ in regions]


# KEEP_CAMERA only (README, last section, "The camera after a swap"): the player camera puts
# itself behind its target whenever the target's camera part (PlayerCameraTarget component,
# [[player + 0x270] + 0x368]) is another object than the one it remembers from its last
# update (camera component + 0xfe0; 0x8039b570). Re-picking the same character keeps the
# camera only because the new player lands at the old address. fs_keep writes the new, still
# sleeping player's part into that slot right before the wake (the camera does not update
# under the swap's pause), so every swap looks like that case. (Bit 1 of the part's byte
# +0x24c also skips the reset, but the part rewrites that byte every frame: tried, lost.)
# The camera: the camera system object [0x84e40c08], its actor handle at +0x38, the actor's
# component [[actor + 0x270] + 0x360]. A leaf: x20 = manager; changes x8-x11, x15.
ACTOR_COMPONENTS, COMPONENT_CAMERA_TARGET, COMPONENT_CAMERA, CAMERA_LAST_TARGET = 0x270, 0x368, 0x360, 0xFE0
CAMERA_SYSTEM = 0x84E40C08
KEEP_CODE = """
    fs_keep:
        ldrb w8, [x20, #0x70]
        add x9, x20, #0x38
        add x10, x20, #0x48
        cmp w8, #1
        csel x9, x9, x10, eq
        ldr w10, [x9]
        cbz w10, fk_out
        ldr x9, [x9, #8]
        cbz x9, fk_out
        ldr w10, [x9, #0x10]
        cmn w10, #1
        b.eq fk_out
        ldr x11, [x9, #8]
        cbz x11, fk_out
        ldr w15, [x11, #0x48]
        cmp w15, w10
        b.ne fk_out
        ldr x11, [x11, #0x40]
        cbz x11, fk_out
        ldr x11, [x11, #0x270]
        cbz x11, fk_out
        ldr x11, [x11, #0x368]
        cbz x11, fk_out
        adrp x9, #0x84e40000
        ldr x9, [x9, #0xc08]
        cbz x9, fk_out
        ldr w10, [x9, #0x48]
        cmn w10, #1
        b.eq fk_out
        ldr x9, [x9, #0x40]
        cbz x9, fk_out
        ldr w15, [x9, #0x48]
        cmp w15, w10
        b.ne fk_out
        ldr x9, [x9, #0x40]
        cbz x9, fk_out
        ldr x9, [x9, #0x270]
        cbz x9, fk_out
        ldr x9, [x9, #0x360]
        cbz x9, fk_out
        str x11, [x9, #0xfe0]
    fk_out:
        ret"""


def field_patch():
    """The Patcher: our routine in DEAD, NODE's enter and update turned into jumps to it."""
    img = exepatch.Image()
    regions = check_dead_code(img)
    stash = stash_address(img)
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = DEAD
    try:
        p = exepatch.Patcher(image=img, cave=DEAD)       # the signature words go to DEAD's start
    finally:
        exepatch.CAVE_REGION = region

    def place(name, addr, blob):
        """As Patcher._place, but over the unused function's code instead of zeros."""
        assert DEAD[0] <= addr and addr + len(blob) <= DEAD[1], "the unused function is full"
        p.raw(addr, blob)
        p.symbols[name] = addr
        p.cave_ptr = addr + len(blob)
        return addr
    p._place = place
    assert (ST_VALID, ST_POS, ST_ROT) == (0, 8, 0x14)       # the offsets written out in the code below
    # Entered like any node routine: x0 = the node. enter clears the state bits first (as
    # the stock create node's enter does), update leaves a finished node alone (as every
    # stock update does). No pointer authentication of our own (plain ret); the callees are
    # whole game functions. fs_stash is ours and a leaf: x0 = manager, x21 = the stash; it
    # changes x8-x15 only.
    p.cave("fs_enter", """
    fs_enter:
        ldr w8, [x0, #{ns:#x}]
        and w8, w8, #0x3fffffff
        str w8, [x0, #{ns:#x}]
        b fs_run
    fs_update:
        ldr w8, [x0, #{ns:#x}]
        lsr w8, w8, #30
        cmp w8, #2
        b.ne fs_run
        ret
    fs_run:
        stp x29, x30, [sp, #-0x30]!
        stp x20, x19, [sp, #0x10]
        stp x22, x21, [sp, #0x20]
        mov x29, sp
        mov x19, x0
        bl #{holder:#x}
        bl #{mgr:#x}
        cbz x0, fs_finish
        mov x20, x0
        adrp x21, #{page:#x}
        add x21, x21, #{lo:#x}
        str x20, [x21, #{s_mgr:#x}]
        ldr x8, [x19, #{ni:#x}]
        ldrb w8, [x8]
        cbnz w8, fs_wake
        ldr w9, [x21, #{s_dcalls:#x}]
        add w9, w9, #1
        str w9, [x21, #{s_dcalls:#x}]
        mov x0, x20
        bl fs_stash
        mov x0, x20
        bl #{adult:#x}
        mov w22, w0
        mov x0, x20
        bl #{child:#x}
        and w0, w0, w22
        tbz w0, #0, fs_out
        b fs_finish
    fs_wake:
        ldr w9, [x21, #{s_wcalls:#x}]
        add w9, w9, #1
        str w9, [x21, #{s_wcalls:#x}]
        ldr w9, [x21]
        cbnz w9, fs_wake_go
        mov x0, x20
        bl fs_stash
        ldr w9, [x21]
        cbz w9, fs_finish
    fs_wake_go:{keep_call}
        mov x0, x20
        add x1, x21, #8
        add x2, x21, #0x14
        bl #{wake:#x}
        tbz w0, #0, fs_out
        str wzr, [x21]
        ldr w9, [x21, #{s_swaps:#x}]
        add w9, w9, #1
        str w9, [x21, #{s_swaps:#x}]
    fs_finish:
        ldr w8, [x19, #{ns:#x}]
        movz w9, #0x8000, lsl #16
        bfxil w9, w8, #0, #0x1e
        str w9, [x19, #{ns:#x}]
    fs_out:
        ldp x22, x21, [sp, #0x20]
        ldp x20, x19, [sp, #0x10]
        ldp x29, x30, [sp], #0x30
        ret
    fs_stash:
        ldrb w8, [x0, #0x70]
        add x9, x0, #0x38
        add x10, x0, #0x48
        cmp w8, #1
        csel x9, x9, x10, eq
        ldr w10, [x9]
        cbz w10, fss_out
        ldr x9, [x9, #8]
        cbz x9, fss_out
        ldr w10, [x9, #0x10]
        cmn w10, #1
        b.eq fss_out
        ldr x11, [x9, #8]
        cbz x11, fss_out
        ldr w15, [x11, #0x48]
        cmp w15, w10
        b.ne fss_out
        ldr x11, [x11, #0x40]
        cbz x11, fss_out
        ldr w15, [x11, #{st:#x}]
        sub w15, w15, #7
        cmp w15, #5
        b.lo fss_out
        str x11, [x21, #{s_actor:#x}]
        add x11, x11, #{tr:#x}
        ldp x13, x14, [x11]
        stp x13, x14, [x21, #8]
        ldp x13, x14, [x11, #0x10]
        stp x13, x14, [x21, #0x18]
        ldp x13, x14, [x11, #0x20]
        stp x13, x14, [x21, #0x28]
        mov w13, #1
        str w13, [x21]
    fss_out:
        ret{keep_code}
    """.format(keep_call="\n        bl fs_keep" if KEEP_CAMERA else "", keep_code=KEEP_CODE if KEEP_CAMERA else "",
               holder=RESIDENT_HOLDER, mgr=RESIDENT_MGR, adult=DELETE_ADULT_LIST, child=DELETE_CHILD_LIST,
               wake=CANCEL_SLEEP, page=stash & ~0xFFF, lo=stash & 0xFFF, tr=ACTOR_TRANSFORM, st=ACTOR_STATE,
               ns=NODE_STATE, ni=NODE_INPUT, s_mgr=ST_MGR, s_actor=ST_ACTOR, s_dcalls=ST_DELETE_CALLS,
               s_wcalls=ST_WAKE_CALLS, s_swaps=ST_SWAPS))
    start = p.symbols["fs_enter"]
    code = dict(p.patches)[start]
    UPDATE, RUN = 4 * 4, 4 * 9                  # where the next two labels are
    assert [i.mnemonic for i in img.cs.disasm(code[:RUN + 4], 0)] == \
        ["ldr", "and", "str", "b", "ldr", "lsr", "cmp", "b.ne", "ret", "stp"]
    p.symbols["fs_update"] = start + UPDATE
    for f, to in ((NODE_ENTER, "fs_enter"), (NODE_UPDATE, "fs_update")):
        p.raw(f, exepatch.assemble("b #%#x" % p.symbols[to], f), expect=img.data[f - img.base:f - img.base + 4])
    assert p.cave_ptr <= DEAD[1]
    for a, b in p.ranges():             # nothing we write is outside the code checked above
        assert any(s <= a and b <= e for s, e in regions), hex(a)
    p.stash = stash
    return p


def check_patch(p):
    """Our routine in an ARM64 emulator (unicorn) against a resident actor manager laid out
    as the game's, the game's routines replaced by stubs that record their arguments,
    return what the test says and scribble over every register a real call may change.
    Checked: what is called with what and in which order, what ends up in the stash, when
    the node is marked finished, and that the callee-saved registers and the stack come
    back as they were. Returns the number of runs."""
    import unicorn
    from unicorn import arm64_const as A
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    mapped = set()

    def need(addr, size=1):
        for pg in range(addr & ~0xFFF, (addr + size + 0xFFF) & ~0xFFF, 0x1000):
            if pg not in mapped:
                mu.mem_map(pg, 0x1000)
                mapped.add(pg)

    for addr, data in p.patches:
        need(addr, len(data))
        mu.mem_write(addr, bytes(data))
    stubs = {RESIDENT_HOLDER: "holder", RESIDENT_MGR: "mgr", DELETE_ADULT_LIST: "adult",
             DELETE_CHILD_LIST: "child", CANCEL_SLEEP: "wake"}
    STOP = 0x30000000
    for a in list(stubs) + [STOP]:
        need(a, 4)
        mu.mem_write(a, struct.pack("<I", RET))
    STACK, HEAP = 0x20000000, 0x12000000
    mu.mem_map(STACK, 0x10000)
    mu.mem_map(HEAP, 0x10000)
    need(p.stash, STASH_SIZE)
    NODE_AT, HOLDER, MGR, LISTS, LINK, ACTOR, INPUT = (HEAP + o for o in (0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x3000))
    calls, answers = [], {}
    scratch = [A.UC_ARM64_REG_X0 + i for i in range(18)]
    saved = [A.UC_ARM64_REG_X19 + i for i in range(10)] + [A.UC_ARM64_REG_X29]

    def on_code(uc, address, size, data):
        name = stubs[address]
        x0, x1, x2 = (uc.reg_read(A.UC_ARM64_REG_X0 + i) for i in range(3))
        calls.append((name, x0) if name != "wake" else (name, x0, x1, x2))
        for r in scratch:                           # what a real call may destroy
            uc.reg_write(r, 0xDEAD0000DEAD0000)
        uc.reg_write(A.UC_ARM64_REG_X0, answers[name])

    for a in stubs:
        mu.hook_add(unicorn.UC_HOOK_CODE, on_code, begin=a, end=a)

    def manager(is_child, alive=True, state=4, ident=0x1234):
        """The manager with one player in the list of his age; the other age's entry empty."""
        mu.mem_write(MGR, b"\0" * 0x80)
        mu.mem_write(LISTS, b"\0" * 0x200)
        child, adult = LISTS, LISTS + 0x80
        mu.mem_write(MGR + 0x38, struct.pack("<IIQ", 1, 0, child))
        mu.mem_write(MGR + 0x48, struct.pack("<IIQ", 1, 0, adult))
        mu.mem_write(MGR + 0x70, bytes([is_child]))
        for entry in (child, adult):
            mu.mem_write(entry + 8, struct.pack("<Qi", LINK + 0x100, -1))
        mine = child if is_child else adult
        mu.mem_write(mine + 8, struct.pack("<Qi", LINK, ident if alive else -1))
        mu.mem_write(LINK + 0x40, struct.pack("<QI", ACTOR, ident))
        mu.mem_write(ACTOR + ACTOR_STATE, struct.pack("<I", state))

    FINISHED = 0x80000000
    count = [0]

    def start(entry, mode, node_state):
        mu.mem_write(NODE_AT, b"\0" * 0x28)
        mu.mem_write(NODE_AT + NODE_STATE, struct.pack("<I", node_state))
        mu.mem_write(NODE_AT + NODE_INPUT, struct.pack("<Q", INPUT))
        mu.mem_write(INPUT, bytes([mode]))
        del calls[:]
        sp = STACK + 0x8000
        for i, r in enumerate(saved):
            mu.reg_write(r, 0x5A5A000000000000 + i)
        mu.reg_write(A.UC_ARM64_REG_SP, sp)
        mu.reg_write(A.UC_ARM64_REG_X0, NODE_AT)
        mu.reg_write(A.UC_ARM64_REG_X30, STOP)
        mu.emu_start(entry, STOP, count=400)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == STOP, hex(mu.reg_read(A.UC_ARM64_REG_PC))
        assert mu.reg_read(A.UC_ARM64_REG_SP) == sp
        assert [mu.reg_read(r) for r in saved] == [0x5A5A000000000000 + i for i in range(len(saved))]
        count[0] += 1
        return struct.unpack("<I", bytes(mu.mem_read(NODE_AT + NODE_STATE, 4)))[0]

    def run(entry, mode):
        """One call of a node routine; True if it left the node finished. An enter is given a
        node that still says "finished" from an earlier run, an update a running one."""
        before = 0x12345 | (FINISHED if entry == NODE_ENTER else 0)
        state = start(entry, mode, before)
        assert state & 0x3FFFFFFF == 0x12345 and state >> 30 in (0, 2), hex(state)
        return state >> 30 == 2

    def idle(mode):
        """An update of a node that is finished already: nothing is called, nothing changes."""
        before = bytes(mu.mem_read(p.stash, STASH_SIZE))
        assert start(NODE_UPDATE, mode, FINISHED | 0x777) == FINISHED | 0x777 and calls == []
        assert bytes(mu.mem_read(p.stash, STASH_SIZE)) == before

    def stash():
        raw = bytes(mu.mem_read(p.stash, STASH_SIZE))
        valid, d_calls = struct.unpack_from("<II", raw, 0)
        mgr, actor, w_calls, swaps = struct.unpack_from("<QQII", raw, ST_MGR)
        assert raw[ST_SWAPS + 4:] == b"\0" * (STASH_SIZE - ST_SWAPS - 4)
        return dict(valid=valid, place=raw[ST_POS:ST_MGR], mgr=mgr, actor=actor, deletes=d_calls, wakes=w_calls, swaps=swaps)

    def clear():
        mu.mem_write(p.stash, b"\0" * STASH_SIZE)

    def transform(seed):
        t = struct.pack("<12f", *[seed + 0.25 * i for i in range(12)])
        mu.mem_write(ACTOR + ACTOR_TRANSFORM, t)
        return t

    answers.update(holder=HOLDER, mgr=MGR)
    nowhere = b"\0" * 48
    D, W = MODE_DELETE, MODE_WAKE
    for is_child in (1, 0):
        asked = [("holder", NODE_AT), ("mgr", HOLDER), ("adult", MGR), ("child", MGR)]
        woken = [("holder", NODE_AT), ("mgr", HOLDER), ("wake", MGR, p.stash + ST_POS, p.stash + ST_ROT)]
        gone = ("adult", "child") if is_child else ("child", "adult")      # (never there, the player's)
        # frame 1: the player is there (asleep, as the sequence leaves him): remembered, asked to go
        clear()
        manager(is_child, state=6)
        t1 = transform(1.5 + is_child)
        answers.update({gone[0]: 1, gone[1]: 0})
        assert not run(NODE_ENTER, D) and calls == asked
        assert stash() == dict(valid=1, place=t1, mgr=MGR, actor=ACTOR, deletes=1, wakes=0, swaps=0)
        # frame 2: going away, somewhere else by now: the stash keeps the first place
        manager(is_child, state=8)
        transform(77.0)
        assert not run(NODE_UPDATE, D) and calls == asked
        assert stash() == dict(valid=1, place=t1, mgr=MGR, actor=ACTOR, deletes=2, wakes=0, swaps=0)
        # frame 3: gone; the other list still busy (cannot happen, but only both = finished)
        manager(is_child, alive=False)
        answers.update({gone[0]: 0, gone[1]: 1})
        assert not run(NODE_UPDATE, D) and calls == asked
        answers.update({gone[0]: 1, gone[1]: 1})
        assert run(NODE_UPDATE, D) and calls == asked
        assert stash() == dict(valid=1, place=t1, mgr=MGR, actor=ACTOR, deletes=4, wakes=0, swaps=0)
        idle(D)
        # the new player is there, at the origin; the wake uses the remembered place until
        # the game says all are awake; then the stash is used up
        manager(is_child, state=6, ident=0x2222)
        transform(0.0)
        answers["wake"] = 0
        assert not run(NODE_ENTER, W) and calls == woken
        assert not run(NODE_UPDATE, W) and calls == woken
        assert stash() == dict(valid=1, place=t1, mgr=MGR, actor=ACTOR, deletes=4, wakes=2, swaps=0)
        answers["wake"] = 1
        assert run(NODE_UPDATE, W) and calls == woken
        assert stash() == dict(valid=0, place=t1, mgr=MGR, actor=ACTOR, deletes=4, wakes=3, swaps=1)
        idle(W)
        # a wake although nobody was remembered (the delete never ran): the player who is
        # there is woken where he is
        clear()
        manager(is_child, state=6)
        t2 = transform(9.0)
        answers["wake"] = 0
        assert not run(NODE_ENTER, W) and calls == woken
        assert stash() == dict(valid=1, place=t2, mgr=MGR, actor=ACTOR, deletes=0, wakes=1, swaps=0)
        answers["wake"] = 1
        assert run(NODE_UPDATE, W) and calls == woken
        assert stash() == dict(valid=0, place=t2, mgr=MGR, actor=ACTOR, deletes=0, wakes=2, swaps=1)
        # a stale handle (the id is someone else's now), an empty list, a player already
        # going away: nothing remembered; and then no wake either, the node just ends
        for breakage in ("id", "count", "state"):
            clear()
            manager(is_child, state=9 if breakage == "state" else 4)
            transform(5.0)
            if breakage == "id":
                mu.mem_write(LINK + 0x48, struct.pack("<I", 0x9999))
            if breakage == "count":
                mu.mem_write(MGR + (0x38 if is_child else 0x48), struct.pack("<I", 0))
            answers.update(adult=1, child=1)
            assert run(NODE_ENTER, D) and calls == asked, breakage
            assert stash() == dict(valid=0, place=nowhere, mgr=MGR, actor=0, deletes=1, wakes=0, swaps=0), breakage
            assert run(NODE_ENTER, W) and calls == woken[:2], breakage
            assert stash() == dict(valid=0, place=nowhere, mgr=MGR, actor=0, deletes=1, wakes=1, swaps=0), breakage
    # KEEP_CAMERA: every wake call writes the camera part of the player who is there into the
    # camera's "last target" slot, and nothing else; a player without components or without
    # that part, no camera system, a stale camera handle, a camera without components or
    # without its component: nothing is written
    COMPS, TARGET, SYS, CLINK, CACTOR, CCOMPS, CAM = (HEAP + o for o in (0x4000, 0x5000, 0x6000, 0x6800, 0x7000, 0x7800, 0x8000))
    need(CAMERA_SYSTEM, 8)
    for is_child in (1, 0):
        for case in ("all there", "no part", "no components", "no system", "stale camera", "no camera components", "no camera"):
            clear()
            manager(is_child, state=6)
            mu.mem_write(HEAP + 0x4000, b"\0" * 0x6000)
            mu.mem_write(ACTOR + ACTOR_COMPONENTS, struct.pack("<Q", 0 if case == "no components" else COMPS))
            mu.mem_write(COMPS + COMPONENT_CAMERA_TARGET, struct.pack("<Q", 0 if case == "no part" else TARGET))
            mu.mem_write(CAMERA_SYSTEM, struct.pack("<Q", 0 if case == "no system" else SYS))
            mu.mem_write(SYS + 0x40, struct.pack("<QI", CLINK, 0x77))
            mu.mem_write(CLINK + 0x40, struct.pack("<QI", CACTOR, 0x78 if case == "stale camera" else 0x77))
            mu.mem_write(CACTOR + ACTOR_COMPONENTS, struct.pack("<Q", 0 if case == "no camera components" else CCOMPS))
            mu.mem_write(CCOMPS + COMPONENT_CAMERA, struct.pack("<Q", 0 if case == "no camera" else CAM))
            mu.mem_write(CAM + CAMERA_LAST_TARGET, struct.pack("<Q", 0x1111))
            before = bytes(mu.mem_read(HEAP + 0x4000, 0x6000))
            answers["wake"] = 1
            assert run(NODE_ENTER, W) and calls == woken, case
            want = bytearray(before)
            if KEEP_CAMERA and case == "all there":
                struct.pack_into("<Q", want, CAM + CAMERA_LAST_TARGET - HEAP - 0x4000, TARGET)
            assert bytes(mu.mem_read(HEAP + 0x4000, 0x6000)) == bytes(want), case
            mu.mem_write(ACTOR + ACTOR_COMPONENTS, struct.pack("<Q", 0))
    mu.mem_write(CAMERA_SYSTEM, struct.pack("<Q", 0))
    # a scene without a resident actor manager: the node ends at once and touches nothing
    answers["mgr"] = 0
    clear()
    mu.mem_write(p.stash, struct.pack("<I", 1))
    for entry in (NODE_ENTER, NODE_UPDATE):
        for mode in (D, W):
            assert run(entry, mode) and calls == [("holder", NODE_AT), ("mgr", HOLDER)]
            assert stash() == dict(valid=1, place=nowhere, mgr=0, actor=0, deletes=0, wakes=0, swaps=0)
    return count[0]


# ---------------------------------------------------------------- the graph

def swap_node(mode):
    """An instance of NODE: the stock create node (an Execute node with one Bool input) under
    NODE's name, its Bool a constant: MODE_DELETE or MODE_WAKE."""
    n = build.vanilla("LoadBancImpl", 35, "SeqExecuteSceneRequestCreateResidentActors")
    inp = n["Parameters"]["Inputs"]
    assert list(inp) == ["Bool"] and len(inp["Bool"]) == 1 and inp["Bool"][0]["Name"] == "IsCreateChildPlayer"
    assert n["Parameters"]["Outputs"] == {} and n["Properties"] == {} and n["Flags"] == []
    n["Name"] = NODE
    # a constant input as the stock graphs write one (LoadBancImpl node 43, IsDirect); never
    # the "left at the declared default" flag: this class has no declaration
    inp["Bool"] = [{"Name": NODE_INPUT_NAME, "Default Value": bool(mode), "Node Index": -1, "Output Index": 0,
                    "Flags": ["Uses Default"]}]
    const = build.vanilla("LoadBancImpl", 43, "SeqQueryGetNonStructGameDataBool")["Parameters"]["Inputs"]["Bool"][0]
    assert {k: const[k] for k in ("Node Index", "Output Index", "Flags")} == \
        {k: inp["Bool"][0][k] for k in ("Node Index", "Output Index", "Flags")}
    n["Queries"] = []
    n["Plugs"] = {}
    return n


def _pending(g, when_set):
    """One step: `when_set` if PENDING_FLAG is set, else nothing (ends at once)."""
    B = build
    q = g.add(B.vanilla("LoadBancImpl", 43, "SeqQueryGetNonStructGameDataBool", GameDataFullName=PENDING_FLAG))
    return g.add(B.t_bool_sel(q, 0, when_set, g.add(B.vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))))


def swap_steps(g):
    """The field swap, one step per frame (the caller's sequence has same_frame=False)."""
    B = build
    is_child = g.add(B.vanilla("LoadBancImpl", 19, "SeqQueryGetNonStructGameDataBool"))
    assert g.nodes[is_child]["Parameters"]["Inputs"]["String"][0]["Default Value"] == "Sequence_IsChildhood"
    create = B.vanilla("LoadBancImpl", 35, "SeqExecuteSceneRequestCreateResidentActors")
    inp = create["Parameters"]["Inputs"]["Bool"]
    assert len(inp) == 1 and inp[0]["Name"] == "IsCreateChildPlayer" and inp[0]["Node Index"] == 19
    assert create["Queries"] == [19] and create["Plugs"] == {"Generic": [{"Node Index": 19, "Name": "IsCreateChildPlayer"}]}
    inp[0]["Node Index"] = is_child
    create["Queries"] = [is_child]
    create["Plugs"]["Generic"][0]["Node Index"] = is_child
    pause = B.vanilla("ChangeBanc", 27, "SeqOneShotRequestPause")
    release = B.vanilla("ChangeBanc", 29, "SeqOneShotReleasePause")
    for n in (pause, release):
        assert [x["Default Value"] for x in n["Parameters"]["Inputs"]["String"]] == ["SceneTransition"]
    wait_actor = lambda: g.add(B.vanilla("LoadBancImpl", 0, "SeqExecuteWaitCreateActor"))      # noqa: E731
    # Passed in game with the sleep (snaildev, 2026-11-10): place and facing kept, but the wake
    # also puts the camera behind the character, as after a door (live: PlayerCamera woke at
    # the player's place and yaw). KEEP_CAMERA (the default now): nobody is put to sleep; the
    # wake only touches actors that are asleep, and the new player is created asleep, so the
    # cameras and Navi stay as they are (and fs_keep stops the camera's own reset).
    sleep = [] if KEEP_CAMERA else [
        g.add(B.vanilla("SleepResidentActor", 2, "SeqOneShotShooterResetShootableAll")),    # as SleepResidentActor.module
        g.add(B.vanilla("SleepResidentActor", 0, "SeqExecuteSceneRequestSleepResidentActors"))]
    return sleep + [
        g.add(pause),                         # the area-change pause: nothing else runs without a player
        g.add(swap_node(MODE_DELETE)),          # remember the place, the player goes
        g.add(create),                          # the game's own: the player is missing, make him
        wait_actor(),
        wait_actor(),
        g.add(swap_node(MODE_WAKE)),            # everybody awake where he stood
        g.add(B.vanilla("MinusMenuToMain", 1, "WaitEquip.module")),
        g.add(release),
    ]


_normal_close_page_with_menu = build.close_page_with_menu


def close_page_with_menu(mod, sizes):
    """The normal build's MinusMenuToMain.module, then: its root's two steps (wait for the
    equipment; close the menu and release the pause) each get a second part that runs in
    the same frame and does nothing unless PENDING_FLAG is set:
      after the wait:  our fade screen over the open menu, to black;
      after the close: the swap, the fade back, PENDING_FLAG off."""
    B = build
    _normal_close_page_with_menu(mod, sizes)        # the normal file stays in the normal folder
    g = B.Graph(os.path.join(B.BUILD, mod, "romfs", MENU_TO_MAIN))
    root = g.node(0, "", "Element_Sequential")
    assert [c["Node Index"] for c in root["Plugs"]["Child"]] == [1, 9]
    assert [p["Default Value"] for p in root["Properties"]["Bool"] if p["Name"] == "IsUpdateNextInFrame"] == [False]
    g.node(1, "WaitEquip.module")
    assert [c["Node Index"] for c in g.node(9, "", "Element_Simultaneous")["Plugs"]["Child"]] == [11, 2]
    g.node(2, "PlusMinusPauseOff.module")
    S = FADE_SCREEN
    visible = g.add(B.vanilla("MinusMenu", 22, "SeqOneShotSetScreenVisible", ScreenName=S, IsVisible=True))
    # the screen opens and gets its first animation requests in one frame, so it is never
    # seen in its layout's own state; the wait for the animation starts a frame later, when
    # the request has been carried out (requests are queued on the screen)
    black = g.add(B.t_sequential([
        g.add(B.t_sequential([
            g.add(B.vanilla("ActionGuide", 6, "SeqExecuteLoadAndOpenScreen", ScreenName=S)),
            g.add(B.t_simultaneous([visible, g.add(B.t_stop_anim(S, "Color", 0.0)),
                                    g.add(B.t_play_anim(S, FADE_IN))], root=False))], root=False)),
        g.add(B.t_wait_anim(S, FADE_IN)),
    ], root=False, same_frame=False))
    back = [g.add(B.t_play_anim(S, FADE_OUT)), g.add(B.t_wait_anim(S, FADE_OUT)),
            g.add(B.t_close_unload_screen(S)), g.add(B.t_set_flag(PENDING_FLAG, False))]
    swap = g.add(B.t_sequential(swap_steps(g) + back, root=False, same_frame=False))
    root["Plugs"]["Child"] = [
        {"Node Index": g.add(B.t_sequential([1, _pending(g, black)], root=False)), "Name": ""},
        {"Node Index": g.add(B.t_sequential([9, _pending(g, swap)], root=False)), "Name": ""}]
    out = g.build()
    B.write(MOD, MENU_TO_MAIN, out, len(out), sizes)        # ours: in our folder, which sorts first


_normal_add_page_screen = build.add_page_screen


def add_page_screen(mod, sizes):
    """The normal build's screen table plus the fade screen's row."""
    B = build
    _normal_add_page_screen(mod, sizes)
    path = os.path.join(B.BUILD, mod, "romfs", B.UISCREEN)
    raw = open(path, "rb").read()
    data = nxfmt.unzs(raw, B.UISCREEN)
    rows = byml.load(data)
    assert byml.dump(rows, byml.version_of(data)) == data
    assert not any(r["Name"] == FADE_SCREEN for r in rows)
    assert sum(r["BodyLayoutFile"] == FADE_LAYOUT for r in rows) == 3       # the game's three fade screens
    # above the menus, their header and the game's own fades (9000) and the box cursor (9002),
    # below the save icon (10000) and the talk window (11000)
    unit = sorted(r["SortKey"] for r in rows if r.get("DrawUnitId", 0) == FADE_ROW["DrawUnitId"])
    assert FADE_ROW["SortKey"] not in unit and [k for k in unit if k > FADE_ROW["SortKey"]] == [10000, 11000], unit
    rows.append(dict({"ArchiveCategory": ["Common"], "BodyLayoutFile": FADE_LAYOUT, "IsHeapReverse": True,
                      "Name": FADE_SCREEN}, **FADE_ROW))
    rows.sort(key=lambda r: r["Name"].encode())
    new = byml.dump(rows, byml.version_of(data))
    assert byml.load(new) == rows
    B.write(mod, B.UISCREEN, nxpack.zs_compress_like(new, raw), len(new), sizes)


_normal_swap_step_list = build._swap_step_list


def _swap_step_list(g, value, hide=True, **kw):
    """The normal build's swap of the menu character; the step after the store also sets
    PENDING_FLAG, in the frame that step starts (no frame added)."""
    steps = _normal_swap_step_list(g, value, hide, **kw)
    store = g.nodes[steps[0]]
    assert store["Name"] == "SeqOneShotSetNonStructGameDataInt" and len(steps) >= 2
    flag = g.add(build.t_set_flag(PENDING_FLAG, True))
    return [steps[0], g.add(build.t_simultaneous([flag, steps[1]], root=False))] + steps[2:]


# ---------------------------------------------------------------- checks on what was written

def _strings(n):
    return {x["Name"]: x.get("Default Value") for kind in ("String", "Float", "Bool", "Int")
            for x in n.get("Parameters", {}).get("Inputs", {}).get(kind, [])}


def _describe(nodes, i):
    """A node as a short text; sequences and selectors with their children."""
    n = nodes[i]
    kids = [c["Node Index"] for c in n.get("Plugs", {}).get("Child", [])]
    name = n.get("Name") or n["Node Type"].replace("Element_", "")
    if n["Node Type"] == "Element_Sequential":
        same = [p["Default Value"] for p in n["Properties"]["Bool"] if p["Name"] == "IsUpdateNextInFrame"][0]
        return "%s[%s]" % ("Seq" if same else "SeqFrames", ", ".join(_describe(nodes, k) for k in kids))
    if n["Node Type"] == "Element_Simultaneous":
        return "All[%s]" % ", ".join(_describe(nodes, k) for k in kids)
    if n["Node Type"] == "Element_BoolSelector":
        q = nodes[n["Parameters"]["Inputs"]["Bool"][0]["Node Index"]]
        return "If(%s ? %s : %s)" % (_strings(q).get("GameDataFullName"), _describe(nodes, kids[0]), _describe(nodes, kids[1]))
    s = _strings(n)
    extra = [str(s[k]) for k in ("ScreenName", "AnimationName", "AnimName", "PauseType", "GameDataFullName", "Value",
                                 "IsVisible", "Frame", "DisplayType", NODE_INPUT_NAME) if k in s]
    return name + ("(%s)" % ", ".join(extra) if extra else "")


def check_graphs():
    """Read the built files back: MinusMenuToMain has the swap where and as intended; the
    picker's A sets the flag; the three unused node classes are used by nothing else,
    neither in the game's graphs nor in ours; the fade screen's row and animations exist."""
    B = build
    used = {}
    ours = {NODE, DEAD_NODE}
    files = [(None, os.path.join(B.SEQ, f)) for f in sorted(os.listdir(B.SEQ)) if f.endswith(".ainb")]
    for folder in sorted(os.listdir(B.BUILD)):
        seq = os.path.join(B.BUILD, folder, "romfs", "Sequence")
        if os.path.isdir(seq):
            files += [(folder, os.path.join(seq, f)) for f in sorted(os.listdir(seq)) if f.endswith(".ainb")]
    for folder, path in files:
        for n in ainbx.load(open(path, "rb").read()).as_dict()["Nodes"]:
            if n.get("Name") in ours:
                used.setdefault(n["Name"], []).append((folder, os.path.basename(path)))
    assert used == {NODE: [(MOD, os.path.basename(MENU_TO_MAIN))] * 2}, used       # the two instances
    # our folder wins over every folder that has the same file, and has the size table too
    others = [f for f in os.listdir(B.BUILD) if f != MOD and os.path.exists(os.path.join(B.BUILD, f, "romfs", MENU_TO_MAIN))]
    assert others == ["Characters page"] and MOD < min(list(B.MODS) + others)
    tables = {open(os.path.join(B.BUILD, m, "romfs", B.rstb.PATH), "rb").read() for m in B.RSTB_MODS}
    assert MOD in B.RSTB_MODS and len(tables) == 1
    assert sorted(os.path.relpath(os.path.join(r, n), os.path.join(B.BUILD, MOD)) for r, _d, names in
                  os.walk(os.path.join(B.BUILD, MOD)) for n in names) == sorted(
        [os.path.join("exefs", exepatch.BUILD_ID + ".ips"), os.path.join("romfs", *MENU_TO_MAIN.split("/")),
         os.path.join("romfs", *B.rstb.PATH.split("/"))])

    nodes = ainbx.load(open(os.path.join(B.BUILD, MOD, "romfs", MENU_TO_MAIN), "rb").read()).as_dict()["Nodes"]
    stock = ainbx.load(open(os.path.join(B.ROMFS, MENU_TO_MAIN), "rb").read()).as_dict()["Nodes"]
    root = [c["Node Index"] for c in nodes[0]["Plugs"]["Child"]]
    assert len(root) == 2 and "Is Root Node" in nodes[0]["Flags"]
    first, second = (_describe(nodes, i) for i in root)
    P, S = PENDING_FLAG, FADE_SCREEN
    assert first == ("Seq[WaitEquip.module, If(%s ? SeqFrames[Seq[SeqExecuteLoadAndOpenScreen(%s), "
                     "All[SeqOneShotSetScreenVisible(%s, True), SeqOneShotRequestStopAnimationScreen(%s, Color, 0.0), "
                     "SeqOneShotRequestPlayAnimationScreen(%s, %s)]], SeqExecuteWaitScreenAnimEnd(%s, %s)] "
                     ": SeqExecuteGenericDummyAction)]" % (P, S, S, S, S, FADE_IN, S, FADE_IN)), first
    close = _describe(nodes, 9)
    assert close.startswith("All[All[SeqOneShotOpenScreen(PlayerStatus_00), ") and "PlusMinusPauseOff.module" in close
    want = ("SeqFrames[" + ("" if KEEP_CAMERA else "SeqOneShotShooterResetShootableAll, SeqExecuteSceneRequestSleepResidentActors, ") +
            "SeqOneShotRequestPause(SceneTransition), %s(False), SeqExecuteSceneRequestCreateResidentActors, "
            "SeqExecuteWaitCreateActor, SeqExecuteWaitCreateActor, %s(True), WaitEquip.module, "
            "SeqOneShotReleasePause(SceneTransition), SeqOneShotRequestPlayAnimationScreen(%s, %s), "
            "SeqExecuteWaitScreenAnimEnd(%s, %s), SeqExecuteCloseAndUnloadScreen(%s), "
            "SeqOneShotSetNonStructGameDataBool(%s, False)]" % (NODE, NODE, S, FADE_OUT, S, FADE_OUT, S, P))
    assert second == "Seq[%s, If(%s ? %s : SeqExecuteGenericDummyAction)]" % (close, P, want), second
    # the stock nodes are still the stock nodes (we only added)
    for i, n in enumerate(stock):
        if i != 0:
            assert nodes[i].get("Name") == n.get("Name") and nodes[i]["Node Type"] == n["Node Type"], i
    create = [n for n in nodes if n.get("Name") == "SeqExecuteSceneRequestCreateResidentActors"]
    assert len(create) == 1
    ours = [n for n in nodes if n.get("Name") == NODE]
    assert [(n["Parameters"], n["Queries"], n["Plugs"], n["Properties"]) for n in ours] == [
        ({"Inputs": {"Bool": [{"Name": NODE_INPUT_NAME, "Default Value": mode, "Node Index": -1, "Output Index": 0,
                               "Flags": ["Uses Default"]}]}, "Outputs": {}}, [], {}, {}) for mode in (MODE_DELETE, MODE_WAKE)]
    q = nodes[create[0]["Parameters"]["Inputs"]["Bool"][0]["Node Index"]]
    assert _strings(q) == {"GameDataIndex": -1, "IsDirect": True, "GameDataFullName": "Sequence_IsChildhood"}, _strings(q)

    page = ainbx.load(open(os.path.join(B.BUILD, "Characters page", "romfs", "Sequence", "CharacterMenu.module.ainb"),
                           "rb").read()).as_dict()["Nodes"]
    sets = [i for i, n in enumerate(page) if n.get("Name") == "SeqOneShotSetNonStructGameDataBool" and
            _strings(n)["GameDataFullName"] == PENDING_FLAG]
    assert len(sets) == 1 and _strings(page[sets[0]])["Value"] is True
    parents = [n for n in page if any(c["Node Index"] == sets[0] for c in n.get("Plugs", {}).get("Child", []))]
    assert len(parents) == 1 and parents[0]["Node Type"] == "Element_Simultaneous"
    assert not any(n.get("Name") == "SeqOneShotSetNonStructGameDataBool" and _strings(n)["GameDataFullName"] == PENDING_FLAG
                   and _strings(n)["Value"] is True for n in nodes)

    for mod in B.RSTB_MODS:
        path = os.path.join(B.BUILD, mod, "romfs", B.UISCREEN)
        if os.path.exists(path):
            rows = byml.load(nxfmt.unzs(open(path, "rb").read(), B.UISCREEN))
            row = [r for r in rows if r["Name"] == FADE_SCREEN]
            assert row == [dict({"ArchiveCategory": ["Common"], "BodyLayoutFile": FADE_LAYOUT, "IsHeapReverse": True,
                                 "Name": FADE_SCREEN}, **FADE_ROW)], row
            assert [r["Name"] for r in rows] == sorted((r["Name"] for r in rows), key=str.encode)
    arc = nxfmt.read(os.path.join(B.ROMFS, B.COMMON_LAYOUTS))
    lay = B.layout.Layout(nxfmt.sarc_files(arc)[FADE_LAYOUT + ".buil"])
    A, V = B.uiedit.ALPHA, B.uiedit.VISIBLE
    val = lambda anim, t: B.uiedit.anim_values(lay, anim, t)        # noqa: E731
    assert val("Color", 0)[(3, V, None)] == [1.0] and val("Color", 0)[(5, V, None)] == [0.0]       # black shown
    assert val(FADE_IN, 0)[(2, A, None)] == [0.0] and val(FADE_IN, FADE_IN_FRAMES)[(2, A, None)] == [1.0]
    assert val(FADE_OUT, 0)[(2, A, None)] == [1.0] and val(FADE_OUT, FADE_OUT_FRAMES)[(2, A, None)] == [0.0]
    data = byml.load(nxfmt.read(os.path.join(B.BUILD, "Character models", "romfs", B.gamedata.PATH)))
    h = B.gamedata.murmur3_32(PENDING_FLAG)
    assert [e for e in data["Data"]["Bool"] if e["Hash"] == h] == \
        [{"DefaultValue": False, "Hash": h, "ResetTypeValue": 0, "SaveFileIndex": -1}]
    return first, second


def compare_with_installed():
    """Files of the normal build's mods that differ here from what the normal emulator has
    installed (expected: only what this build changes on purpose)."""
    out = []
    for mod in sorted(os.listdir(BUILD_FIELD)):
        ours, theirs = os.path.join(BUILD_FIELD, mod), os.path.join(build.EDEN_LOAD, mod)
        if not os.path.isdir(os.path.join(ours, "romfs")) and not os.path.isdir(os.path.join(ours, "exefs")):
            continue
        if not os.path.isdir(theirs):
            out.append((mod, "(not installed in the normal emulator)"))
            continue
        files = set()
        for base in (ours, theirs):
            for root, _d, names in os.walk(base):
                files |= {os.path.relpath(os.path.join(root, n), base) for n in names}
        for rel in sorted(files):
            a, b = os.path.join(ours, rel), os.path.join(theirs, rel)
            if not (os.path.exists(a) and os.path.exists(b)) or open(a, "rb").read() != open(b, "rb").read():
                out.append((mod, rel))
    return out


# ---------------------------------------------------------------- main

# The default since it passed in game (snaildev, 2026-11-10, "that fixes it"): nobody is put to
# sleep and the camera is told the new player is the one it followed (fs_keep), so the camera
# stays where it was. `--sleep-all` builds the first version that passed (everybody sleeps,
# the camera ends up behind the character as after a door). `--keep-camera` is accepted and
# means the default.
KEEP_CAMERA = True


def main():
    global KEEP_CAMERA
    KEEP_CAMERA = "--sleep-all" not in sys.argv
    install = "--install" in sys.argv
    if install:
        for _ in range(10):     # a just-closed emulator takes a moment to go away
            if not fieldenv.running():
                break
            time.sleep(1)
        else:
            print("The field swap emulator copy is running. Close it first: it keeps the mod files open.")
            sys.exit(1)
    # the normal full build, into our own folder, with our three additions hooked in
    build.BUILD = BUILD_FIELD
    build.SIZES = {}
    build.EXE_PATCHES = {}
    assert MOD < min(build.MODS), "our folder must sort before the normal mods' folders"
    shutil.rmtree(os.path.join(BUILD_FIELD, MOD), ignore_errors=True)
    build.RSTB_MODS = build.RSTB_MODS + [MOD]       # the combined size table in our folder too (it wins)
    build.GAMEDATA_FLAGS = build.GAMEDATA_FLAGS + [("Bool", PENDING_FLAG, {"ResetTypeValue": 0, "SaveFileIndex": -1})]
    build.close_page_with_menu = close_page_with_menu
    build.add_page_screen = add_page_screen
    build._swap_step_list = _swap_step_list
    argv, sys.argv = sys.argv, [sys.argv[0]]        # build.main() must not see --install: it would install into the normal mods folder
    try:
        assert "--install" not in sys.argv
        build.main()
    finally:
        sys.argv = argv
    import npc_characters as npc
    folders = list(build.MODS) + [npc.MOD, MOD]

    print(MOD)
    p = field_patch()
    build.write_ips(MOD, p)
    print("checks (field swap)")
    runs = check_patch(p)
    print("  code patch: %d runs emulated; %s's enter and update -> %x-%x inside the unused %s (%x-%x); transform kept at %x"
          % (runs, NODE, p.symbols["fs_enter"], p.cave_ptr, DEAD_NODE, DEAD[0], DEAD[1], p.stash))
    first, second = check_graphs()
    print("  menu close, step 1: " + first)
    print("  menu close, step 2: " + second)
    diff = compare_with_installed()
    print("  differs from the normal emulator's installed mods:")
    for mod, rel in diff:
        print("    %s: %s" % (mod, rel))
    if install:
        fieldenv.setup()
        fieldenv.check()
        for name in os.listdir(fieldenv.LOAD):
            shutil.rmtree(os.path.join(fieldenv.LOAD, name))
        for mod in folders:
            shutil.copytree(os.path.join(BUILD_FIELD, mod), os.path.join(fieldenv.LOAD, mod))
            print("  installed ->", os.path.join(fieldenv.LOAD, mod))


if __name__ == "__main__":
    main()
