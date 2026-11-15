"""Character customization (its own track, started 2026-11-13).

    venv\Scripts\python.exe mods\customize.py             build into build-custom\ and check
    venv\Scripts\python.exe mods\customize.py --install   also copy the folder into the normal
        emulator's mods folder (seconds; nothing else there is touched)
    venv\Scripts\python.exe mods\customize.py --remove    take the folder out again (any session
        may, when it is in the way of its own install; --install puts it back)
    venv\Scripts\python.exe mods\customize.py --install-only   copy the folder as last built
        (the build takes minutes since the palettes); refused unless that build was made
        over the very files that are installed now (build-custom\built-over.json)
    (--parts and --clothes, the switches the clothing colours were built with until they
    passed in game, are still taken and mean nothing)

It holds the build lock for its run and stamps the install record as "customize"
(tools\modstate.py; OOT_SESSION gives another name).

snaildev's design: on the character select page X opens "customize" for the selected
character. The character moves from the right third to the middle of the screen, the
picker's 2D UI goes away and a customization UI comes in: options left and right of the
character (hair colour, eye colour, clothing colour, outfits, show / hide equipment,
accessories).

This file is the first step, the mode itself (the options are shown and can be operated,
their numbers are kept in unsaved GameData Ints, nothing reads them yet).

How it is built: as ONE folder, MOD, laid over what is installed. Other sessions work on
the normal build's sources at the same time, so this does not run the normal build and
writes nothing into build\: it reads the files the installed folders ship (the way the
emulator layers them: of a file several folders have, the first folder's by name), makes
its own versions of the ones it changes and writes them into build-custom\MOD. The
folder's name sorts before every other folder's, so its versions win, and switched off in
the emulator's Add-Ons list everything is what was installed before. It also ships the
size table: every installed table's entries, each at its largest, and its own. A normal
install (`mods\build.py --install`) removes the folder (it is in build.RETIRED: its files
are made from the ones that install replaces); run this again afterwards.

1. The page's graph (Sequence/CharacterMenu.module.ainb, the picker's graph plus ours):
   every button loop of the picker asks for X first. X: the picker's layout plays
   PAGE_OUT, FLAG gets its "customizing" bit, our screen SCREEN is loaded and opened and
   plays "In"; then one button loop per option (Up / Down: the option; Left / Right: its
   value; B or -: back). Back: "Out", PAGE_IN, the bit off, our screen closed and
   unloaded, then the picker's own way in by the saved character (its cursor ends on the
   selected character's slot).
2. The picker's layout gets two animations (nothing else of it changes): PAGE_OUT moves the
   light (the vignette window) from the right third to the middle and takes the picker's
   own panes away, PAGE_IN is the way back.
3. Our screen: a layout of its own, LAYOUT, on a screen row of its own one sort key above
   the page. Texts cannot be changed by a graph, so every value is a text pane and the
   state is animations stopped at a frame: "Focus" (which option), "Val_ff" (option ff's
   value).
4. The 3D picture: a code patch on the 3D UI camera's update, at the instruction that
   stores the picture offset (VIEW_STORE). While the offset is the page view's
   (build.VIEW_OFFSET_X, written by the page view patch every update while the page shows
   the character), it is blended towards VIEW_CENTRE by a count kept in FLAG (see FLAG).
   The code sits in the one-shot body of a node class nothing uses (DEAD).
5. The GameDataList with our unsaved Ints added (FLAG and one per option).
"""
import bisect
import copy
import os
import shutil
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build  # noqa: E402  (the normal mods, used as a library)
import ainbx  # noqa: E402
import byml  # noqa: E402
import exepatch  # noqa: E402
import bympatch  # noqa: E402
import gamedata  # noqa: E402
import layout  # noqa: E402
import modstate  # noqa: E402  (one build at a time, the record of what is installed)
import msbt  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402
import rstb  # noqa: E402
import sarcedit  # noqa: E402
import ui_sandbox as ui  # noqa: E402  (the UI session's picker, used as a library)
import uiedit  # noqa: E402
from uiedit import ALPHA, TRANSLATE, VISIBLE  # noqa: E402

BUILD_CUSTOM = os.path.join(ROOT, "build-custom")
# Sorts before every folder that is installed, the voices folder "Character (voice test)"
# included (whose own size table would otherwise be the one the emulator takes).
MOD = "Character (customize test)"
SESSION = os.environ.get("OOT_SESSION", "customize")       # this session's name in the build lock and the install record
SCREEN = LAYOUT = "CharacterCustomize_00"
SCREEN_ROW = {"DrawUnitId": 1, "SortKey": 5001}     # the page is 5000, the header 8749
GRAPH = "Sequence/CharacterMenu.module.ainb"

# FLAG (Int, not saved) = 2 * count + bit. The graph only ever adds to it: + 1 when X opens
# the mode (the bit: "customizing"), - 1 on the way back; the page's entry sets it to 0.
# The code patch moves the count by one each camera update, up to GLIDE while the bit is
# set and down to 0 while it is clear; the count is where the character stands between
# the page's place (0) and the middle (GLIDE). Adding 2 never changes the bit, so the two
# writers cannot undo each other, whatever order a queued GameData set lands in.
FLAG = "CharacterMenu_Customize"
GLIDE = 20                  # camera updates (60 a second) from the right third to the middle
# The character's origin in the middle of the screen: build.VIEW_OFFSET_X with
# LINK_SCREEN_X = 0 (the camera aims VIEW_CAMERA_X right of him).
VIEW_CENTRE = -(build.VIEW_CAMERA_X / build.VIEW_HALF_WIDTH) / 2
CENTRE_X = 0.0              # layout units: where the light goes (build.SPOT_X on the page)
VIEW_STORE = 0x8289D4D0     # "stur x20, [x19, #0xcc]": the picture offset into the camera
# The patch's code: the one-shot body of a sequence node class no graph, pack or parameter
# file of the game names (README, voices section, "Code space": the fourth largest such
# body; the others are taken by the voices, the cloth and the slide). Checked at every
# build by the NPC session's check_unused_node.
DEAD_NODE = "SeqOneShotSetDebugBancParam"
DEAD = (0x809D76A0, 0x809D77CC)
DEAD_SHA1 = "3627f0da660a1f81b4638a052b52b1e3c906fb52"      # its bytes in game v1.1.1

# The options: (key, label, values). Value 0 is what the game shows without us. The first
# ROWS stand left of the character, the others right. Their numbers are kept in unsaved
# Ints "CharacterMenu_Custom<key>".
# What is built (for the v0.2.0 release the main session left out what changed nothing,
# snaildev: "disable the unimplemented ones for now"; Outfit and Accessory are still that and
# are no rows at all until they do something):
# - without a switch: Hair Color, Eye Color, Clothing Color | Equipment. Clothing Color
#   passed in game on 2026-11-14 (snaildev: Link, Saria, young Zelda, young Malon, the colours'
#   names, the field: all "Yes"); `--clothes`, which built it until then, is still taken
#   and means nothing.
# - `--parts` (2026-11-14, built offline, not seen): Hair Color, Eye Color, Equipment |
#   Clothing Color, Clothing Color 2, Clothing Color 3: a colour a PART of the clothes
#   (clothing by textures, below). snaildev's look at the one colour: "most NPCs seem to have
#   at least 1 alternate color (links vest is slightly darker green, but when I change
#   colors, everything changes except his belt/belt buckle, which it would also be able to
#   choose different options for those)".
CLOTH_VALUES = ["Default", "Green", "Red", "Blue", "Purple", "Yellow", "Black", "White"]
CLOTH_KEYS = ("Clothes", "Clothes2", "Clothes3")        # the three clothing colours' options (the first alone without --parts)
PARTS = True            # three clothing colours, a part of the clothes each (a switch's until snaildev's look of 2026-11-14: "those clothing colors work")
OPTIONS = [
    ("Hair", "Hair Color", ["Default", "Blonde", "Brown", "Black", "Red", "Orange", "Green", "Blue", "Pink", "White"]),
    ("Eyes", "Eye Color", ["Default", "Blue", "Green", "Brown", "Gray", "Red", "Violet", "Gold"]),
]
if PARTS:
    OPTIONS += [("Gear", "Equipment", ["Shown", "Hidden"])]
    OPTIONS += [(k, "Clothing Color" + (" %d" % (n + 1) if n else ""), list(CLOTH_VALUES)) for n, k in enumerate(CLOTH_KEYS)]
else:
    OPTIONS += [("Clothes", "Clothing Color", list(CLOTH_VALUES)), ("Gear", "Equipment", ["Shown", "Hidden"])]
CLOTHES_ON = True           # the clothing palettes are built (they were a switch's until Clothing Color passed)
ROWS = 3


def option_flag(f):
    return "CharacterMenu_Custom" + OPTIONS[f][0]


def flags():
    return [FLAG] + [option_flag(f) for f in range(len(OPTIONS))]


# ---------------------------------------------------------------- the layouts
# Layout units: the screen is 3840 x 2160, its centre (0, 0), y up. The header ends at
# y = 818, the footer starts at y = -954 (README, portrait run). The character stands in
# the middle; the light around him is half dark 800 units out and black from about 1000
# (build.SPOT_HALF_WIDTH), so the columns stand on black.
TITLE = "Customize"
COL_X = (-1320.0, 1320.0)   # the two columns' centres
COL_W = 700.0
ROW_Y = (300.0, 0.0, -300.0)
HEAD_Y = 640.0              # as the picker's title line
LABEL_DY, LABEL_SIZE = 78.0, 40.0
VALUE_W, VALUE_H, VALUE_SIZE = 480.0, 100.0, 64.0
LINE_DY, LINE_W = -66.0, 620.0
ARROW_DX = 300.0            # the two glyphs beside the focused option's value: ZL before it, ZR after it
VALUE_KEYS = (("ZL", -ARROW_DX), ("ZR", ARROW_DX))
HINT_Y, HINT_SIZE, HINT_W, HINT_GAP = -600.0, 38.0, 150.0, 30.0
# No hint line (snaildev, fourth look: "select, change, and back button prompts are redundant"); the code that lays one
# out stays: an entry is (column, [button glyphs], text), e.g. (0, ["ZL", "ZR"], "Change").
HINTS = []
# What may hang where (three looks by snaildev, 2026-11-13; README, mods list, this folder):
# 1. Columns faded in (alpha and visibility on the column panes), the options in a group
#    pane each (N_Row_ff, dimmed by "Focus" through its alpha): the title and the hints,
#    which hung on the column itself, showed at once; an option showed only once "Focus"
#    changed its own group's alpha.
# 2. Columns only moved in from off the screen (where the layout had them at rest), same
#    groups: no option ever showed, not after "Focus" changed its group either.
# So what an animation does to a pane (alpha, visibility, place) did not arrive at the
# panes under a group pane of ours inside it; why is not known (the group had an alpha
# animation of its own, and it was a second level down: either may be it). This layout
# therefore uses only what has been seen working in game:
# - no group panes: every text and glyph hangs on its column directly (as the title and
#   hints did, and as the UI sandbox's list rows hang on the pane that scrolls them);
# - the columns are AT REST WHERE THEY ARE SHOWN, so the mode is whole even if "In" did
#   nothing; "In" / "Out" move them in from / out to AWAY;
# - no alpha animation at all. The focused option is marked by the only ornament line
#   and by the ZL / ZR glyphs beside its value: one marker pane (N_Mark_00, on the top
#   pane, the line and the two glyphs directly on it) that "Focus" puts at the option,
#   as the sandbox's focus line is moved; the glyphs are hidden on an option with one
#   value. "In" shows the marker when the columns have arrived, "Out" hides it.
IN_FRAMES, OUT_FRAMES = 14, 10
# The two animations that bring the columns and take them away are NOT called "In" and "Out":
# the game plays a layout's "In" by itself when a screen opens (125 of its layouts have one).
# Seen by snaildev in the fourth look, when ours had that name and the columns stood at rest
# where they are shown: they slid in with the layout's own values while the screen opened,
# stood for about a second (the open node had not returned yet), and slid in again when the
# graph asked for "In" and set the values. So: nothing of ours plays at the open, the whole
# of it (N_Custom_00) is hidden at rest, and SHOW, asked for by the graph after it has set
# the option and the values, makes it visible as its first act.
SHOW, HIDE = "ColsIn", "ColsOut"
SHIFT = 160.0               # how far the picker's own panes slide as they go (PAGE_OUT)
AWAY = (-1100.0, 1100.0)    # where "In" brings the columns from and "Out" takes them: both wholly off the screen
MARK, MARK_LINE, MARK_KEYS = "N_Mark_00", "P_MarkLine_00", ("Pa_MarkL_00", "Pa_MarkR_00")
PAGE_OUT, PAGE_IN = "CustomOut", "CustomIn"
PAGE_FADE = 12              # frames in which the picker's own panes go / come
HIDDEN = ("N_IsOpen_00", "P_Overlay_00", "N_DemoMessage")      # Key Items' own content


def onehot(n, i):
    return [1.0 if j == i else 0.0 for j in range(n)]


def flat(value, frames):
    return uiedit.hermite([(0, value, 0.0), (frames, value, 0.0)])


def preorder(ed):
    """The panes of a layout are stored in pre-order: a pane, then its whole subtree, then
    its next sibling; each pane records its parent and where its subtree ends. Every layout
    of the game and the picker's are so, and uiedit's insert asks for a block in that order
    but does not check it: given a pane's children after the pane's next sibling it still
    writes a file that passes Editor.validate, in which a pane's recorded subtree is no
    longer its children. The first three builds of this mode's layout were like that (both
    columns first, then their contents), and in game the options did not follow what was
    done to their column (see "What may hang where"). -> the number of panes."""
    views = [ed.pane(i) for i in range(len(ed.names()))]
    stack = []
    for i, v in enumerate(views):
        while stack and views[stack[-1]].end <= i:
            stack.pop()
        assert v.parent == (stack[-1] if stack else -1), (ed.names()[i], "is not stored under its parent")
        assert i < v.end <= (views[stack[-1]].end if stack else len(views)), ed.names()[i]
        stack.append(i)
    return len(views)


def option_place(f):
    """-> (column, x, y) of option f: where its value stands"""
    side, i = divmod(f, ROWS)
    return side, COL_X[side], ROW_Y[i]


def make_layout():
    """LAYOUT.buil and its messages {label: text}: Key Items' layout (as the page's own
    layout starts from it) with nothing of Key Items' shown, and our panes."""
    lay = build.page_layout()
    ed = uiedit.Editor(lay)
    ed.validate()
    P = ui.Panes(ed)
    top = P.null("N_Custom_00", None, visible=False)
    # In pre-order: a pane, then everything under it, then its next sibling (see preorder).
    glyphs = 0
    for side in range(2):
        col = P.null("N_Col_%02d" % side, top)
        left = COL_X[side] - COL_W / 2
        if side == 0:
            P.text("T_Head_00", col, (left + COL_W / 2, HEAD_Y), (COL_W, 70), 46.0, TITLE, h_align=0)
        for f, (_key, label, values) in enumerate(OPTIONS):
            if option_place(f)[0] != side:
                continue
            _, x, y = option_place(f)
            P.text("T_Label_%02d" % f, col, (x, y + LABEL_DY), (COL_W, 60), LABEL_SIZE, label)
            for v, value in enumerate(values):
                P.text("T_Val_%02d_%02d" % (f, v), col, (x, y), (VALUE_W, VALUE_H), VALUE_SIZE, value, big=True,
                       visible=v == 0)
        at = left
        for k, (hint_side, buttons, text) in enumerate(HINTS):
            if hint_side != side:
                continue
            for button in buttons:
                pane = "Pa_Hint_%02d" % glyphs
                glyphs += 1
                P.part(pane, col, ui.KEY, (at + ui.KEY_W / 2, HINT_Y), scale=ui.KEY_SCALE, size=(102, 102))
                P.msgs["%s-T_Icon_00" % pane] = ui.key_glyph(button)
                at += ui.KEY_W + 8
            P.text("T_Hint_%02d" % k, col, (at + 6 + HINT_W / 2, HINT_Y), (HINT_W, 60), HINT_SIZE, text, h_align=0)
            at += 6 + HINT_W + HINT_GAP
    # the marker, at rest on the first option
    assert len(OPTIONS[0][2]) > 1               # its glyphs shown at rest, as "Focus" has them on the first option
    mark = P.null(MARK, top, option_place(0)[1:])
    P.line(MARK_LINE, mark, (0, LINE_DY), LINE_W / ui.FOCUS_LINE_W, solid=True)
    for pane, (button, dx) in zip(MARK_KEYS, VALUE_KEYS):
        P.part(pane, mark, ui.KEY, (dx, 0), scale=ui.KEY_SCALE, size=(102, 102))
        P.msgs["%s-T_Icon_00" % pane] = ui.key_glyph(button)
    assert preorder(ed)
    P.insert()
    for name in HIDDEN:
        assert ed.pane(name).visible, name
        ed.pane(name).visible = False

    n = len(OPTIONS)
    names = ["N_Col_00", "N_Col_01"]
    T = IN_FRAMES
    assert not {"In", "Out"} & set(anim_frames(lay))
    ed.add_anim(SHOW, frames=T, consts=[(ed.key("N_Custom_00", VISIBLE), 1.0)],
                curves=[(ed.key(c, TRANSLATE), [uiedit.ease_curve(0, AWAY[s], T, 0.0), flat(0.0, T)])
                        for s, c in enumerate(names)]
                + [(ed.key(MARK, VISIBLE), uiedit.hermite([(0, 0.0, 0.0), (T - 2, 0.0, 0.0), (T - 2, 1.0, 0.0), (T, 1.0, 0.0)]))])
    ed.add_anim(HIDE, frames=OUT_FRAMES, consts=[(ed.key(MARK, VISIBLE), 0.0)],
                curves=[(ed.key(c, TRANSLATE), [uiedit.hermite([(0, 0.0, 0.0), (OUT_FRAMES, AWAY[s], 2.0 * AWAY[s] / OUT_FRAMES)]),
                                                flat(0.0, OUT_FRAMES)]) for s, c in enumerate(names)])
    places = [option_place(f)[1:] for f in range(n)]
    choice = [float(len(values) > 1) for _key, _label, values in OPTIONS]
    ed.add_anim("Focus", frames=n - 1,
                curves=[(ed.key(MARK, TRANSLATE), [uiedit.state_curve([x for x, _ in places]),
                                                   uiedit.state_curve([y for _, y in places])])]
                + [(ed.key(pane, VISIBLE), uiedit.state_curve(choice)) for pane in MARK_KEYS])
    for f, (_key, _label, values) in enumerate(OPTIONS):
        if len(values) > 1:
            ed.add_anim("Val_%02d" % f, frames=len(values) - 1,
                        curves=[(ed.key("T_Val_%02d_%02d" % (f, v), VISIBLE), uiedit.state_curve(onehot(len(values), v)))
                                for v in range(len(values))])
    uiedit.rename(lay, LAYOUT)
    out = layout.build(lay)
    check = layout.Layout(out)                  # what the game will read
    uiedit.Editor(check).validate()
    assert layout.build(check) == out and check.name == LAYOUT
    assert preorder(uiedit.Editor(check)) > preorder(uiedit.Editor(build.page_layout()))
    return out, P.msgs


def anim_frames(lay):
    return {it[0].text: it[1].get("<I", 0x28)[0] for it in lay.root.ptr[0x140].items}


def check_layout(data, msgs, known):
    """What each state shows, from the layout as built; everything inside its column and
    between the header and the footer; every part a layout the archive has (`known`)."""
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    names = ed.names()
    idx = names.index
    at = lambda anim, f: uiedit.anim_values(lay, anim, float(f))        # noqa: E731
    n = len(OPTIONS)
    frames = anim_frames(lay)
    assert frames["Focus"] == n - 1 and frames[SHOW] == IN_FRAMES and frames[HIDE] == OUT_FRAMES
    assert "In" not in frames and "Out" not in frames                   # nothing the game plays by itself at the open
    top = ed.pane("N_Custom_00")
    assert not top.visible and names[top.parent] == "N_All_00"         # hidden until SHOW
    cols = ("N_Col_00", "N_Col_01")
    parent = lambda name: names[ed.pane(name).parent]                   # noqa: E731
    mark = ed.pane(MARK)
    assert parent(MARK) == "N_Custom_00" and mark.visible and all(parent(c) == "N_Custom_00" for c in cols)
    assert names[mark.index + 1:mark.end] == [MARK_LINE] + list(MARK_KEYS)       # the marker's own, all directly on it
    assert all(ed.pane(p).visible and parent(p) == MARK for p in (MARK_LINE,) + MARK_KEYS)
    for f in range(n):
        side, x, y = option_place(f)
        v = at("Focus", f)
        # the marker at the option, its glyphs only where there is a choice; nothing else
        assert v[(idx(MARK), TRANSLATE, None)] == [x, y]
        values = OPTIONS[f][2]
        assert all(v[(idx(p), VISIBLE, None)] == [float(len(values) > 1)] for p in MARK_KEYS)
        assert set(k[:2] for k in v) == {(idx(MARK), TRANSLATE)} | {(idx(p), VISIBLE) for p in MARK_KEYS}
        if f == 0:
            assert tuple(mark.pos) == (x, y)                            # at rest what "Focus" makes of frame 0
        panes = ["T_Val_%02d_%02d" % (f, k) for k in range(len(values))]
        assert [ed.pane(p).visible for p in panes] == [k == 0 for k in range(len(values))]
        # every pane of an option directly on its column, where the option stands
        for p in panes + ["T_Label_%02d" % f]:
            assert parent(p) == cols[side] and ed.pane(p).pos[0] == x, p
        assert all(ed.pane(p).pos[1] == y for p in panes) and ed.pane("T_Label_%02d" % f).pos[1] == y + LABEL_DY
        assert msgs["T_Label_%02d" % f] == OPTIONS[f][1] and [msgs[p] for p in panes] == values
        if len(values) > 1:
            assert frames["Val_%02d" % f] == len(values) - 1
            for k in range(len(values)):
                v = at("Val_%02d" % f, k)
                assert [v[(idx(p), VISIBLE, None)][0] for p in panes] == onehot(len(values), k)
        else:
            assert "Val_%02d" % f not in frames
    for s, c in enumerate(cols):
        # at rest where they are shown; SHOW and HIDE only move them, and show / hide the marker (SHOW: and the whole)
        col = ed.pane(c)
        assert col.visible and tuple(col.pos) == (0.0, 0.0)
        assert all(ed.pane(i).parent == col.index for i in range(col.index + 1, col.end))      # nothing a level further down
        for anim, first, last, shown in ((SHOW, AWAY[s], 0.0, (0.0, 1.0)), (HIDE, 0.0, AWAY[s], (0.0, 0.0))):
            a, b = at(anim, 0), at(anim, frames[anim])
            whole = {(top.index, VISIBLE)} if anim == SHOW else set()
            assert set(k[:2] for k in a) == {(idx(x), TRANSLATE) for x in cols} | {(idx(MARK), VISIBLE)} | whole, anim
            assert anim != SHOW or a[(top.index, VISIBLE, None)] == b[(top.index, VISIBLE, None)] == [1.0]
            assert a[(idx(c), TRANSLATE, None)] == [first, 0.0] and b[(idx(c), TRANSLATE, None)] == [last, 0.0]
            assert (a[(idx(MARK), VISIBLE, None)], b[(idx(MARK), VISIBLE, None)]) == ([shown[0]], [shown[1]])
    for name in HIDDEN:
        assert not ed.pane(name).visible
    assert not ed.pane("W_Window_00").visible or names[ed.pane("W_Window_00").parent] in HIDDEN
    for k, rec, _ in lay.root.ptr[0xC0].items:
        assert rec.ptr[0x0].text in known, rec.ptr[0x0].text
    # no animation of ours changes an alpha
    for anim in lay.root.ptr[0x140].items:
        if anim[0].text in (SHOW, HIDE, "Focus") or anim[0].text.startswith("Val_"):
            for t in anim[1].ptr[0].ptr[8].items:
                assert t.ptr[0][3] != ALPHA, anim[0].text
    rects = uiedit.pane_rects(lay, hidden=True)
    ours = [name for name in names if name in msgs or name.split("-")[0] in [m.split("-")[0] for m in msgs]] + [MARK_LINE]
    assert len(ours) >= len(msgs)
    spans = {}
    for name in ours:
        x0, y0, x1, y1, _ = rects[name]
        # the marker's own panes: wherever "Focus" puts the marker
        moves = [(option_place(f)[1] - mark.pos[0], option_place(f)[2] - mark.pos[1]) for f in range(n)] \
            if parent(name) == MARK else [(0.0, 0.0)]
        for dx, dy in moves:
            side = 0 if x0 + dx < 0 else 1
            lo, hi = COL_X[side] - COL_W / 2, COL_X[side] + COL_W / 2
            assert lo - 1 <= x0 + dx and x1 + dx <= hi + 1 and -900 <= y0 + dy and y1 + dy <= 700, (name, x0, x1, y0, y1, dx, dy)
            assert abs(x0 + dx) >= 900 and abs(x1 + dx) >= 900, (name, "inside the character's light")
            # and off the screen where SHOW starts and HIDE ends (the marker is hidden then)
            assert parent(name) == MARK or x1 + AWAY[side] < -1920 or x0 + AWAY[side] > 1920, (name, "on the screen when away")
        if "Hint" in name:
            spans.setdefault(side, []).append((x0, x1))
    for side, row in spans.items():         # the hints stand side by side
        row.sort()
        assert all(a[1] <= b[0] for a, b in zip(row, row[1:])), row
    return len(names), len(frames)


PROMPT_KEY, PROMPT_TEXT, PROMPT = "Pa_KeyX_00", "T_Custom_00", "Customize"
# Where the prompt stands (snaildev, 2026-11-13, on seeing it under the grid: "underneath the
# divider under the character's name on the right, so it's roughly wide enough to fit the
# divider"): centred under the detail block's line, the head line's glyph and text (a key at
# ui.KEY_SCALE beside text of size 46) enlarged to the size of the name over that line. Its
# width is an estimate (0.55 of the size a letter, as ui.name_size): about 0.8 of the line.
# The detail texts under the line are empty in every build (build.PICKER_DETAIL_TEXTS);
# write_files checks that, since the prompt stands where they would be drawn.
PROMPT_SIZE = 46.0                      # the text's size; the glyph and the gap follow it (84, the name's: "too big")
PROMPT_DROP = 85.0                      # its centre under the line's
PROMPT_GAP, PROMPT_SLACK = 14.0, 60.0   # glyph to text at size 46; the text pane's room beyond the estimate
DETAIL_LINE, DETAIL_TEXT = "P_DetailLine_00", "T_CellText_"


def add_page_anims(data):
    """The picker's layout with the prompt for X and with PAGE_OUT and PAGE_IN; also the
    prompt's messages. The prompt: the X glyph and "Customize" under the line of the detail
    block (see PROMPT_SIZE); two panes more in the picker's own container, copies of its ZL
    glyph and its title.
    The light: the window's place, from the page's (build.SPOT_X) to CENTRE_X on the curve
    the code patch moves the picture by (a Hermite segment with both slopes 0 = 3t^2 -
    2t^3). The picker's own panes: their container N_Sandbox_00 fades and slides a little
    to the left, and is hidden at the end (a hidden pane hides its children whatever its
    alpha does to them)."""
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    ed.validate()
    before = ed.names()
    assert PROMPT_KEY not in before and PROMPT_TEXT not in before
    preorder(ed)
    zl, title = ed.pane("Pa_KeyZL_00"), ed.pane("T_Title_00")
    assert [round(x, 6) for x in zl.scale] == [ui.KEY_SCALE, ui.KEY_SCALE] and zl.visible and title.visible
    assert before[zl.parent] == before[title.parent] == "N_Sandbox_00"
    head = lay.root.ptr[0x80].get("T_Title_00").get("<2f", 0x08)
    assert head[0] == head[1] and title.size[1] == 70.0
    lx0, ly0, lx1, ly1, shown = uiedit.pane_rects(lay, hidden=True)[DETAIL_LINE]
    assert shown and abs((lx0 + lx1) / 2 - ui.PICK_DETAIL_POS[0]) < 1e-3 and (ly0 + ly1) / 2 == ui.PICK_DETAIL_POS[1]
    k = PROMPT_SIZE / head[0]
    key_w, gap, text_w = ui.KEY_W * k, PROMPT_GAP * k, 0.55 * PROMPT_SIZE * len(PROMPT)
    width = key_w + gap + text_w
    assert width <= lx1 - lx0, (width, lx1 - lx0)                           # inside the line's width
    left, y = (lx0 + lx1) / 2 - width / 2, (ly0 + ly1) / 2 - PROMPT_DROP
    key = ed.new_part("Pa_KeyZL_00", PROMPT_KEY, ui.KEY, size=(102, 102))
    key.node.set("<2f", 0x1C, left + key_w / 2, y)
    key.node.set("<2f", 0x10, ui.KEY_SCALE * k, ui.KEY_SCALE * k)
    text = ed.clone("T_Title_00", PROMPT_TEXT)
    text.node.set("<2f", 0x1C, left + key_w + gap + (text_w + PROMPT_SLACK) / 2, y)
    text.node.set("<2f", 0x2C, text_w + PROMPT_SLACK, title.size[1] * k)
    uiedit.set_text(text, ref="*", font_size=PROMPT_SIZE, h_align=0)
    ed.insert("N_Sandbox_00", [key, text])
    prompt = {"%s-T_Icon_00" % PROMPT_KEY: ui.key_glyph("X"), PROMPT_TEXT: PROMPT}
    win, box = ed.pane("W_Window_00"), ed.pane("N_Sandbox_00")
    spot = (build.SPOT_X, build.SPOT_Y)
    assert tuple(win.pos) == spot and tuple(box.pos) == (0.0, 0.0) and box.visible and win.visible
    assert win.scale[0] * build.MASK_QUADRANT[0] >= 1920 + abs(CENTRE_X) + 40      # still covers the screen there
    have = anim_frames(lay)
    assert PAGE_OUT not in have and PAGE_IN not in have
    for anim in lay.root.ptr[0x140].items:          # nothing else moves the two panes
        for t in anim[1].ptr[0].ptr[8].items:
            k = t.ptr[0]
            assert not (uiedit.key_pane(k) == win.index and k[3] == TRANSLATE)
            assert uiedit.key_pane(k) != box.index, anim[0].text
    T, F = GLIDE, PAGE_FADE
    assert 0 < F < T
    ed.add_anim(PAGE_OUT, frames=T, curves=[
        (ed.key("W_Window_00", TRANSLATE), [uiedit.hermite([(0, spot[0], 0.0), (T, CENTRE_X, 0.0)]), flat(spot[1], T)]),
        (ed.key("N_Sandbox_00", VISIBLE), uiedit.hermite([(0, 1.0, 0.0), (F, 1.0, 0.0), (F, 0.0, 0.0), (T, 0.0, 0.0)])),
        (ed.key("N_Sandbox_00", ALPHA), uiedit.linear_curve([(0, 1.0), (F, 0.0), (T, 0.0)])),
        (ed.key("N_Sandbox_00", TRANSLATE), [uiedit.hermite([(0, 0.0, 0.0), (F, -SHIFT, -2.0 * SHIFT / F),
                                                             (F, -SHIFT, 0.0), (T, -SHIFT, 0.0)]), flat(0.0, T)])])
    ed.add_anim(PAGE_IN, frames=T, consts=[(ed.key("N_Sandbox_00", VISIBLE), 1.0)], curves=[
        (ed.key("W_Window_00", TRANSLATE), [uiedit.hermite([(0, CENTRE_X, 0.0), (T, spot[0], 0.0)]), flat(spot[1], T)]),
        (ed.key("N_Sandbox_00", ALPHA), uiedit.linear_curve([(0, 0.0), (T - F, 0.0), (T, 1.0)])),
        (ed.key("N_Sandbox_00", TRANSLATE), [uiedit.hermite([(0, -SHIFT, 0.0), (T - F, -SHIFT, 0.0),
                                                             (T - F, -SHIFT, 2.0 * SHIFT / F), (T, 0.0, 0.0)]), flat(0.0, T)])])
    out = layout.build(lay)
    check = layout.Layout(out)
    uiedit.Editor(check).validate()
    assert layout.build(check) == out
    # as built: the ends are the page as it is and the mode as described, and the light is
    # where the code patch has the character at every frame
    w, b = ed.names().index("W_Window_00"), ed.names().index("N_Sandbox_00")
    for anim, x0, x1 in ((PAGE_OUT, spot[0], CENTRE_X), (PAGE_IN, CENTRE_X, spot[0])):
        for f in range(T + 1):
            v = uiedit.anim_values(check, anim, float(f))
            s = (f / T) ** 2 * (3 - 2 * f / T)
            assert abs(v[(w, TRANSLATE, None)][0] - (x0 + (x1 - x0) * s)) < 1e-3 and abs(v[(w, TRANSLATE, None)][1] - spot[1]) < 1e-6
    v = uiedit.anim_values(check, PAGE_OUT, float(T))
    assert v[(b, VISIBLE, None)] == [0.0] and v[(b, ALPHA, None)] == [0.0]
    assert uiedit.anim_values(check, PAGE_OUT, 0.0)[(b, TRANSLATE, None)] == [0.0, 0.0]
    v = uiedit.anim_values(check, PAGE_IN, float(T))
    assert v[(b, VISIBLE, None)] == [1.0] and v[(b, ALPHA, None)] == [1.0] and v[(b, TRANSLATE, None)] == [0.0, 0.0]
    old = layout.Layout(data)
    now = uiedit.Editor(check)
    assert preorder(now) == len(before) + 2
    at = now.pane("N_Sandbox_00").end - 2           # the two new panes: the last of the picker's own
    assert now.names() == before[:at] + [PROMPT_KEY, PROMPT_TEXT] + before[at:] and len(now.names()) < 512
    assert set(anim_frames(check)) - set(anim_frames(old)) == {PAGE_OUT, PAGE_IN}
    rects = uiedit.pane_rects(check, hidden=True)
    line, pk, pt = rects[DETAIL_LINE], rects[PROMPT_KEY], rects[PROMPT_TEXT]
    assert line == uiedit.pane_rects(old, hidden=True)[DETAIL_LINE]
    for name, (x0, y0, x1, y1, _) in ((PROMPT_KEY, pk), (PROMPT_TEXT, pt)):
        # under the line and clear of it, over the footer, in the room the picker's own check
        # gives the detail texts (right of the character, on the screen)
        assert 940 <= x0 and x1 <= 1840 and -880 <= y0 and y1 < line[1], (name, x0, y0, x1, y1)
    # side by side, the glyph and the estimated text centred under the line, text as tall as the name
    assert pk[2] < pt[0] and abs((pk[0] + pt[2] - PROMPT_SLACK) / 2 - (line[0] + line[2]) / 2) < 1e-2
    assert abs((pk[1] + pk[3]) / 2 - (pt[1] + pt[3]) / 2) < 1e-3
    assert check.root.ptr[0x80].get(PROMPT_TEXT).get("<2f", 0x08) == (PROMPT_SIZE, PROMPT_SIZE)
    # nothing else of the picker is drawn there but the detail texts (empty: write_files)
    sb = now.pane("N_Sandbox_00")
    under = 0
    for name in now.names()[sb.index + 1:sb.end]:
        x0, y0, x1, y1, _ = rects[name]
        apart = x1 <= pk[0] or pt[2] <= x0 or y1 <= min(pk[1], pt[1]) or max(pk[3], pt[3]) <= y0
        if name.startswith(DETAIL_TEXT):
            under += not apart
        elif name.startswith(("T_", "Pa_", "P_")) and name not in (PROMPT_KEY, PROMPT_TEXT):
            assert apart, (name, x0, y0, x1, y1)
    # (main session, 2026-11-14: the full build no longer makes the empty detail text panes, ui.PICK_CELL_TEXT;
    # then there is nothing to stand on, and the bounds checked above are the place's whole check)
    detail_panes = [name for name in now.names()[sb.index + 1:sb.end] if name.startswith(DETAIL_TEXT)]
    assert under or not detail_panes, "the detail texts are no longer where the prompt stands: its place wants another look"
    assert [k.text for k, rec, _ in check.root.ptr[0xC0].items if rec.ptr[0x0].text == ui.KEY].count(PROMPT_KEY) == 1
    return out, prompt


def installed():
    """The mod folders the normal emulator has now, in the order it layers them (by name;
    of a file several have, the first one's is used), ours left out."""
    load = build.EDEN_LOAD
    out = sorted(f for f in os.listdir(load) if os.path.isdir(os.path.join(load, f)) and f != MOD)
    assert out == sorted(out, key=str.lower), "the folders' order depends on how capitals are sorted"
    assert all(MOD < f for f in out), "a folder sorts before ours"
    return out


def resolve(rel):
    """(folder, path) of a romfs file as the game gets it now."""
    for folder in installed():
        path = os.path.join(build.EDEN_LOAD, folder, "romfs", *rel.split("/"))
        if os.path.exists(path):
            return folder, path
    raise AssertionError("no installed folder ships " + rel)


def read_installed(rel):
    return open(resolve(rel)[1], "rb").read()


def write_files(page_layout, ours, msgs, page_msgs):
    """Our versions of the files the picker's folder ships: the layout archive (the picker's
    layout with the prompt and the two animations, ours added), the category list, the
    screen table and the messages (ours, and the prompt's `page_msgs` added to the
    picker's)."""
    B = build
    sizes = B.SIZES
    # the layout archive: every layout four times
    rel = ui.COMMON
    raw = read_installed(rel)
    entries, meta = sarcedit.parse(nxfmt.unzs(raw, rel))
    name = ui.LAYOUT + ".buil"
    assert [n for n, _ in entries].count(name) == ui.LAYOUT_COPIES and LAYOUT + ".buil" not in [n for n, _ in entries]
    assert len(set(d for n, d in entries if n == name)) == 1
    entries = [(n, page_layout if n == name else d) for n, d in entries] + [(LAYOUT + ".buil", ours)] * ui.LAYOUT_COPIES
    new = sarcedit.build(entries, meta)
    back = nxfmt.sarc_entries(new)
    assert [e for e, _, _ in back].count(LAYOUT + ".buil") == ui.LAYOUT_COPIES
    assert nxfmt.sarc_files(new)[name] == page_layout and nxfmt.sarc_files(new)[LAYOUT + ".buil"] == ours
    B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), sizes, size=ui.align32(len(new)) + 4144)
    # the category list
    rel = ui.CATEGORY
    raw = read_installed(rel)
    data = nxfmt.unzs(raw, rel)
    cat = byml.load(data)
    assert byml.dump(cat, byml.version_of(data)) == data
    lst = cat["Common"]
    assert ui.LAYOUT in lst and LAYOUT not in lst
    lst.insert(bisect.bisect([x.lower() for x in lst], LAYOUT.lower()), LAYOUT)
    new = byml.dump(cat, byml.version_of(data))
    B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), sizes, size=ui.align32(len(new)) + 264)
    # the screen table: a row of our own, as the page's, one sort key higher
    rel = B.UISCREEN
    raw = read_installed(rel)
    data = nxfmt.unzs(raw, rel)
    rows = byml.load(data)
    assert byml.dump(rows, byml.version_of(data)) == data
    page = [r for r in rows if r["Name"] == B.PAGE_SCREEN]
    assert len(page) == 1 and page[0]["BodyLayoutFile"] == ui.LAYOUT and not any(r["Name"] == SCREEN for r in rows)
    assert page[0]["DrawUnitId"] == SCREEN_ROW["DrawUnitId"] and page[0]["SortKey"] == SCREEN_ROW["SortKey"] - 1
    assert not any(r.get("DrawUnitId", 0) == SCREEN_ROW["DrawUnitId"] and r["SortKey"] == SCREEN_ROW["SortKey"] for r in rows)
    rows.append(dict(page[0], Name=SCREEN, BodyLayoutFile=LAYOUT, **SCREEN_ROW))
    rows.sort(key=lambda r: r["Name"].encode())
    new = byml.dump(rows, byml.version_of(data))
    assert byml.load(new) == rows
    B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), sizes)
    # the messages
    msg = "LayoutMsg/%s.msbt" % LAYOUT
    for rel in ui.MALS:
        raw = read_installed(rel)
        sarc = nxfmt.unzs(raw, rel)
        files = nxfmt.sarc_files(sarc)
        assert msg not in files and ui.MSG in files
        m = msbt.Msbt(files[ui.MSG_BASE])
        for label, text in msgs.items():
            assert len(label) < 64, label
            m.add(label, text)
        page = msbt.Msbt(files[ui.MSG])
        for label, text in page_msgs.items():
            page.add(label, text)
        entries, meta = sarcedit.parse(sarc)
        assert [n for n, _ in entries].count(ui.MSG) == 1
        new = sarcedit.build([(n, page.to_bytes() if n == ui.MSG else d) for n, d in entries] + [(msg, m.to_bytes())], meta)
        after = nxfmt.sarc_files(new)
        assert all(after[n] == d for n, d in files.items() if n != ui.MSG) and set(after) == set(files) | {msg}
        back, back_page, old_page = msbt.Msbt(after[msg]), msbt.Msbt(after[ui.MSG]), msbt.Msbt(files[ui.MSG])
        assert all(back.get(label) == text for label, text in msgs.items())
        assert all(back_page.get(label) == text for label, text in page_msgs.items())
        assert all(back_page.get(label) == old_page.get(label) for label in old_page.labels)
        cells = [label for label in old_page.labels if label.startswith(DETAIL_TEXT)]
        # (no such texts at all when the build does not make their panes, ui.PICK_CELL_TEXT: main session, 2026-11-14)
        assert not any(old_page.get(label) for label in cells), \
            "the picker's detail texts are not empty: the prompt for X stands where they are drawn (PROMPT_DROP)"
        B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), sizes, size=ui.align32(len(new)) + 312)


def write_gamedata():
    """The installed GameDataList with our Ints (not saved, 0 at the start), appended in
    place with the bookkeeping the normal build does for its own (tools\\gamedata.py)."""
    B = build
    rel = gamedata.PATH
    raw = read_installed(rel)
    data = nxfmt.unzs(raw, rel)
    old = byml.load(data)
    taken = {e["Hash"] for t, lst in old["Data"].items() if t != "Bool64bitKey" for e in lst}
    assert gamedata.murmur3_32(B.CHARACTER_FLAG) in taken, "the installed list is not the mod's"
    expect = copy.deepcopy(old)
    values = []
    # by name: FLAG, the six options, the owner; by hash alone: the options kept per stored number
    for h in [gamedata.murmur3_32(name) for name in flags() + [KEEP_OWNER]] + [KEEP_BASE + k for k in range(KEEP_COUNT)]:
        assert h not in taken, hex(h)
        taken.add(h)
        values.append({"DefaultValue": 0, "Hash": h, "ResetTypeValue": 0, "SaveFileIndex": -1})
        expect["Data"]["Int"].append(dict(values[-1]))
    tmpl = next(i for i, e in enumerate(old["Data"]["Int"]) if set(e) == set(values[0]) and e["DefaultValue"] == 0)
    p = bympatch.Patch(data)
    p.append_dicts(["Data", "Int"], tmpl, values)
    for key in ("AllDataSaveOffset", "AllDataSaveSize"):
        p.add_int(["MetaData", key], 8 * len(values))
        expect["MetaData"][key] += 8 * len(values)
    new = p.bytes()
    assert byml.load(new) == expect, "GameDataList edit check failed"
    B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), B.SIZES)
    return len(values)


def write_table():
    """The size table: every installed table's entries, each at its largest, and ours.
    -> (tables read, entries raised for our files)."""
    B = build
    tables = []
    for folder in installed():
        path = os.path.join(B.EDEN_LOAD, folder, "romfs", *rstb.PATH.split("/"))
        if os.path.exists(path):
            tables.append(rstb.Rstb(nxfmt.read(path)))
    assert tables, "no installed folder ships a size table"
    t = rstb.Rstb(tables[0].to_bytes())
    for other in tables[1:]:
        for h, v in other.crc.items():
            t.crc[h] = max(t.crc.get(h, 0), v)
        for k, v in other.names.items():
            t.names[k] = max(t.names.get(k, 0), v)
    raised = 0
    for path, size in sorted(B.SIZES.items()):
        if t.get(path) is None:         # a new file: only our recoloured textures are that
            assert path.startswith("Tex/") and zlib.crc32(path.encode()) not in t.crc, "a file no installed folder has: " + path
        old, new = t.ensure(path, size)
        raised += old is None or new > old
        if not path.startswith("Tex/"):
            print("  size table: %s %s -> %s" % (path, old, new))
    stock = open(os.path.join(B.ROMFS, rstb.PATH), "rb").read()
    B._write_raw(MOD, rstb.PATH, nxpack.zs_compress_like(t.to_bytes(), stock))
    back = rstb.Rstb(nxfmt.read(os.path.join(B.BUILD, MOD, "romfs", *rstb.PATH.split("/"))))
    for other in tables:        # nothing an installed folder asks for is smaller in ours
        assert all(back.crc.get(h, 0) >= v for h, v in other.crc.items())
        assert all(back.names.get(k, 0) >= v for k, v in other.names.items())
    assert all(back.get(path) >= size for path, size in B.SIZES.items())
    return len(tables), raised


# ---------------------------------------------------------------- the graph
# Sounds. The game's menu sounds belong to screens: SeqOneShotStartSound (0x80e02110) takes
# a screen name and a key, finds that screen's object and plays the key on its sound user,
# "UI_<screen name>" in the sound link file (SLink2/slink2...bslnk); the name "GlobalSound"
# plays on the user UI_GlobalSound instead. A key the user does not have is silent, which
# is what the first build of this mode was (snaildev, 2026-11-13, "there's no sounds"): its
# keys were asked from the header's user (the template node's MenuOverlay_00), which has
# only the tab sounds. slink_users() reads who has what; the build checks every pair.
# The second build compared four sources, a button each (snaildev, 2026-11-13): the header's
# tab sound played (Left), the + menu's page slide asked from the + menu's own screen did
# not (Right: that screen is loaded for good but closed while our page shows), and
# selecting had "the wrong sound" (the shop's cursor from GlobalSound on Down; Key Items'
# cursor from its closed screen on Up, which he did not tell apart). What he wants: the
# sound the + menu's equipment page makes when the cursor moves over its slots, here and
# on the character page itself; a page turn for a changed value is right.
# So: the page turn stays the header's (ZL / ZR, as the picker's own page turn), and
# everything else is asked from GlobalSound, the one user that plays whatever screen is
# open. It has no sound of the menus, so three of its entries are given other users'
# sounds in the sound link file (BORROWED, write_slink): the entry keeps its key and gets
# the parameter block of the other user's entry. Blocks are shared between users in the
# game's own file (4,144 of its 14,960 are), and every UI sound is in one archive that is
# always loaded (Sound/Resource/UI_Static...bars), so nothing else has to change. The
# three keys are the amiibo marker's sounds, which the game plays when an amiibo is
# scanned: with this folder on they sound like the menus.
SOUNDS = {
    "move": ("GlobalSound", "AmiiboMarker_OK"),
    "open": ("GlobalSound", "AmiiboMarker_StartUp"),
    "back": ("GlobalSound", "AmiiboMarker_Cancel"),
    "ZL": ("MenuOverlay_00", "mc_TabToLeft"),
    "ZR": ("MenuOverlay_00", "mc_TabToRight"),
}
# GlobalSound's key -> the (user, key) whose sound it plays
BORROWED = {
    "AmiiboMarker_OK": ("UI_PlusMenu_00", "mc_button_on_EquipmentMenu"),        # the cursor over the equipment page's slots
    "AmiiboMarker_StartUp": ("UI_ImportantMenu_00", "mc_OpenDetail"),           # Key Items: an item's detail opens
    "AmiiboMarker_Cancel": ("UI_ImportantMenu_00", "mc_ReturnFromDetail"),      # ... and closes
}
PICKER_MOVE = ("MenuOverlay_00", "mc_CursorMove")       # what the picker asks for on a cursor move: silent, see above
SLINK = "SLink2/slink2.Product.111.bslnk.zs"
TAKES_FRAMES = {"SeqExecuteGenericWaitFrame", "SeqExecuteWaitCreateActor", "SeqExecuteWaitScreenAnimEnd",
                "WaitEquip.module", "SeqExecuteLoadAndOpenScreen", "SeqExecuteCloseAndUnloadScreen"}


def _kids(nodes, i):
    return [c["Node Index"] for c in nodes[i].get("Plugs", {}).get("Child", [])]


def _inputs(node):
    return {p["Name"]: p.get("Default Value") for ps in node.get("Parameters", {}).get("Inputs", {}).values() for p in ps}


def slink_users(d, only=None):
    """{user name: {key: (where its entry is, asset number, parent entry, parameter block,
    condition)}} of a sound link file (XLNK, version 0x22). The file: +0x48 the number of
    users, +0x50 where the users' data ends (the condition table), +0x58 the name table;
    from +0x60 the users' name hashes (crc32, sorted), then, 8-aligned, each one's offset.
    A user's data starts with its counts (+8 entries, +0xc sounds) and ends with its
    entries, 48 bytes each: +0 the key's place in the name table, +8 the asset number (-1:
    a group whose members are the entries that name it as their parent), +0x10 the parent
    entry (-1 none), +0x18 the key's crc32, +0x20 the parameter block (its place in the
    file's one table of blocks), +0x28 the condition (-1 none). An entry without parent and
    condition is what a key asked for by name plays."""
    import zlib
    assert d[:4] == b"XLNK" and struct.unpack_from("<I", d, 8)[0] == 0x22
    count = struct.unpack_from("<I", d, 0x48)[0]
    end, names_at = struct.unpack_from("<QQ", d, 0x50)
    hashes = struct.unpack_from("<%dI" % count, d, 0x60)
    offsets = struct.unpack_from("<%dQ" % count, d, (0x60 + 4 * count + 7) & ~7)
    assert list(hashes) == sorted(hashes) and all(0x60 < o < end for o in offsets) and end < names_at
    names, pos = {}, names_at
    while pos < len(d):
        e = d.index(b"\0", pos)
        if e > pos:
            names[pos - names_at] = bytes(d[pos:e])
        pos = e + 1
    by_hash = {zlib.crc32(n): n.decode("latin1") for n in names.values()}
    order = sorted(offsets) + [end]
    out = {}
    for h, o in zip(hashes, offsets):
        user = by_hash[h]
        if only is not None and user not in only:
            continue
        stop = order[order.index(o) + 1]
        entries, found, p = {}, 0, o
        while p <= stop - 48:
            key, asset, _flag, _w, parent, _guid, key_hash = struct.unpack_from("<QhHiiII", d, p)
            if key in names and zlib.crc32(names[key]) == key_hash:
                param, cond = struct.unpack_from("<Qi", d, p + 0x20)
                entries.setdefault(names[key].decode("latin1"), (p, asset, parent, param, cond))
                found += 1
                p += 48
            else:
                p += 4
        assert found == struct.unpack_from("<I", d, o + 8)[0], (user, found)     # every entry the user counts
        out[user] = entries
    return out


def write_slink():
    """The sound link file with GlobalSound's BORROWED entries on the other users' parameter
    blocks: 8 bytes each, nothing else. -> the lines of the report."""
    B = build
    stock = os.path.join(B.ROMFS, *SLINK.split("/"))
    shipped = [f for f in installed() if os.path.exists(os.path.join(B.EDEN_LOAD, f, "romfs", *SLINK.split("/")))]
    assert not shipped, "an installed folder has a sound link file of its own: %s" % shipped
    raw = open(stock, "rb").read()
    old = nxfmt.unzs(raw, SLINK)
    d = bytearray(old)
    wanted = {"UI_GlobalSound"} | {user for user, _key in BORROWED.values()} | {"UI_" + s for s, _k in SOUNDS.values()}
    users = slink_users(d, wanted)
    lines = []
    for key, (user, source) in BORROWED.items():
        at, asset, parent, param, cond = users["UI_GlobalSound"][key]
        _at, s_asset, _parent, s_param, _cond = users[user][source]
        # ours: a sound played by its name; theirs: a sound (not a group)
        assert asset >= 0 and parent == -1 and cond == -1 and s_asset >= 0 and param != s_param, (key, user, source)
        struct.pack_into("<Q", d, at + 0x20, s_param)
        lines.append("%s = %s of %s" % (key, source, user))
    changed = [i for i in range(len(d)) if d[i] != old[i]]
    now = slink_users(d, wanted)
    assert len(d) == len(old) and 0 < len(changed) <= 8 * len(BORROWED)
    for user in wanted:
        for key, entry in users[user].items():
            want = entry
            if user == "UI_GlobalSound" and key in BORROWED:
                src_user, src_key = BORROWED[key]
                want = entry[:3] + (users[src_user][src_key][3],) + entry[4:]
            assert now[user][key] == want, (user, key)
    # what the graph asks for is there: a key of its user, played by name
    rows = {r["Name"]: r for r in byml.load(nxfmt.unzs(read_installed(B.UISCREEN), B.UISCREEN))}
    for screen, key in SOUNDS.values():
        _at, _asset, parent, _param, cond = now["UI_" + screen][key]
        assert parent == -1 and cond == -1, (screen, key)
        assert screen == "GlobalSound" or rows[screen].get("LifetimeGroup") == "Resident", screen
    # the first build's mistake, as a test of the reader: the picker's cursor key is not the header's
    assert PICKER_MOVE[1] not in now["UI_" + PICKER_MOVE[0]] and PICKER_MOVE[1] in now["UI_ImportantMenu_00"]
    B.write(MOD, SLINK, nxpack.zs_compress_like(bytes(d), raw), len(d))         # same size: the game's own table entry
    return lines, len(changed)


def t_sound(what):
    screen, key = SOUNDS[what]
    return build.vanilla("ImportantMenu", 69, "SeqOneShotStartSound", ScreenName=screen, SLinkKey=key)


def neighbour(f, button):
    """The option the D-pad or the left stick goes to from option f, or None: up and down
    inside a column, left and right to the same row of the other column; no going round
    (as in the picker's grid)."""
    side, i = divmod(f, ROWS)
    to = {"Up": f - 1 if i > 0 else None, "Down": f + 1 if i < ROWS - 1 else None,
          "Left": f - ROWS if side > 0 else None, "Right": f + ROWS}[button]
    return to if to is not None and 0 <= to < len(OPTIONS) else None


def picker_loops(nodes, init):
    """The picker's button loops, in its list's order, and {stored character number: its
    loop}. A loop is what a gate (a root sequence of two waited frames; one per character,
    made in the list's order) leads to; the numbers are the cases of the selector the page
    starts with (`init`: by the saved character to that character's place, anything else
    to the first)."""
    loops = []
    gate_to = {}
    for i, n in enumerate(nodes):
        if n["Node Type"] == "Element_Sequential" and "Is Root Node" in n.get("Flags", []) and \
                [nodes[c].get("Name") for c in _kids(nodes, i)] == ["SeqExecuteGenericWaitFrame"] * 2:
            gate_to[i] = n["Plugs"]["Transition"][0]["Node Index"]
            loops.append(gate_to[i])
    assert len(loops) == len(set(loops)) >= 2, len(loops)
    for L in loops:
        assert nodes[L]["Node Type"] == "Element_Simultaneous" and "Is Root Node" in nodes[L]["Flags"]
        assert len(_kids(nodes, L)) == 1
    sel = nodes[_kids(nodes, init)[0]]
    query = nodes[sel["Parameters"]["Inputs"]["Int"][0]["Node Index"]]
    assert sel["Node Type"] == "Element_S32Selector" and \
        [x["Default Value"] for x in query["Parameters"]["Inputs"]["String"]] == [build.CHARACTER_FLAG]
    numbers = {}
    for plug in sel["Plugs"]["Child"]:
        jump = nodes[plug["Node Index"]]
        assert jump.get("Name") == "SeqOneShotEnd"
        start = nodes[jump["Plugs"]["Transition"][0]["Node Index"]]         # shows that character's place ...
        loop = gate_to[start["Plugs"]["Transition"][0]["Node Index"]]       # ... and goes to its gate
        if plug.get("Is Default"):
            assert loop == loops[0]
        else:
            assert plug["Condition"] not in numbers
            numbers[plug["Condition"]] = loop
    assert sorted(numbers.values()) == sorted(loops) and numbers[0] == loops[0]
    return loops, numbers


def customize_graph(g):
    """The picker's graph (a Graph of the installed file) with the mode added; returns what
    the checks need."""
    B = build
    nodes = g.nodes
    S, PAGE = SCREEN, B.PAGE_SCREEN

    def only(name):
        hit = [i for i, n in enumerate(nodes) if n.get("Name") == name]
        assert len(hit) == 1, (name, hit)
        return hit[0]

    chk, pad = only("SeqQueryControllerTrigMaskChecker"), only(ui.RELEASE_CHECKER)
    root = g.d["Commands"][0]["Root Node Index"]
    init = nodes[root]["Plugs"]["Transition"][0]["Node Index"]
    assert "Is Root Node" in nodes[init]["Flags"]
    loops, numbers = picker_loops(nodes, init)
    before = {L: _kids(nodes, L)[0] for L in loops}
    out_x = [o for o in nodes[chk]["Parameters"]["Outputs"]["Bool"] if o["Name"] == "TrigX"]
    assert len(out_x) == 1 and not out_x[0]["Is Output"]            # the picker does not ask for X
    out_x[0]["Is Output"] = True
    assert nodes[chk]["Parameters"]["Outputs"]["Bool"].index(out_x[0]) == B.TRIG["X"]
    # the page's entry: FLAG is 0 whatever it was left at
    enter_page = _kids(nodes, root)[0]
    assert nodes[enter_page]["Node Type"] == "Element_Simultaneous" and \
        "SeqOneShotOpenScreen" in [nodes[c].get("Name") for c in _kids(nodes, enter_page)]
    nodes[enter_page]["Plugs"]["Child"].append({"Node Index": g.add(B.t_set_int(FLAG, 0)), "Name": ""})

    ends = {}

    def go(target):
        if target not in ends:
            ends[target] = g.add(B.t_end(target))
        return ends[target]

    def value_sel(f, cases, default):
        """By option f's number: the child of its case, `default` for 0 and anything else."""
        q = g.add(B.vanilla("ResetGimmickImpl", 8, "SeqQueryGetNonStructGameDataInt", GameDataFullName=option_flag(f)))
        assert nodes[q]["Plugs"]["Generic"] == [{"Node Index": 6, "Name": "Value"}]
        sel = g.add(ui.t_int_sel(g, q, cases, default))
        nodes[q]["Plugs"]["Generic"][0]["Node Index"] = sel
        return sel

    n = len(OPTIONS)
    many = [f for f in range(n) if len(OPTIONS[f][2]) > 1]
    sound = {what: g.add(t_sound(what)) for what in SOUNDS}
    cloops = [g.add(B.t_simultaneous([], root=True)) for _ in range(n)]
    cgates = [g.add(B.t_sequential([g.add(B.t_wait_frames(1)), g.add(B.t_wait_frames(1))], then=cloops[f],
                                   root=True, same_frame=False)) for f in range(n)]
    stop_focus = [g.add(B.t_stop_anim(S, "Focus", float(f))) for f in range(n)]
    stop_val = {(f, v): g.add(B.t_stop_anim(S, "Val_%02d" % f, float(v))) for f in many for v in range(len(OPTIONS[f][2]))}

    # X: the picker's panes go and the picture starts for the middle in the frame of the
    # press; our screen is loaded and opened (frames; nothing of it shows: SHOW), then in one
    # frame the first option is marked, every option gets its value and SHOW is asked for
    first = g.add(B.t_simultaneous([g.add(B.t_add_int(FLAG, 1)), g.add(B.t_play_anim(PAGE, PAGE_OUT)),
                                    sound["open"]], root=False))
    shown = g.add(B.t_simultaneous([
        g.add(B.vanilla("MinusMenu", 22, "SeqOneShotSetScreenVisible", ScreenName=S, IsVisible=True)),
        stop_focus[0]], root=False))
    values = [value_sel(f, [(v, stop_val[(f, v)]) for v in range(1, len(OPTIONS[f][2]))], stop_val[(f, 0)]) for f in many]
    enter = g.add(B.t_sequential(
        [first, g.add(B.vanilla("ActionGuide", 6, "SeqExecuteLoadAndOpenScreen", ScreenName=S)), shown] + values
        + [g.add(B.t_play_anim(S, SHOW))], then=cgates[0], root=True))
    # B / -: back. One step a frame: the wait starts the frame after the animation was asked for
    leave = g.add(B.t_sequential([
        g.add(B.t_simultaneous([g.add(B.t_add_int(FLAG, -1)), g.add(B.t_play_anim(S, HIDE)),
                                g.add(B.t_play_anim(PAGE, PAGE_IN)), sound["back"]], root=False)),
        g.add(B.t_wait_anim(S, HIDE)),
        g.add(B.t_close_unload_screen(S)),
    ], then=init, root=True, same_frame=False))

    idle = g.add(B.vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))
    moves = {}
    for f in range(n):
        chain = idle
        count = len(OPTIONS[f][2])
        if f in many:           # ZR / ZL: the next / previous value, going round
            for button, step in (("ZR", 1), ("ZL", -1)):
                cases = []
                for v in range(count):
                    w = (v + step) % count
                    change = g.add(B.t_sequential([stop_val[(f, w)], g.add(B.t_set_int(option_flag(f), w)),
                                                   sound[button]], then=cgates[f], root=True))
                    cases.append((v, go(change)))
                turn = g.add(B.t_simultaneous([value_sel(f, cases[1:], cases[0][1])], root=True))
                chain = g.add(B.t_bool_sel(chk, B.TRIG[button], go(turn), chain))
        for button in ("Right", "Left", "Down", "Up"):      # D-pad or left stick: another option
            to = neighbour(f, button)
            if to is None:
                continue
            if to not in moves:
                moves[to] = g.add(B.t_sequential([stop_focus[to], sound["move"]], then=cgates[to], root=True))
            chain = g.add(B.t_bool_sel(pad, B.TRIG[button], go(moves[to]), chain))
        chain = g.add(B.t_bool_sel(chk, B.TRIG["Minus"], go(leave), chain))
        chain = g.add(B.t_bool_sel(chk, B.TRIG["B"], go(leave), chain))
        nodes[cloops[f]]["Plugs"] = B._plugs([chain])

    # the picker's own cursor sound: asked from a user that has no such key, so silent since
    # the picker exists; now the equipment page's (snaildev, 2026-11-13)
    asked = [i for i, node in enumerate(nodes[:len(nodes)]) if node.get("Name") == "SeqOneShotStartSound" and
             (_inputs(node)["ScreenName"], _inputs(node)["SLinkKey"]) == PICKER_MOVE]
    assert len(asked) == 1, asked
    moved = t_sound("move")
    nodes[asked[0]]["Parameters"] = moved["Parameters"]
    # the picker's loops: X first, then everything as it was
    go_enter = go(enter)
    for L in loops:
        plug = nodes[L]["Plugs"]["Child"]
        plug[0]["Node Index"] = g.add(B.t_bool_sel(chk, B.TRIG["X"], go_enter, before[L]))
    return dict(loops=loops, numbers=numbers, before=before, cloops=cloops, init=init, chk=chk, pad=pad,
                enter=enter, leave=leave)


def check_graph(nodes, cmd, info, layouts):
    """Runs the graph as the game would for every button in every loop of ours and for X
    in every loop of the picker. -> number of runs."""
    B = build
    trig = {v: k for k, v in B.TRIG.items()}
    loops, cloops, numbers = info["loops"], info["cloops"], info["numbers"]
    number_of = {L: v for v, L in numbers.items()}
    where = {r: ("pick", c) for c, r in enumerate(loops)}
    where.update({r: ("custom", f) for f, r in enumerate(cloops)})
    checkers = ("SeqQueryControllerTrigMaskChecker", ui.RELEASE_CHECKER)
    n = len(OPTIONS)

    def run(i, st):
        """Runs node i; returns the root node it jumps to, a command name, or None."""
        node = nodes[i]
        t, name = node["Node Type"], node.get("Name")
        if t == "Element_StateEnd":
            return node["Properties"]["String"][0]["Default Value"]
        if t == "Element_BoolSelector":
            q = node["Parameters"]["Inputs"]["Bool"][0]
            qn = nodes[q["Node Index"]].get("Name")
            yes = True                  # may the menu close; the swap's own questions
            if qn in checkers:
                out = nodes[q["Node Index"]]["Parameters"]["Outputs"]["Bool"][q["Output Index"]]
                assert out["Is Output"] and out["Name"] == ("Trig" if qn == checkers[0] else "Release") + trig[q["Output Index"]]
                yes = trig[q["Output Index"]] == st["button"]
            return run(_kids(nodes, i)[0 if yes else 1], st)
        if t == "Element_S32Selector":
            query = nodes[node["Parameters"]["Inputs"]["Int"][0]["Node Index"]]
            assert query.get("Name") == "SeqQueryGetNonStructGameDataInt"
            flag = [x["Default Value"] for x in query["Parameters"]["Inputs"]["String"]][0]
            assert flag not in st["queued"], "%s is asked for in the frame it was set" % flag
            plugs = node["Plugs"]["Child"]
            hit = [p for p in plugs if not p.get("Is Default") and p.get("Condition") == st["flags"].get(flag, 0)]
            assert len(hit) <= 1 and sum(bool(p.get("Is Default")) for p in plugs) == 1
            return run((hit or [p for p in plugs if p.get("Is Default")])[0]["Node Index"], st)
        if t in ("Element_Sequential", "Element_Simultaneous"):
            for c in _kids(nodes, i):
                r = run(c, st)
                if r is not None:
                    return r
            tr = node.get("Plugs", {}).get("Transition", [])
            return tr[0]["Node Index"] if tr else None
        v = {p["Name"]: p.get("Default Value") for ps in node.get("Parameters", {}).get("Inputs", {}).values() for p in ps}
        if name == "SeqOneShotEnd":
            return node["Plugs"]["Transition"][0]["Node Index"]
        if name in TAKES_FRAMES:
            st["frames"] += 1
            st["queued"].clear()
        if name in ("SeqOneShotRequestStopAnimationScreen", "SeqOneShotRequestPlayAnimationScreen"):
            play = "Play" in name
            frames = layouts[v["ScreenName"]]
            assert v["AnimationName"] in frames, v
            if play:
                assert not v["IsReverse"]
            else:               # the game ignores a stop beyond the last frame
                assert 0 <= v["Frame"] <= frames[v["AnimationName"]] and v["Frame"] == int(v["Frame"]), v
            st["log"].append(("play" if play else "stop", v["ScreenName"], v["AnimationName"]) + (() if play else (int(v["Frame"]),)))
        elif name == "SeqOneShotSetNonStructGameDataInt":
            st["flags"][v["GameDataFullName"]] = v["Value"]
            st["queued"].add(v["GameDataFullName"])
        elif name == "SeqOneShotAddNonStructGameDataInt":
            st["flags"][v["GameDataFullName"]] = st["flags"].get(v["GameDataFullName"], 0) + v["Value"]
            st["queued"].add(v["GameDataFullName"])
        elif name in ("SeqExecuteLoadAndOpenScreen", "SeqExecuteCloseAndUnloadScreen", "SeqExecuteWaitScreenAnimEnd",
                      "SeqOneShotSetScreenVisible"):
            st["log"].append((name, v["ScreenName"]) + ((v["AnimName"],) if "AnimName" in v else ()))
        elif name == "SeqOneShotStartSound":
            st["log"].append(("sound", v["ScreenName"], v["SLinkKey"]))
        return None

    def settle(i, st):
        st["button"] = None
        for _ in range(30):
            if isinstance(i, str) or i in where:
                return i if isinstance(i, str) else where[i]
            i = run(i, st)
            assert i is not None, "the graph stops outside a button loop"
        raise AssertionError("no button loop reached")

    def press(loop, button, flags_):
        st = {"button": button, "flags": dict(flags_), "queued": set(), "frames": 0, "log": []}
        r = run(loop, st)
        end = None if r is None else settle(r, st)
        assert end is None or st["frames"] >= 2, "back at a button loop in the frame of the press"
        return end, st

    def order(log, *want):
        at = [log.index(w) for w in want]
        assert at == sorted(at), (want, log)

    count = 0
    S, PAGE = SCREEN, B.PAGE_SCREEN
    place_of = {v: loops.index(L) for v, L in numbers.items()}
    samples = [{}, {option_flag(f): len(OPTIONS[f][2]) - 1 for f in range(n)}, {option_flag(0): 3, option_flag(2): 99}]
    # the page's entry
    st = {"button": None, "flags": {FLAG: 7}, "queued": set(), "frames": 0, "log": []}
    assert settle(run(cmd, st), st) == ("pick", 0) and st["flags"][FLAG] == 0
    count += 1
    # X in every loop of the picker
    moved = 0
    assert not [1 for node in nodes if node.get("Name") == "SeqOneShotStartSound" and
                (_inputs(node)["ScreenName"], _inputs(node)["SLinkKey"]) == PICKER_MOVE]
    for c, L in enumerate(loops):
        for fl in samples:
            end, st = press(L, "X", dict(fl, **{FLAG: 0, B.CHARACTER_FLAG: number_of[L]}))
            assert end == ("custom", 0) and st["flags"][FLAG] == 1, (c, end)
            log = st["log"]
            order(log, ("play", PAGE, PAGE_OUT), ("SeqExecuteLoadAndOpenScreen", S), ("SeqOneShotSetScreenVisible", S),
                  ("stop", S, "Focus", 0), ("play", S, SHOW))
            assert log[-1] == ("play", S, SHOW) or log[-2:] == [("play", S, SHOW), ("sound",) + SOUNDS["open"]] or \
                [e for e in log if e[1] == S][-1] == ("play", S, SHOW)       # asked for last: after every value
            for f in range(n):
                value = fl.get(option_flag(f), 0)
                value = value if 0 <= value < len(OPTIONS[f][2]) else 0
                stops = [e for e in log if e[:3] == ("stop", S, "Val_%02d" % f)]
                assert stops == ([("stop", S, "Val_%02d" % f, value)] if len(OPTIONS[f][2]) > 1 else []), (f, stops)
            assert [e for e in log if e[0] == "sound"] == [("sound",) + SOUNDS["open"]]
            count += 1
        # every other button: the node that was the loop's whole content before
        sel = nodes[_kids(nodes, L)[0]]
        q = sel["Parameters"]["Inputs"]["Bool"][0]
        assert sel["Node Type"] == "Element_BoolSelector" and (q["Node Index"], q["Output Index"]) == (info["chk"], B.TRIG["X"])
        assert _kids(nodes, _kids(nodes, L)[0])[1] == info["before"][L]
        # ... and a move of the picker's cursor asks for the sound that plays
        for button in ("Up", "Down", "Left", "Right"):
            end, st = press(L, button, {FLAG: 0, B.CHARACTER_FLAG: number_of[L]})
            sounds = [e for e in st["log"] if e[0] == "sound"]
            assert (end is None and not sounds) or (end[0] == "pick" and sounds == [("sound",) + SOUNDS["move"]]), (c, button, sounds)
            moved += end is not None
            count += 1
    assert moved >= len(loops)                  # the picker's cursor does move
    # every button in every loop of ours
    for f, L in enumerate(cloops):
        values = OPTIONS[f][2]
        for button in sorted(B.TRIG) + [None]:
            for chosen in (number_of[loops[0]], number_of[loops[-1]], 1, 10 ** 6):
                for fl in samples + [{option_flag(f): v} for v in range(len(values))]:
                    start = dict(fl, **{FLAG: 1, B.CHARACTER_FLAG: chosen})
                    end, st = press(L, button, start)
                    log, now = st["log"], st["flags"]
                    count += 1
                    if button in ("Up", "Down", "Left", "Right") and neighbour(f, button) is not None:
                        to = neighbour(f, button)
                        assert end == ("custom", to) and log == [("stop", S, "Focus", to), ("sound",) + SOUNDS["move"]] and now == start
                    elif button in ("ZL", "ZR") and len(values) > 1:
                        step = 1 if button == "ZR" else -1
                        old = start.get(option_flag(f), 0)
                        new = ((old if 0 <= old < len(values) else 0) + step) % len(values)
                        assert end == ("custom", f) and now == dict(start, **{option_flag(f): new}), (f, button, old, now)
                        assert log == [("stop", S, "Val_%02d" % f, new), ("sound",) + SOUNDS[button]], log
                    elif button in ("B", "Minus"):
                        assert end == ("pick", place_of.get(chosen, 0)) and now == dict(start, **{FLAG: 0}), (f, button, end)
                        order(log, ("play", S, HIDE), ("SeqExecuteWaitScreenAnimEnd", S, HIDE), ("SeqExecuteCloseAndUnloadScreen", S))
                        assert ("play", PAGE, PAGE_IN) in log and ("sound",) + SOUNDS["back"] in log
                        assert not [e for e in log if e[0] in ("stop", "play") and e[1] == S and e[2] != HIDE]
                    else:
                        assert end is None and now == start and not log, (f, button, end, log)
    # every root node can be reached from the entry
    roots = {i for i, node in enumerate(nodes) if "Is Root Node" in node.get("Flags", [])}

    def jumps(i, acc):
        node = nodes[i]
        if node.get("Name") == "SeqOneShotEnd":
            acc.add(node["Plugs"]["Transition"][0]["Node Index"])
        for tr in node.get("Plugs", {}).get("Transition", []):
            acc.add(tr["Node Index"])
        for c in _kids(nodes, i):
            jumps(c, acc)
        return acc

    seen, todo = {cmd}, [cmd]
    while todo:
        for t in jumps(todo.pop(), set()):
            if t in roots and t not in seen:
                seen.add(t)
                todo.append(t)
    assert seen == roots, sorted(roots - seen)[:10]
    used = {(e["ScreenName"], e["AnimationName"]) for e in (
        {p["Name"]: p.get("Default Value") for ps in node["Parameters"]["Inputs"].values() for p in ps}
        for node in nodes if node.get("Name") in ("SeqOneShotRequestStopAnimationScreen", "SeqOneShotRequestPlayAnimationScreen"))}
    assert {(S, a) for a in layouts[S] if a in ("In", "Out", "Focus") or a.startswith("Val_")} <= used
    assert {(PAGE, PAGE_OUT), (PAGE, PAGE_IN)} <= used
    return count


# ---------------------------------------------------------------- the code patch
# x20 = the picture offset (x in its low half, y in its high half), x19 = the camera. Called
# with bl in place of the store and left with ret (the function keeps its return address in
# its frame; README, Hookshot section: a routine entered with bl leaves with ret, and its
# entry is its lowest address). x8-x17 and v16-v18 are free there: every one of x8-x13 is
# written before it is read on each way on from the store, the function never uses
# x14-x17 or a vector register above v15. The GameData lookup is the one of
# build.gamedata_subs, inline (Int map +0xe0, capacity +0xec, records +0xd8, count +0xd0,
# 0x28 each, value +0x1c), so the patch needs no other mod's code.
HOOK = """
    cv_hook:
        adr x8, cv_consts
        ldr w9, [x8]
        cmp w20, w9
        b.ne cv_out
        adrp x11, #{got_page:#x}
        ldr x11, [x11, #{got_off:#x}]
        ldr x11, [x11]
        cbz x11, cv_out
        ldr w14, [x8, #4]
        ldr x12, [x11, #0xe0]
        cbz x12, cv_out
        ldr w13, [x11, #0xec]
        cbz w13, cv_out
        udiv w15, w14, w13
        msub w15, w15, w13, w14
        mov w16, w15
    cv_probe:
        ldr x17, [x12, w16, uxtw #3]
        cmp w17, w14
        b.eq cv_found
        cbz w17, cv_out
        add w16, w16, #1
        cmp w16, w13
        csel w16, wzr, w16, hs
        cmp w16, w15
        b.ne cv_probe
        b cv_out
    cv_found:
        lsr x17, x17, #32
        ldr w12, [x11, #0xd0]
        cmp w17, w12
        b.hs cv_out
        ldr x12, [x11, #0xd8]
        mov w13, #0x28
        umaddl x12, w17, w13, x12
        ldr w13, [x12, #0x1c]
        cmp w13, #{top}
        b.hi cv_out
        lsr w15, w13, #1
        tbz w13, #0, cv_down
        cmp w15, #{glide}
        b.hs cv_blend
        add w13, w13, #2
        b cv_store
    cv_down:
        cbz w15, cv_out
        sub w13, w13, #2
    cv_store:
        str w13, [x12, #0x1c]
        lsr w15, w13, #1
    cv_blend:
        ucvtf s16, w15
        ldr s17, [x8, #8]
        fmul s16, s16, s17
        fmov s17, #3.0
        fmov s18, #2.0
        fmsub s17, s18, s16, s17
        fmul s16, s16, s16
        fmul s16, s16, s17
        fmov s17, w20
        ldr s18, [x8, #12]
        fsub s18, s18, s17
        fmadd s17, s18, s16, s17
        fmov w13, s17
        bfxil x20, x13, #0, #32
    cv_out:
        stur x20, [x19, #0xcc]
        ret
    cv_consts:
        .byte {consts}
"""
# cv_consts, after the code (in one piece with it): the page view's offset as bits, FLAG's
# hash, 1 / GLIDE, VIEW_CENTRE
CONSTS = 16


def f32_bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def custom_patch(facts):
    """The Patcher: the constants and the routine in DEAD, the store turned into a call; and the
    colour routines (colour_patch). -> (the Patcher, the colour code's end, the tables' size)"""
    import npc_characters as npc
    img = exepatch.Image()
    npc.check_unused_node(img, DEAD_NODE, DEAD, DEAD_SHA1)
    assert img.dis(VIEW_STORE - 8, 4) == [
        "8289d4c8: stp      s4, s3, [x19, #0xc0]", "8289d4cc: str      s5, [x19, #0xbc]",
        "8289d4d0: stur     x20, [x19, #0xcc]", "8289d4d4: ldr      x8, [x0, #8]"]
    assert img.func_start(VIEW_STORE) == img.func_start(build.CAMERA_BLOCK_LOADED) == 0x8289D110
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = DEAD
    try:
        p = exepatch.Patcher(image=img, cave=DEAD)      # the signature words go to DEAD's start
    finally:
        exepatch.CAVE_REGION = region

    def place(name, addr, blob):
        """As Patcher._place, but over the unused routine's code instead of zeros."""
        assert any(lo <= addr and addr + len(blob) <= hi for lo, hi in (DEAD, COLOUR_CODE, COLOUR_MORE, COLOUR_KEEP, COLOUR_TEX)), \
            "the unused routine is full (%s, %x, %d bytes)" % (name, addr, len(blob))
        p.raw(addr, blob)
        p.symbols[name] = addr
        p.cave_ptr = addr + len(blob)
        return addr
    p._place = place
    consts = struct.pack("<IIff", f32_bits(build.VIEW_OFFSET_X), gamedata.murmur3_32(FLAG), 1.0 / GLIDE, VIEW_CENTRE)
    assert len(consts) == CONSTS
    got = build.GAMEDATA_MGR_GOT
    hook = p.cave("cv_hook", HOOK.format(consts=", ".join("%d" % c for c in consts), got_page=got & ~0xFFF,
                                         got_off=got & 0xFFF, glide=GLIDE, top=2 * GLIDE + 1))
    blob = dict(p.patches)[hook]
    assert len(blob) % 4 == 0 and blob[-CONSTS:] == consts
    p.asm(VIEW_STORE, "bl {cv_hook}", expect="stur x20, [x19, #0xcc]")
    assert p.cave_ptr <= DEAD[1]
    view_end = p.cave_ptr
    code_end, tables = colour_patch(p, facts)
    assert p.cave_ptr == view_end
    return p, code_end, tables


def check_patch(p):
    """The routine run in an ARM64 emulator against a GameData manager laid out as the
    game's, for every value of FLAG and offsets that are and are not the page's.
    -> number of runs."""
    import random
    import unicorn
    from unicorn import arm64_const as A
    hook = p.symbols["cv_hook"]
    code = dict(p.patches)[hook][:-CONSTS]
    ins = list(p.img.cs.disasm(code, hook))
    assert len(ins) * 4 == len(code), "the routine does not disassemble"
    # one way out, a ret at its end; no call; every branch stays inside and none goes below the entry
    assert [i.mnemonic for i in ins].count("ret") == 1 and ins[-1].mnemonic == "ret"
    assert not any(i.mnemonic in ("bl", "blr", "br") for i in ins)
    for i in ins:
        if i.mnemonic == "b" or i.mnemonic.startswith("b.") or i.mnemonic in ("cbz", "cbnz", "tbz", "tbnz"):
            target = int(i.op_str.split("#")[-1], 16)
            assert hook <= target < hook + len(code), (i.mnemonic, i.op_str)
    site = list(p.img.cs.disasm(dict(p.patches)[VIEW_STORE], VIEW_STORE))
    assert len(site) == 1 and site[0].mnemonic == "bl" and site[0].op_str == "#%#x" % hook
    used = set()
    for i in ins:
        for tok in i.op_str.replace("[", " ").replace("]", " ").replace(",", " ").split():
            if tok[:1] in "xwsdv" and tok[1:].isdigit():
                used.add((tok[0] in "sdv", int(tok[1:])))
    assert {r for v, r in used if not v} <= set(range(8, 18)) | {19, 20} and {r for v, r in used if v} <= {16, 17, 18}, used
    h = gamedata.murmur3_32(FLAG)
    got = build.GAMEDATA_MGR_GOT
    page_bits = f32_bits(build.VIEW_OFFSET_X)
    DATA, STACK, RET = 0x20000000, 0x30000000, 0x40000000
    CELL, MGR, MAP, REC, CAM = (DATA + o for o in (0, 0x100, 0x1000, 0x2000, 0x3000))
    keep = [A.UC_ARM64_REG_X0 + i for i in list(range(8)) + [18, 19] + list(range(21, 29))] + [A.UC_ARM64_REG_X29]
    keep_v = [A.UC_ARM64_REG_V0 + i for i in range(16)]
    runs = [0]

    def f32(x):
        return struct.unpack("<f", struct.pack("<f", x))[0]

    def run(x20, value, mgr=True, flag=True, cap=8, crowd=False):
        mu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        for page in sorted({DEAD[0] & ~0xFFF, (DEAD[1] - 1) & ~0xFFF, got & ~0xFFF, RET}):
            mu.mem_map(page, 0x1000)
        for addr, data in p.patches:
            if DEAD[0] <= addr < DEAD[1]:
                mu.mem_write(addr, data)
        mu.mem_write(got, struct.pack("<Q", CELL))
        mu.mem_map(DATA, 0x10000)
        mu.mem_map(STACK, 0x10000)
        mu.mem_write(CELL, struct.pack("<Q", MGR if mgr else 0))
        mu.mem_write(MGR + 0xe0, struct.pack("<Q", MAP))
        mu.mem_write(MGR + 0xec, struct.pack("<I", cap))
        mu.mem_write(MGR + 0xd0, struct.pack("<I", 5))
        mu.mem_write(MGR + 0xd8, struct.pack("<Q", REC))
        slot = h % cap
        if crowd:           # another flag sits in our first place: ours is one further on
            mu.mem_write(MAP + 8 * slot, struct.pack("<II", h ^ 0x10000, 1))
            slot = (slot + 1) % cap
        if flag:
            mu.mem_write(MAP + 8 * slot, struct.pack("<II", h, 3))
        mu.mem_write(REC + 3 * 0x28 + 0x1c, struct.pack("<I", value & 0xFFFFFFFF))
        sp = STACK + 0x8000
        mu.reg_write(A.UC_ARM64_REG_SP, sp)
        mu.reg_write(A.UC_ARM64_REG_X20, x20)
        mu.reg_write(A.UC_ARM64_REG_X30, RET)
        for k, r in enumerate(keep):
            mu.reg_write(r, CAM if r == A.UC_ARM64_REG_X19 else 0x1111000 + k)
        for k, r in enumerate(keep_v):
            mu.reg_write(r, (0x2222 << 64) | (0x3333000 + k))
        for r in range(8, 18):      # what the routine may change: nothing it relies on
            mu.reg_write(A.UC_ARM64_REG_X0 + r, random.getrandbits(64))
        mu.emu_start(hook, RET, count=2000)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == RET and mu.reg_read(A.UC_ARM64_REG_SP) == sp
        assert [mu.reg_read(r) for r in keep] == [CAM if r == A.UC_ARM64_REG_X19 else 0x1111000 + k for k, r in enumerate(keep)]
        assert [mu.reg_read(r) for r in keep_v] == [(0x2222 << 64) | (0x3333000 + k) for k in range(16)]
        out = mu.reg_read(A.UC_ARM64_REG_X20)
        assert struct.unpack("<Q", bytes(mu.mem_read(CAM + 0xcc, 8)))[0] == out, "the offset is not stored"
        runs[0] += 1
        return out, struct.unpack("<I", bytes(mu.mem_read(REC + 3 * 0x28 + 0x1c, 4)))[0]

    def want(x20, value):
        """(offset, FLAG) after one camera update."""
        if x20 & 0xFFFFFFFF != page_bits or not 0 <= value <= 2 * GLIDE + 1:
            return x20, value & 0xFFFFFFFF
        count, bit = value >> 1, value & 1
        if bit and count < GLIDE:
            count += 1
        elif not bit:
            if count == 0:
                return x20, value
            count -= 1
        t = count / GLIDE
        s = t * t * (3 - 2 * t)
        page = f32(build.VIEW_OFFSET_X)
        return (page + (f32(VIEW_CENTRE) - page) * s, x20 >> 32), 2 * count + bit

    def check(x20, value, **kw):
        out, now = run(x20, value, **kw)
        exp, exp_flag = want(x20, value)
        if isinstance(exp, tuple):
            x = struct.unpack("<f", struct.pack("<I", out & 0xFFFFFFFF))[0]
            assert abs(x - exp[0]) < 2e-7 and out >> 32 == exp[1] and now == exp_flag, (hex(x20), value, x, exp, now)
        else:
            assert out == exp and now == exp_flag, (hex(x20), value, hex(out), now)
        return out

    high = [0, f32_bits(0.125), 0xFFFFFFFF]
    for value in list(range(0, 2 * GLIDE + 2)) + [2 * GLIDE + 2, 2 * GLIDE + 3, 1000, -1, -2]:
        for hi in high:
            check(page_bits | hi << 32, value)
        for kw in ({"cap": 64}, {"crowd": True}, {"cap": 1}):
            check(page_bits, value, **kw)
        for other in (0, f32_bits(1 / 6), f32_bits(-1 / 6), page_bits ^ 1, f32_bits(VIEW_CENTRE), 0x8000000000000000 | page_bits ^ 0x80000000):
            assert run(other, value) == (other, value & 0xFFFFFFFF)
        for kw in ({"mgr": False}, {"flag": False}):
            assert run(page_bits, value, **kw) == (page_bits, value & 0xFFFFFFFF)
    # a whole visit: X, the way to the middle, a rest there, B, the way back, a rest on the page
    value, xs = 1, []
    for _ in range(GLIDE + 5):
        out = check(page_bits, value)
        value = want(page_bits, value)[1]
        xs.append(struct.unpack("<f", struct.pack("<I", out & 0xFFFFFFFF))[0])
    assert value == 2 * GLIDE + 1 and xs == sorted(xs) and abs(xs[-1] - VIEW_CENTRE) < 2e-7 and xs[GLIDE - 1] == xs[-1]
    value -= 1
    back = []
    for _ in range(GLIDE + 5):
        out = check(page_bits, value)
        value = want(page_bits, value)[1]
        back.append(struct.unpack("<f", struct.pack("<I", out & 0xFFFFFFFF))[0])
    assert value == 0 and back == sorted(back, reverse=True) and back[-1] == f32(build.VIEW_OFFSET_X)
    assert struct.pack("<f", back[GLIDE - 1]) == struct.pack("<I", page_bits)      # exactly the page's again
    return runs[0]


def ips_ranges(data):
    """[(start, end)] of the bytes an IPS32 file changes (file offsets)."""
    assert data[:5] == b"IPS32"
    o, out = 5, []
    while data[o:o + 4] != b"EEOF":
        at, n = struct.unpack_from(">IH", data, o)
        o += 6
        if n == 0:                                  # a run of one byte
            n = struct.unpack_from(">H", data, o)[0]
            o += 3
        else:
            o += n
        out.append((at, at + n))
    return out


def check_installed_patches():
    """No byte of the executable that an installed folder's patch changes is changed by
    ours. -> number of patch files looked at."""
    import glob
    B = build
    mine = ips_ranges(open(os.path.join(B.BUILD, MOD, "exefs", exepatch.BUILD_ID + ".ips"), "rb").read())
    count = 0
    for folder in installed():
        for path in glob.glob(os.path.join(B.EDEN_LOAD, folder, "exefs", "*.ips")):
            count += 1
            for a0, a1 in ips_ranges(open(path, "rb").read()):
                assert all(a1 <= b0 or b1 <= a0 for b0, b1 in mine), "%s patches our bytes at %x" % (folder, a0)
    return count


# ---------------------------------------------------------------- hair and eye colours
# The first two options that change the character (2026-11-13; README, mods list, this
# folder, "Colours"). What the game's own data says:
# - Hair is coloured by two shader parameters of the hair material, ns_param_color0 and
#   ns_param_color1: two women share one hair texture and differ in colour 0 alone (red,
#   green); 58 values among 90 hair materials.
# - The iris is coloured by ns_param_color2 of Mt_Eye_Inner, read as a shift of the iris
#   texture's own colour: x = hue (0.5 = as the texture, which is blue, hue 0.608, for the
#   50 characters that share one; Impa and Sheik, red eyes, have 0.97 and 0.89), y =
#   saturation, z = brightness (0.5 = as it is), w something else (kept as the model has it).
# - Clothes are textures; no parameter colours them (velvet and satin have a sheen colour).
# How the game sets such a value on a living model (its own code at 0x80232e2c does it for
# colour 0 of every material of a model): through the model unit's virtual functions,
# +0xd0 material index by name, +0xe0 parameter index by name, +0x158 "set four floats"; the
# unit is nn::g3d underneath, a material object's first word is the material of the model
# file, whose +0x58 is the file's own value block (the same offsets), which is how "Default"
# puts a model's own colours back.
# Where our routine runs (second cut; the first, at the equipment sync's call of the hair
# routine, never reached the field character: read from snaildev's running game, his model's
# values were the file's own while the menu character's were ours):
# - cz_head, in place of the call of the head gear update (0x804d756c) in the routine every
#   player-like actor's update runs each frame (0x804d74b0: the field player's update
#   0x82c3d740 calls it through 0x804d7190, two more actor updates call it directly): the
#   head gear update as before, then the colours and the gear for that actor, where an
#   option is not 0. So a character that is made anew, woken or shown has them a frame later.
# - cz_frame, at the 3D UI camera update's way out (0x8289d600), while FLAG is not 0
#   (customizing, or gliding back): for the menu character and for the field character (the
#   pouch's target, asleep under the menu) on every update, and there an option at 0 puts
#   the model's own values back, so a change shows at once and "Default" is the model again.
#   There the function is about to reload x19-x24 and return, so nothing is live but the
#   callee-saved registers.
# The code lives in the enter and update routines of the node class SeqExecuteLoadFile and
# the enter routine of SeqQueryCreate3DLayer, the tables in the routines of
# SeqExecuteSaveFile: no file of the game names any of the three classes (check_dead).
COLOUR_CODE_NODE, COLOUR_CODE = "SeqExecuteLoadFile", (0x832E1620, 0x832E19F0)
COLOUR_DATA_NODE, COLOUR_DATA = "SeqExecuteSaveFile", (0x832E39D0, 0x832E3C00)
COLOUR_CODE_SHA1 = "11a664b9c4b74a885260c9d0cd2528e905b52d9c"
COLOUR_DATA_SHA1 = "907e84f7d213ef307dc8427c13d19ac0a66565d8"
COLOUR_MORE_NODE, COLOUR_MORE = "SeqQueryCreate3DLayer", (0x8103AE80, 0x8103B160)
COLOUR_MORE_SHA1 = "9495f7da79875d832a52a7e7dcccfc448793e7f1"
HEAD_CALL, HEAD_ROUTINE = 0x804D756C, 0x804D7B20
FRAME_SITE = 0x8289D600
POUCH_GOT = 0x84C9E0D0              # -> the variable that holds the pouch; its +0x470 is its target's equipment component
# Equipment (read from snaildev's running game, a child, 2026-11-13): the equipment component
# is [[actor + 0x270] + 0x200] (its +0x18 the actor); it has 21 handles of 0x18 bytes from
# +0x20 (+0x8 link, +0x10 id). Seen: 0 the sword, 1 the item in hand (a bottle, asleep), 4
# the shield; then the thirteen "slots" of the game's sync routine from +0xe0: 8 the head
# (a mask), 12 the sword's sheath, 16 the ocarina, 17 a gauntlet, 20 the ocarina's holder.
# "Hidden" clears the "shown" bit (build.ACTOR_SHOWN) of the actors in GEAR_HANDLES:
# everything but the item in hand, the head and 17 to 19. The first cut had only
# the thirteen: sheath and ocarina went, sword and shield stayed (snaildev).
# Which handle is what, from the game's code (2026-11-14; README, player models, "The
# equipment's 21 handles"): the routine that puts a finished piece into its handle
# (0x80398360) takes for 9, 10 and 11 the same kind of actor as for the head (armour): they
# are the adult's Goron / Zora tunic, Iron / Hover boots and gauntlets, which are drawn IN
# PLACE of materials of the body that the piece hides. Until then they were hidden with
# the gear (the mask was 0x11FEFD; snaildev's looks were as a child, who has none of them),
# which would have left an adult in a tunic without a body there: they are out of the mask
# since. 17 (the child's Goron bracelet), 18 (the Stone of Agony) and 19 stay shown as before.
MENU_GLOBAL = 0x84E9CFA8            # the game's "[x + 0x4e8]" menu actor manager hangs on what this holds
MODEL_TYPE_GOT = 0x84CD9D58         # what the hair routine compares the model component's +0xf8 with
GEAR_FIRST, GEAR_COUNT = 0x20, 21
GEAR_HANDLES = 0x11F0FD
# The options are kept per character (snaildev: "this will be tracked separately for each
# character"): the six Ints the graph reads and writes are the options of the character
# whose number KEEP_OWNER holds; an Int a stored number (GameData knows an Int by a hash
# only: theirs are KEEP_BASE + the number, no names) holds a character's six options, four
# bits each. cz_keep, on every update of the menu's camera: the stored number is the
# owner's -> his Int gets the six; it is another -> the six are loaded from that one's Int
# and he is the owner. Not saved yet: they last until the game is closed.
KEEP_OWNER = "CharacterMenu_CustomOwner"
KEEP_BASE, KEEP_COUNT = 0x1337C000, 128
COLOUR_KEEP_NODE, COLOUR_KEEP = "SeqExecuteRequestSaveGameData", (0x83BCDC10, 0x83BCDDE0)
COLOUR_KEEP_SHA1 = "8640703a62099a5702116449729d974746f35ac7"
# Colour 0 and colour 1 of the hair, linear, as the game's own hair has them: Zelda's (adult),
# Ingo's, darker than any of the game's, Nabooru's, a Kokiri boy's, Saria's; blue, pink and
# white are ours, made as bright as those.
HAIR_COLOURS = {
    "Blonde": ((1.0, 0.392, 0.0), (0.791, 0.312, 0.0)),
    "Brown": ((0.40, 0.16, 0.07), (0.47, 0.14, 0.13)),
    "Black": ((0.06, 0.05, 0.05), (0.12, 0.11, 0.11)),
    "Red": ((0.72, 0.06, 0.02), (0.69, 0.09, 0.17)),
    "Orange": ((0.79, 0.35, 0.043), (0.90, 0.45, 0.10)),
    "Green": ((0.464, 0.631, 0.0975), (0.457, 0.658, 0.034)),
    "Blue": ((0.08, 0.25, 0.75), (0.10, 0.40, 0.85)),
    "Pink": ((0.90, 0.22, 0.42), (0.95, 0.40, 0.55)),
    "White": ((0.80, 0.82, 0.85), (0.90, 0.90, 0.95)),
}
# The iris: colour 2 as it is set for a character with the common iris texture (hue 0.608).
# What the three numbers do, from snaildev's look at the first cut (2026-11-13: "gold turned
# out bright red, brown is black, green is hazel, grey is light grey, violet is red to
# pink") and the game's own values: each is added to the texture's colour, twice its
# distance from a half, and the result is CLAMPED, not wrapped: hue' = hue + 2 (x - 0.5)
# held to 0..1 (both ends are red), the same for saturation (y) and brightness (z). So a
# hue wanted is x = 0.5 + (hue - 0.608) / 2: 0.376 green, 0.26 gold, 0.23 orange brown,
# 0.586 violet; red is anything far enough (Impa's 0.97). Another iris texture: the same,
# less half of how far its hue is from 0.608 (model_facts; D_HUE holds that per number).
EYE_COLOURS = {
    "Blue": (0.5, 0.5, 0.5),
    "Green": (0.376, 0.50, 0.455),
    "Brown": (0.231, 0.42, 0.42),
    "Gray": (0.5, 0.12, 0.45),
    "Red": (0.95, 0.56, 0.45),
    "Violet": (0.586, 0.48, 0.46),
    "Gold": (0.2585, 0.56, 0.50),
}
# (the second cut's brightnesses were 0.04 to 0.06 higher: "a bit bright / light for natural eye colors", snaildev)
# Materials that are hair. The first is the one the player's code asks for by name; a
# character without hair has a stand-in under that name (npc_characters.HAIR_STAND_INS),
# which must keep its own colours (model_facts: is it hair). The others are hair wherever
# a model has them.
HAIR_NAMES = ("Mt_Hairs", "Mt_HairLong", "Mt_Hair_Front", "Mt_Hair1", "Mt_Hair3", "Mt_Ponytail", "Mt_BodyHair", "Mt_Eyebrow")
EYE_NAME = "Mt_Eye_Inner"
PARAM_NAMES = ("ns_param_color0", "ns_param_color1", "ns_param_color2")
COMMON_IRIS_HUE = 0.608
D_CLOTHES = 0x18                        # the three clothing colours' options' hashes (and four bytes to spare)
D_PARAM, D_NAMES = 0x28, 0x58
D_EYE = D_NAMES + 16 * len(HAIR_NAMES)
D_HAIRS = D_EYE + 16
D_BROWS = D_HAIRS + 6 * len(HAIR_COLOURS)       # the eyebrows' colour 0 and colour 1 a hair colour, as the hair's own table before it
D_EYES = (D_BROWS + 6 * len(HAIR_COLOURS) + 7) & ~7
D_MASK = (D_EYES + 3 * len(EYE_COLOURS) + 7) & ~7
D_HUE = D_MASK + 16


# Hair by textures, the proof (2026-11-13; snaildev: "let's go ahead and try hair"). A hair's
# colour is its texture's, so a hair colour is another texture on the living material. A
# material of a model file lists its textures (names at +0x20, as many as the byte +0xa3
# says) and its samplers (the byte +0xa2), one texture a sampler in the game's files. The
# proof gives a material MORE textures than samplers: the ones behind the samplers' own
# are a palette the game loads with the model and nothing draws; cz_tex then puts a
# palette entry in the place of the hair's own colour texture on the living material.
# What two tries and two reads of snaildev's running game said (2026-11-14):
# - A model with more textures than samplers loads. But the game asks for, and binds, only
#   as many of a material's textures as the byte +0xa2 says: the model loader's loop
#   (0x803819c0), its binder (0x803802d4), a second binder (0x80457404) and nn::g3d's own
#   bind and release (0x814ebe98, 0x814ebf40) all read +0xa2, while the living material's
#   lists are as long as +0xa3 says. In every material of the game the two are equal (770
#   model files, 2,341 materials looked at), so reading +0xa3 in those five places changes
#   nothing for the game's own models and makes a palette load: COUNT_PATCHES.
# - A living material holds its textures as two lists, +0x50 the views and +0x58 their
#   descriptor slots (the file's lists, as bound: views +0x18, slots +0x98; +0x90 is the
#   samplers' slots), and the drawing does not follow a write to those lists: the engine's
#   own routine for it (0x834c5910: material object, place, {view, slot}) writes them and
#   then calls what the material object's +0x78 points at, which is how the drawing hears
#   of it. cz_tex calls that routine.
# The third try (snaildev, 2026-11-14): "Yes it works for Link", with other characters' hair
# textures as the palette; and so the hair was "thicker, like it's missing a layer that
# trims away to form proper wisps" (another character's cut-out) and the colours were not
# the names'. Since then the palette is each character's OWN hair texture recoloured
# (tools\texrecolor.py: the colours of every compressed block's end points are changed in
# place, so the cut-out, the strands, the mips and the size are the texture's own), on
# every hair material of every player model the installed folders ship.
PALETTE_MATERIALS = ("Mt_Hairs", "Mt_HairLong", "Mt_Hair_Front", "Mt_Hair1", "Mt_Hair3", "Mt_Ponytail", "Mt_BodyHair")
# What a hair colour makes of a hair texture: (hue, saturation, brightness) its mean gets;
# the strands' own differences in the three stay (half of them in hue). As stored (sRGB).
# For scale: Link's hair texture is (0.10, 0.66, 0.59), Saria's (0.19, 0.80, 0.51),
# Nabooru's (0.99, 0.38, 0.26).
HAIR_TEXTURE = {
    "Blonde": (0.125, 0.60, 0.74),
    "Brown": (0.07, 0.62, 0.30),
    "Black": (0.07, 0.30, 0.09),
    "Red": (0.015, 0.85, 0.48),
    "Orange": (0.066, 0.82, 0.70),
    "Green": (0.29, 0.75, 0.46),
    "Blue": (0.60, 0.72, 0.50),
    "Pink": (0.93, 0.45, 0.80),
    "White": (0.10, 0.06, 0.88),
}
MAT_SAMPLERS, MAT_TEXTURES = 0xA2, 0xA3         # a material record's two counts
MAT_VIEWS, MAT_NAMES, MAT_SLOTS = 0x18, 0x20, 0x98
OBJ_VIEWS, OBJ_SLOTS = 0x50, 0x58               # a material object's two lists: its textures' views, their descriptor slots
SET_TEXTURE = 0x834C5910                        # the engine's: (material object, place, &{view, slot})
# the loops over a material's textures that count them by the byte for the samplers: (address, register, base)
COUNT_PATCHES = ((0x803819C0, "w19", "x22"), (0x803802D4, "w23", "x26"), (0x80457404, "w8", "x28"),
                 (0x814EBE98, "w8", "x0"), (0x814EBF40, "w10", "x0"))
COLOUR_TEX_NODE, COLOUR_TEX = "SeqExecuteLoadStartupMap", (0x83BCCE70, 0x83BCD000)
COLOUR_TEX_SHA1 = "b78d8b76d0675afa93169b2850c7df1205c03a12"

# Clothing by textures (2026-11-14; snaildev: "Let's keep working on getting clothing colors
# working"). The same way as the hair: a palette of recoloured copies of a material's own
# colour texture behind its samplers' own, and the engine's routine puts the Clothing Color
# option's entry in the first place. What is different:
# - WHICH materials and WHICH of their colours. A character's clothes are several
#   materials, and a clothing texture holds several colours (Link's vest: the green, the
#   brown belt, the pale trim). So a clothing colour replaces ONE colour of a character,
#   his clothes' main one, wherever his clothing textures have it, and leaves the others.
#   The main colour is read from the model: every triangle of every clothing material is
#   weighed by its area and the texel at its UVs (cloth_facts), and cloth_main decides: a
#   hue, where one hue covers 35% or more of the clothes; else the largest clothing
#   material's own hue, where that covers a fifth of it (a white outfit with a coloured
#   vest: the game's own colour variants of such models differ in that accent); else the
#   unsaturated part of the clothes, "neutral" (Malon's cream dress, an Iron Knuckle's
#   armour). Clothing materials are all but the ones CLOTH_NOT names (skin, face, eyes,
#   hair, teeth, weapons, metal, footwear, stones); a creature has none.
# - Each clothing texture then gets its copies if enough of it is that colour: within
#   CLOTH_HUE_OUT of the texture's own peak nearest the character's hue, or unsaturated
#   (cloth_weight); cloth_fn turns that part to the colour (as hair_fn does the whole
#   hair: the hue, the saturation about the part's mean, the brightness scaled) and blends
#   by the weight. A texture two characters share gets one set of copies while they agree
#   on what its colour is (the copies' names carry a letter a kind: palette_name).
# - Most clothing textures are ASTC, which tools\texrecolor.py decodes and encodes again,
#   block by block (README, player models, "Clothing colours").
# - Every material of the model that draws a recoloured texture gets the palette (the hat's
#   band draws the vest's texture). The code tells a clothing palette from a hair palette
#   by its length (cz_cloth), so the two must differ.
# - A colour a PART (--parts; snaildev after his look at the one colour: the vest is another
#   green than the tunic, the belt changes with nothing). A part is whole materials: every
#   clothing material has a main colour of its OWN (cloth_own: what a fifth of its surface
#   shows), the materials of one texture and one colour are a piece, pieces of one hue
#   (within CLOTH_MERGE: Link's tunic and his shorts are one weave in two textures) are one
#   part. The part that has the character's main colour is the first, the next two by area
#   the second and third; a smaller part follows the part nearest its hue (within
#   CLOTH_FOLLOW) or keeps its colour (cloth_parts). Each material then gets the copies of
#   its texture for ITS colour, so two materials of one texture can have two colours (a
#   shirt and trousers painted in one texture). What whole materials cannot do: two colours
#   INSIDE one material (a villager whose outfit is one material: 41 of 107 characters);
#   that needs the model's triangles divided between two materials, which is not built.
# - The code tells the three colours' palettes, and the hair's, apart by their LENGTH:
#   CLOTH_LENGTHS (7, 8, 10: the copies and, to fill, the last one again; 9 is the hair's).
#   A name twice in a material's list is nothing new to the game (462 of its own materials
#   have one).
CLOTHES_FLAGS = tuple("CharacterMenu_Custom" + k for k in CLOTH_KEYS)       # (GameData has the Ints of the options that are built)
CLOTH_MERGE, CLOTH_FOLLOW, CLOTH_SLOTS = 0.02, 0.05, 3        # (0.035 left adult Link's tunic trim, 0.048 from the weave, green)
# The order of the parts (snaildev's look at the three colours, 2026-11-14: on Link 1 was the tunic, 2 the vest, 3 the
# leather, "as expected"; on Saria 1 was the vest, 2 the boots and belt and 3 "the base"; on young Zelda 2 "does
# nothing", it was her petticoat, and 3 "should be the secondary"): the parts of the character's main colour first,
# the garment before what is worn over it; then the others by area, what is worn under the clothes last.
CLOTH_OVER, CLOTH_UNDER = ("Vest", "Cape", "Mant"), ("Panie", "Petticoat")
# name -> (hue, saturation, brightness, how much of the brightness is the colour's own: the rest is the garment's)
CLOTH_TEXTURE = {
    "Green": (0.30, 0.62, 0.36, 0.7),
    "Red": (0.995, 0.80, 0.48, 0.7),
    "Blue": (0.61, 0.70, 0.46, 0.7),
    "Purple": (0.77, 0.55, 0.42, 0.7),
    "Yellow": (0.14, 0.78, 0.74, 0.8),
    "Black": (0.60, 0.10, 0.09, 1.0),
    "White": (0.11, 0.05, 0.86, 1.0),
}
CLOTH_NOT = ("Skin", "Face", "Eye", "Hair", "Teeth", "Teet", "ChildTeeth", "Mouth", "Shadow", "Hand", "Head", "Rock", "Fur", "Feather",
             "Knife", "Blade", "Weapon", "Sword", "Spear", "Broom", "Flute", "Stone", "Gem", "Jewel", "Crystal", "Koume", "Kotake",
             "BossHead", "Glitter", "Snow", "Lens", "Weak", "Organ", "Score", "Leaves", "Flowers", "FrizzlePatch", "Ponytail",
             "BodyHair", "Metal", "Ring", "Chain", "Buckle", "Buttons", "Shoes", "Boots", "Sandal", "Mask", "FireHair", "IceHair",
             "WaistFur", "Body_Fur", "Body_AlphaMask")
CLOTH_HUE_IN, CLOTH_HUE_OUT = 0.065, 0.115      # a hue this near the main one is it wholly / not at all
CLOTH_BINS = 72                                 # hues are counted in bins this many round
CLOTH_TAGS = "cdefgijklmnopqrstuvwxyzab"        # (no h: the hair's)
CLOTH_VERSION = 2                               # of the recolouring: a change here makes every cached copy anew
CLOTH_FACTS = 2                                 # of what cloth_facts keeps of a model (2: the footwear too)
# how many textures a material has behind its samplers' own: the first, second, third clothing colour's palette
CLOTH_LENGTHS = [x for x in range(len(CLOTH_TEXTURE), len(CLOTH_TEXTURE) + 4) if x != len(HAIR_TEXTURE)][:CLOTH_SLOTS]


def cloth_material(name, names):
    """Is a material clothing, by its name and the model's other materials' names."""
    stem = name[3:] if name.startswith("Mt_") else name
    if stem in ("Hand", "Teet") or any(stem.startswith(x) for x in CLOTH_NOT if x not in ("Hand", "Teet")):
        return False
    return stem != "Body" or any(n.startswith("Mt_Skin") for n in names)       # a body beside a skin is a suit; alone it is the creature


def cloth_footwear(name):
    """Is a material footwear by its name (boots, shoes, sandals)? Footwear is no clothing: it has
    no say in a character's main colour and makes no part of its own, but it goes with the
    part of its hue (cloth_parts)."""
    stem = name[3:] if name.startswith("Mt_") else name
    return stem.startswith(("Shoes", "Boots", "Sandal"))


def _smooth(x, lo, hi):
    import numpy as np
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def cloth_weight(hsv, mode, centre):
    """(n, 3) hue, saturation, brightness -> (n,) how much a texel is the clothes' main colour:
    mode "hue": by how near its hue is to `centre` (not the greys, not the blacks); mode
    "neutral": the greys, whites and blacks."""
    import numpy as np
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    if mode == "hue":
        dh = np.abs(((h - centre + 0.5) % 1.0) - 0.5)
        return np.clip((CLOTH_HUE_OUT - dh) / (CLOTH_HUE_OUT - CLOTH_HUE_IN), 0.0, 1.0) * _smooth(s, 0.10, 0.22) * _smooth(v, 0.04, 0.10)
    return np.maximum(1.0 - _smooth(s, 0.20, 0.34), 1.0 - _smooth(v, 0.06, 0.12))


def cloth_fn(mode, centre, s_mean, v_mean, target):
    """What a clothing colour does to a texel of a clothing texture whose main colour is
    (mode, centre), of mean saturation and brightness (s_mean, v_mean)."""
    import math
    import numpy as np
    import texrecolor
    hue, sat, val, own = target
    vm = min(max(v_mean, 0.03), 0.97)
    goal = val ** own * vm ** (1.0 - own)
    gamma = max(math.log(goal) / math.log(vm), 0.3)        # (0.45 at first: white on Link's dark green came out mid grey, 0.51 to 0.59)

    def fn(rgb):
        hsv = texrecolor.rgb_to_hsv(rgb)
        w = cloth_weight(hsv, mode, centre)
        if mode == "hue":
            dh = ((hsv[:, 0] - centre + 0.5) % 1.0) - 0.5
            h2 = hue + 0.5 * dh
            s2 = np.clip(sat * np.sqrt(hsv[:, 1] / max(s_mean, 0.05)), 0.0, 1.0)
        else:
            h2 = np.full(len(hsv), hue)
            s2 = np.full(len(hsv), sat)
        v = np.clip(hsv[:, 2], 0.0, 1.0)
        v2 = v * (goal / vm) if goal < vm else np.power(v, gamma)      # darker: scaled; brighter: black stays black
        new = texrecolor.hsv_to_rgb(np.stack([h2, s2, v2], axis=1))
        return rgb + (new - rgb) * w[:, None]
    return fn


def _hue_peak(hist, around=None, reach=None, half=4):
    """(bin, hue, weight) of the largest window of 2 * half + 1 bins of a hue histogram; with
    `around` (a hue) and `reach` (bins): of the windows whose middle is that near it."""
    import numpy as np
    h = np.asarray(hist, dtype=np.float64)
    n = len(h)
    sm = sum(np.roll(h, k) for k in range(-half, half + 1))
    bins = range(n) if around is None else [(int(around * n) + k) % n for k in range(-reach, reach + 1)]
    i = max(bins, key=lambda b: sm[b])
    idx = [(i + k) % n for k in range(-half, half + 1)]
    ang = (np.array(idx) + 0.5) / n * 2 * np.pi
    hue = float(np.arctan2((h[idx] * np.sin(ang)).sum(), (h[idx] * np.cos(ang)).sum()) / (2 * np.pi) % 1.0) if h[idx].sum() > 0 else (i + 0.5) / n
    return i, hue, float(sm[i])


def cloth_main(mats):
    """The main colour of a model's clothes, from cloth_facts' numbers for its materials:
    ("hue", hue), ("neutral", None) or None (no clothes, or nothing to tell)."""
    import numpy as np
    cand = {n: r for n, r in mats.items() if not r.get("foot")}        # (cloth_facts keeps the clothing materials, and the footwear, which has no say here)
    area = sum(r["area"] for r in cand.values())
    if area <= 0:
        return None
    hist = np.sum([np.array(r["hist"]) for r in cand.values()], axis=0)
    _bin, hue, peak = _hue_peak(hist)
    if peak >= 0.35 * area:
        return ("hue", hue)
    big = max(cand.values(), key=lambda r: r["area"])
    _bin, hue, peak = _hue_peak(big["hist"])
    if peak >= 0.20 * big["area"]:
        return ("hue", hue)
    if big["grey"] >= 0.20 * big["area"]:
        return ("neutral", None)
    return None


def _hue_apart(a, b):
    return abs(((a - b + 0.5) % 1.0) - 0.5)


def cloth_own(rec):
    """A clothing material's OWN main colour, from what its surface shows: ("hue", hue) where
    one hue covers a fifth of it, else ("neutral", None) where a fifth of it is grey, else None."""
    if rec["area"] <= 0:
        return None
    _bin, hue, peak = _hue_peak(rec["hist"])
    if peak >= 0.20 * rec["area"]:
        return ("hue", hue)
    if rec["grey"] >= 0.20 * rec["area"]:
        return ("neutral", None)
    return None


def cloth_parts(mats, main):
    """The parts of a model's clothes, a clothing colour each (see CLOTHES_FLAGS above), from
    cloth_facts' numbers for its materials and its main colour (cloth_main).
    -> ({material: (which colour: 0, 1, 2; the material's own main colour)},
        [(which colour or None, share of the clothes, the part's colour, [materials])], largest first)"""
    feet = {n: r for n, r in mats.items() if r.get("foot")}
    mats = {n: r for n, r in mats.items() if not r.get("foot")}
    total = sum(r["area"] for r in mats.values())
    parts = []
    for name, rec in sorted(mats.items(), key=lambda kv: (-kv[1]["area"], kv[0])):
        own = cloth_own(rec)
        if own is None:
            continue
        same = [q for q in parts if q["colour"][0] == own[0]]
        # a piece: the materials of one texture and one colour; then pieces of one hue are one part
        p = next((q for q in same if rec["tex"] in q["textures"] and (own[0] != "hue" or _hue_apart(q["colour"][1], own[1]) <= 0.06)), None) or \
            next((q for q in same if own[0] == "hue" and _hue_apart(q["colour"][1], own[1]) <= CLOTH_MERGE), None)
        if p is None:
            p = {"colour": own, "area": 0.0, "members": [], "textures": set()}
            parts.append(p)
        p["area"] += rec["area"]
        p["members"].append((name, own))
        p["textures"].add(rec["tex"])
    if not parts or total <= 0:
        return {}, []
    order = sorted(parts, key=lambda q: -q["area"])

    def named(q, words):            # is the part's largest material one of these
        return any(w in q["members"][0][0] for w in words)
    like = [q for q in order if main is not None and q["colour"][0] == main[0] and (main[0] != "hue" or _hue_apart(q["colour"][1], main[1]) <= 0.06)]
    like = [q for q in like if q is like[0] or q["area"] >= 0.15 * total] or order[:1]      # (a small piece of the main colour is not a row of its own before the others)
    like.sort(key=lambda q: named(q, CLOTH_OVER))                   # (stable: by area, the vest after the tunic)
    rest = [q for q in order if not any(q is x for x in like)]
    rest.sort(key=lambda q: named(q, CLOTH_UNDER))
    slots = [q for k, q in enumerate(like + rest) if k == 0 or q["area"] >= 0.04 * total][:CLOTH_SLOTS]
    given, summary = {}, []
    for q in order:
        slot = next((k for k, x in enumerate(slots) if x is q), None)
        if slot is None and q["area"] <= 0.10 * total:      # a small part (trim): with the part nearest its hue, or the first grey part
            # (a larger one is a fourth garment and keeps its colour: adult Link's pale undershirt is 0.04 of a turn from his leather)
            if q["colour"][0] == "hue":
                near = sorted((_hue_apart(x["colour"][1], q["colour"][1]), k) for k, x in enumerate(slots) if x["colour"][0] == "hue")
                slot = near[0][1] if near and near[0][0] <= CLOTH_FOLLOW else None
            else:
                slot = next((k for k, x in enumerate(slots) if x["colour"][0] != "hue"), None)
        if slot is not None:
            for name, own in q["members"]:
                given[name] = (slot, own)
        summary.append((slot, q["area"] / total, q["colour"], [name for name, _own in q["members"]]))
    # Footwear makes no part; where it is a part's own hue (within CLOTH_MERGE, as pieces are one part) it goes with that
    # part, as child Link's boots, which are his leather material itself, go with his belt. (snaildev, 2026-11-14, after the
    # parts' order: "Only thing to fix is adult link's boots don't change with color 3 (the boots and flaps both stay
    # default)": his are materials of their own, Mt_Boots and Mt_Boots_Far, and footwear was no clothing at all until
    # then. The flaps, 0.037 of a turn from the leather, draw the boots' texture and so follow the boots.)
    for name, rec in sorted(feet.items()):
        own = cloth_own(rec)
        if own and own[0] == "hue":
            near = sorted((_hue_apart(x["colour"][1], own[1]), k) for k, x in enumerate(slots) if x["colour"][0] == "hue")
            if near and near[0][0] <= CLOTH_MERGE:
                given[name] = (near[0][1], own)
                summary.append((near[0][1], rec["area"] / total, own, [name]))
    return given, summary


def cloth_shows(rec, main):
    """Does a clothing material's SURFACE show the main colour: a twentieth of it within
    CLOTH_HUE_OUT of the hue, or a tenth of it grey. (A texture can have the colour where no
    triangle draws it: a quarter of adult Link's glove texture is a yellow green that his
    gloves do not show; by the texture alone his gloves got the palette.)"""
    if rec["area"] <= 0:
        return False
    if main[0] != "hue":
        return rec["grey"] >= 0.10 * rec["area"]
    b, reach = int(main[1] * CLOTH_BINS), int(round(CLOTH_HUE_OUT * CLOTH_BINS))
    return sum(rec["hist"][(b + k) % CLOTH_BINS] for k in range(-reach, reach + 1)) >= 0.05 * rec["area"]


def texture_file(name, unify):
    """(the texture whose file a texture name is drawn from, that file's path) or (None, None):
    an installed folder's, the game's, or the file of the name it is unified with."""
    for tex in (name, unify.get(name)):
        if not isinstance(tex, str):
            continue
        path = os.path.join(nxfmt.ROMFS, "Tex", tex + ".bntx.zs")
        try:
            path = resolve("Tex/%s.bntx.zs" % tex)[1]
        except AssertionError:
            pass
        if os.path.exists(path):
            return tex, path
    return None, None


def cloth_facts(models, unify):
    """{(number, age): {material: {"tex", "area", "hist", "grey"}}}: for every material of every
    player model that has a colour texture this can recolour, what its surface shows: every
    triangle's area (as stored: a rigid part's vertices are its bone's, which leaves the areas
    as they are) at three points, the texel at their UVs; "hist" the area by hue (CLOTH_BINS
    bins) of the coloured texels, "grey" the area of the others (grey, white, black), "area"
    both. Kept in build-custom\\cloth-facts.json by the model files' sizes and dates."""
    import json
    import bfres
    import numpy as np
    import npcportrait as pp
    import texrecolor
    cache_path = os.path.join(BUILD_CUSTOM, "cloth-facts.json")
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    textures = {}

    def texels(name):
        if name not in textures:
            textures[name] = None
            tex, path = texture_file(name, unify)
            if path:
                data = nxfmt.read(path)
                t = texrecolor.data_range(data)[0]
                if texrecolor.can_recolour(t):
                    textures[name] = (texrecolor.decoded(data), t.comp[3] == 5)
        return textures[name]

    def geometry(f, s):
        d = f.data
        base = f.sections[1]["pos"]
        vtx = f.u64(s.off + 0x10)
        assert d[vtx:vtx + 4] == b"FVTX"
        off, nattr, nbuf, _idx, count = struct.unpack_from("<IBBHI", d, vtx + 0x48)
        sizes = [f.u32(f.u64(vtx + 0x30) + 0x10 * i) for i in range(nbuf)]
        strides = [f.u32(f.u64(vtx + 0x38) + 0x10 * i) for i in range(nbuf)]
        starts, at = [], base + off
        for size in sizes:
            at = (at + 7) & ~7
            starts.append(at)
            at += size
        attrs = {}
        arr = f.u64(vtx + 8)
        for i in range(nattr):
            name = f.string(f.u64(arr + i * 0x10))
            fmt, _pad, aoff, bi = struct.unpack_from("<HHHH", d, arr + i * 0x10 + 8)
            if name in ("_p0", "_u0"):
                buf = np.frombuffer(bytes(d[starts[bi]:starts[bi] + sizes[bi]]), np.uint8)
                attrs[name] = pp._attr(buf, strides[bi], count, aoff, fmt)
        mesh = f.u64(s.off + 0x18)
        ioff, prim, ifmt, icount, first = struct.unpack_from("<5I", d, mesh + 0x20)
        assert prim == 3 and ifmt in (1, 2) and icount % 3 == 0, ("mesh", s.name, prim, ifmt)
        dt = "<u2" if ifmt == 1 else "<u4"
        tris = np.frombuffer(bytes(d[base + ioff:base + ioff + icount * np.dtype(dt).itemsize]), dt).astype(np.int64) + first
        assert tris.max() < count, s.name
        return attrs["_p0"][:, :3].astype(np.float64), attrs["_u0"], tris.reshape(-1, 3)

    out = {}
    for number, age, rel, path in models:
        key = "%d.%d|%s|%d|%d" % (CLOTH_VERSION, CLOTH_FACTS, path, os.path.getsize(path), int(os.path.getmtime(path)))
        if key not in cache:
            f = bfres.Fres(nxfmt.read(path))
            m = f.model()
            per = {}
            for s in m.shapes:
                name = m.material_names[s.material]
                tex = pp.material_info(f, m, s.material).get("_a0")
                if tex is None or not (cloth_material(name, m.material_names) or cloth_footwear(name)) or texels(tex) is None:
                    continue
                rgba, cut = texels(tex)
                try:
                    pos, uv, tris = geometry(f, s)
                except KeyError:            # a shape without positions or UVs
                    continue
                a, b, c_ = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
                wt = 0.5 * np.linalg.norm(np.cross(b - a, c_ - a), axis=1) / 3.0
                h, w = rgba.shape[:2]
                rec = per.setdefault(name, {"tex": tex, "area": 0.0, "hist": np.zeros(CLOTH_BINS), "grey": 0.0, "foot": cloth_footwear(name)})
                assert rec["tex"] == tex
                for wa, wb, wc in ((2 / 3, 1 / 6, 1 / 6), (1 / 6, 2 / 3, 1 / 6), (1 / 6, 1 / 6, 2 / 3)):
                    p = wa * uv[tris[:, 0]] + wb * uv[tris[:, 1]] + wc * uv[tris[:, 2]]
                    px = rgba[np.floor((p[:, 1] % 1.0) * h).astype(int) % h, np.floor((p[:, 0] % 1.0) * w).astype(int) % w].astype(np.float64) / 255.0
                    shown = px[:, 3] > 0.5 if cut else np.ones(len(px), bool)
                    hsv = texrecolor.rgb_to_hsv(px[:, :3])
                    coloured = shown & (hsv[:, 1] > 0.22) & (hsv[:, 2] > 0.10)
                    rec["area"] += float(wt[shown].sum())
                    rec["grey"] += float(wt[shown & ~coloured].sum())
                    np.add.at(rec["hist"], np.floor(hsv[coloured, 0] * CLOTH_BINS).astype(int) % CLOTH_BINS, wt[coloured])
            for rec in per.values():
                rec["hist"] = [round(float(x), 7) for x in rec["hist"]]
            cache[key] = per
        out[(number, age)] = cache[key]
    os.makedirs(BUILD_CUSTOM, exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"))
    return out


def brow_colour(target):
    """The eyebrows' colour for a hair colour: the colour the hair's texture gets (HAIR_TEXTURE:
    hue, saturation, brightness as stored), as the linear colour a material's colour
    parameters hold. The eyebrow material (Mt_Eyebrow, 16 of the characters; the others'
    eyebrows are painted in the face's texture) draws a grey texture in its colour 0 and
    colour 1, and in the game's own models those are the hair texture's colour made linear
    (Link: colour 0 = 0.309 0.151 0.052, his hair texture's mean made linear 0.307 0.158
    0.033). Until snaildev's word (2026-11-14: "we still don't have a separate eyebrow color
    ... it should match the hair") the eyebrows got the hair's SHINE colours, which are
    brighter and redder than the hair looks."""
    import colorsys
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in colorsys.hsv_to_rgb(*target))


def add_textures(data, material, names):
    """A model file's bytes with `names` added to a material's texture list, behind its
    samplers' own. -> the new bytes. New: the three lists a texture has a place in (the
    names, relocated as the old ones are; the views and the textures' descriptor slots,
    which the game fills in), and the count of textures (+0xa3; the samplers' stays).
    Read back and compared."""
    import bfres
    f = bfres.Fres(data)
    m = f.model()
    o = f.u64(m.off + 0x38) + m.material_names.index(material) * bfres.MATERIAL_SIZE
    ns, nt = f.data[o + MAT_SAMPLERS], f.data[o + MAT_TEXTURES]
    samplers = f.read_dict(f.u64(o + 0x38))[0]
    assert ns == nt == len(samplers) and ns >= 2 and samplers[0] == "_a0", (material, ns, nt, samplers)
    old = [f.u64(f.u64(o + MAT_NAMES) + 8 * k) for k in range(nt)]
    own = [f.string(x) for x in old]
    assert all(f.u64(f.u64(o + MAT_VIEWS) + 8 * k) == 0 for k in range(nt))
    assert all(f.u64(f.u64(o + MAT_SLOTS) + 8 * k) == 2 ** 64 - 1 for k in range(nt))
    listed = {loc for _si, loc in f.pointers()}
    assert all(o + off in listed for off in (MAT_VIEWS, MAT_NAMES, MAT_SLOTS))
    assert all(f.u64(o + MAT_NAMES) + 8 * k in listed for k in range(nt))
    n = nt + len(names)
    assert n < 256 and not set(names) & set(own)
    name_arr = f.alloc(struct.pack("<%dQ" % n, *(old + [f.add_string(x) for x in names])))
    f.add_reloc(name_arr, 1, n, 0)
    views = f.alloc(bytes(8 * n))
    slots = f.alloc(b"\xff" * (8 * n))
    for off, value in ((MAT_VIEWS, views), (MAT_NAMES, name_arr), (MAT_SLOTS, slots)):
        struct.pack_into("<Q", f.data, o + off, value)
    f.data[o + MAT_TEXTURES] = n
    out = f.build()
    g = bfres.Fres(out)
    g.check()
    gm = g.model()
    assert gm.material_names == m.material_names and [b.name for b in gm.bones] == [b.name for b in m.bones]
    go = g.u64(gm.off + 0x38) + gm.material_names.index(material) * bfres.MATERIAL_SIZE
    assert (g.data[go + MAT_SAMPLERS], g.data[go + MAT_TEXTURES]) == (ns, n)
    assert [g.string(g.u64(g.u64(go + MAT_NAMES) + 8 * k)) for k in range(n)] == own + list(names)
    assert all(g.u64(g.u64(go + MAT_VIEWS) + 8 * k) == 0 and g.u64(g.u64(go + MAT_SLOTS) + 8 * k) == 2 ** 64 - 1 for k in range(n))
    assert g.read_dict(g.u64(go + 0x38))[0] == samplers
    # nothing else of the file changed: every other material's record, and this one's but the three pointers and the count
    for i in range(len(m.material_names)):
        a, b = f.u64(m.off + 0x38) + i * bfres.MATERIAL_SIZE, g.u64(gm.off + 0x38) + i * bfres.MATERIAL_SIZE
        ra, rb = bytearray(data[a:a + bfres.MATERIAL_SIZE]), bytearray(out[b:b + bfres.MATERIAL_SIZE])
        if i == m.material_names.index(material):
            for off in (MAT_VIEWS, MAT_NAMES, MAT_SLOTS):
                ra[off:off + 8] = rb[off:off + 8] = bytes(8)
            ra[MAT_TEXTURES] = rb[MAT_TEXTURES] = 0
        assert ra == rb, m.material_names[i]
    return out


def palette_name(name, tag, k):
    """The name of a texture's recoloured copy: as long as the texture's own (the last "Alb"
    becomes "A", the tag and a digit), so the copy's file is the old one's size to the byte."""
    at = name.rfind("Alb")
    assert at >= 0 and len(tag) == 1 and 1 <= k <= 9, (name, tag, k)
    return name[:at] + "A%s%d" % (tag, k) + name[at + 3:]


def hair_fn(mean, target):
    """What a hair colour does to a texel of a hair texture whose mean is `mean`."""
    import math
    import numpy as np
    import texrecolor
    hm, sm, vm = mean
    hue, sat, val = target
    gamma = math.log(val) / math.log(min(max(vm, 0.03), 0.97))        # the mean brightness goes to the colour's, 0 and 1 stay

    def fn(rgb):
        hsv = texrecolor.rgb_to_hsv(rgb)
        dh = ((hsv[:, 0] - hm + 0.5) % 1.0) - 0.5
        out = np.stack([hue + 0.5 * dh, np.clip(sat * np.sqrt(hsv[:, 1] / max(sm, 0.05)), 0.0, 1.0),
                        np.power(np.clip(hsv[:, 2], 0.0, 1.0), gamma)], axis=1)
        return texrecolor.hsv_to_rgb(out)
    return fn


def player_models(facts):
    """[(stored number, age, file as the game names it, where its bytes are now)]: Link's two
    (the game's own) and every character's the installed folders ship."""
    out = []
    for number in range(len(facts)):
        for age in ("Child", "Adult"):
            rel = "Model/Player_%s.Player_%s.bfres.zs" % (age, age) if number == 0 else npc_model_file(age, number - len(build.CHARACTERS))
            try:
                path = resolve(rel)[1]
            except AssertionError:
                path = os.path.join(nxfmt.ROMFS, *rel.split("/")) if number == 0 else None
            if path and os.path.exists(path):
                out.append((number, age, rel, path))
    return out


def write_palettes(facts):
    """Every player model with a palette on its hair materials (the nine hair colours of that
    material's own colour texture), and the palette's texture files, into our folder.
    -> (model files, hair materials, texture files, bytes of models, bytes of textures,
    [(number, age, material, texture) left out and why])"""
    import json
    import bfres
    import texrecolor
    import npcportrait as pp
    B = build
    colours = [v for k, _l, values in OPTIONS if k == "Hair" for v in values[1:]]
    assert colours == list(HAIR_TEXTURE) and len(colours) <= 9
    tables = [rstb.Rstb(nxfmt.read(os.path.join(B.EDEN_LOAD, folder, "romfs", *rstb.PATH.split("/"))))
              for folder in installed() if os.path.exists(os.path.join(B.EDEN_LOAD, folder, "romfs", *rstb.PATH.split("/")))]

    def entry(key):
        have = [t.get(key) for t in tables + [B.stock_table()] if t.get(key) is not None]
        assert have, "no size table has " + key
        return max(have)

    cache_dir = os.path.join(BUILD_CUSTOM, "tex-cache")
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, "index.json")
    try:
        index = json.load(open(index_path, encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    stock_names = None
    made, skipped = {}, []          # texture name -> its nine copies' names (None: cannot be recoloured)
    # A texture name without a file of its own is one the game has "unified" with another
    # (System/Gfx/UnifyTex: 5,186 names, each with the name of the texture whose file it
    # shares; child Link's hair textures are adult Link's). Its palette is that one's.
    unify_rel = "System/Gfx/UnifyTex.Product.100.byml.zs"
    try:
        unify_path = resolve(unify_rel)[1]
    except AssertionError:
        unify_path = os.path.join(nxfmt.ROMFS, *unify_rel.split("/"))
    unify = byml.load(nxfmt.read(unify_path))
    assert unify.get("Player_Child_Hairs_Alb") == "Mt_Hairs_Player_Adult_Alb", "the unified textures' table is not as it was read"

    def palette(tex):
        nonlocal stock_names
        if tex in made:
            return made[tex]
        made[tex] = None
        src = os.path.join(nxfmt.ROMFS, "Tex", tex + ".bntx.zs")
        try:
            src = resolve("Tex/%s.bntx.zs" % tex)[1]
        except AssertionError:
            pass
        if not os.path.exists(src):
            shared = unify.get(tex)
            if isinstance(shared, str) and shared != tex:
                made[tex] = palette(shared)
            return made[tex]
        raw = open(src, "rb").read()
        data = nxfmt.unzs(raw, tex + ".bntx.zs")
        t, _start, _end = texrecolor.data_range(data)
        if t.format not in ("BC1", "BC3", "BC7") or t.name != tex:
            return None
        if stock_names is None:
            stock_names = {n[:-len(".bntx.zs")] for n in os.listdir(os.path.join(nxfmt.ROMFS, "Tex"))}
        mean = None
        names = []
        for k, colour in enumerate(colours, 1):
            new = palette_name(tex, "h", k)
            assert new not in stock_names, new
            key = "%d|%d|%s|%s" % (len(raw), int(os.path.getmtime(src)), colour, HAIR_TEXTURE[colour])
            cached = os.path.join(cache_dir, new + ".bntx.zs")
            if index.get(new) != key or not os.path.exists(cached):
                if mean is None:
                    mean = texrecolor.mean_colour(data)
                fn = hair_fn(mean, HAIR_TEXTURE[colour])
                out = texrecolor.recolour(data, new, fn)
                texrecolor.check(data, out, fn)
                assert len(out) == len(data)
                with open(cached, "wb") as f:
                    f.write(nxpack.zs_compress_like(out, raw))
                index[new] = key
            packed = open(cached, "rb").read()
            B.write(MOD, "Tex/%s.bntx.zs" % new, packed, len(data), B.SIZES, size=entry("Tex/%s.bntx" % tex))
            names.append(new)
        made[tex] = names
        return names

    # ---- the clothes (CLOTHES_ON): per texture and kind of main colour, the copies' names
    cloth_colours = [v for k, _l, values in OPTIONS if k == "Clothes" for v in values[1:]]
    assert not CLOTHES_ON or (cloth_colours == list(CLOTH_TEXTURE) and len(cloth_colours) <= 9)
    assert len(CLOTH_TEXTURE) != len(HAIR_TEXTURE), "the code tells a clothing palette from a hair palette by its length"
    cloth_made, cloth_tags, cloth_kinds = {}, {}, {}        # (texture, kind) -> names or None; texture -> {kind: letter}; (name, main) -> kind
    cloth_stats = {"encoded": 0, "plan": []}

    def cloth_palette(name, main):
        """The names of a texture's copies for a model whose clothes' main colour is `main`, or
        None (not enough of the texture is that colour, or it cannot be recoloured)."""
        nonlocal stock_names
        import numpy as np
        mode, hue = main
        ask = (name, mode, None if hue is None else int(hue * CLOTH_BINS))
        if ask in cloth_kinds:
            return cloth_made[cloth_kinds[ask]]
        tex, src = texture_file(name, unify)
        cloth_kinds[ask] = (tex, None)
        cloth_made.setdefault((tex, None), None)
        if src is None:
            return None
        raw = open(src, "rb").read()
        data = nxfmt.unzs(raw, tex + ".bntx.zs")
        t = texrecolor.data_range(data)[0]
        if not texrecolor.can_recolour(t) or t.name != tex:
            return None
        px = texrecolor.decoded(data)
        step = max(1, int((px.shape[0] * px.shape[1] / 250000.0) ** 0.5))       # a quarter of a million texels say enough
        px = px[::step, ::step].reshape(-1, 4).astype(np.float64) / 255.0
        if t.comp[3] == 5 and (px[:, 3] > 0.5).any():
            px = px[px[:, 3] > 0.5]
        hsv = texrecolor.rgb_to_hsv(px[:, :3])
        if mode == "hue":           # the texture's own peak nearest the model's hue: two models a little apart agree on it
            coloured = (hsv[:, 1] > 0.22) & (hsv[:, 2] > 0.10)
            hist = np.bincount(np.floor(hsv[coloured, 0] * CLOTH_BINS).astype(int) % CLOTH_BINS, minlength=CLOTH_BINS)
            centre_bin, _hue, _peak = _hue_peak(hist, around=hue, reach=6, half=2)
            kind, centre = ("hue", centre_bin), (centre_bin + 0.5) / CLOTH_BINS
        else:
            kind, centre = ("neutral", None), None
        cloth_kinds[ask] = (tex, kind)
        if (tex, kind) in cloth_made:
            return cloth_made[(tex, kind)]
        cloth_made[(tex, kind)] = None
        w = cloth_weight(hsv, mode, centre)
        share = float(w.mean())
        if share < (0.02 if mode == "hue" else 0.05):
            return None
        s_mean, v_mean = float((w * hsv[:, 1]).sum() / w.sum()), float((w * hsv[:, 2]).sum() / w.sum())
        tags = cloth_tags.setdefault(tex, {})
        tags[kind] = CLOTH_TAGS[len(tags)]
        if stock_names is None:
            stock_names = {n[:-len(".bntx.zs")] for n in os.listdir(os.path.join(nxfmt.ROMFS, "Tex"))}
        names = []
        for k, colour in enumerate(cloth_colours, 1):
            new = palette_name(tex, tags[kind], k)
            assert new not in stock_names, new
            key = "%d|%d|%d|%s|%s|%s|%.4f|%.4f" % (CLOTH_VERSION, len(raw), int(os.path.getmtime(src)), colour, CLOTH_TEXTURE[colour],
                                               kind, s_mean, v_mean)
            cached = os.path.join(cache_dir, new + ".bntx.zs")
            if index.get(new) != key or not os.path.exists(cached):
                fn = cloth_fn(mode, centre, s_mean, v_mean, CLOTH_TEXTURE[colour])
                out = texrecolor.recolour(data, new, fn)
                texrecolor.check(data, out, fn)
                assert len(out) == len(data)
                with open(cached, "wb") as fh:
                    fh.write(nxpack.zs_compress_like(out, raw))
                index[new] = key
                cloth_stats["encoded"] += 1
                if cloth_stats["encoded"] % 50 == 0:        # a first build makes hundreds: keep what is made
                    json.dump(index, open(index_path, "w", encoding="utf-8"))
            packed = open(cached, "rb").read()
            B.write(MOD, "Tex/%s.bntx.zs" % new, packed, len(data), B.SIZES, size=entry("Tex/%s.bntx" % tex))
            names.append(new)
        cloth_stats["plan"].append("%-58s %-9s %-13s %4.0f%% of it, saturation %.2f, brightness %.2f -> %s" % (
            tex, t.format, "neutral" if mode != "hue" else "hue %.3f" % centre, 100 * share, s_mean, v_mean, "%s .. %s" % (names[0], names[-1])))
        cloth_made[(tex, kind)] = names
        return names

    model_list = player_models(facts)
    clothes = cloth_facts(model_list, unify) if CLOTHES_ON else {}
    cloth_models, cloth_materials, mains, plans, colours_of = 0, 0, {}, {}, {}
    models = hair_models = materials = model_bytes = 0
    for number, age, rel, path in model_list:
        raw = open(path, "rb").read()
        data = nxfmt.unzs(raw, rel)
        f = bfres.Fres(data)
        m = f.model()
        new = data
        haired = set()
        for i, name in enumerate(m.material_names):
            if name not in PALETTE_MATERIALS or (name == HAIR_NAMES[0] and not facts[number][0]):
                continue
            tex = pp.material_info(f, m, i)
            if not ("_gn0" in tex and "_th0" in tex):
                continue
            names = palette(tex["_a0"])
            if names is None:
                skipped.append((number, age, name, tex["_a0"]))
                continue
            new = add_textures(new, name, names)
            haired.add(name)
            materials += 1
        mats = clothes[(number, age)]
        main = cloth_main(mats)
        mains[(number, age)] = main
        # which of the clothing colours each of its clothing materials has, and which colour of the material that is
        if PARTS:
            given, plans[(number, age)] = cloth_parts(mats, main)
        else:
            given = {name: (0, main) for name, rec in mats.items() if main and cloth_shows(rec, main)}
        by_material, by_texture = {}, {}        # a clothing material's palette; a texture's, for whatever else draws it (the largest material's)
        for name, (slot, colour) in sorted(given.items(), key=lambda kv: (-mats[kv[0]]["area"], kv[0])):
            names = cloth_palette(mats[name]["tex"], colour)
            if names:
                by_material[name] = (slot, names)
                by_texture.setdefault(mats[name]["tex"], (slot, names))
        got, used = 0, set()
        for i, name in enumerate(m.material_names):
            tex = pp.material_info(f, m, i).get("_a0")
            o = f.u64(m.off + 0x38) + i * bfres.MATERIAL_SIZE
            ns, nt = f.data[o + MAT_SAMPLERS], f.data[o + MAT_TEXTURES]
            slot, names = by_material.get(name) or by_texture.get(tex) or (None, None)
            if names is None or not (ns == nt >= 2):
                continue
            # (footwear that draws a part's texture was left out for one build: child Link's boots are his leather material itself,
            # they changed with the belt, and what was left out was the trim on them, Mt_Fur_Boots, which then did not follow: snaildev)
            if name in haired or name in HAIR_NAMES[1:] or (name == HAIR_NAMES[0] and facts[number][0]):
                continue                    # what the hair's loop asks for by name has the hair's palette or none
            if f.read_dict(f.u64(o + 0x38))[0][0] != "_a0":
                continue
            new = add_textures(new, name, names + [names[-1]] * (CLOTH_LENGTHS[slot] - len(names)))    # (its length says which colour's it is)
            got += 1
            used.add(slot)
        cloth_materials += got
        cloth_models += bool(got)
        colours_of[(number, age)] = len(used)
        if new is data:
            continue
        key = rel[:-3]
        B.write(MOD, rel, nxpack.zs_compress_like(new, raw), len(new), B.SIZES, size=entry(key) + (len(new) - len(data)) + 0x200)
        models += 1
        hair_models += bool(haired)
        model_bytes += len(nxpack.zs_compress_like(new, raw)) if False else os.path.getsize(os.path.join(B.BUILD, MOD, "romfs", *rel.split("/")))
    json.dump(index, open(index_path, "w", encoding="utf-8"))
    textures = sorted({n for names in made.values() if names for n in names})        # (a unified name has the same nine)
    tex_bytes = sum(os.path.getsize(os.path.join(B.BUILD, MOD, "romfs", "Tex", n + ".bntx.zs")) for n in textures)
    assert models and materials and textures
    cloth_textures = sorted({n for names in cloth_made.values() if names for n in names})
    assert not set(cloth_textures) & set(textures)
    # what was decided, for reading: a line a model file, then a line a recoloured texture
    with open(os.path.join(BUILD_CUSTOM, "cloth-plan.txt"), "w", encoding="utf-8") as fh:
        for (number, age), main in sorted(mains.items()):
            mats = clothes.get((number, age), {})
            area = sum(r["area"] for r in mats.values()) or 1.0
            said = "-" if not main else "neutral" if main[0] != "hue" else "hue %.3f" % main[1]
            if PARTS:
                fh.write("%3d %-5s main %-10s %s\n" % (number, age, said, " | ".join(
                    "%s: %s %.0f%% [%s]" % ("-" if slot is None else slot + 1, "grey" if colour[0] != "hue" else "hue %.3f" % colour[1], 100 * share,
                                            ", ".join(n[3:] for n in names)) for slot, share, colour, names in plans[(number, age)])))
            else:
                fh.write("%3d %-5s %-12s %s\n" % (number, age, said, ", ".join("%s %.0f%%" % (n[3:], 100 * r["area"] / area)
                                                                              for n, r in sorted(mats.items(), key=lambda kv: -kv[1]["area"])[:6])))
        fh.write("\n" + "\n".join(sorted(cloth_stats["plan"])) + "\n")
    cloth = dict(models=cloth_models, materials=cloth_materials, textures=len(cloth_textures), made=cloth_stats["encoded"],
                 bytes=sum(os.path.getsize(os.path.join(B.BUILD, MOD, "romfs", "Tex", n + ".bntx.zs")) for n in cloth_textures),
                 sources=len({k[0] for k, names in cloth_made.items() if names}),
                 hue=sum(1 for v in mains.values() if v and v[0] == "hue"), neutral=sum(1 for v in mains.values() if v and v[0] != "hue"),
                 none=sum(1 for v in mains.values() if not v),
                 two=sum(1 for v in colours_of.values() if v == 2), three=sum(1 for v in colours_of.values() if v == 3))
    assert not CLOTHES_ON or (cloth["models"] and cloth["textures"])
    assert CLOTHES_ON or hair_models == models
    return hair_models, materials, len(textures), model_bytes, tex_bytes, skipped, dict(cloth, files=models)


def model_facts():
    """Per stored character number, from the model files the installed folders ship (the
    game's own for Link): is the material Mt_Hairs hair (it has the hair shader's two
    textures, the colour ramp and the thickness map), which other hair materials there
    are, the iris texture and its mean hue (None without Mt_Eye_Inner or its texture).
    -> [(is hair, [other hair materials], iris texture, hue)], index = stored number (1,
    Retro Link, is not in the picker: no hair, the common hue). Kept in
    build-custom\\model-facts.json by the files' sizes and dates: reading 100 models takes
    most of a minute."""
    import json
    import bfres
    import bntx
    import numpy as np
    import npcportrait as pp
    cache_path = os.path.join(BUILD_CUSTOM, "model-facts.json")
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    hues = {}

    def hue_of(tex):
        if tex not in hues:
            try:
                path = resolve("Tex/%s.bntx.zs" % tex)[1]
            except AssertionError:
                path = os.path.join(nxfmt.ROMFS, "Tex", tex + ".bntx.zs")
            hues[tex] = None
            if os.path.exists(path):
                arc = bntx.Bntx(nxfmt.read(path))
                t = arc[tex] if tex in arc else list(arc.tex.values())[0]
                px = t.rgba().reshape(-1, 4).astype(np.float64) / 255.0
                r, g, b = px[:, 0], px[:, 1], px[:, 2]
                mx, mn = px[:, :3].max(axis=1), px[:, :3].min(axis=1)
                d = np.maximum(mx - mn, 1e-9)
                h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) / 6.0
                w = (mx - mn) * (px[:, 3] > 0.5)            # by how coloured a texel is
                if w.sum() > 0:
                    hues[tex] = float(np.arctan2((w * np.sin(2 * np.pi * h)).sum(),
                                                 (w * np.cos(2 * np.pi * h)).sum()) / (2 * np.pi) % 1.0)
        return hues[tex]

    # the numbers that have a model: Link, and every C<nnn> an installed folder ships (a
    # short roster leaves gaps; a number without a model gets no hair and the common hue)
    import re
    shipped = set()
    for folder in installed():
        d = os.path.join(build.EDEN_LOAD, folder, "romfs", "Model")
        if os.path.isdir(d):
            for name in os.listdir(d):
                m = re.match(r"Player_(?:Child|Adult)_C(\d{3})\.Player_(?:Child|Adult)\.bfres\.zs$", name)
                if m:
                    shipped.add(int(m.group(1)) + len(build.CHARACTERS))
    assert shipped, "no installed folder ships a character's model"
    out = []
    for number in range(max(shipped) + 1):
        if number != 0 and number not in shipped:
            out.append((False, [], None, None))
            continue
        path = None
        for age in ("Child", "Adult"):
            rel = "Model/Player_%s.Player_%s.bfres.zs" % (age, age) if number == 0 else \
                npc_model_file(age, number - len(build.CHARACTERS))
            try:
                path = resolve(rel)[1]
            except AssertionError:
                path = os.path.join(nxfmt.ROMFS, *rel.split("/")) if number == 0 else None
            if path and os.path.exists(path):
                break
            path = None
        assert path is not None, number
        key = "%s|%d|%d" % (path, os.path.getsize(path), int(os.path.getmtime(path)))
        if key not in cache:
            f = bfres.Fres(nxfmt.read(path))
            m = f.model()
            tex = {name: pp.material_info(f, m, i) for i, name in enumerate(m.material_names)}
            hair = [name for name, t in tex.items() if "_gn0" in t and "_th0" in t and name.startswith(("Mt_Hair", "Mt_Ponytail", "Mt_BodyHair"))]
            iris = tex.get(EYE_NAME, {}).get("_a0")
            cache[key] = ["Mt_Hairs" in hair, [h for h in hair if h != "Mt_Hairs"], iris, hue_of(iris) if iris else None,
                          sorted(tex)]
        is_hair, others, iris, hue, names = cache[key]
        assert "Mt_Hairs" in names, (number, "no material of that name: not a player model")
        out.append((is_hair, others, iris, hue))
    os.makedirs(BUILD_CUSTOM, exist_ok=True)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"))
    assert len(out) >= 3 and out[0][0] and out[0][3] is not None, "Link's own model is not as expected"
    assert abs(out[0][3] - COMMON_IRIS_HUE) < 0.01, out[0]
    known = set(HAIR_NAMES)
    for number, (_h, others, _i, _hue) in enumerate(out):
        assert set(others) <= known, (number, others, "a hair material HAIR_NAMES does not have")
    return out


def npc_model_file(age, n):
    return "Model/Player_%s_C%03d.Player_%s.bfres.zs" % (age, n, age)


def iris_shift(hue):
    """What a character's iris texture adds to an eye colour's x, in 255ths (-128..127)."""
    shift = 0 if hue is None else int(round((COMMON_IRIS_HUE - hue) / 2 * 255))
    assert -128 <= shift <= 127
    return shift


def byte(x):
    v = int(round(x * 255))
    assert 0 <= v <= 255, x
    return v


def colour_data(facts):
    """The tables' block (COLOUR_DATA): hashes, 1 / 255, the names, the colours, and per
    stored number whether Mt_Hairs is hair and what the iris texture's hue asks to be added
    to an eye colour's x (a signed byte). One entry more than there are numbers: a number
    beyond them (another build's character) reads it, no hair and nothing added. Then, for
    cz_keep: the owner's hash, KEEP_BASE and the six options' hashes."""
    hair = [v for k, _l, values in OPTIONS if k == "Hair" for v in values[1:]]
    eyes = [v for k, _l, values in OPTIONS if k == "Eyes" for v in values[1:]]
    assert hair == list(HAIR_COLOURS) and eyes == list(EYE_COLOURS), "the options' values and the colour tables differ"
    n = len(facts)
    assert n + 1 <= 128 and all(len(s) < 16 for s in HAIR_NAMES + (EYE_NAME,) + PARAM_NAMES)
    d_keep = (D_HUE + n + 1 + 3) & ~3
    d = bytearray(d_keep + 8 + 4 * len(OPTIONS))
    keys = [k for k, _l, _v in OPTIONS]
    struct.pack_into("<IIIIfI", d, 0, gamedata.murmur3_32(FLAG), gamedata.murmur3_32(option_flag(keys.index("Hair"))),
                     gamedata.murmur3_32(option_flag(keys.index("Eyes"))), gamedata.murmur3_32(build.CHARACTER_FLAG), 1.0 / 255.0,
                     gamedata.murmur3_32(option_flag(keys.index("Gear"))))
    struct.pack_into("<3I", d, D_CLOTHES, *(gamedata.murmur3_32(x) for x in CLOTHES_FLAGS))     # (GameData has the Ints of the options that are built)
    struct.pack_into("<II%dI" % len(OPTIONS), d, d_keep, gamedata.murmur3_32(KEEP_OWNER), KEEP_BASE,
                     *(gamedata.murmur3_32(option_flag(f)) for f in range(len(OPTIONS))))
    assert "Clothes" in keys and all(option_flag(keys.index(k)) == x for k, x in zip(CLOTH_KEYS, CLOTHES_FLAGS) if k in keys)
    assert OPTIONS[keys.index("Gear")][2] == ["Shown", "Hidden"]
    assert len(OPTIONS) * 4 <= 32 and all(len(values) <= 16 for _k, _l, values in OPTIONS) and n <= KEEP_COUNT
    for i, s in enumerate(PARAM_NAMES):
        d[D_PARAM + 16 * i:D_PARAM + 16 * i + len(s)] = s.encode()
    for i, s in enumerate(HAIR_NAMES):
        d[D_NAMES + 16 * i:D_NAMES + 16 * i + len(s)] = s.encode()
    d[D_EYE:D_EYE + len(EYE_NAME)] = EYE_NAME.encode()
    for i, name in enumerate(hair):
        c0, c1 = HAIR_COLOURS[name]
        d[D_HAIRS + 6 * i:D_HAIRS + 6 * i + 6] = bytes(byte(x) for x in c0 + c1)
        brow = brow_colour(HAIR_TEXTURE[name])
        d[D_BROWS + 6 * i:D_BROWS + 6 * i + 6] = bytes(byte(x) for x in brow + brow)
    for i, name in enumerate(eyes):
        d[D_EYES + 3 * i:D_EYES + 3 * i + 3] = bytes(byte(x) for x in EYE_COLOURS[name])
    for number, (is_hair, _others, _iris, hue) in enumerate(facts):
        if is_hair:
            d[D_MASK + number // 8] |= 1 << (number % 8)
        d[D_HUE + number] = iris_shift(hue) & 255
    assert D_MASK + 16 == D_HUE and D_EYES + 3 * len(eyes) <= D_MASK and D_BROWS + 6 * len(hair) <= D_EYES
    assert HAIR_NAMES[-1] == "Mt_Eyebrow" and list(HAIR_TEXTURE) == hair         # (cz_actor: the last name's colours are the eyebrows' table's)
    return bytes(d)


# Every routine is entered with bl and left with ret, its entry its lowest address.
CZ_REC = """
    cz_rec:
        mov x0, xzr
        adrp x11, #{got_page:#x}
        ldr x11, [x11, #{got_off:#x}]
        ldr x11, [x11]
        cbz x11, ce_out
        ldr x12, [x11, #0xe0]
        cbz x12, ce_out
        ldr w13, [x11, #0xec]
        cbz w13, ce_out
        udiv w15, w14, w13
        msub w15, w15, w13, w14
        mov w16, w15
    ce_probe:
        ldr x17, [x12, w16, uxtw #3]
        cmp w17, w14
        b.eq ce_found
        cbz w17, ce_out
        add w16, w16, #1
        cmp w16, w13
        csel w16, wzr, w16, hs
        cmp w16, w15
        b.ne ce_probe
        b ce_out
    ce_found:
        lsr x17, x17, #32
        ldr w12, [x11, #0xd0]
        cmp w17, w12
        b.hs ce_out
        ldr x12, [x11, #0xd8]
        mov w13, #0x28
        umaddl x0, w17, w13, x12
    ce_out:
        ret
"""
# w14 = an Int's hash -> w0 = its value (0 if GameData has no such Int)
CZ_INT = """
    cz_int:
        stp x29, x30, [sp, #-0x10]!
        bl {cz_rec}
        cbz x0, ci_out
        ldr w0, [x0, #0x1c]
    ci_out:
        ldp x29, x30, [sp], #0x10
        ret
"""
# No arguments: the options per character (see KEEP_OWNER). x19-x25 are saved and used.
CZ_KEEP = """
    cz_keep:
        stp x29, x30, [sp, #-0x50]!
        stp x19, x20, [sp, #0x10]
        stp x21, x22, [sp, #0x20]
        stp x23, x24, [sp, #0x30]
        str x25, [sp, #0x40]
        adrp x19, #{data_page:#x}
        add x19, x19, #{data_off:#x}
        ldr w14, [x19, #12]
        bl {cz_rec}
        cbz x0, ck_out
        ldr w20, [x0, #0x1c]
        cmp w20, #{keep}
        b.hs ck_out
        ldr w14, [x19, #{d_keep:#x}]
        bl {cz_rec}
        cbz x0, ck_out
        mov x21, x0
        ldr w14, [x19, #{d_keep4:#x}]
        add w14, w14, w20
        bl {cz_rec}
        cbz x0, ck_out
        mov x22, x0
        ldr w8, [x21, #0x1c]
        cmp w8, w20
        cset w24, eq
        ldr w23, [x22, #0x1c]
        csel w23, wzr, w23, eq
        mov w25, wzr
    ck_opt:
        add x8, x19, #{d_opts:#x}
        ldr w14, [x8, w25, uxtw #2]
        bl {cz_rec}
        cbz x0, ck_next
        lsl w9, w25, #2
        cbz w24, ck_load
        ldr w8, [x0, #0x1c]
        and w8, w8, #15
        lsl w8, w8, w9
        orr w23, w23, w8
        b ck_next
    ck_load:
        lsr w8, w23, w9
        and w8, w8, #15
        str w8, [x0, #0x1c]
    ck_next:
        add w25, w25, #1
        cmp w25, #{options}
        b.lo ck_opt
        cbz w24, ck_owner
        str w23, [x22, #0x1c]
        b ck_out
    ck_owner:
        str w20, [x21, #0x1c]
    ck_out:
        ldr x25, [sp, #0x40]
        ldp x23, x24, [sp, #0x30]
        ldp x21, x22, [sp, #0x20]
        ldp x19, x20, [sp, #0x10]
        ldp x29, x30, [sp], #0x50
        ret
"""
# x0 = three bytes, x1 = where their three floats go (each byte / 255); x26 = the tables
CZ_RGB = """
    cz_rgb:
        ldr s1, [x26, #0x10]
        mov x9, xzr
    cr_next:
        ldrb w8, [x0, x9]
        ucvtf s0, w8
        fmul s0, s0, s1
        str s0, [x1, x9, lsl #2]
        add x9, x9, #1
        cmp x9, #3
        b.lo cr_next
        ret
"""
# x25 = the model unit, x0 = a material's name, x1 = a parameter's name, x2 = four floats to
# set (their fourth is filled in here with the model's own) or 0 = the model's own four.
# Nothing happens if the unit has no such material or the material no such parameter.
CZ_PUT = """
    cz_put:
        stp x29, x30, [sp, #-0x30]!
        stp x1, x2, [sp, #0x10]
        str x0, [sp, #0x20]
        mov x0, x25
        add x1, sp, #0x20
        ldr x8, [x25]
        ldr x8, [x8, #0xd0]
        blr x8
        cmn w0, #1
        b.eq cp_out
        sxth w0, w0
        str w0, [sp, #0x28]
        ldr x8, [sp, #0x10]
        str x8, [sp, #0x20]
        mov w1, w0
        mov x0, x25
        add x2, sp, #0x20
        ldr x8, [x25]
        ldr x8, [x8, #0xe0]
        blr x8
        cmn w0, #1
        b.eq cp_out
        sxth w2, w0
        ldrsw x1, [sp, #0x28]
        ldr x8, [x25, #0x110]
        cbnz x8, cp_ptrs
        ldr x8, [x25, #0x108]
        add x8, x8, x1, lsl #7
        b cp_res
    cp_ptrs:
        ldr x8, [x8, x1, lsl #3]
    cp_res:
        ldr x9, [x8]
        ldr x10, [x9, #0x10]
        ldr x10, [x10]
        ldr x10, [x10, #0x20]
        mov w11, #0x18
        smaddl x10, w2, w11, x10
        ldrh w10, [x10, #0x10]
        ldr x9, [x9, #0x58]
        add x9, x9, x10
        ldr x3, [sp, #0x18]
        cbnz x3, cp_alpha
        mov x3, x9
        b cp_set
    cp_alpha:
        ldr w10, [x9, #0xc]
        str w10, [x3, #0xc]
    cp_set:
        mov x0, x25
        ldr x8, [x25]
        ldr x8, [x8, #0x158]
        blr x8
    cp_out:
        ldp x29, x30, [sp], #0x30
        ret
"""
# x25 = the model unit, x0 = a material's name, w1 = the colour (0 = the model's own), w2 as
# cz_actor's w1. Where the material has a palette (more textures than samplers), the
# colour's palette texture is set as the material's first, its colour texture, with the
# engine's own routine; 0, or a colour the palette does not have, sets the model's own
# again where w2 says so. Nothing is done while the texture wanted is not bound (still
# loading) or is the one that is there.
CZ_TEX = """
    cz_tex:
        stp x29, x30, [sp, #-0x20]!
        str x0, [sp, #0x10]
        stp w1, w2, [sp, #0x18]
        mov x0, x25
        add x1, sp, #0x10
        ldr x8, [x25]
        ldr x8, [x8, #0xd0]
        blr x8
        cmn w0, #1
        b.eq ct_out
        sxth x1, w0
        ldr x8, [x25, #0x110]
        cbnz x8, ct_ptrs
        ldr x8, [x25, #0x108]
        add x0, x8, x1, lsl #7
        b ct_obj
    ct_ptrs:
        ldr x0, [x8, x1, lsl #3]
    ct_obj:
        ldp w1, w2, [sp, #0x18]
        bl {cz_texo}
    ct_out:
        ldp x29, x30, [sp], #0x20
        ret
"""
# x0 = a material object, w1 = the colour (0 = the model's own), w2 as cz_actor's w1: what
# cz_tex does, for the material itself (the clothes' loop has no names).
CZ_TEXO = """
    cz_texo:
        stp x29, x30, [sp, #-0x20]!
        ldr x9, [x0]
        ldrb w10, [x9, #{mat_samplers:#x}]
        ldrb w11, [x9, #{mat_textures:#x}]
        cmp w11, w10
        b.ls co_out
        mov w5, wzr
        cbz w1, co_have
        add w5, w1, w10
        sub w5, w5, #1
        cmp w5, w11
        csel w5, wzr, w5, hs
    co_have:
        cbnz w5, co_go
        cbz w2, co_out
    co_go:
        ldr x15, [x9, #{mat_views:#x}]
        ldr x16, [x9, #{mat_slots:#x}]
        ldr x14, [x0, #{obj_views:#x}]
        cbz x15, co_out
        cbz x16, co_out
        cbz x14, co_out
        ldr x12, [x15, w5, uxtw #3]
        cbz x12, co_out
        ldr x13, [x14]
        cmp x13, x12
        b.eq co_out
        ldr x13, [x16, w5, uxtw #3]
        stp x12, x13, [sp, #0x10]
        mov w1, wzr
        add x2, sp, #0x10
        bl #{set_texture:#x}
    co_out:
        ldp x29, x30, [sp], #0x20
        ret
"""
# x25 = the model unit, x1 = the three clothing colours (words; 0 = the model's own), w2 as
# cz_actor's w1: every material of the unit that has a clothing palette gets its colour's
# entry. Which of the three a palette is for is its length: as many textures behind the
# material's samplers' own as there are clothing colours (the first colour's), one more
# (the second's) or three more (the third's); two more is the hair's palette, which is
# left to the hair's loop. The materials are counted and found the way the game's own loop
# over a model's materials does it (0x80232e2c: the unit's virtual +0xc8, then by number).
CZ_CLOTH = """
    cz_cloth:
        stp x29, x30, [sp, #-0x30]!
        stp x19, x20, [sp, #0x10]
        stp x21, x22, [sp, #0x20]
        mov x19, x1
        mov w20, w2
        mov x0, x25
        ldr x8, [x25]
        ldr x8, [x8, #0xc8]
        blr x8
        mov w21, w0
        mov w22, wzr
    cl_next:
        cmp w22, w21
        b.hs cl_out
        ldr x8, [x25, #0x110]
        cbnz x8, cl_ptrs
        ldr x8, [x25, #0x108]
        add x0, x8, x22, lsl #7
        b cl_obj
    cl_ptrs:
        ldr x0, [x8, x22, lsl #3]
    cl_obj:
        add w22, w22, #1
        cbz x0, cl_next
        ldr x9, [x0]
        cbz x9, cl_next
        ldrb w10, [x9, #{mat_samplers:#x}]
        ldrb w11, [x9, #{mat_textures:#x}]
        sub w11, w11, w10
        subs w11, w11, #{cloths}
        b.lo cl_next
        cmp w11, #3
        b.hi cl_next
        cmp w11, #2
        b.eq cl_next
        cset w12, hi
        sub w11, w11, w12
        ldr w1, [x19, w11, uxtw #2]
        mov w2, w20
        bl {cz_texo}
        b cl_next
    cl_out:
        ldp x21, x22, [sp, #0x20]
        ldp x19, x20, [sp, #0x10]
        ldp x29, x30, [sp], #0x30
        ret
"""
# x0 = a player or menu character's actor: the options' colours on every unit of its model.
# w1 = what an option at 0 ("Default") does: 1 puts the model's own values back (the menu,
# where the option may just have been another colour), 0 touches nothing (a model that was
# just made has them; a character nobody customized is never written to).
CZ_ACTOR = """
    cz_actor:
        stp x29, x30, [sp, #-0xa0]!
        str w1, [sp, #0x78]
        stp x19, x20, [sp, #0x10]
        stp x21, x22, [sp, #0x20]
        stp x23, x24, [sp, #0x30]
        stp x25, x26, [sp, #0x40]
        stp x27, x28, [sp, #0x50]
        cbz x0, ca_out
        str x0, [sp, #0x80]
        bl {cz_gear}
        ldr x0, [sp, #0x80]
        ldr x8, [x0, #0x270]
        cbz x8, ca_out
        ldr x9, [x8, #8]
        cbz x9, ca_out
        ldr x10, [x9]
        ldr x10, [x10, #0xf8]
        adrp x11, #{type_page:#x}
        ldr x11, [x11, #{type_off:#x}]
        cmp x10, x11
        b.ne ca_out
        ldr x19, [x9, #0x18]
        cbz x19, ca_out
        adrp x26, #{data_page:#x}
        add x26, x26, #{data_off:#x}
        ldr w14, [x26, #4]
        bl {cz_int}
        mov w20, w0
        ldr w14, [x26, #8]
        bl {cz_int}
        mov w21, w0
        ldr w14, [x26, #12]
        bl {cz_int}
        mov w22, w0
        mov w27, wzr
    ca_cloth:
        add x8, x26, #{d_clothes:#x}
        ldr w14, [x8, w27, uxtw #2]
        bl {cz_int}
        cmp w0, #{cloths}
        csel w0, w0, wzr, ls
        add x8, sp, #0x90
        str w0, [x8, w27, uxtw #2]
        add w27, w27, #1
        cmp w27, #3
        b.lo ca_cloth
        cmp w20, #{hairs}
        csel w20, w20, wzr, ls
        cmp w21, #{eyes}
        csel w21, w21, wzr, ls
        mov w8, #{nchar}
        cmp w22, w8
        csel w22, w22, w8, lo
        ldrsw x23, [x19, #0x20]
        mov x24, xzr
    ca_unit:
        cmp x24, x23
        b.ge ca_out
        ldr x8, [x19, #0x28]
        ldr x8, [x8, x24, lsl #3]
        add x24, x24, #1
        cbz x8, ca_unit
        ldr x25, [x8]
        cbz x25, ca_unit
        add x1, sp, #0x90
        ldr w2, [sp, #0x78]
        ldp w8, w9, [sp, #0x90]
        ldr w10, [sp, #0x98]
        orr w8, w8, w9
        orr w8, w8, w10
        orr w8, w8, w2
        cbz w8, ca_hairs
        bl {cz_cloth}
    ca_hairs:
        ldr w8, [sp, #0x78]
        orr w8, w8, w20
        cbz w8, ca_eyes
        mov x27, xzr
    ca_hair:
        cbnz x27, ca_hair_go
        add x8, x26, #{d_mask:#x}
        lsr w9, w22, #3
        ldrb w8, [x8, w9, uxtw]
        and w9, w22, #7
        lsr w8, w8, w9
        tbz w8, #0, ca_hair_next
    ca_hair_go:
        mov x28, xzr
    ca_hair_col:
        mov x2, xzr
        cbz w20, ca_hair_put
        sub w8, w20, #1
        add w8, w8, w8, lsl #1
        lsl w8, w8, #1
        add w8, w8, w28
        add w8, w8, w28, lsl #1
        cmp x27, #{brow}
        b.ne ca_hair_tab
        add w8, w8, #{brows}
    ca_hair_tab:
        add x0, x26, #{d_hairs:#x}
        add x0, x0, w8, uxtw
        add x1, sp, #0x60
        bl {cz_rgb}
        add x2, sp, #0x60
    ca_hair_put:
        add x0, x26, #{d_names:#x}
        add x0, x0, x27, lsl #4
        add x1, x26, #{d_param:#x}
        add x1, x1, x28, lsl #4
        bl {cz_put}
        add x28, x28, #1
        cmp x28, #2
        b.lo ca_hair_col
        add x0, x26, #{d_names:#x}
        add x0, x0, x27, lsl #4
        mov w1, w20
        ldr w2, [sp, #0x78]
        bl {cz_tex}
    ca_hair_next:
        add x27, x27, #1
        cmp x27, #{names}
        b.lo ca_hair
    ca_eyes:
        ldr w8, [sp, #0x78]
        orr w8, w8, w21
        cbz w8, ca_unit
        mov x2, xzr
        cbz w21, ca_eye_put
        sub w9, w21, #1
        add w9, w9, w9, lsl #1
        add x8, x26, #{d_eyes:#x}
        add x8, x8, w9, uxtw
        ldrb w10, [x8]
        add x9, x26, #{d_hue:#x}
        ldrsb w9, [x9, w22, uxtw]
        add w10, w10, w9
        cmp w10, #0
        csel w10, w10, wzr, ge
        mov w9, #255
        cmp w10, w9
        csel w10, w10, w9, le
        strb w10, [sp, #0x70]
        ldrb w10, [x8, #1]
        strb w10, [sp, #0x71]
        ldrb w10, [x8, #2]
        strb w10, [sp, #0x72]
        add x0, sp, #0x70
        add x1, sp, #0x60
        bl {cz_rgb}
        add x2, sp, #0x60
    ca_eye_put:
        add x0, x26, #{d_eye:#x}
        add x1, x26, #{d_param2:#x}
        bl {cz_put}
        b ca_unit
    ca_out:
        ldp x27, x28, [sp, #0x50]
        ldp x25, x26, [sp, #0x40]
        ldp x23, x24, [sp, #0x30]
        ldp x21, x22, [sp, #0x20]
        ldp x19, x20, [sp, #0x10]
        ldp x29, x30, [sp], #0xa0
        ret
"""
# x0 = an actor, w1 as cz_actor's: the equipment's "shown" bits by the Equipment option.
# 1 (Hidden) hides the actors in GEAR_HANDLES; 0 shows them where w1 says so, else leaves
# them as they are. ldclr / ldset: the bit is changed the way the game changes it.
CZ_GEAR = """
    cz_gear:
        stp x29, x30, [sp, #-0x20]!
        stp x19, x20, [sp, #0x10]
        mov x19, x0
        mov w20, w1
        adrp x8, #{data_page:#x}
        add x8, x8, #{data_off:#x}
        ldr w14, [x8, #0x14]
        bl {cz_int}
        cmp w0, #1
        b.eq cg_go
        cbz w20, cg_out
    cg_go:
        cset w20, eq
        ldr x8, [x19, #0x270]
        cbz x8, cg_out
        ldr x9, [x8, #0x200]
        cbz x9, cg_out
        movz w15, #{gear_lo:#x}
        movk w15, #{gear_hi:#x}, lsl #16
        mov w10, wzr
    cg_slot:
        lsr w11, w15, w10
        tbz w11, #0, cg_next
        mov w11, #0x18
        umaddl x11, w10, w11, x9
        ldr w13, [x11, #{gear_id:#x}]
        cmn w13, #1
        b.eq cg_next
        ldr x12, [x11, #{gear_link:#x}]
        cbz x12, cg_next
        ldr w14, [x12, #0x48]
        cmp w14, w13
        b.ne cg_next
        ldr x12, [x12, #0x40]
        cbz x12, cg_next
        ldr w14, [x12, #0x2c]
        sub w14, w14, #7
        cmp w14, #5
        b.lo cg_next
        add x12, x12, #{shown:#x}
        mov w13, #{shown_bit:#x}
        cbz w20, cg_show
        ldclr w13, w14, [x12]
        b cg_next
    cg_show:
        ldset w13, w14, [x12]
    cg_next:
        add w10, w10, #1
        cmp w10, #{gear_count}
        b.lo cg_slot
    cg_out:
        ldp x19, x20, [sp, #0x10]
        ldp x29, x30, [sp], #0x20
        ret
"""
# in place of "bl 0x804d7b20" in the players' per-frame routine: x0 = the part of the actor
# that routine works on, its +0x110 the actor
CZ_HEAD = """
    cz_head:
        stp x29, x30, [sp, #-0x20]!
        str x19, [sp, #0x10]
        mov x19, x0
        bl #{head:#x}
        ldr x0, [x19, #0x110]
        mov w1, wzr
        bl {cz_actor}
        ldr x19, [sp, #0x10]
        ldp x29, x30, [sp], #0x20
        ret
"""
# In place of "ldp d9, d8, [sp, #0x50]", the first instruction of the camera update's way
# out (x19-x24 are reloaded right after: ours to use). Every update: the keeping per
# character, then the menu character (an option at 0 puts the model's own back only while
# customizing; so a character that was just picked gets his options here: the per-frame
# place of cz_head is not passed by the menu character, snaildev's sixth look), and while
# customizing the field character too.
CZ_FRAME = """
    cz_frame:
        stp x29, x30, [sp, #-0x10]!
        bl {cz_keep}
        adrp x19, #{data_page:#x}
        add x19, x19, #{data_off:#x}
        ldr w14, [x19]
        bl {cz_int}
        cmp w0, #0
        cset w20, ne
        adrp x8, #{menu_page:#x}
        ldr x8, [x8, #{menu_off:#x}]
        cbz x8, cf_field
        ldr x8, [x8, #0x4e8]
        cbz x8, cf_field
        ldr w9, [x8, #{slot_id:#x}]
        cmn w9, #1
        b.eq cf_field
        ldr x10, [x8, #{slot_link:#x}]
        cbz x10, cf_field
        ldr w11, [x10, #0x48]
        cmp w11, w9
        b.ne cf_field
        ldr x0, [x10, #0x40]
        cbz x0, cf_field
        ldr w11, [x0, #0x2c]
        sub w11, w11, #7
        cmp w11, #5
        b.lo cf_field
        mov w1, w20
        bl {cz_actor}
    cf_field:
        cbz w20, cf_out
        adrp x8, #{pouch_page:#x}
        ldr x8, [x8, #{pouch_off:#x}]
        ldr x8, [x8]
        cbz x8, cf_out
        ldr x8, [x8, #0x470]
        cbz x8, cf_out
        ldr x0, [x8, #0x18]
        cbz x0, cf_out
        ldr w11, [x0, #0x2c]
        sub w11, w11, #7
        cmp w11, #5
        b.lo cf_out
        mov w1, #1
        bl {cz_actor}
    cf_out:
        ldp x29, x30, [sp], #0x10
        ldp d9, d8, [sp, #0x50]
        ret
"""
# (name, text, which of our three places): in the order they need each other
CZ_BLOCKS = (("cz_rec", CZ_REC, "more"), ("cz_int", CZ_INT, "more"), ("cz_keep", CZ_KEEP, "keep"), ("cz_rgb", CZ_RGB, "code"),
             ("cz_put", CZ_PUT, "code"), ("cz_texo", CZ_TEXO, "tex"), ("cz_tex", CZ_TEX, "tex"), ("cz_cloth", CZ_CLOTH, "keep"),
             ("cz_gear", CZ_GEAR, "more"), ("cz_actor", CZ_ACTOR, "code"),
             ("cz_head", CZ_HEAD, "more"),
             ("cz_frame", CZ_FRAME, "more"))
CZ_PLACES = {"code": COLOUR_CODE, "more": COLOUR_MORE, "keep": COLOUR_KEEP, "tex": COLOUR_TEX}


def check_dead(img, node, region, sha1, slots=(0xE8, 0xF0)):
    """The routines of a node class no graph names (its enter and update, one after the
    other; `slots`: where its vtable has them) fill `region`: the only words of the whole
    image that point into the region are those of that class's own vtable, no call or
    branch from outside goes into it, no address in it is formed anywhere, and no graph
    file names the class (the game's, every installed folder's, ours). As
    npc_characters.check_unused_node, for the routines of an Execute or a Query node."""
    import glob
    import hashlib
    import numpy as np
    VT_NAME = 0x98
    lo, hi = region
    have = hashlib.sha1(img.data[lo - img.base:hi - img.base]).hexdigest()
    assert have == sha1, "the unused routines are not the ones this patch was written for (%s)" % have
    q = np.frombuffer(img.data[:len(img.data) // 8 * 8], dtype="<u8")
    getters = [x for s in img.strings(node) for x in img.xrefs(s) if img.u32(x + 8) == 0xD65F03C0]
    tables = [img.base + int(i) * 8 - VT_NAME for x in getters for i in np.nonzero(q == x)[0]]
    assert len(tables) == 1, tables
    routines = [struct.unpack_from("<Q", img.data, tables[0] + o - img.base)[0] for o in slots]
    assert routines[0] == lo and routines == sorted(routines) and routines[-1] < hi, [hex(x) for x in routines]
    inside = np.nonzero((q >= lo) & (q < hi))[0]
    assert sorted(img.base + int(i) * 8 for i in inside) == sorted(tables[0] + o for o in slots), \
        "something else points into the unused routines"
    starts = [x for x in range(lo, hi, 4) if img.u32(x) == img.PACIBSP]
    assert starts == routines, [hex(x) for x in starts]
    assert not any(img.xrefs(x) for x in range(lo, hi, 4))
    # every branch of the code (the words up to .rodata; beyond it a word that reads as a
    # branch is data): b and bl, then b.cond, cbz / cbnz, tbz / tbnz
    w = img.words[:(exepatch.CAVE_END - img.base) // 4].astype(np.int64)
    for mask, value, bits, shift in ((0x7C000000, 0x14000000, 26, 0), (0xFF000010, 0x54000000, 19, 5),
                                     (0x7E000000, 0x34000000, 19, 5), (0x7E000000, 0x36000000, 14, 5)):
        idx = np.nonzero((w & mask) == value)[0]
        imm = (w[idx] >> shift) & ((1 << bits) - 1)
        target = img.base + idx * 4 + np.where(imm & (1 << (bits - 1)), imm - (1 << bits), imm) * 4
        site = img.base + idx * 4
        into = (target >= lo) & (target < hi) & ((site < lo) | (site >= hi))
        assert not into.any(), "a branch into the unused routines from %x" % int(site[into][0])
    graphs = glob.glob(os.path.join(nxfmt.ROMFS, "Sequence", "*.ainb*"))
    graphs += glob.glob(os.path.join(BUILD_CUSTOM, "*", "romfs", "**", "*.ainb*"), recursive=True)
    graphs += glob.glob(os.path.join(build.EDEN_LOAD, "*", "romfs", "**", "*.ainb*"), recursive=True)
    assert len(graphs) > 100
    for path in graphs:
        data = nxfmt.read(path) if path.endswith(".zs") else open(path, "rb").read()
        assert node.encode() not in data, "%s uses %s" % (path, node)


def colour_patch(p, facts):
    """The colour routines and their tables into the Patcher, and the two calls."""
    img = p.img
    check_dead(img, COLOUR_CODE_NODE, COLOUR_CODE, COLOUR_CODE_SHA1)
    check_dead(img, COLOUR_DATA_NODE, COLOUR_DATA, COLOUR_DATA_SHA1)
    check_dead(img, COLOUR_MORE_NODE, COLOUR_MORE, COLOUR_MORE_SHA1, slots=(0xE8,))
    check_dead(img, COLOUR_KEEP_NODE, COLOUR_KEEP, COLOUR_KEEP_SHA1)
    check_dead(img, COLOUR_TEX_NODE, COLOUR_TEX, COLOUR_TEX_SHA1)
    # the two places, and what the routines take for granted about the code around them
    assert img.dis(HEAD_CALL - 12, 8) == [              # the players' per-frame routine: four updates in a row, each given x19
        "804d7560: mov      x0, x19", "804d7564: bl       #0x804d6eb0", "804d7568: mov      x0, x19",
        "804d756c: bl       #0x804d7b20", "804d7570: mov      x0, x19", "804d7574: bl       #0x804d7690",
        "804d7578: mov      x0, x19", "804d757c: bl       #0x804d7810"]
    assert img.func_start(HEAD_CALL) == 0x804D74B0 and img.dis(0x804d74d4, 2) == [
        "804d74d4: ldr      x8, [x19, #0x110]", "804d74d8: cbz      x8, #0x804d7560"]
    assert img.dis(0x804d7b4c, 4) == [                  # the head gear update: actor, its components, the equipment component
        "804d7b4c: ldr      x8, [x0, #0x110]", "804d7b50: ldr      x8, [x8, #0x270]", "804d7b54: cbz      x8, #0x804d8124",
        "804d7b58: ldr      x20, [x8, #0x200]"]
    assert img.dis(0x804d7bac, 9) == [                  # ... and a slot's actor: id, link, the link's id, the actor, not going away
        "804d7bac: ldr      w9, [x20, #0xf0]", "804d7bb0: cmn      w9, #1", "804d7bb4: b.eq     #0x804d7b68",
        "804d7bb8: ldr      x8, [x20, #0xe8]", "804d7bbc: ldr      w10, [x8, #0x48]", "804d7bc0: cmp      w10, w9",
        "804d7bc4: b.ne     #0x804d7b68", "804d7bc8: ldr      x8, [x8, #0x40]", "804d7bcc: cbz      x8, #0x804d7b68"]
    assert img.dis(0x80a9abc4, 10) == [                 # a pouch player's node: the pouch, its target's equipment component, the hair routine on it
        "80a9abc4: ldr      x8, [x21]", "80a9abc8: ldr      x9, [x8, #0x470]", "80a9abcc: cbz      x9, #0x80a9abec",
        "80a9abd0: mov      w10, #0x13c4", "80a9abd4: ldrb     w11, [x9, x10]", "80a9abd8: and      w11, w11, #0xfc",
        "80a9abdc: orr      w11, w11, #1", "80a9abe0: strb     w11, [x9, x10]", "80a9abe4: ldr      x0, [x8, #0x470]",
        "80a9abe8: bl       #0x804d8240"]
    assert img.dis(0x80a9aa7c, 4)[0] == "80a9aa7c: adrp     x21, #0x84c9e000" and img.dis(0x80a9aa88, 1) == [
        "80a9aa88: ldr      x21, [x21, #0xd0]"]
    assert (build.ACTOR_SHOWN, build.ACTOR_SHOWN_BIT) == (0x4AC, 0x10000)
    assert img.dis(SET_TEXTURE, 24) == [                # the engine sets a texture: both lists, then tells whoever listens
        "834c5910: sub      sp, sp, #0x10", "834c5914: ldp      x9, x10, [x0, #0x50]", "834c5918: ldr      x10, [x10, w1, sxtw #3]",
        "834c591c: ldr      x8, [x9, w1, sxtw #3]", "834c5920: str      x10, [sp, #8]", "834c5924: ldr      x10, [x2]",
        "834c5928: str      x10, [x9, w1, sxtw #3]", "834c592c: ldr      x9, [x2, #8]", "834c5930: ldr      x10, [x0, #0x58]",
        "834c5934: str      x9, [x10, w1, sxtw #3]", "834c5938: ldr      x3, [x0, #0x78]", "834c593c: cbz      x3, #0x834c5968",
        "834c5940: ldr      x9, [x2]", "834c5944: cmp      x8, x9", "834c5948: b.eq     #0x834c5954",
        "834c594c: add      sp, sp, #0x10", "834c5950: br       x3", "834c5954: ldr      x8, [x0, #0x58]",
        "834c5958: ldr      x9, [sp, #8]", "834c595c: ldr      x8, [x8, w1, sxtw #3]", "834c5960: cmp      x9, x8",
        "834c5964: b.ne     #0x834c594c", "834c5968: add      sp, sp, #0x10", "834c596c: ret      "]
    assert img.dis(0x811fb720, 8) == [                  # a living material is given its lists: the file's views and texture slots
        "811fb720: ldr      x11, [x23, #0x18]", "811fb724: ldr      x12, [x19, #0x50]", "811fb728: ldr      x11, [x11, x9, lsl #3]",
        "811fb72c: str      x11, [x12, x9, lsl #3]", "811fb730: ldr      x11, [x23, #0x98]", "811fb734: ldr      x12, [x19, #0x58]",
        "811fb738: ldr      x11, [x11, x9, lsl #3]", "811fb73c: str      x11, [x12, x9, lsl #3]"]
    assert img.dis(0x811fb5e0, 1) == ["811fb5e0: ldrb     w24, [x23, #0xa3]"]      # ... as many as +0xa3 says
    assert img.dis(0x803819cc, 2) == ["803819cc: ldr      x8, [x22, #0x20]", "803819d0: ldr      x23, [x8, x20, lsl #3]"]   # the loader goes by the names
    assert img.dis(0x80380464, 6) == [                  # the binder: the texture's view and slot into the file's two lists
        "80380464: ldp      x9, x8, [x25, #0x88]", "80380468: ldr      x10, [x26, #0x18]", "8038046c: sub      x0, x29, #0x68",
        "80380470: str      x9, [x10, x28, lsl #3]", "80380474: ldr      x9, [x26, #0x98]", "80380478: str      x8, [x9, x28, lsl #3]"]
    assert img.dis(FRAME_SITE - 4, 9) == [
        "8289d5fc: strb     w9, [x10, x8]", "8289d600: ldp      d9, d8, [sp, #0x50]",
        "8289d604: ldp      d11, d10, [sp, #0x40]", "8289d608: ldp      d13, d12, [sp, #0x30]",
        "8289d60c: ldp      d15, d14, [sp, #0x20]", "8289d610: ldp      x20, x19, [sp, #0x90]",
        "8289d614: ldp      x22, x21, [sp, #0x80]", "8289d618: ldp      x24, x23, [sp, #0x70]",
        "8289d61c: ldp      x29, x30, [sp, #0x60]"]
    assert img.func_start(FRAME_SITE) == 0x8289D110
    assert img.dis(0x80c384b8, 4) == [                  # the menu actor manager, as a menu node gets it
        "80c384b8: adrp     x8, #0x84e9c000", "80c384bc: add      x8, x8, #0xfa8",
        "80c384c0: ldr      x8, [x8]", "80c384c4: ldr      x8, [x8, #0x4e8]"]
    assert img.dis(0x804d825c, 12) == [                 # the hair routine: actor, model component, its type, the unit list
        "804d825c: ldr      x21, [x0, #0x18]", "804d8260: ldr      x8, [x21, #0x270]", "804d8264: cbz      x8, #0x804d83f0",
        "804d8268: ldr      x0, [x8, #8]", "804d826c: cbz      x0, #0x804d83f0", "804d8270: ldr      x8, [x0]",
        "804d8274: adrp     x9, #0x84cd9000", "804d8278: ldr      x8, [x8, #0xf8]", "804d827c: ldr      x9, [x9, #0xd58]",
        "804d8280: cmp      x8, x9", "804d8284: b.ne     #0x804d83f8", "804d8288: ldr      x19, [x0, #0x18]"]
    assert img.dis(0x80232e74, 12) == [                 # the game setting colour 0: parameter index, then the four floats
        "80232e74: ldr      x8, [x19]", "80232e78: ldr      x8, [x8, #0xe0]", "80232e7c: blr      x8",
        "80232e80: cmn      w0, #1", "80232e84: b.eq     #0x80232ea4", "80232e88: ldr      x8, [x19]",
        "80232e8c: add      x3, sp, #8", "80232e90: mov      w2, w0", "80232e94: mov      x0, x19",
        "80232e98: mov      w1, w21", "80232e9c: ldr      x8, [x8, #0x158]", "80232ea0: blr      x8"]
    assert img.dis(0x80232e2c, 5) == [                  # ... and before it, how many materials the unit has (cz_cloth counts them so)
        "80232e2c: ldr      x8, [x19]", "80232e30: mov      x0, x19", "80232e34: ldr      x8, [x8, #0xc8]",
        "80232e38: blr      x8", "80232e3c: cbz      w0, #0x80232eb0"]
    assert img.cstr(0x83dd2ed1) == PARAM_NAMES[0] and img.cstr(0x83d86779) == HAIR_NAMES[0]
    got = build.GAMEDATA_MGR_GOT
    keep = p.cave_ptr
    data = colour_data(facts)
    at = COLOUR_DATA[0] + 16            # behind sixteen bytes that return at once, whatever reads the class's entry as a routine
    assert at + len(data) <= COLOUR_DATA[1], "the tables do not fit (%d bytes over)" % (at + len(data) - COLOUR_DATA[1])
    p.raw(COLOUR_DATA[0], exepatch.assemble("ret", COLOUR_DATA[0]) * 4)
    p.raw(at, data)
    p.data_at.add(at)
    p.symbols["cz_data"] = at
    values = dict(got_page=got & ~0xFFF, got_off=got & 0xFFF, type_page=MODEL_TYPE_GOT & ~0xFFF, type_off=MODEL_TYPE_GOT & 0xFFF,
                  data_page=at & ~0xFFF, data_off=at & 0xFFF, hairs=len(HAIR_COLOURS), eyes=len(EYE_COLOURS),
                  nchar=len(facts), names=len(HAIR_NAMES), brow=len(HAIR_NAMES) - 1, brows=D_BROWS - D_HAIRS, d_mask=D_MASK, d_hairs=D_HAIRS, d_names=D_NAMES, d_param=D_PARAM,
                  d_param2=D_PARAM + 32, d_eyes=D_EYES, d_hue=D_HUE, d_eye=D_EYE, head=HEAD_ROUTINE,
                  menu_page=MENU_GLOBAL & ~0xFFF, menu_off=MENU_GLOBAL & 0xFFF,
                  slot_id=build.MENU_SLOT + 0x18, slot_link=build.MENU_SLOT + 0x10,
                  pouch_page=POUCH_GOT & ~0xFFF, pouch_off=POUCH_GOT & 0xFFF,
                  gear_lo=GEAR_HANDLES & 0xFFFF, gear_hi=GEAR_HANDLES >> 16, gear_count=GEAR_COUNT,
                  gear_id=GEAR_FIRST + 0x10, gear_link=GEAR_FIRST + 8,
                  shown=build.ACTOR_SHOWN, shown_bit=build.ACTOR_SHOWN_BIT, keep=KEEP_COUNT, options=len(OPTIONS),
                  mat_samplers=MAT_SAMPLERS, mat_textures=MAT_TEXTURES, mat_views=MAT_VIEWS, mat_slots=MAT_SLOTS,
                  obj_views=OBJ_VIEWS, set_texture=SET_TEXTURE, cloths=len(CLOTH_TEXTURE), d_clothes=D_CLOTHES,
                  d_keep=len(data) - 8 - 4 * len(OPTIONS), d_keep4=len(data) - 4 - 4 * len(OPTIONS), d_opts=len(data) - 4 * len(OPTIONS))
    assert GEAR_HANDLES >> GEAR_COUNT == 0 and GEAR_FIRST + 0x18 * 8 == 0xE0     # the game's thirteen slots are handles 8 to 20
    n_cloth = len(CLOTH_TEXTURE)            # what cz_cloth reads from a palette's length
    assert CLOTH_LENGTHS == [n_cloth, n_cloth + 1, n_cloth + 3] and len(HAIR_TEXTURE) == n_cloth + 2 and len(CLOTHES_FLAGS) == 3
    ends = {where: lo for where, (lo, _hi) in CZ_PLACES.items()}
    for name, text, where in CZ_BLOCKS:
        p.cave_ptr = ends[where]
        p.cave(name, text.format(**dict(values, **p.symbols)))
        ends[where] = p.cave_ptr
    assert all(ends[where] <= hi for where, (_lo, hi) in CZ_PLACES.items())
    p.cave_ptr = keep
    p.asm(HEAD_CALL, "bl {cz_head}", expect="bl #%#x" % HEAD_ROUTINE)
    p.asm(FRAME_SITE, "bl {cz_frame}", expect="ldp d9, d8, [sp, #0x50]")
    for addr, reg, base in COUNT_PATCHES:           # a material's textures are counted by their own byte
        p.asm(addr, "ldrb %s, [%s, #%#x]" % (reg, base, MAT_TEXTURES), expect="ldrb %s, [%s, #%#x]" % (reg, base, MAT_SAMPLERS))
    return ends, len(data)


def check_colour(p, facts):
    """The routines run in an ARM64 emulator on actors built the way the game's code reads
    them (actor, its components, the model object and its units, material objects, the
    model file's materials and their value blocks; the equipment component and its slots'
    actors), with the three virtual functions of a unit and the head gear update answered
    from here; a GameData manager, a menu actor manager and a pouch as the game lays them
    out. For every hair and eye colour, the gear shown and hidden, characters with and
    without hair, units with and without the materials: what is set is what the tables
    say, and nothing else is touched; and the keeping of the options per character.
    -> number of runs."""
    import random
    import unicorn
    from unicorn import arm64_const as A
    rnd = random.Random(1337)
    code = {name: (p.symbols[name], dict(p.patches)[p.symbols[name]]) for name, _t, _w in CZ_BLOCKS}
    for name, (addr, blob) in code.items():
        ins = list(p.img.cs.disasm(blob, addr))
        assert len(ins) * 4 == len(blob), (name, "does not disassemble")
        assert [i.mnemonic for i in ins].count("ret") == 1 and ins[-1].mnemonic == "ret", name
        assert not any(i.mnemonic == "br" for i in ins)
        for i in ins:           # every branch stays inside its routine and none goes below the entry; calls go to our entries or the head gear update
            if i.mnemonic == "bl":
                assert int(i.op_str.split("#")[-1], 16) in [a for a, _b in code.values()] + [HEAD_ROUTINE, SET_TEXTURE], (name, i.op_str)
            elif i.mnemonic == "b" or i.mnemonic.startswith("b.") or i.mnemonic in ("cbz", "cbnz", "tbz", "tbnz"):
                assert addr <= int(i.op_str.split("#")[-1], 16) < addr + len(blob), (name, i.mnemonic, i.op_str)
        assert any(lo <= addr and addr + len(blob) <= hi for lo, hi in CZ_PLACES.values())
    for site, name in ((HEAD_CALL, "cz_head"), (FRAME_SITE, "cz_frame")):
        ins = list(p.img.cs.disasm(dict(p.patches)[site], site))
        assert len(ins) == 1 and ins[0].mnemonic == "bl" and ins[0].op_str == "#%#x" % p.symbols[name]
    assert dict(p.patches)[p.symbols["cz_data"]] == colour_data(facts)
    got, n = build.GAMEDATA_MGR_GOT, len(facts)
    keys = [k for k, _l, _v in OPTIONS]
    hashes = {k: gamedata.murmur3_32(v) for k, v in (
        ("flag", FLAG), ("hair", option_flag(keys.index("Hair"))), ("eyes", option_flag(keys.index("Eyes"))),
        ("char", build.CHARACTER_FLAG), ("gear", option_flag(keys.index("Gear"))), ("clothes", CLOTHES_FLAGS[0]), ("clothes2", CLOTHES_FLAGS[1]),
        ("clothes3", CLOTHES_FLAGS[2]))}
    assert len(set(hashes.values())) == len(hashes)
    NCLOTH = len(CLOTH_TEXTURE)
    CLOTHED = ("Mt_Cloth", "Mt_Metal", "Mt_Teeth", "Mt_Skin")       # the materials that have a clothing palette where a scene has one
    owner_hash, option_hashes = gamedata.murmur3_32(KEEP_OWNER), [gamedata.murmur3_32(option_flag(f)) for f in range(len(OPTIONS))]
    HEAP, STACK, RET, STUB = 0x20000000, 0x30000000, 0x40000000, 0x50000000
    S_D0, S_E0, S_158, S_TYPE, S_C8 = STUB, STUB + 0x10, STUB + 0x20, STUB + 0x30, STUB + 0x40
    for addr, reg, base in COUNT_PATCHES:
        ins = list(p.img.cs.disasm(dict(p.patches)[addr], addr))
        assert len(ins) == 1 and (ins[0].mnemonic, ins[0].op_str) == ("ldrb", "%s, [%s, #0xa3]" % (reg, base)), (hex(addr), ins[0].op_str)
    NPARAM = 129                    # as the character shader; each has sixteen bytes here
    NAMED = {PARAM_NAMES[0]: 102, PARAM_NAMES[1]: 103, PARAM_NAMES[2]: 104}
    SHOWN, BIT = build.ACTOR_SHOWN, build.ACTOR_SHOWN_BIT
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]        # noqa: E731
    runs = [0]

    def scene(values, actors, menu=None, pouch=None, mgr=True, flags=True, slot=None, ints=None, palette=0, odd=None, cloth=0):
        """actors: [{"units": [{material: its value block} or None], "gear": {slot: {...}}}];
        menu / pouch: which of them is the menu character / the pouch's target (None: nobody);
        slot: what is odd about the menu's slot; ints: more GameData Ints {hash: value};
        palette: how many textures the hair material has behind its samplers' own; odd: what
        is wrong with the hair's textures ("swapped": its first is a palette entry already);
        cloth: how many the CLOTHED materials have (a clothing palette if as many as there
        are clothing colours).
        -> the emulator and where things are."""
        mu = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        mu.ctl_set_cpu_model(A.UC_CPU_ARM64_MAX)            # ldset / ldclr are ARMv8.1
        pages = {RET, STUB}
        places = list(CZ_PLACES.values()) + [COLOUR_DATA]
        for lo, hi in places:
            pages |= {lo & ~0xFFF, (hi - 1) & ~0xFFF}
        for a in (got, MODEL_TYPE_GOT, MENU_GLOBAL, POUCH_GOT, HEAD_ROUTINE, SET_TEXTURE):
            pages.add(a & ~0xFFF)
        for page in sorted(pages):
            mu.mem_map(page, 0x1000)
        for addr, blob in p.patches:
            if any(lo <= addr < hi for lo, hi in places):
                mu.mem_write(addr, blob)
        mu.mem_map(HEAP, 0x400000)
        mu.mem_map(STACK, 0x10000)
        top = [HEAP + 0x100]

        def alloc(size, fill=None):
            a = top[0]
            top[0] += (size + 0x1F) & ~0xF
            mu.mem_write(a, bytes(rnd.getrandbits(8) for _ in range(size)) if fill is None else bytes([fill]) * size)
            return a

        def put(addr, fmt, *v):
            mu.mem_write(addr, struct.pack(fmt, *v))
        # GameData: a map of 64 places, our Ints among others
        entries = [(h, values.get(key, 0)) for key, h in hashes.items() if flags or key == "flag"] + list((ints or {}).items())
        assert len(set(h for h, _v in entries)) == len(entries) <= 40
        cap = 64
        cell, gm, gmap, rec = alloc(8, 0), alloc(0x100, 0), alloc(cap * 8, 0), alloc((len(entries) + 2) * 0x28, 0)
        put(got, "<Q", cell)
        put(cell, "<Q", gm if mgr else 0)
        put(gm + 0xe0, "<Q", gmap)
        put(gm + 0xec, "<I", cap)
        put(gm + 0xd0, "<I", len(entries) + 2)
        put(gm + 0xd8, "<Q", rec)
        taken, gd = set(), {}
        for k, (h, v) in enumerate(entries):
            at = h % cap
            while at in taken:
                at = (at + 1) % cap
            taken.add(at)
            put(gmap + 8 * at, "<II", h, k + 2)
            put(rec + (k + 2) * 0x28 + 0x1c, "<i", v)
            gd[h] = rec + (k + 2) * 0x28 + 0x1c
        put(MODEL_TYPE_GOT, "<Q", S_TYPE)
        mvt, uvt = alloc(0x100), alloc(0x200)
        put(mvt + 0xf8, "<Q", S_TYPE)
        for off, stub in ((0xd0, S_D0), (0xe0, S_E0), (0x158, S_158), (0xc8, S_C8)):
            put(uvt + off, "<Q", stub)
        model, made = {}, []        # unit address -> {"names", "inst": {(mat, par): address}, "own": {(mat, par): bytes}}
        for spec in actors:
            actor, comps, mobj, lst, equip = alloc(0x500), alloc(0x400, 0), alloc(0x40), alloc(0x40), alloc(0x1400)
            put(actor + 0x270, "<Q", comps)
            put(actor + 0x2c, "<I", spec.get("state", 4))
            put(comps + 8, "<Q", mobj)
            put(comps + 0x200, "<Q", equip)
            put(equip + 0x18, "<Q", actor)
            put(mobj, "<Q", mvt)
            put(mobj + 0x18, "<Q", lst)
            units = spec.get("units", [])
            arr = alloc(8 * max(len(units), 1), 0)
            put(lst + 0x20, "<i", len(units))
            put(lst + 0x28, "<Q", arr)
            mine = []
            for k, unit in enumerate(units):
                if unit is None:
                    continue
                holder, u = alloc(8), alloc(0x200)
                put(arr + 8 * k, "<Q", holder)
                put(holder, "<Q", u)
                put(u, "<Q", uvt)
                names = list(unit)
                objs = alloc(0x80 * max(len(names), 1))
                if rnd.random() < 0.5:          # the unit's other way to its material objects: a list of pointers
                    ptrs = alloc(8 * max(len(names), 1))
                    for i in range(len(names)):
                        put(ptrs + 8 * i, "<Q", objs + 0x80 * i)
                    put(u + 0x110, "<Q", ptrs)
                    put(u + 0x108, "<Q", 0xDEAD0000)
                else:
                    put(u + 0x110, "<Q", 0)
                    put(u + 0x108, "<Q", objs)
                info = {"names": names, "inst": {}, "own": {}}
                for i, name in enumerate(names):
                    res, shader, refl, parr = alloc(0xB0), alloc(0x10), alloc(0x60), alloc(0x18 * NPARAM)
                    own, inst = alloc(16 * NPARAM), alloc(16 * NPARAM)
                    put(objs + 0x80 * i, "<Q", res)
                    put(objs + 0x80 * i + 0x48, "<Q", inst)
                    put(res + 0x10, "<Q", shader)
                    put(shader, "<Q", refl)
                    put(refl + 0x20, "<Q", parr)
                    put(res + 0x58, "<Q", own)
                    # its textures: seven samplers; the file's views and slots as bound; the living material's two lists
                    extra = palette if name in PALETTE_MATERIALS else cloth if name in CLOTHED else 0
                    nt = 7 + extra
                    views, slots, iviews, islots = alloc(8 * nt), alloc(8 * nt), alloc(8 * nt), alloc(8 * nt)
                    vs = [HEAP + 0x200000 + 0x100 * k_ for k_ in rnd.sample(range(1, 4000), nt)]
                    ss = [rnd.getrandbits(40) for _ in range(nt)]
                    if extra and odd == "not bound":
                        vs = vs[:7] + [0] * extra
                    mu.mem_write(res + MAT_SAMPLERS, bytes([7, nt]))
                    put(res + MAT_VIEWS, "<Q", views)
                    put(res + MAT_SLOTS, "<Q", slots)
                    put(objs + 0x80 * i + OBJ_VIEWS, "<Q", iviews)
                    put(objs + 0x80 * i + OBJ_SLOTS, "<Q", islots)
                    first = list(zip(vs, ss))
                    if extra and odd == "swapped":      # another colour's texture is there already
                        first[0] = first[7 + rnd.randrange(extra)]
                    for j in range(nt):
                        put(views + 8 * j, "<Q", vs[j])
                        put(slots + 8 * j, "<Q", ss[j])
                        put(iviews + 8 * j, "<Q", first[j][0])
                        put(islots + 8 * j, "<Q", first[j][1])
                    info.setdefault("tex", {})[i] = dict(iviews=iviews, islots=islots, vs=vs, ss=ss, first=first, extra=extra,
                                                         obj=objs + 0x80 * i)
                    mu.mem_write(own, unit[name])
                    mu.mem_write(inst, unit[name])
                    for j in range(NPARAM):
                        put(parr + 0x18 * j + 0x10, "<H", 16 * j)
                        info["inst"][(i, j)] = inst + 16 * j
                        info["own"][(i, j)] = unit[name][16 * j:16 * j + 16]
                model[u] = info
                mine.append(u)
            # the equipment: its handles, empty but for the ones asked for
            gear = {}
            for k in range(GEAR_COUNT):
                put(equip + GEAR_FIRST + 0x18 * k + 0x10, "<i", -1)
            for k, g in spec.get("gear", {}).items():
                link, a = alloc(0x60, 0), alloc(0x500)
                word = (rnd.getrandbits(32) & ~BIT) | (BIT if g.get("shown", True) else 0)
                put(equip + GEAR_FIRST + 0x18 * k + 8, "<Q", 0 if g.get("no link") else link)
                put(equip + GEAR_FIRST + 0x18 * k + 0x10, "<i", g.get("id", 40 + k))
                put(link + 0x48, "<i", g.get("link_id", 40 + k))
                put(link + 0x40, "<Q", 0 if g.get("no actor") else a)
                put(a + 0x2c, "<I", g.get("state", 4))
                put(a + SHOWN, "<I", word)
                gear[k] = (a, word, g)
            made.append(dict(actor=actor, units=mine, gear=gear, equip=equip, comps=comps))
        # the menu: the global, its holder, the manager, slot 0; the pouch and its target
        holder, mm, link = alloc(0x600, 0), alloc(0x100, 0), alloc(0x60, 0)
        put(MENU_GLOBAL, "<Q", holder)
        put(holder + 0x4e8, "<Q", mm)
        put(mm + build.MENU_SLOT + 0x18, "<i", -1)
        if menu is not None:
            slot = slot or {}
            put(mm + build.MENU_SLOT + 0x10, "<Q", link)
            put(mm + build.MENU_SLOT + 0x18, "<i", slot.get("id", 77))
            put(link + 0x48, "<i", slot.get("link_id", 77))
            put(link + 0x40, "<Q", made[menu]["actor"] if slot.get("actor", True) else 0)
        var, bag = alloc(8, 0), alloc(0x500, 0)
        put(POUCH_GOT, "<Q", var)
        put(var, "<Q", bag)
        if pouch is not None:
            put(bag + 0x470, "<Q", made[pouch]["equip"])
        log = []

        def cstr(addr):
            out = b""
            while True:
                ch = bytes(mu.mem_read(addr + len(out), 1))
                if ch == b"\0":
                    return out.decode()
                out += ch

        def stub(uc, address, size, _user):
            x = lambda r: uc.reg_read(A.UC_ARM64_REG_X0 + r)         # noqa: E731
            if address == S_D0:             # (unit, &name) -> the material's index or -1
                name = cstr(struct.unpack("<Q", bytes(uc.mem_read(x(1), 8)))[0])
                names = model[x(0)]["names"]
                r = names.index(name) if name in names else 0xFFFFFFFF
                log.append(("material", x(0), name, r))
            elif address == S_E0:           # (unit, material, &name) -> the parameter's index or -1
                name = cstr(struct.unpack("<Q", bytes(uc.mem_read(x(2), 8)))[0])
                assert (x(1) & 0xFFFFFFFF) < len(model[x(0)]["names"])
                r = NAMED.get(name, 0xFFFFFFFF)
                log.append(("parameter", x(0), x(1) & 0xFFFFFFFF, name, r))
            elif address == S_158:          # (unit, material, parameter, &four floats)
                mat, par = x(1) & 0xFFFFFFFF, x(2) & 0xFFFFFFFF
                v = bytes(uc.mem_read(x(3), 16))
                uc.mem_write(model[x(0)]["inst"][(mat, par)], v)
                log.append(("set", x(0), mat, par, v))
                r = 0
            elif address == S_C8:           # (unit) -> how many materials it has
                r = len(model[x(0)]["names"])
                log.append(("count", x(0), r))
            elif address == HEAD_ROUTINE:
                log.append(("head gear update", x(0)))
                r = 0
            elif address == SET_TEXTURE:     # (material object, place, &{view, slot}): both lists, as the engine's does
                at = x(1) & 0xFFFFFFFF
                pair = bytes(uc.mem_read(x(2), 16))
                lv, ls = (struct.unpack("<Q", bytes(uc.mem_read(x(0) + o_, 8)))[0] for o_ in (OBJ_VIEWS, OBJ_SLOTS))
                uc.mem_write(lv + 8 * at, pair[:8])
                uc.mem_write(ls + 8 * at, pair[8:])
                log.append(("texture", x(0), at) + struct.unpack("<QQ", pair))
                r = 0
            else:
                return
            uc.reg_write(A.UC_ARM64_REG_X0, r)
            for reg in range(1, 18):        # a call may change every register a caller cannot rely on
                uc.reg_write(A.UC_ARM64_REG_X0 + reg, rnd.getrandbits(64))
            for reg in range(0, 8):
                uc.reg_write(A.UC_ARM64_REG_V0 + reg, rnd.getrandbits(128))
            uc.reg_write(A.UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_X30))
        for a in (S_D0, S_E0, S_158, S_C8, HEAD_ROUTINE, SET_TEXTURE):
            mu.mem_write(a, struct.pack("<I", 0xD65F03C0))      # ret, never reached: the hook leaves first
            mu.hook_add(unicorn.UC_HOOK_CODE, stub, begin=a, end=a)
        return mu, dict(actors=made, model=model, log=log, alloc=alloc, put=put, mem=lambda a, k: bytes(mu.mem_read(a, k)),
                        int_of=lambda h: struct.unpack("<i", bytes(mu.mem_read(gd[h], 4)))[0])

    def call(mu, entry, x0, frame=None, x1=None):
        sp = STACK + 0x8000
        keep = [A.UC_ARM64_REG_X0 + i for i in range(19, 29)] + [A.UC_ARM64_REG_X29]
        vals = [rnd.getrandbits(64) for _ in keep]
        for r, v in zip(keep, vals):
            mu.reg_write(r, v)
        keep_v = [A.UC_ARM64_REG_V0 + i for i in range(8, 16)]
        vv = [rnd.getrandbits(64) for _ in keep_v]              # the low halves are a callee's to keep
        for r, v in zip(keep_v, vv):
            mu.reg_write(r, v)
        for r in range(1, 19):
            mu.reg_write(A.UC_ARM64_REG_X0 + r, rnd.getrandbits(64))
        mu.reg_write(A.UC_ARM64_REG_SP, sp)
        mu.reg_write(A.UC_ARM64_REG_X0, x0)
        if x1 is not None:
            mu.reg_write(A.UC_ARM64_REG_X1, x1)
        mu.reg_write(A.UC_ARM64_REG_X30, RET)
        if frame is not None:
            mu.mem_write(sp + 0x50, struct.pack("<QQ", *frame))
        mu.emu_start(entry, RET, count=400000)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == RET and mu.reg_read(A.UC_ARM64_REG_SP) == sp
        now = [mu.reg_read(r) for r in keep]
        if frame is None:
            assert now == vals, "a register a caller relies on was changed"
            assert [mu.reg_read(r) & (2 ** 64 - 1) for r in keep_v] == vv
        else:                   # the camera update's way out: x19 to x24 are reloaded right after (we use x19 and x20), and d9 and d8 here
            assert now[2:] == vals[2:]
            assert [mu.reg_read(r) & (2 ** 64 - 1) for r in keep_v[2:]] == vv[2:]
            assert (mu.reg_read(A.UC_ARM64_REG_V9) & (2 ** 64 - 1), mu.reg_read(A.UC_ARM64_REG_V8) & (2 ** 64 - 1)) == tuple(frame)
        runs[0] += 1

    def options(values):
        hair, eyes, char = (values.get(k, 0) for k in ("hair", "eyes", "char"))
        hair = hair if 0 <= hair <= len(HAIR_COLOURS) else 0
        eyes = eyes if 0 <= eyes <= len(EYE_COLOURS) else 0
        char = char if 0 <= char < n else n
        return hair, eyes, char

    def cloth_of(values):
        """the three clothing colours as the code takes them (a number that is no colour is the model's own)"""
        return [c if 0 <= c <= NCLOTH else 0 for c in (values.get(k, 0) for k in ("clothes", "clothes2", "clothes3"))]

    def expect(made, values, model, own_back):
        """What must be set on an actor's model, in order: [(unit, material index, parameter
        index, bytes)]. own_back: an option at 0 sets the model's own values (else nothing)."""
        hair, eyes, char = options(values)
        is_hair = char < n and facts[char][0]
        shift = iris_shift(None if char == n else facts[char][3])
        out = []
        for u in made["units"]:
            info = model[u]
            names = info["names"]
            if hair or own_back:
                for k, name in enumerate(HAIR_NAMES):
                    if (k == 0 and not is_hair) or name not in names:
                        continue
                    for c in range(2):
                        key = (names.index(name), NAMED[PARAM_NAMES[c]])
                        own = info["own"][key]
                        if hair == 0:
                            out.append((u,) + key + (own,))
                        else:
                            rgb = brow_colour(list(HAIR_TEXTURE.values())[hair - 1]) if name == HAIR_NAMES[-1] else \
                                list(HAIR_COLOURS.values())[hair - 1][c]
                            out.append((u,) + key + (struct.pack("<3f", *(f32(byte(x)) * f32(1 / 255.0) for x in rgb)) + own[12:],))
            if (eyes or own_back) and EYE_NAME in names:
                key = (names.index(EYE_NAME), NAMED[PARAM_NAMES[2]])
                own = info["own"][key]
                if eyes == 0:
                    out.append((u,) + key + (own,))
                else:
                    x, y, z = list(EYE_COLOURS.values())[eyes - 1]
                    xb = min(255, max(0, byte(x) + shift))
                    out.append((u,) + key + (struct.pack("<3f", *(f32(b) * f32(1 / 255.0) for b in (xb, byte(y), byte(z)))) + own[12:],))
        return out

    def close(a, b):
        # the same bytes (a model's own values, which here are any bytes at all), or three floats a rounding apart
        return len(a) == len(b) and all(x[:3] == y[:3] and x[3][12:] == y[3][12:] and (x[3] == y[3] or all(
            abs(p_ - q_) < 1e-6 for p_, q_ in zip(struct.unpack("<3f", x[3][:12]), struct.unpack("<3f", y[3][:12])))) for x, y in zip(a, b))

    def sets_of(s, made):
        return [e[1:] for e in s["log"] if e[0] == "set" and e[1] in made["units"]]

    def check_model(s, made, want):
        """The model holds it: every value of every material is its own or what was asked."""
        for u in made["units"]:
            info = s["model"][u]
            for key, at in info["inst"].items():
                last = [w_[3] for w_ in want if w_[:3] == (u,) + key]
                have = s["mem"](at, 16)
                if last:
                    assert close([(0, 0, 0, have)], [(0, 0, 0, last[-1])])
                else:
                    assert have == info["own"][key]

    def check_gear(s, made, values, own_back):
        """Every handle's "shown" bit: off in GEAR_HANDLES when hidden, on when shown and asked
        to put things back; everything else, and every other bit, as it was."""
        hidden = values.get("gear", 0) == 1
        for k, (a, word, g) in made["gear"].items():
            now = struct.unpack("<I", s["mem"](a + SHOWN, 4))[0]
            alive = g.get("id", 40 + k) == g.get("link_id", 40 + k) != -1 and not g.get("no link") and not g.get("no actor") \
                and not 7 <= g.get("state", 4) <= 11
            if not (GEAR_HANDLES >> k & 1) or not alive or not (hidden or own_back):
                assert now == word, (k, g, hex(now), hex(word))
            else:
                assert now == ((word & ~BIT) if hidden else (word | BIT)), (k, g, hidden)

    def check_textures(s, made, values, own_back, odd=None):
        """Every living material's two lists: as they were, but the hair's first place where it
        has a palette: the colour's entry; or the file's own where there is no entry for the
        colour and things are put back. And the engine's routine was called for exactly that."""
        hair, _eyes, char = options(values)
        clothes = cloth_of(values)
        is_hair = char < n and facts[char][0]
        calls = [e[1:] for e in s["log"] if e[0] == "texture"]
        asked = []
        for u in made["units"]:
            info = s["model"][u]
            wants = {}
            for hairs in (False, True):         # a unit's clothes first (its materials by number), then its hair
                for i, tx in info["tex"].items():
                    name = info["names"][i]
                    if (name in PALETTE_MATERIALS) != hairs:
                        continue
                    want = list(tx["first"])
                    if hairs:
                        on, colour = tx["extra"] and (name != HAIR_NAMES[0] or is_hair) and (hair or own_back), hair
                    else:                       # a clothing palette: by its length the first, second or third colour's
                        slot = {NCLOTH: 0, NCLOTH + 1: 1, NCLOTH + 3: 2}.get(tx["extra"])
                        on, colour = slot is not None and (clothes[slot] or own_back), 0 if slot is None else clothes[slot]
                    if on:
                        to = 6 + colour if 0 < colour <= tx["extra"] else 0
                        if (to or own_back) and tx["vs"][to] and want[0][0] != tx["vs"][to]:
                            want[0] = (tx["vs"][to], tx["ss"][to])
                            asked.append((tx["obj"], 0) + want[0])
                    wants[i] = want
            for i, tx in info["tex"].items():
                k_ = len(tx["vs"])
                now = list(zip(struct.unpack("<%dQ" % k_, s["mem"](tx["iviews"], 8 * k_)), struct.unpack("<%dQ" % k_, s["mem"](tx["islots"], 8 * k_))))
                assert now == wants[i], (info["names"][i], hair, clothes, own_back, odd, tx["extra"])
        mine = {tx["obj"] for u in made["units"] for tx in s["model"][u]["tex"].values()}
        assert [c for c in calls if c[0] in mine] == asked, (hair, own_back, odd)

    def material(*names):
        return {name: bytes(rnd.getrandbits(8) for _ in range(16 * NPARAM)) for name in names}

    haired = [k for k in range(n) if facts[k][0]]
    bald = [k for k in range(n) if not facts[k][0] and k != 1]
    assert haired and bald and 0 in haired
    odd_hue = [k for k in range(n) if facts[k][3] is not None and abs(facts[k][3] - COMMON_IRIS_HUE) > 0.05]
    assert odd_hue, "no character with an iris texture of its own"
    shapes = [
        [material("Mt_Face", "Mt_Hairs", "Mt_Eye_Inner", "Mt_Eyebrow", "Mt_Cloth")],
        [material("Mt_Skin"), None, material("Mt_Hairs", "Mt_Hair_Front"), material("Mt_Eye_Inner", "Mt_Teeth")],
        [material("Mt_Hairs", "Mt_HairLong", "Mt_Hair1", "Mt_Hair3", "Mt_Ponytail", "Mt_BodyHair", "Mt_Eyebrow", "Mt_Eye_Inner")],
        [material("Mt_Cloth", "Mt_Metal")],
        [],
    ]
    gears = [
        {},
        {0: {}, 1: {"state": 6}, 4: {}, 8: {}, 12: {}, 16: {}, 17: {}, 20: {}},  # a child as snaildev's game has him
        {k: {"shown": k % 3 != 0} for k in range(GEAR_COUNT)},
        {0: {"id": -1}, 2: {"link_id": 3}, 3: {"no actor": True}, 4: {"state": 7}, 5: {"state": 11}, 6: {"state": 12},
         7: {"no link": True}, 9: {"state": 6}, 18: {"shown": False}, 20: {"shown": False}},
    ]
    chars = [0, haired[-1], bald[0], odd_hue[0], n, n + 5, -1, 10 ** 6]
    entry = p.symbols["cz_actor"]
    count = 0
    for units in shapes:
        for char in chars:
            for hair in list(range(len(HAIR_COLOURS) + 1)) + [len(HAIR_COLOURS) + 1, -3]:
                for eyes in (0, 1 + hair % len(EYE_COLOURS), len(EYE_COLOURS), len(EYE_COLOURS) + 1):
                    count += 1
                    values = {"flag": 1, "hair": hair, "eyes": eyes, "char": char, "gear": (0, 1, 1, 0, 2, -1)[count % 6],
                              "clothes": (0, 2, NCLOTH, 0, NCLOTH + 1, 1, -1, 0, 5)[count % 9],
                              "clothes2": (0, 0, 3, 6, 0, NCLOTH, 9)[count % 7], "clothes3": (0, 4, 0, 0, 1)[count % 5]}
                    back = count % 2                    # both ways of "Default", each half the time
                    pal = (0, 9, 3, 9, 1)[count % 5]
                    odd = (None, None, "swapped", None, "swapped", None, "not bound")[count % 7]
                    mu, s = scene(values, [{"units": units, "gear": gears[count % len(gears)]}], palette=pal, odd=odd,
                                  cloth=(NCLOTH, 0, NCLOTH + 1, 5, NCLOTH + 3, 9, NCLOTH, NCLOTH + 4, NCLOTH + 1, 6, NCLOTH + 3)[count % 11])
                    made = s["actors"][0]
                    call(mu, entry, made["actor"], x1=back | (rnd.getrandbits(31) << 33))
                    want = expect(made, values, s["model"], bool(back))
                    assert close(sets_of(s, made), want), (char, hair, eyes, back, values)
                    check_model(s, made, want)
                    check_gear(s, made, values, bool(back))
                    check_textures(s, made, values, bool(back), odd)
                    if any(cloth_of(values)) or back:   # the unit's materials are counted once a unit, and only then
                        assert [e[1] for e in s["log"] if e[0] == "count"] == made["units"]
                    else:
                        assert not [e for e in s["log"] if e[0] == "count"]
                    if not back and not any(options(values)[:2]) and not any(cloth_of(values)):   # nobody customized: not one question asked of the model
                        assert not s["log"], s["log"][:3]
    # the eyes of every character, every colour: x is the colour's for the common iris less half the texture's distance from it
    for char in range(n):
        if facts[char][2] is None:
            continue
        for eyes in range(1, len(EYE_COLOURS) + 1):
            values = {"flag": 1, "eyes": eyes, "char": char}
            mu, s = scene(values, [{"units": [material("Mt_Eye_Inner")]}])
            made = s["actors"][0]
            call(mu, entry, made["actor"], x1=0)
            sets = sets_of(s, made)
            assert close(sets, expect(made, values, s["model"], False))
            x, y, z = struct.unpack("<3f", sets[0][3][:12])
            cx, cy, cz_ = list(EYE_COLOURS.values())[eyes - 1]
            base = COMMON_IRIS_HUE if facts[char][3] is None else facts[char][3]
            hue_common, hue_here = COMMON_IRIS_HUE + 2 * (cx - 0.5), base + 2 * (x - 0.5)
            if 0.0 < x < 1.0:           # the same hue as on the common iris (to a 255th or two), unless x ran into an end
                assert abs(hue_here - hue_common) < 0.02, (char, eyes, x, hue_here, hue_common)
            else:
                assert (x == 0.0 and hue_here >= hue_common - 0.02) or (x == 1.0 and hue_here <= hue_common + 0.02), (char, eyes, x)
            assert abs(y - cy) < 0.003 and abs(z - cz_) < 0.003
    # nothing to do: no actor, no components, no model, another kind of model, no unit list; no GameData, unknown flags
    units = shapes[0]
    for what in ("null", "no components", "no model", "other type", "no list", "no manager", "no flags"):
        values = {"flag": 1, "hair": 3, "eyes": 2, "gear": 1}
        mu, s = scene(values, [{"units": units, "gear": gears[1]}], mgr=what != "no manager", flags=what != "no flags")
        made = s["actors"][0]
        a = made["actor"]
        if what == "no components":
            s["put"](a + 0x270, "<Q", 0)
        elif what == "no model":
            s["put"](made["comps"] + 8, "<Q", 0)
        elif what == "other type":
            s["put"](MODEL_TYPE_GOT, "<Q", S_TYPE + 4)
        elif what == "no list":
            mobj = struct.unpack("<Q", s["mem"](made["comps"] + 8, 8))[0]
            s["put"](mobj + 0x18, "<Q", 0)
        call(mu, entry, 0 if what == "null" else a, x1=1)
        if what in ("no manager", "no flags"):          # the options read as 0: the model's own values and the gear shown, put back
            assert close(sets_of(s, made), expect(made, {}, s["model"], True))
            check_gear(s, made, {}, True)
            mu, s = scene(values, [{"units": units, "gear": gears[1]}], mgr=what != "no manager", flags=what != "no flags")
            made = s["actors"][0]
            call(mu, entry, made["actor"], x1=0)        # ... or, without the putting back, nothing at all
            assert not s["log"], what
            check_gear(s, made, {}, False)
        else:
            assert not s["log"], what
            check_gear(s, made, values if what in ("no model", "other type", "no list") else {}, False)
    # cz_head: the head gear update first, with what it was given; then the options of that part's actor, nothing put back
    for values in ({"hair": 4, "eyes": 5, "char": 0, "gear": 1}, {"char": 0}, {"gear": 1}, {"eyes": 7, "char": 3},
                   {"clothes": 3, "char": 0}, {"clothes": NCLOTH, "hair": 9, "char": 0, "gear": 1}, {"clothes2": 5, "char": 0},
                   {"clothes3": 1, "clothes": 2, "char": 0}):
        mu, s = scene(values, [{"units": shapes[0], "gear": gears[2]}], palette=9,
                      cloth=NCLOTH + 3 if "clothes3" in values else NCLOTH + 1 if "clothes2" in values else NCLOTH)
        made = s["actors"][0]
        part = s["alloc"](0x200)
        s["put"](part + 0x110, "<Q", made["actor"])
        call(mu, p.symbols["cz_head"], part)
        assert s["log"][0] == ("head gear update", part) and not [e for e in s["log"][1:] if e[0] == "head gear update"]
        want = expect(made, values, s["model"], False)
        assert close(sets_of(s, made), want)
        check_model(s, made, want)
        check_gear(s, made, values, False)
        check_textures(s, made, values, False)
        assert bool([e for e in s["log"] if e[0] == "texture"]) == bool(
            values.get("clothes3") or values.get("clothes2") or ("clothes3" not in values and values.get("clothes")) or values.get("hair"))
        if values == {"char": 0}:
            assert len(s["log"]) == 1               # nobody customized: the head gear update and nothing else
    mu, s = scene({"hair": 2}, [{"units": shapes[0]}])
    part = s["alloc"](0x200)
    s["put"](part + 0x110, "<Q", 0)                 # a part without an actor (the routine around it allows for that)
    call(mu, p.symbols["cz_head"], part)
    assert s["log"] == [("head gear update", part)]
    # cz_frame: the menu character if it is there and staying, on every update (the putting back only while FLAG is not
    # 0); the pouch's target only while FLAG is not 0, with the putting back
    frame = (0x400921FB54442D18, 0x3FF0000000000000)
    two = [{"units": shapes[0], "gear": gears[1]}, {"units": shapes[2], "gear": gears[2]}]
    for flag in (0, 1, 2, 41):
        for slot in (None, {}, {"id": -1}, {"link_id": 5}, {"actor": False}, {"state": 7}, {"state": 11}, {"state": 12}, {"state": 6}):
            for field in (None, 4, 9, 3):
                values = {"flag": flag, "hair": (2, 0)[(flag + (field or 0)) % 2], "eyes": 3, "char": 0, "gear": (flag + (field or 0)) % 2,
                          "clothes": (0, 4, 0, NCLOTH)[(flag + (field or 0)) % 4], "clothes3": (0, 0, 2)[(flag + (field or 0)) % 3]}
                actors = [dict(two[0], state=(slot or {}).get("state", 4)), dict(two[1], state=field or 4)]
                mu, s = scene(values, actors, menu=None if slot is None else 0, pouch=None if field is None else 1, slot=slot,
                              palette=9, cloth=(NCLOTH, NCLOTH + 3)[flag % 2])
                call(mu, p.symbols["cz_frame"], rnd.getrandbits(64), frame=frame)
                live = [slot is not None and slot.get("id", 77) == slot.get("link_id", 77) != -1 and
                        slot.get("actor", True) and not 7 <= slot.get("state", 4) <= 11,
                        flag != 0 and field is not None and not 7 <= field <= 11]
                for made, on in zip(s["actors"], live):
                    want = expect(made, values, s["model"], flag != 0) if on else []
                    assert close(sets_of(s, made), want), (flag, slot, field)
                    check_model(s, made, want)
                    check_gear(s, made, values if on else {}, on and flag != 0)
                    check_textures(s, made, values if on else {}, on and flag != 0)
                assert not [e for e in s["log"] if e[0] == "head gear update"]
    mu, s = scene({"flag": 1}, two, menu=0, pouch=1, mgr=False)
    call(mu, p.symbols["cz_frame"], 0, frame=frame)
    assert not s["log"]

    # cz_keep: the six Ints are the owner's; another stored number -> his are loaded and he is the owner
    # (the samples below are written for all six options, in OPTIONS' full order; a build that leaves some out,
    # UNFINISHED, checks with the values of the ones it has: main session, 2026-11-14)
    all_keys = ("Hair", "Eyes", "Clothes", "Outfit", "Gear", "Accessory", "Clothes2", "Clothes3")

    def shown(six):
        return [(list(six) + [0, 0])[all_keys.index(k)] for k, _l, _v in OPTIONS]

    def pack(six):
        return sum((v & 15) << (4 * k) for k, v in enumerate(six))

    def keep_case(char, owner, working, kept_char, kept_owner, missing=None, entry="cz_keep"):
        ints = {hashes["char"]: char, owner_hash: owner}
        ints.update(zip(option_hashes, working))
        for number, kept in ((owner, kept_owner), (char, kept_char)):
            if 0 <= number < KEEP_COUNT:
                ints[KEEP_BASE + number] = kept
        for h in missing or ():
            ints.pop(h, None)
        mu, s = scene({}, [{"units": shapes[0]}], menu=0, flags=False, ints=ints)
        if entry == "cz_keep":
            call(mu, p.symbols[entry], rnd.getrandbits(64))
        else:
            call(mu, p.symbols[entry], rnd.getrandbits(64), frame=frame)
        now = {h: s["int_of"](h) for h in ints}
        want = dict(ints)
        if 0 <= char < KEEP_COUNT and not missing:
            if char == owner:
                want[KEEP_BASE + char] = pack(working)
            else:
                want.update(zip(option_hashes, [(kept_char >> (4 * k)) & 15 for k in range(len(OPTIONS))]))
                want[owner_hash] = char
        assert now == want, (char, owner, working, hex(kept_char), {hex(h): (now[h], want[h]) for h in now if now[h] != want[h]})
        return s

    top = [len(values) - 1 for _k, _l, values in OPTIONS]
    for _ in range(60):
        char, owner = rnd.choice([0, 1, 2, 5, n - 1, KEEP_COUNT - 1]), rnd.choice([0, 2, 5, KEEP_COUNT - 1])
        working = [rnd.randint(0, m) for m in top]
        keep_case(char, owner, working, pack([rnd.randint(0, m) for m in top]), pack([rnd.randint(0, m) for m in top]))
    for char in (KEEP_COUNT, KEEP_COUNT + 7, -1, 10 ** 6):            # a number beyond the kept ones: nothing is touched
        keep_case(char, 3, shown([1, 2, 3, 0, 1, 0]), 0x123456, 0x654321)
    keep_case(4, 2, shown([1, 2, 3, 0, 1, 0]), 0x010203, 0x030201, missing=[owner_hash])           # no owner Int, or none for him: nothing
    keep_case(4, 2, shown([1, 2, 3, 0, 1, 0]), 0x010203, 0x030201, missing=[KEEP_BASE + 4])
    keep_case(4, 4, shown([1, 2, 3, 0, 1, 0]), 0x010203, 0x030201, missing=[KEEP_BASE + 4])
    # a visit: Link's options, then Saria is picked (hers come up), she gets a hair colour, then Link again (his are back)
    state = {"owner": 0, "work": shown([3, 1, 0, 0, 1, 0]), 0: 0, 2: pack(shown([5, 7, 0, 0, 0, 0]))}
    for char, change in ((0, None), (2, None), (2, (0, 9)), (2, None), (0, None), (2, None)):
        if change:
            state["work"][change[0]] = change[1]
        s = keep_case(char, state["owner"], list(state["work"]), state[char], state[state["owner"]])
        state["work"] = [s["int_of"](h) for h in option_hashes]
        state["owner"] = s["int_of"](owner_hash)
        state[char] = s["int_of"](KEEP_BASE + char)
    assert state["owner"] == 2 and state["work"] == shown([9, 7, 0, 0, 0, 0]) and state[0] == pack(shown([3, 1, 0, 0, 1, 0])) and state[2] == pack(shown([9, 7, 0, 0, 0, 0]))
    # ... and through cz_frame: the character that was just picked gets his own options in the same update
    ints = {owner_hash: 0, KEEP_BASE + 0: pack(shown([1, 1, 0, 0, 0, 0])), KEEP_BASE + haired[-1]: pack(shown([4, 2, 0, 0, 1, 0]))}
    ints.update({h: v for h, v in zip(option_hashes, shown([1, 1, 0, 0, 0, 0])) if h not in hashes.values()})
    values = {"flag": 0, "char": haired[-1], "hair": 1, "eyes": 1, "gear": 0}
    mu, s = scene(values, [{"units": shapes[0], "gear": gears[1]}], menu=0, ints=ints)
    call(mu, p.symbols["cz_frame"], 0, frame=frame)
    made = s["actors"][0]
    now = {"flag": 0, "char": haired[-1], "hair": 4, "eyes": 2, "gear": 1}
    want = expect(made, now, s["model"], False)
    assert close(sets_of(s, made), want) and want
    check_gear(s, made, now, False)
    assert s["int_of"](owner_hash) == haired[-1] and s["int_of"](hashes["hair"]) == 4 and s["int_of"](hashes["gear"]) == 1
    return runs[0]


# ---------------------------------------------------------------- the build
def build_folder():
    """build-custom\\MOD from what is installed. -> the lines of the report."""
    B = build
    B.BUILD, B.SIZES, B.EXE_PATCHES = BUILD_CUSTOM, {}, {}      # this run's own output; build\\ is not touched
    assert MOD in B.RETIRED, "the normal build must remove our folder when it installs"
    shutil.rmtree(os.path.join(BUILD_CUSTOM, MOD), ignore_errors=True)
    os.makedirs(BUILD_CUSTOM, exist_ok=True)
    folders = installed()
    print("%s, over the %d installed folders (%s)" % (MOD, len(folders), ", ".join(folders)))
    borrowed, slink_bytes = write_slink()
    # layouts
    archive = nxfmt.unzs(read_installed(ui.COMMON), ui.COMMON)
    page_layout = nxfmt.sarc_files(archive)[ui.LAYOUT + ".buil"]
    known = {n[:-5] for n, _, _ in nxfmt.sarc_entries(archive)}
    assert ui.KEY in known, "the installed layout archive is not the picker's"
    page_new, page_msgs = add_page_anims(page_layout)
    ours, msgs = make_layout()
    panes, anims = check_layout(ours, msgs, known)
    with open(os.path.join(BUILD_CUSTOM, LAYOUT + ".buil"), "wb") as f:      # beside the folder, for the editor tools
        f.write(ours)
    write_files(page_new, ours, msgs, page_msgs)
    ints = write_gamedata()
    # graph: the installed one, read and written the way the normal build edits the game's
    # own graphs; unchanged it comes back byte for byte
    owner, path = resolve(GRAPH)
    g = B.Graph(path)
    assert g.build() == g.src, "the installed graph does not rebuild exactly"
    before = len(g.nodes)
    info = customize_graph(g)
    cmd = g.d["Commands"][0]["Root Node Index"]
    layouts = {SCREEN: anim_frames(layout.Layout(ours)), B.PAGE_SCREEN: anim_frames(layout.Layout(page_new))}
    runs = check_graph(g.nodes, cmd, info, layouts)
    data = g.build()
    B.write(MOD, GRAPH, data, len(data), B.SIZES)
    back = ainbx.load(data).as_dict()
    assert len(back["Nodes"]) == len(g.nodes) and back["Commands"][0]["Root Node Index"] == cmd
    assert check_graph(back["Nodes"], cmd, info, layouts) == runs        # the same run on the file as written
    assert back["Nodes"][before:] != [] and [n.get("Name") for n in back["Nodes"][:before]] == \
        [n.get("Name") for n in ainbx.load(g.src).as_dict()["Nodes"]]
    # code, and the hair's palettes
    facts = model_facts()
    palettes = write_palettes(facts)
    p, ends, colour_tables = custom_patch(facts)
    B.write_ips(MOD, p)
    emulated = check_patch(p)
    coloured = check_colour(p, facts)
    others = check_installed_patches()
    tables, raised = write_table()
    hook = p.symbols["cv_hook"]
    files = sorted(os.path.relpath(os.path.join(d, n), os.path.join(BUILD_CUSTOM, MOD)).replace(os.sep, "/")
                   for d, _dirs, names in os.walk(os.path.join(BUILD_CUSTOM, MOD)) for n in names)
    return [
        "  layout %s: %d bytes, %d panes, %d animations, %d messages; the prompt for X, %s and %s in the picker's layout"
        " (%d -> %d bytes)" % (LAYOUT, len(ours), panes, anims, len(msgs), PAGE_OUT, PAGE_IN, len(page_layout), len(page_new)),
        "  graph: %d nodes (the picker's %d, from '%s'), %d characters; %d button presses run offline, twice (as built, as written)"
        % (len(g.nodes), before, owner, len(info["loops"]), runs),
        "  code patch: %d runs emulated; the picture offset's store at %x calls %x-%x in the unused %s (%x-%x, %d bytes left);"
        " no byte shared with the %d installed patches"
        % (emulated, VIEW_STORE, hook, p.cave_ptr, DEAD_NODE, DEAD[0], DEAD[1], DEAD[1] - p.cave_ptr, others),
        "  colours, gear and keeping: hair (%d), eyes (%d), equipment shown / hidden (handles %s), the options kept"
        " per character (%d Ints); %d stored numbers (%d with hair, %d with an iris material, %d of them with an iris texture of"
        " their own); %d runs emulated; routines in the unused %s; %d bytes of tables in %s; called from %x (every player's update)"
        " and %x (the menu's camera)"
        % (len(HAIR_COLOURS), len(EYE_COLOURS), ", ".join(str(k) for k in range(GEAR_COUNT) if GEAR_HANDLES >> k & 1), KEEP_COUNT,
           len(facts), sum(f[0] for f in facts), sum(f[2] is not None for f in facts),
           sum(f[3] is not None and abs(f[3] - COMMON_IRIS_HUE) > 0.01 for f in facts), coloured,
           ", ".join("%s (%x-%x, %d bytes left)" % (node, CZ_PLACES[w][0], ends[w], CZ_PLACES[w][1] - ends[w]) for w, node in (
               ("code", COLOUR_CODE_NODE), ("more", COLOUR_MORE_NODE), ("keep", COLOUR_KEEP_NODE), ("tex", COLOUR_TEX_NODE))),
           colour_tables, COLOUR_DATA_NODE, HEAD_CALL, FRAME_SITE),
        "  hair by textures: %d model files with a palette on %d hair materials, %d recoloured textures (%d colours each of %d);"
        " %.0f MB of models (%d files, the ones with only a clothing palette too), %.0f MB of textures;"
        " left out (no texture file, or a format not recoloured): %s"
        % (palettes[0], palettes[1], palettes[2], len(HAIR_TEXTURE), palettes[2] // len(HAIR_TEXTURE), palettes[3] / 1e6, palettes[6]["files"], palettes[4] / 1e6,
           ", ".join(sorted({"%s (%s)" % (tex, mat) for _n, _a, mat, tex in palettes[5]})) or "none"),
        "  clothing by textures: %s" % ("not built (the option is not: --clothes)" if not CLOTHES_ON else
                                        "%(models)d model files with a palette on %(materials)d materials, %(textures)d recoloured textures"
                                        " (%(n)d colours each of %(sources)d textures' kinds; %(made)d made in this run), %(mb).0f MB;"
                                        " the main colour is a hue for %(hue)d model files, the greys for %(neutral)d, none for %(none)d;"
                                        " %(how)s (build-custom\\cloth-plan.txt)" % dict(
                                            palettes[6], n=len(CLOTH_TEXTURE), mb=palettes[6]["bytes"] / 1e6,
                                            how="a colour a part of the clothes: two colours for %d model files, three for %d" % (
                                                palettes[6]["two"], palettes[6]["three"]) if PARTS else "one colour for the clothes")),
        "  sounds: %s" % ", ".join("%s = %s on %s" % (what, key, screen) for what, (screen, key) in SOUNDS.items()),
        "  sound link file: %d bytes changed; GlobalSound's %s" % (slink_bytes, ", ".join(borrowed)),
        "  game data: %d unsaved Ints added to the installed list" % ints,
        "  size table: the %d installed tables' entries at their largest, %d raised for our files" % (tables, raised),
        "  %d files: %s" % (len(files), ", ".join(files)),
    ]


def installed_sums():
    """{folder/file: sha1} of every file of every installed folder but ours: what a build of
    ours was made over (it reads them, so it is only right over these very files)."""
    import hashlib
    out = {}
    for folder in installed():
        for d, _dirs, names in os.walk(os.path.join(build.EDEN_LOAD, folder)):
            for n in names:
                path = os.path.join(d, n)
                h = hashlib.sha1()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 22), b""):
                        h.update(chunk)
                out[os.path.relpath(path, build.EDEN_LOAD).replace(os.sep, "/")] = h.hexdigest()
    return out


def source_sums(files=None):
    """{file: sha1} of the project's scripts this run has loaded (after a build: what the build
    was made from; another session's work on a script of its own does not count), or of
    `files` as they are now (a file that is gone has no sum)."""
    import hashlib
    if files is None:
        files = set()
        for mod in list(sys.modules.values()):
            path = os.path.abspath(getattr(mod, "__file__", None) or "")
            if path.endswith(".py") and os.path.dirname(path) in (HERE, os.path.join(ROOT, "tools")):
                files.add(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    return {f: hashlib.sha1(open(os.path.join(ROOT, *f.split("/")), "rb").read()).hexdigest()
            for f in sorted(files) if os.path.exists(os.path.join(ROOT, *f.split("/")))}


BUILT_OVER = os.path.join(BUILD_CUSTOM, "built-over.json")
SWITCHES = ("--split",)         # what changes the build


def main():
    import json
    B = build
    install, remove, only = "--install" in sys.argv, "--remove" in sys.argv, "--install-only" in sys.argv
    if (install or remove or only) and B.emulator_running():
        print("The emulator is running (%s). Close it first: it keeps the mod files open." % ", ".join(B.emulator_running()))
        sys.exit(1)
    if remove:      # for a session whose own install our folder is in the way of (the voices' wants to sort first)
        with modstate.building(SESSION):
            dst = os.path.join(B.EDEN_LOAD, MOD)
            there = os.path.isdir(dst)
            shutil.rmtree(dst, ignore_errors=True)
            if there:
                modstate.stamp(SESSION, B.EDEN_LOAD, [], removed=[MOD], note="the customize folder taken out")
            print("%s: %s" % (MOD, "removed from " + B.EDEN_LOAD if there else "was not installed"))
        return
    # one build at a time, and the record of what is installed (tools\modstate.py, the rules
    # of 2026-11-13): the lock for the whole run, since this build reads the installed folders
    # (another session's install under it would give a mix), and a stamp for our one folder
    switches = [x for x in SWITCHES if x in sys.argv]
    with modstate.building(SESSION):
        # The folder as last built is right for now if the build was made with the same switches, from the same
        # sources and over the very files that are installed now: then --install copies it (seconds) in place of
        # building again (minutes: the palettes). --install-only never builds; a plain run always does.
        why = None
        if install or only:
            try:
                built = json.load(open(BUILT_OVER, encoding="utf-8"))
            except (OSError, ValueError):
                built = None
            if not built or not os.path.isdir(os.path.join(BUILD_CUSTOM, MOD)):
                why = "there is no finished build in %s" % BUILD_CUSTOM
            elif built["switches"] != switches:
                why = "the last build was made with %s, this asks for %s" % (built["switches"] or "no switch", switches or "none")
            elif built["sources"] != source_sums(list(built["sources"])):
                now = source_sums(list(built["sources"]))
                diff = sorted(k for k in built["sources"] if built["sources"][k] != now.get(k))
                why = "the sources changed since the last build (%s%s)" % (", ".join(diff[:4]), " ..." if len(diff) > 4 else "")
            else:
                now = installed_sums()
                if now != built["sums"]:
                    diff = sorted(k for k in set(now) | set(built["sums"]) if now.get(k) != built["sums"].get(k))
                    why = "the last build was made over other files than are installed now (%d differ: %s%s)" % (
                        len(diff), ", ".join(diff[:4]), " ..." if len(diff) > 4 else "")
        if only and why:
            print("Not installed: %s. Run this with --install, which builds over what is there." % why)
            sys.exit(1)
        if why or not (install or only):
            if why:
                print("Building: %s." % why)
            try:
                os.remove(BUILT_OVER)
            except OSError:
                pass
            print("\n".join(build_folder()))
            json.dump({"switches": switches, "sources": source_sums(), "sums": installed_sums()}, open(BUILT_OVER, "w", encoding="utf-8"))
        else:
            print("%s: as last built (nothing it was built from or over has changed)" % MOD)
        if install or only:
            dst = os.path.join(B.EDEN_LOAD, MOD)
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(os.path.join(BUILD_CUSTOM, MOD), dst)
            modstate.stamp(SESSION, B.EDEN_LOAD, [MOD], note="laid over the %d folders installed then" % len(installed()))
            print("  installed ->", dst)


if __name__ == "__main__":
    main()
