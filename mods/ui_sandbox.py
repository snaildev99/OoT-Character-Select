"""UI sandbox: the Characters page as a character picker, or as a set of sample layouts,
built from the game's own UI.

    venv\\Scripts\\python.exe mods\\ui_sandbox.py             the character picker into build-ui\\
    venv\\Scripts\\python.exe mods\\ui_sandbox.py --samples   the six sample pages instead
    ... --install                                           then install into the sandbox emulator
                                                            (tools\\uienv.py; launcher
                                                            PLAY-debug-ui.bat), never the normal one

Builds every mod of mods\\build.py exactly as the normal build does (output redirected to
build-ui\\; build\\ is never touched), then replaces the Characters page's 2D screen and graph.
The picker build goes through npc_characters.build_mods(): the same mods with the exe patch
that knows the whole ring of characters, plus the folder "NPC characters" (their models).

Screen (Link stands on the right third, x = 640; everything here stays left of x = 260):
  layout CharacterMenu_00 = build.page_layout() (Key Items' layout with the light on Link) plus
  N_All_00 -> N_Sandbox_00 with our panes (class Panes). Parts of our own (CUSTOM_PARTS, copies
  of the game's with small edits):
    PaCharacterIndex_00      Threads of Time list entry: title from our messages, the inner gem
                             green and off by default, animation IsChosen (0 empty, 1 filled)
    PaCharacterDot_00        only that diamond: the "current character" marker
    PaCharacterBtn_00        thin button with its text from our messages
    PaCharacterScrollBar_00  scroll bar sized for the list, animation Pos (thumb position)
  Texts: LayoutMsg/CharacterMenu_00.msbt, one label per text pane path (reference "*").

State is shown the way the game does it, with animations stopped at a frame; every way back
to a button loop takes at least one frame (check_graph): the first version looped inside one
frame, which froze the game.

Character picker (default; section "the character picker"): the ring of 100 on 17 sheets of
six equipment-style slots. D-pad moves inside the sheet, X / Y turn the sheet (cursor to its
first slot), A stores the number and runs the normal build's swap, R / ZR go to Key Items,
B / - close. On entry the saved number decides the sheet, the cursor and the green diamond.

Sample pages (--samples; the version snaildev tested on 2026-11-10, built byte for byte the same):
    Page      frame = page                       which container is visible
    Chosen    frame = character                  IsChosen of every row / marker
    Cur_<p>   frame = focused item of page p     each part's own Select (0 or 1), detail text,
                                                 the focus line (a plain pane we move ourselves)
    Scroll    frame = first visible list row     list position, thumb, row visibility
    ScrollDn_<s> / ScrollUp_<s>                  the same change as a 5 frame move (played)
  Graph: one state per focused item (per item and scroll position on the list). D-pad moves,
  A on a character runs the swap, X / Y change page.
"""
import bisect
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ainbx  # noqa: E402
import build  # noqa: E402  (the normal mods, used as a library)
import byml  # noqa: E402
import exepatch  # noqa: E402
import layout  # noqa: E402
import msbt  # noqa: E402
import npc_characters as npc  # noqa: E402  (the NPC session's build, used as a library)
import nxfmt  # noqa: E402
import nxpack  # noqa: E402
import sarcedit  # noqa: E402
import uiedit  # noqa: E402
import uienv  # noqa: E402
from uiedit import ALPHA, SCALE, TRANSLATE, VISIBLE  # noqa: E402,F401

BUILD_UI = os.path.join(ROOT, "build-ui")
SCREEN = build.PAGE_SCREEN                 # "CharacterMenu_00" (screen name)
LAYOUT = "CharacterMenu_00"                # our layout (file CharacterMenu_00.buil)
COMMON = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs"
CATEGORY = "UI/LayoutArchiveCategory.Product.100.byml.zs"
MALS = ["Mals/USen.Product.110.sarc.zs", "Mals/EUen.Product.110.sarc.zs"]
MSG = "LayoutMsg/%s.msbt" % LAYOUT
MSG_BASE = "LayoutMsg/ImportantMenu_01.msbt"   # Key Items' messages (labels of the panes we keep)
LAYOUT_COPIES = 4                          # every layout is stored 4 times in the archive


def align32(n):
    return (n + 31) & ~31


_common = {}


def common_layouts():
    if not _common:
        _common.update(nxfmt.sarc_files(nxfmt.read(os.path.join(nxfmt.ROMFS, COMMON))))
    return _common


def vanilla_layout(name):
    return layout.Layout(common_layouts()[name + ".buil"])


def has_anim(part_layout, anim, cache={}):
    if part_layout not in cache:
        data = CUSTOM_BUILT.get(part_layout) or common_layouts()[part_layout + ".buil"]
        cache[part_layout] = layout.Layout(data).root.ptr[0x140].names()
    return anim in cache[part_layout]


# ---------------------------------------------------------------- characters shown
CHARACTERS = [c["name"] for c in build.CHARACTERS]          # index = stored value
DETAILS = [
    ("Link", "The Hero of Time, as he appears\nin this adventure."),
    ("Retro Link", "Link as he looked in 1998."),
    ("Empty slot", "Room for another character."),
]


def char_name(i):
    return CHARACTERS[i] if i < len(CHARACTERS) else "Character %d" % (i + 1)


# ---------------------------------------------------------------- geometry (layout units)
# Screen 3840 x 2160, centre (0, 0), y up. Link stands at x = 640 (build.LINK_SCREEN_X); the
# header reaches down to about y = 690, the footer up to about y = -950.
UI_LEFT, UI_RIGHT = -1840, 260
TITLE_POS = (UI_LEFT + 700, 640)           # page title, left aligned from UI_LEFT
HINT_POS = (-790, -840)
DETAIL_POS = (-150, 60)                    # name / line / caption between the list and Link
DETAIL_W = 800

LIST_X = -1294                             # row origin, as on Threads of Time (bar: x - 540 .. x + 428)
LIST_Y = -20                               # centre of the visible rows
ROW_PITCH = 192                            # Threads of Time: slide list item height
LIST_VISIBLE = 6
LIST_ROWS = 9
SCROLL_STEPS = LIST_ROWS - LIST_VISIBLE    # scroll positions 0 .. SCROLL_STEPS
SCROLL_FRAMES = 5                          # length of a scroll move
WIN_H = LIST_VISIBLE * ROW_PITCH
BAR_X = LIST_X - 578                       # scroll bar, left of the rows as on Threads of Time
THUMB_H = WIN_H * LIST_VISIBLE / LIST_ROWS


def thumb_y(step):
    """Thumb centre inside the bar for scroll position `step` (may be fractional)."""
    travel = WIN_H - THUMB_H
    return travel / 2 - travel * step / SCROLL_STEPS


# ---------------------------------------------------------------- parts of our own
def part_index():
    """Threads of Time list entry (PaChallengeIndex_00) for characters.
    Vanilla: P_DotBase_00 = the diamond's dark frame (texture ChallengeIndexDot_00: opaque
    frame, dark middle), P_Dot_00 = the gem inside (ChallengeIndexDot_01, grey ramp coloured
    black -> white = palette 7 -> 6, deep red -> orange red); the game's code shows or hides
    P_Dot_00, no animation does. Here: gem hidden by default and switched by a new animation
    IsChosen; palette 6 / 7 with red and green exchanged (same brightness ramp, green); the
    title takes its text from the screen's messages ("*" instead of the code-filled "@...")."""
    lay = vanilla_layout("PaChallengeIndex_00")
    ed = uiedit.Editor(lay)
    ed.validate()
    title = lay.root.ptr[0x80].get("T_Title_00")
    assert title.ptr[0x18].text == "@T_Title_00"
    uiedit.set_text(title, ref="*")
    dot = ed.pane("P_Dot_00")
    mat = lay.root.ptr[0x40].get("P_Dot_00")
    assert dot.visible and mat.get("<6I", 0x18) == (1, 1, 1, 1, 6, 7)
    users = [k.text for k, m, _ in lay.root.ptr[0x40].items if set(m.get("<6I", 0x18)) & {6, 7}]
    assert users == ["P_Dot_00"], users                       # nothing else has these colours
    pal = uiedit.palette(lay)
    assert [round(x, 3) for x in pal[6]] == [1.0, 0.212, 0.09, 1.0], pal[6]
    for i in (6, 7):
        r, g, b, a = pal[i]
        uiedit.set_palette(lay, i, (g, r, b, a))
    dot.visible = False
    ed.add_anim("IsChosen", frames=1, curves=[(ed.key("P_Dot_00", VISIBLE), uiedit.state_curve([0.0, 1.0]))],
                template="Decide")
    uiedit.rename(lay, "PaCharacterIndex_00")
    return lay


def part_dot():
    """Only the diamond of PaCharacterIndex_00, centred on the part's origin."""
    lay = part_index()
    ed = uiedit.Editor(lay)
    # Hidden and shrunk to nothing: the part's RegionType animation switches the two
    # backgrounds' visibility, and nothing animates these panes' scale.
    for name in ("N_IndexBg_00", "N_IndexBg_01", "P_IndexDeco_00", "A_Align_00", "N_Cursor_00"):
        ed.pane(name).visible = False
        ed.pane(name).scale = (0.0001, 0.0001)
    # dot centre = N_All_00 (left edge) + N_Contents_00 (-8) + P_DotBase_00 (44)
    assert ed.pane("N_All_00").pos == (-480.0, 0.0) and ed.pane("N_Contents_00").pos == (-8.0, 0.0)
    assert ed.pane("P_DotBase_00").pos == (44.0, 0.0) and ed.pane("N_Dot_00").pos == (0.0, 0.0)
    ed.pane("N_All_00").pos = (-36.0, 0.0)
    uiedit.set_text(lay.root.ptr[0x80].get("T_Title_00"), ref="@T_Title_00")     # hidden: no message wanted
    uiedit.rename(lay, "PaCharacterDot_00")
    return lay


def part_button():
    """Thin button (PaCommonBtnThin_00) whose text comes from the screen's messages."""
    lay = vanilla_layout("PaCommonBtnThin_00")
    texts = lay.root.ptr[0x80]
    assert texts.get("T_Btn_00").ptr[0x18].text == "" and texts.get("T_Btn_01").ptr[0x18].text == "=T_Btn_00"
    uiedit.set_text(texts.get("T_Btn_00"), ref="*")
    uiedit.rename(lay, "PaCharacterBtn_00")
    return lay


def part_scrollbar():
    """Scroll bar (PaScrollBar_01) WIN_H tall with a thumb for LIST_VISIBLE of LIST_ROWS rows
    and an animation Pos: frame = scroll position (the game's code sizes and moves the
    vanilla one)."""
    lay = vanilla_layout("PaScrollBar_01")
    ed = uiedit.Editor(lay)
    for name in ("P_Base_00", "P_Line_00"):
        assert ed.pane(name).size == (32.0, 312.0) and ed.pane(name).pos == (0.0, 0.0)
        ed.pane(name).size = (32.0, float(WIN_H))
    thumb = ed.pane("P_ScrollBar_00")
    assert thumb.size == (22.0, 32.0) and thumb.pos == (0.0, 0.0)
    thumb.size = (22.0, float(THUMB_H))
    thumb.pos = (0.0, thumb_y(0))
    steps = range(SCROLL_STEPS + 1)
    ed.add_anim("Pos", frames=SCROLL_STEPS, template="SunLight",
                curves=[(ed.key("P_ScrollBar_00", TRANSLATE),
                         [uiedit.state_curve([0.0 for _ in steps]), uiedit.state_curve([thumb_y(s) for s in steps])])])
    uiedit.rename(lay, "PaCharacterScrollBar_00")
    return lay


CUSTOM_PARTS = {"PaCharacterIndex_00": part_index, "PaCharacterDot_00": part_dot,
                "PaCharacterBtn_00": part_button, "PaCharacterScrollBar_00": part_scrollbar}
CUSTOM_BUILT = {}     # name -> bytes


def build_custom_parts(parts):
    for name, fn in parts.items():
        lay = fn()
        uiedit.Editor(lay).validate()
        data = layout.build(lay)
        check = layout.Layout(data)
        uiedit.Editor(check).validate()
        assert layout.build(check) == data and check.name == name
        CUSTOM_BUILT[name] = data
    return CUSTOM_BUILT


# ---------------------------------------------------------------- footprints
# What a part covers around its origin (x0, y0, x1, y1): its hit box B_Hit_00 when it has
# one, else its visible pictures and texts (uiedit.part_bounds). For these the pictures
# are the better measure (their hit box covers only part of what is drawn):
FOOTPRINT_FROM_PICTURES = {"PaOptionBtn_00", "PaOptionBtnL_00", "PaMessageBtn_00", "PaChallengeIndex_00",
                           "PaCharacterIndex_00", "PaCommonBtnThin_00", "PaCharacterBtn_00",
                           "PaCommonBtnThinAction_00"}
_footprints = {}


def footprint(part):
    if part not in _footprints:
        lay = layout.Layout(CUSTOM_BUILT.get(part) or common_layouts()[part + ".buil"])
        ed = uiedit.Editor(lay)
        rects = uiedit.pane_rects(lay, hidden=True)
        if "B_Hit_00" in rects and part not in FOOTPRINT_FROM_PICTURES:
            box = rects["B_Hit_00"][:4]
        else:
            box = None
            for i in range(len(ed.panes)):
                v = ed.pane(i)
                r = rects[v.name]
                if not r[4] or v.type not in (1, 2) or any(w in v.name for w in ("Cursor", "Glow", "Eff")):
                    continue
                box = r[:4] if box is None else (min(box[0], r[0]), min(box[1], r[1]),
                                                 max(box[2], r[2]), max(box[3], r[3]))
            assert box is not None, part
        _footprints[part] = box
    return _footprints[part]


def fit(part, centre, cell, max_scale=1.0):
    """(position, scale) that centres a part's footprint on `centre` inside `cell` (w, h)."""
    x0, y0, x1, y1 = footprint(part)
    scale = min(cell[0] / (x1 - x0), cell[1] / (y1 - y0), max_scale)
    return (centre[0] - scale * (x0 + x1) / 2, centre[1] - scale * (y0 + y1) / 2), scale


# ---------------------------------------------------------------- pages
class Item:
    """One element of a page: a part pane, where it sits, and what it stands for."""

    def __init__(self, part, pos, scale=1.0, size=(200, 200), anims=(), char=None, label=None, texts=None,
                 dot=None, detail=None, label_pos=None, label_size=40, label_w=760, focus=True):
        self.part, self.pos, self.scale, self.size, self.anims = part, pos, scale, size, list(anims)
        self.char = char              # character index chosen with A (None: nothing)
        self.label, self.label_pos, self.label_size, self.label_w = label, label_pos, label_size, label_w
        self.texts = texts or {}      # text pane inside the part -> text
        self.dot = dot                # position of a PaCharacterDot_00 marker (None: no marker)
        self.detail = detail          # index into DETAILS shown while focused
        self.focus = focus            # can take the cursor
        self.pane = self.dot_pane = self.label_pane = None   # pane names, set by make_layout


class Page:
    def __init__(self, key, tag, title, items, kind="list", columns=1):
        self.key, self.tag, self.title, self.items = key, tag, title, items   # tag: in pane names
        self.kind, self.columns = kind, columns
        self.index = None

    @property
    def focusable(self):
        return [i for i, it in enumerate(self.items) if it.focus]

    @property
    def has_detail(self):
        return any(it.detail is not None for it in self.items)

    def states(self):
        """Graph states: (item,) or, on the scrolling list, (item, first visible row)."""
        if self.kind == "scroll":
            return [(i, s) for s in range(SCROLL_STEPS + 1) for i in range(s, s + LIST_VISIBLE)]
        return [(i,) for i in self.focusable]

    def moves(self, state):
        """{button: next state} for the D-pad."""
        i = state[0]
        out = {}
        if self.kind == "scroll":
            s = state[1]
            if i + 1 < len(self.items):
                out["Down"] = (i + 1, s if i + 1 < s + LIST_VISIBLE else s + 1)
            if i > 0:
                out["Up"] = (i - 1, s if i - 1 >= s else s - 1)
            return out
        f = self.focusable
        at = f.index(i)
        c = self.columns
        steps = {"Up": -c, "Down": c}
        if c > 1:
            steps.update(Left=-1, Right=1)
        for b, d in steps.items():
            to = at + d
            same_row = to // c == at // c
            if 0 <= to < len(f) and (abs(d) == c or same_row):
                out[b] = (f[to],)
        return out


FOCUS_LINE_W = 1460.0        # P_Element_00, the game's ornament line (texture SystemLine_02)


def focus_line(it, pos):
    """(x, y, x scale) of the focus line under an item placed at `pos`: just below its
    footprint, nine tenths as wide."""
    x0, y0, x1, y1 = footprint(it.part)
    return (pos[0] + it.scale * (x0 + x1) / 2, pos[1] + it.scale * y0 - 6,
            it.scale * (x1 - x0) * 0.9 / FOCUS_LINE_W)


def make_pages():
    pages = []
    # A: Threads of Time list
    rows = [Item("PaCharacterIndex_00", (0, 0), size=(960, 184), char=i if i < len(CHARACTERS) else None,
                 texts={"T_Title_00": char_name(i)}, detail=min(i, len(DETAILS) - 1)) for i in range(LIST_ROWS)]
    pages.append(Page("list", "Row", "A   List (Threads of Time style)", rows, kind="scroll"))
    # B: equipment-style slots, 3 x 2 (slot shapes of the Equipment page's first two rows)
    gx, gy = (-1500, -1140, -780), (290, -350)        # slots are 220 x 540; names under each row
    patterns = (0, 1, 2, 5, 6, 7)
    slots = []
    for i in range(6):
        x, y = gx[i % 3], gy[i // 3]
        slots.append(Item("PaEquipmentBtn_00", (x, y), size=(848, 608), anims=[("ItemPattern", patterns[i])],
                          char=i if i < len(CHARACTERS) else None, label=char_name(i), label_pos=(x, y - 312),
                          label_w=340, dot=(x, y + 205), detail=min(i, len(DETAILS) - 1)))
    pages.append(Page("grid", "Slot", "B   Slots (Equipment style)", slots, columns=3))
    # C: save-file cards (the name where the card has its place name, the diamond on
    # the emblem area at its right end)
    cards = []
    for i in range(3):
        pos, scale = fit("PaDataFileBtn_00", (-1150, 310 - 300 * i), (1180, 240))
        x, y = pos
        cards.append(Item("PaDataFileBtn_00", pos, scale=scale, size=(1806, 360),
                          char=i if i < len(CHARACTERS) else None, label=char_name(i),
                          label_pos=(x + 150 * scale, y + 36 * scale), label_size=56, label_w=600,
                          dot=(x + 900 * scale, y),
                          detail=min(i, len(DETAILS) - 1)))
    pages.append(Page("cards", "Card", "C   Cards (save file style)", cards))
    # D: plain buttons
    btns = []
    for i in range(5):
        x, y = -1340, 420 - 200 * i
        btns.append(Item("PaCharacterBtn_00", (x, y), size=(720, 180), char=i if i < len(CHARACTERS) else None,
                         texts={"T_Btn_00": char_name(i)}, dot=(x - 440, y), detail=min(i, len(DETAILS) - 1)))
    pages.append(Page("buttons", "Btn", "D   Buttons", btns))
    # catalogue: every button type, 2 x 6 cells
    cat = [("PaCommonBtnThin_00", {"T_Btn_00": "Text"}, ()), ("PaCommonBtnThinAction_00", {}, ()),
           ("PaOptionBtn_00", {}, ()), ("PaOptionBtnL_00", {"T_BtnON_00": "On", "T_BtnOFF_00": "Off"}, ()),
           ("PaMapFloorTabBtn_00", {}, ()), ("PaChallengeIndex_00", {}, ()),
           ("PaMessageBtn_00", {}, ()), ("PaMessageLogChoiceBtn_00", {}, ()),
           ("PaEquipmentBtn_00", {}, (("ItemPattern", 25),)), ("PaItemMenuBtn_00", {}, ()),
           ("PaCollectHeartBtn_00", {}, ()), ("PaOcarinaMelodyTypeBtn_00", {}, ())]
    items = []
    for i, (part, texts, anims) in enumerate(cat):
        x, y = (-1400, -520)[i % 2], 505 - 232 * (i // 2)
        pos, scale = fit(part, (x, y + 14), (800, 150))
        items.append(Item(part, pos, scale=scale, size=(720, 180), texts=texts, anims=anims, label=part,
                          label_pos=(x, y - 92), label_size=30))
    pages.append(Page("catalogue", "Cat", "E   Catalogue: buttons", items, columns=2))
    # catalogue: panels and extras (nothing to select), 2 x 5 cells
    cat = ["PaImportantInfo_00", "PaShortCutSortWindow_00", "PaMenuMessage_00", "PaActorInfo_00",
           "PaTutorialGuide_00", "PaShopBuyInfo_00", "PaSlideListSeparator_00", "PaSortPageIndicator_00",
           "PaABtn_00", "PaScrollBar_00"]
    items = []
    for i, part in enumerate(cat):
        x, y = (-1400, -520)[i % 2], 470 - 270 * (i // 2)
        pos, scale = fit(part, (x, y + 18), (800, 190), max_scale=2.5 if part == "PaSortPageIndicator_00" else 1.0)
        items.append(Item(part, pos, scale=scale, size=(720, 180), label=part, label_pos=(x, y - 112),
                          label_size=30, focus=False))
    pages.append(Page("panels", "Pan", "F   Catalogue: panels and extras", items, kind="static"))
    for k, p in enumerate(pages):
        p.index = k
    return pages


HINT = "D-pad: move      A: choose      X / Y: next / previous layout      R: Key Items"


# ---------------------------------------------------------------- the screen layout
class Panes:
    """New panes for the screen layout (build.page_layout()), collected as one block for
    Editor.insert under N_All_00, with the messages of the text panes. Every pane is a copy
    of one the page layout already has: N_Info_00 (null), T_Name_00 / T_Caption_00 (text),
    Pa_Parts_00 (part), P_Element_00 (the ornament line)."""

    def __init__(self, ed):
        self.ed, self.block, self.msgs = ed, [], {}
        assert ed.pane("N_Info_00").visible and ed.pane("Pa_Parts_00").visible
        assert ed.pane("P_Element_00").size == (FOCUS_LINE_W, 64.0)

    def add(self, np_):
        self.block.append(np_)
        return len(self.block) - 1

    def null(self, name, parent, pos=(0, 0), visible=True):
        n = self.ed.clone("N_Info_00", name, parent)
        n.node.set("<2f", 0x1C, *pos)
        n.node.set("<I", 0x38, (n.node.get("<I", 0x38)[0] & ~1) | int(visible))
        return self.add(n)

    def text(self, name, parent, pos, size, font_size, message, big=False, h_align=1, v_align=1, visible=True):
        n = self.ed.clone("T_Name_00" if big else "T_Caption_00", name, parent)
        n.node.set("<2f", 0x1C, *pos)
        n.node.set("<2f", 0x2C, *size)
        if not visible:
            n.node.set("<I", 0x38, n.node.get("<I", 0x38)[0] & ~1)
        uiedit.set_text(n, ref="*", font_size=font_size, h_align=h_align, v_align=v_align)
        self.msgs[name] = message
        return self.add(n)

    def part(self, name, parent, part_layout, pos, scale=1.0, size=(200, 200), anims=()):
        p = self.ed.new_part("Pa_Parts_00", name, part_layout, parent, anims=anims, size=size)
        p.node.set("<2f", 0x1C, *pos)
        p.node.set("<2f", 0x10, *(scale if isinstance(scale, tuple) else (scale, scale)))
        assert p.node.get("<I", 0x38)[0] & 1
        return self.add(p)

    def line(self, name, parent, pos, x_scale, solid=False):
        """The game's ornament line; solid = at full alpha (the focus lines)."""
        f = self.ed.clone("P_Element_00", name, parent)
        if solid:
            f.node.set("<I", 0x24, 0)                              # alpha table entry 0 = 1.0
        f.node.set("<2f", 0x1C, *pos)
        f.node.set("<2f", 0x10, x_scale, 1.0)
        return self.add(f)

    def insert(self):
        self.ed.insert("N_All_00", self.block)
        self.ed.pane("Pa_Parts_00").visible = False     # Key Items' own guide panel (hidden by GuideIn anyway)


def make_layout(pages):
    """CharacterMenu_00.buil and its messages {label: text}; fills in the pages' pane names."""
    lay = build.page_layout()
    ed = uiedit.Editor(lay)
    ed.validate()
    P = Panes(ed)
    msgs, null, text, add = P.msgs, P.null, P.text, P.add

    def part(name, parent, it, part_layout=None, pos=None, scale=None, size=None, anims=None):
        return P.part(name, parent, part_layout or it.part, pos or it.pos, it.scale if scale is None else scale,
                      size or it.size, it.anims if anims is None else anims)

    def focus(page, parent):
        """The focus line of a page: the game's ornament line at full alpha, moved under
        the focused item by the page's Cur_ animation. It does not depend on the parts
        (their own selected look is driven through their Select animation)."""
        it = page.items[page.focusable[0]]
        page.focus_pane = "P_Focus_%s" % page.tag
        return P.line(page.focus_pane, parent, it.line[:2], it.line[2], solid=True)

    sandbox = null("N_Sandbox_00", None)
    # shared detail block: name, the game's decoration line, caption
    detail_all = null("N_DetailAll_00", sandbox, DETAIL_POS)
    P.line("P_DetailLine_00", detail_all, (0, 0), (DETAIL_W - 60) / FOCUS_LINE_W)
    for j, (name, caption) in enumerate(DETAILS):
        d = null("N_Detail_%02d" % j, detail_all, visible=False)
        text("T_DetailName_%02d" % j, d, (0, 96), (DETAIL_W, 130), 84.0, name, big=True)
        text("T_DetailText_%02d" % j, d, (0, -150), (DETAIL_W, 220), 46.0, caption, v_align=0)

    for k, page in enumerate(pages):
        pg = null("N_Page_%02d" % k, sandbox, visible=k == 0)
        text("T_Title_%02d" % k, pg, TITLE_POS, (1400, 70), 46.0, page.title, h_align=0)
        if page.kind == "scroll":
            bar = Item("PaCharacterScrollBar_00", (BAR_X, LIST_Y), size=(60, WIN_H))
            page.bar_pane = "Pa_ListBar_00"
            part(page.bar_pane, pg, bar)
            # scissor: the rows' cursor glow reaches 180 left of the bar and 50 above / below a row
            sx0, sx1 = LIST_X - 720, LIST_X + 440
            sc = ed.clone("N_Info_00", "S_List_00", pg)
            sc.node.set("<2f", 0x1C, (sx0 + sx1) / 2, LIST_Y)
            sc.node.set("<2f", 0x2C, sx1 - sx0, WIN_H + 2 * 44)
            sc.node.set("<I", 0x38, uiedit.SCISSOR_FLAGS)
            sci = add(sc)
            page.scroll_pane = "N_ListScroll_00"
            scroll = null(page.scroll_pane, sci, (LIST_X - (sx0 + sx1) / 2, 0))
            for r, it in enumerate(page.items):
                it.pane = "Pa_Row_%02d" % r
                it.dot_pane = it.pane                              # the row has its own diamond
                it.row_y = (LIST_VISIBLE - 1) / 2 * ROW_PITCH - r * ROW_PITCH
                it.line = focus_line(it, (0, it.row_y))            # inside the scrolling pane
                part(it.pane, scroll, it, pos=(0, it.row_y))
            focus(page, scroll)
        else:
            for i, it in enumerate(page.items):
                it.pane = "Pa_%s_%02d" % (page.tag, i)
                part(it.pane, pg, it)
                if it.dot is not None:
                    it.dot_pane = "Pa_%sDot_%02d" % (page.tag, i)
                    part(it.dot_pane, pg, it, part_layout="PaCharacterDot_00", pos=it.dot, scale=1.0,
                         size=(120, 120), anims=[])
                if it.label:
                    it.label_pane = "T_%s_%02d" % (page.tag, i)
                    text(it.label_pane, pg, it.label_pos, (it.label_w, 60 if it.label_size < 50 else 90),
                         float(it.label_size), it.label, big=it.label_size >= 50)
                it.line = focus_line(it, it.pos)
            if page.focusable:
                focus(page, pg)
        for it in page.items:
            for tp, value in it.texts.items():
                msgs["%s-%s" % (it.pane, tp)] = value
    text("T_Hint_00", sandbox, HINT_POS, (2100, 60), 38.0, HINT)
    P.insert()

    make_animations(ed, pages)
    uiedit.rename(lay, LAYOUT)
    out = layout.build(lay)
    check = layout.Layout(out)                  # what the game will read
    uiedit.Editor(check).validate()
    assert layout.build(check) == out
    return out, msgs


def onehot(n, i):
    return [1.0 if j == i else 0.0 for j in range(n)]


def make_animations(ed, pages):
    """The state animations of the module docstring. Each is authored with one key per
    integer frame (uiedit.state_curve), so stopping it at frame i gives exactly state i."""
    def merge(acc, drive):
        for a, d in zip(acc, drive):
            a.extend(d)

    # Page: container visibility, detail block on the character pages
    n = len(pages)
    curves = [(ed.key("N_Page_%02d" % k, VISIBLE), uiedit.state_curve(onehot(n, k))) for k in range(n)]
    curves.append((ed.key("N_DetailAll_00", VISIBLE),
                   uiedit.state_curve([1.0 if p.has_detail else 0.0 for p in pages])))
    ed.add_anim("Page", frames=n - 1, curves=curves)

    # Chosen: every diamond (rows and markers) of every page
    nc = len(CHARACTERS)
    acc = ([], [], [])
    for page in pages:
        for it in page.items:
            if it.dot_pane and it.char is not None:
                merge(acc, ed.drive(it.dot_pane, "IsChosen", values=onehot(nc, it.char)))
    ed.add_anim("Chosen", frames=max(nc - 1, 1), consts=acc[0], curves=acc[1], events=acc[2])

    # Cur_<p>: the focused item
    for page in pages:
        f = page.focusable
        if not f:
            continue
        frames = len(page.items)
        acc = ([], [], [])
        for i, it in enumerate(page.items):
            if has_anim(it.part, "Select"):
                merge(acc, ed.drive(it.pane, "Select", values=onehot(frames, i)))
        curves = acc[1]
        if page.has_detail:
            for j in range(len(DETAILS)):
                curves.append((ed.key("N_Detail_%02d" % j, VISIBLE),
                               uiedit.state_curve([1.0 if it.detail == j else 0.0 for it in page.items])))
        lines = [it.line for it in page.items]
        curves.append((ed.key(page.focus_pane, TRANSLATE),
                       [uiedit.state_curve([ln[0] for ln in lines]), uiedit.state_curve([ln[1] for ln in lines])]))
        curves.append((ed.key(page.focus_pane, SCALE),
                       [uiedit.state_curve([ln[2] for ln in lines]), uiedit.state_curve([1.0 for _ in lines])]))
        page.cursor_anim = "Cur_%s" % page.key
        ed.add_anim(page.cursor_anim, frames=max(frames - 1, 1), consts=acc[0], curves=curves, events=acc[2])

    # Scroll: list position. The scissor clips the rows; rows outside the window are hidden
    # as well, so the list is still right if the scissor did not clip.
    page = next(p for p in pages if p.kind == "scroll")
    steps = range(SCROLL_STEPS + 1)

    def in_window(r, s):
        return s <= r < s + LIST_VISIBLE

    x0 = ed.pane(page.scroll_pane).pos[0]
    acc = ([], [], [])
    merge(acc, ed.drive(page.bar_pane, "Pos", values=[s / SCROLL_STEPS for s in steps]))
    acc[1].append((ed.key(page.scroll_pane, TRANSLATE),
                   [uiedit.state_curve([x0 for _ in steps]), uiedit.state_curve([s * ROW_PITCH for s in steps])]))
    for r, it in enumerate(page.items):
        acc[1].append((ed.key(it.pane, VISIBLE), uiedit.state_curve([float(in_window(r, s)) for s in steps])))
    ed.add_anim("Scroll", frames=SCROLL_STEPS, consts=acc[0], curves=acc[1], events=acc[2])

    # ScrollDn_<s> / ScrollUp_<s>: the move from the neighbouring position to s, played
    T = SCROLL_FRAMES
    page.tweens = {}
    for s in steps:
        for name, frm in (("ScrollDn_%d" % s, s - 1), ("ScrollUp_%d" % s, s + 1)):
            if not 0 <= frm <= SCROLL_STEPS:
                continue
            slot = ed.part_anim_slot("Pos")
            consts = [(ed.key(page.bar_pane, uiedit.PART_ENABLE, part_anim=slot), 1.0)]
            curves = [(ed.key(page.bar_pane, uiedit.PART_FRAME, part_anim=slot),
                       uiedit.ease_curve(0, frm / SCROLL_STEPS, T, s / SCROLL_STEPS)),
                      (ed.key(page.scroll_pane, TRANSLATE),
                       [uiedit.hermite([(0, x0, 0.0), (T, x0, 0.0)]),
                        uiedit.ease_curve(0, frm * ROW_PITCH, T, s * ROW_PITCH)])]
            for r, it in enumerate(page.items):
                now, before = in_window(r, s), in_window(r, frm)
                if now:
                    consts.append((ed.key(it.pane, VISIBLE), 1.0))
                elif before:     # the row that leaves: shown during the move, hidden at its end
                    curves.append((ed.key(it.pane, VISIBLE), uiedit.hermite([(0, 1.0, 0.0), (T, 1.0, 0.0), (T, 0.0, 0.0)])))
                else:
                    consts.append((ed.key(it.pane, VISIBLE), 0.0))
            ed.add_anim(name, frames=T, consts=consts, curves=curves, events=[("Pos", page.bar_pane)])
            page.tweens[(frm, s)] = name


# ---------------------------------------------------------------- graph
def t_int_sel(g, query, cases, default):
    """Element_S32Selector on an Int query output (template: MinusMenu node 8): child by
    value, `default` otherwise."""
    n = build.vanilla("MinusMenu", 8, "")
    assert n["Node Type"] == "Element_S32Selector" and n["Queries"] == [7]
    assert n["Parameters"]["Inputs"]["Int"][0]["Node Index"] == 7 and n["Plugs"]["Int"][0]["Node Index"] == 7
    n["Queries"] = [query]
    n["Parameters"]["Inputs"]["Int"][0]["Node Index"] = query
    n["Plugs"]["Int"][0]["Node Index"] = query
    n["Plugs"]["Child"] = [{"Node Index": c, "Name": "", "Condition": v} for v, c in cases] + \
                          [{"Node Index": default, "Name": "", "Is Default": True}]
    return n


def page_graph(picker=False):
    """What every graph of ours for the page starts with: (graph, entry node, the button
    query, the chain every button loop ends with (R / ZR to Key Items, B / - close, exactly
    as the normal page), go(target) = the node that jumps to a root node).
    picker: ZR is not in that chain (it turns the sheet there); + is, and opens the + menu
    the way Key Items does: content screen closed at once, the 3D character hidden, command
    PouchMenu (the - menu's graph already routes it for our page, like Next and Main)."""
    B = build
    S = SCREEN
    base = B.Graph(os.path.join(B.SEQ, "ImportantMenu.module.ainb"))
    g = B.Graph(like=base, filename="CharacterMenu.module")
    g.d["Blackboard ID"] = 69420
    root = g.add(B.t_sequential([], root=True))                  # 0: entry
    mask = g.add(B.vanilla("ImportantMenu", 5, "SeqQueryControllerGetMask"))
    chk = g.add(B.vanilla("ImportantMenu", 4, "SeqQueryControllerTrigMaskChecker"))
    g.nodes[mask]["Plugs"]["Generic"][0]["Node Index"] = chk
    g.nodes[chk]["Queries"] = [mask]
    g.nodes[chk]["Parameters"]["Inputs"]["Pointer"][0]["Node Index"] = mask
    used = ["B", "Minus", "R", "ZR", "A", "X", "Y", "Left", "Right", "Up", "Down"]
    if picker:
        used = ["B", "Minus", "R", "Plus", "ZL", "ZR", "A", "Left", "Right", "Up", "Down"]
    for o in g.nodes[chk]["Parameters"]["Outputs"]["Bool"]:
        o["Is Output"] = o["Name"] in ["Trig" + b for b in used]
    closable = g.add(B.vanilla("ImportantMenu", 31, "SeqQueryIsClosableMinusMenuByBbutton"))
    dummy = g.add(B.vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))

    # leaving: exactly as the normal page
    end_main = g.add(B.t_end(g.add(B.t_state_end("Main"))))
    st_next = g.add(B.t_state_end("Next"))
    overlap = g.add(B.t_simultaneous([g.add(B.t_sound("mc_TabToRight")), g.add(B.t_stop_anim(S, "InFromChallenge")),
                                      g.add(B.t_play_anim(S, "InChallenge")),
                                      g.add(B.t_set_flag(B.PAGE_VIEW_FLAG, False))], then=st_next))
    end_next = g.add(B.t_end(overlap))
    if picker:
        pouch = g.add(B.t_simultaneous([g.add(B.t_close_unload_screen(S)), g.add(B.t_display(4)),
                                        g.add(B.t_set_flag(B.PAGE_VIEW_FLAG, False))],
                                       then=g.add(B.t_state_end("PouchMenu"))))
        zr2 = g.add(B.t_bool_sel(chk, B.TRIG["Plus"], g.add(B.t_end(pouch)), dummy))
    else:
        zr2 = g.add(B.t_bool_sel(chk, B.TRIG["ZR"], end_next, dummy))
    r2 = g.add(B.t_bool_sel(chk, B.TRIG["R"], end_next, zr2))
    can_close = g.add(B.t_bool_sel(closable, 0, end_main, r2))
    minus2 = g.add(B.t_bool_sel(chk, B.TRIG["Minus"], can_close, r2))
    common = g.add(B.t_bool_sel(chk, B.TRIG["B"], can_close, minus2))

    g.d["Modules"] = [m for m in base.d["Modules"] if m["Path"] == "OpenAppMenuOverlay.module.ainb"]
    assert len(g.d["Modules"]) == 1
    g.d["Modules"].append({"Path": "WaitEquip.module.ainb", "Category": "Sequence", "Instance Count": 1})

    ends = {}

    def go(target):
        if target not in ends:
            ends[target] = g.add(B.t_end(target))
        return ends[target]

    return g, root, chk, common, go


def finish_graph(g, root, init, also=()):
    """The entry (as the normal page: open the screen, its in-animation, header, scene,
    the page view flag, show Link; then the nodes `also`), then `init`."""
    B = build
    S = SCREEN
    enter_page = g.add(B.t_simultaneous(
        [g.add(B.t_open_screen(S)), g.add(B.t_stop_anim(S, "InChallenge")),
         g.add(B.t_stop_anim(S, "DetailIn")), g.add(B.t_stop_anim(S, "GuideIn")),
         g.add(B.t_stop_anim(S, "DemoMessageIn")), g.add(B.t_play_anim(S, "InFromChallenge")),
         g.add(B.t_overlay(B.PAGE_PRESET)), g.add(B.t_env("Pouch")),
         g.add(B.t_set_flag(B.PAGE_VIEW_FLAG, True))], root=False))
    g.nodes[root]["Plugs"] = B._plugs([enter_page, g.add(B.t_display(0))] + list(also), then=init)
    g.d["Commands"] = [{"Name": "Root", "GUID": B.guid("CharacterMenu.module:Root"), "Root Node Index": root}]


def flag_query(g):
    """Query node for the saved character number (an Int output for t_int_sel)."""
    q = g.add(build.vanilla("ResetGimmickImpl", 8, "SeqQueryGetNonStructGameDataInt",
                            GameDataFullName=build.CHARACTER_FLAG))
    assert g.nodes[q]["Plugs"]["Generic"] == [{"Node Index": 6, "Name": "Value"}]
    return q


def sandbox_graph(mod, pages):
    """Sequence/CharacterMenu.module.ainb for the sample pages (see module docstring)."""
    B = build
    S = SCREEN
    g, root, chk, common, go = page_graph()

    # states
    loops, enters = {}, {}
    for page in pages:
        for st in page.states() or [()]:
            loops[(page.index, st)] = g.add(B.t_simultaneous([], root=True))

    def apply_state(page, st):
        """One-shot nodes that show state `st` (cursor only; the scroll position is
        set by the page start or by the move that changed it)."""
        return [g.add(B.t_stop_anim(S, page.cursor_anim, float(st[0])))] if st else []

    def enter(page, st, sound=True):
        """Root sequence: show the state, let a frame pass, then read buttons. The frame
        matters: a trigger stays set for the whole frame, so going straight back to
        the button loop would run the same move again, for ever (the first sandbox)."""
        key = (page.index, st, sound)
        if key not in enters:
            nodes = apply_state(page, st) + ([g.add(B.t_sound("mc_CursorMove"))] if sound and st else [])
            first = g.add(B.t_simultaneous(nodes, root=False)) if nodes else \
                g.add(B.t_wait_frames(1))
            enters[key] = g.add(B.t_sequential([first, g.add(B.t_wait_frames(1))], then=loops[(page.index, st)],
                                               root=True, same_frame=False))
        return enters[key]

    def first_state(page):
        return (page.states() or [()])[0]

    def page_start(page, sound):
        nodes = [g.add(B.t_stop_anim(S, "Page", float(page.index)))]
        if page.kind == "scroll":
            nodes.append(g.add(B.t_stop_anim(S, "Scroll", 0.0)))
        if sound:
            nodes.append(g.add(B.t_sound(sound)))
        return g.add(B.t_sequential([g.add(B.t_simultaneous(nodes, root=False))],
                                    then=enter(page, first_state(page), sound=False), root=True))

    n_pages = len(pages)
    next_page = {p.index: page_start(p, "mc_TabToRight") for p in pages}    # reached with X
    prev_page = {p.index: page_start(p, "mc_TabToLeft") for p in pages}     # reached with Y
    for page in pages:
        for st in page.states() or [()]:
            chain = common
            it = page.items[st[0]] if st else None
            if it is not None and it.char is not None:
                # A: mark the choice at once, then swap the menu's Link (many frames)
                pre = g.add(B.t_simultaneous([g.add(B.t_sound("mc_NormalDecide")),
                                              g.add(B.t_stop_anim(S, "Chosen", float(it.char)))], root=False))
                swap = g.add(B.t_sequential([pre] + B.character_swap_steps(g, it.char),
                                            then=enter(page, st, sound=False), root=True, same_frame=False))
                chain = g.add(B.t_bool_sel(chk, B.TRIG["A"], go(swap), chain))
            chain = g.add(B.t_bool_sel(chk, B.TRIG["Y"], go(prev_page[(page.index - 1) % n_pages]), chain))
            chain = g.add(B.t_bool_sel(chk, B.TRIG["X"], go(next_page[(page.index + 1) % n_pages]), chain))
            if st:
                for button, to in sorted(page.moves(st).items()):
                    target = enter(page, to)
                    if page.kind == "scroll" and to[1] != st[1]:
                        target = g.add(B.t_sequential([g.add(B.t_play_anim(S, page.tweens[(st[1], to[1])]))],
                                                      then=target, root=True))
                    chain = g.add(B.t_bool_sel(chk, B.TRIG[button], go(target), chain))
            g.nodes[loops[(page.index, st)]]["Plugs"] = B._plugs([chain])

    # entry: as the normal page; then, by the saved character, its diamond and page A with
    # the cursor on its row. The selector sits under a root node of its own with
    # SeqOneShotEnd children, the same shape as the button loops.
    lst = pages[0]
    assert lst.kind == "scroll" and [it.char for it in lst.items[:len(CHARACTERS)]] == list(range(len(CHARACTERS)))
    starts = []
    for c in range(len(CHARACTERS)):
        show = g.add(B.t_simultaneous([g.add(B.t_stop_anim(S, "Chosen", float(c))),
                                       g.add(B.t_stop_anim(S, "Page", 0.0)),
                                       g.add(B.t_stop_anim(S, "Scroll", 0.0))], root=False))
        starts.append(g.add(B.t_sequential([show], then=enter(lst, (c, 0), sound=False), root=True)))
    query = flag_query(g)
    sel = g.add(t_int_sel(g, query, [(c, go(starts[c])) for c in range(1, len(CHARACTERS))], go(starts[0])))
    g.nodes[query]["Plugs"]["Generic"][0]["Node Index"] = sel
    init = g.add(B.t_simultaneous([sel], root=True))
    finish_graph(g, root, init)
    assert len(loops) == sum(len(p.states() or [()]) for p in pages)
    check_graph(g, sorted(loops.values()), ("Page", "Chosen", "Cur", "Scroll", "ScrollDn", "ScrollUp"))
    return write_graph(g, mod)


def write_graph(g, mod):
    out = g.build()
    build.write(mod, "Sequence/CharacterMenu.module.ainb", out, len(out), build.SIZES)
    return g


# ---------------------------------------------------------------- the character picker
# The whole ring of characters (npc_characters.ring(): Link, Retro Link, every humanoid NPC)
# on sheets of 18 square slots, 6 x 3, in the left half of the screen; the focused one's
# name and caption stand right of the character. ZL / ZR turn the sheet (R still goes to
# Key Items, + opens the + menu). Over the grid: "Character Select" at its left end, and at
# its right end "page x of y" between the ZL and ZR glyphs. Those are text in the game's
# button glyph font (External_00 = Font2/Scalable/External.*.bfotf) at the game's own
# codepoints (StaticMsg/GuideKeyIcon.msbt), exactly as the - header's L / R are, so a mod
# that replaces that font changes ours with it.
#
# The slot is the inventory's (PaItemMenuBtn_00; the Equipment page's PaEquipmentBtn_00 is
# built the same way and has the same cursor animations), as our own part PaCharacterSlot_00
# with one more animation, IsChosen: the shadow background (P_Base_00) gives way to the
# orange one (P_Equip_00), as State does for an equipped item, but without the item icon
# State would show. ItemPattern, set as each slot's initial frame, picks the shape
# (SLOT_SHAPES). The Equipment page's "square" (ItemPattern 8 there, MenuBtnBase_26) is a
# trapezium, narrower at the bottom (snaildev, 2026-11-10).
#
# A layout cannot change a text, and nothing but our graph drives this screen, so every
# character has text panes of its own, and the state is a handful of independent animations.
# Stopped at a frame (a state):
#     Sheet   frame = sheet       which N_Sheet_ss shows (title, names under the slots, detail
#                                 texts); hides the slots the last sheet lacks
#     Det     frame = slot        which detail texts of a sheet show (T_CellName_ccc /
#                                 T_CellText_ccc: visible when their slot is focused; their
#                                 sheet's container does the rest)
#     Cur     frame = slot        the cursor at once, no animation: the slots' Select at its
#                                 end on that slot, at its start on the others
#     Mark    frame = slot        the orange slot (IsChosen); frame 18 = none
# Played (the part's own animation run through, as the game's code does on its screens):
#     In_jj / Out_jj / Dec_jj     Select (8 frames) / UnSelect (14) / Decide (16) of slot jj
#     Pulse                       CursorLoop of all slots, looping: the idle pulse (period 40)
# A screen animation applies the part animation it drives only while it is itself applied
# (0x802f2c20), and a later request is applied later in the frame, so the last request wins
# while both run. One that outlasts a later, shorter one would win in the end, so a move
# stops the target's Out before it plays its In, and everything that places the cursor at
# once first stops every Out (at its end) and every In (at its start): the node `settle`.
PICK_COLS, PICK_ROWS = 6, 3
PER_SHEET = PICK_COLS * PICK_ROWS
PICK_PAD = 96                                  # the grid's distance from the left edge and from the centre
PICK_LEFT, PICK_RIGHT = -1920 + PICK_PAD, -PICK_PAD
PICK_PITCH_X = (PICK_RIGHT - PICK_LEFT) / PICK_COLS
PICK_PITCH_Y = 400
PICK_TOP = 530                                 # top edge of the first row
BOX = 264                                      # the square a slot is given (names sit under it)
SLOT_SRC, SLOT = "PaItemMenuBtn_00", "PaCharacterSlot_00"
# ItemPattern frame -> what the slot's background draws at scale 1 (texture alpha over 0.6,
# tools\bntx.py): the inventory's three squarish shapes, all near 260, so no scaling.
#   9 (to 22)  MenuBtnBase_13  259 x 263, sides straight (widths 223 .. 233 top to bottom):
#              snaildev's choice (2026-11-10, shape test with one shape per row: "top row")
#   4          MenuBtnBase_08  259 x 263, top and bottom edges cut short at one end
#   23         MenuBtnBase_11  278 x 263, a little wider towards the bottom (229 .. 275)
SLOT_SHAPES = {9: (259, 263), 4: (259, 263), 23: (278, 263)}
SLOT_PATTERNS = [9] * PER_SHEET
SLOT_SCALE = 1.0
GRID_LEFT = PICK_LEFT + (PICK_PITCH_X - SLOT_SHAPES[9][0]) / 2      # where the first / last column is drawn
GRID_RIGHT = PICK_RIGHT - (PICK_PITCH_X - SLOT_SHAPES[9][0]) / 2
SLOT_ANIMS = {"In": ("Select", 8), "Out": ("UnSelect", 14), "Dec": ("Decide", 16)}
PULSE = ("CursorLoop", 1000, 40)               # part animation, its length, its period
LABEL_W, LABEL_H, LABEL_SIZE = 280, 80, 30.0
PICK_HEAD_Y = 640                              # the line over the grid
PICK_TITLE, PICK_TITLE_W = "Character Select", 900
KEY_SRC, KEY = "PaGuideKey_00", "PaCharacterKey_00"
KEY_SCALE = 0.7                                # the part's glyph is 102 high
KEY_W = 102 * KEY_SCALE
PAGE_W, PAGE_GAP = 300, 14                     # "page x of y" between the two glyphs
KEY_X = {"ZR": GRID_RIGHT - KEY_W / 2, "ZL": GRID_RIGHT - 1.5 * KEY_W - 2 * PAGE_GAP - PAGE_W}
PAGE_X = GRID_RIGHT - KEY_W - PAGE_GAP - PAGE_W / 2
PICK_DETAIL_POS = (1380, 60)                   # right of the character (x = 640)
PICK_DETAIL_W = 760
PICK_ANIMS = ("Sheet", "Det", "Cur", "Mark", "Pulse", "In", "Out", "Dec")
NO_MARK = PER_SHEET                            # Mark's frame for "no slot of this sheet"


def part_slot():
    """The equipment slot with the animation IsChosen (see the section comment)."""
    lay = vanilla_layout(SLOT_SRC)
    ed = uiedit.Editor(lay)
    ed.validate()
    assert ed.pane("P_Base_00").visible and not ed.pane("P_Equip_00").visible
    # the item count (text filled in by the game's code): never shown here
    assert ed.pane("N_Num_00").visible and ed.pane("T_ItemNum_00").parent == ed.names().index("N_Num_00")
    ed.pane("N_Num_00").visible = False
    for name, frames in list(SLOT_ANIMS.values()) + [PULSE[:2], ("ItemPattern", 35), ("StockNum", 1)]:
        assert lay.root.ptr[0x140].get(name).get("<I", 0x28)[0] == frames, name
    ed.add_anim("IsChosen", frames=1, template="StockNum",
                curves=[(ed.key("P_Base_00", VISIBLE), uiedit.state_curve([1.0, 0.0])),
                        (ed.key("P_Equip_00", VISIBLE), uiedit.state_curve([0.0, 1.0]))])
    uiedit.rename(lay, SLOT)
    return lay


def key_glyph(button, cache={}):
    """The character the game's own key guides use for a button (StaticMsg/GuideKeyIcon.msbt:
    private-use codepoints of the glyph font, U+E046 for ZL, U+E047 for ZR, ...)."""
    if not cache:
        files = nxfmt.sarc_files(nxfmt.read(os.path.join(nxfmt.ROMFS, MALS[0])))
        m = msbt.Msbt(files["StaticMsg/GuideKeyIcon.msbt"])
        cache.update((label, m.get(label)) for label in m.labels)
    glyph = cache[button]
    assert len(glyph) == 1 and 0xE000 <= ord(glyph) <= 0xF8FF, (button, glyph)
    return glyph


def part_key():
    """The game's key guide (PaGuideKey_00: a glyph in the font External_00 with a text to
    its left, both filled in by code) as a glyph alone that takes its text from the screen's
    messages."""
    lay = vanilla_layout(KEY_SRC)
    ed = uiedit.Editor(lay)
    ed.validate()
    texts = lay.root.ptr[0x80]
    icon, guide = texts.get("T_Icon_00"), texts.get("T_Guide_00")
    assert icon.ptr[0x0].text == "External_00" and icon.get("<f", 8)[0] == 102.0
    assert icon.ptr[0x18].text == "@T_Icon_00" and guide.ptr[0x18].text == "@T_Guide_00"
    uiedit.set_text(icon, ref="*")
    assert ed.pane("T_Icon_00").pos == (0.0, 4.0) and ed.pane("T_Guide_00").visible
    ed.pane("T_Guide_00").visible = False
    uiedit.rename(lay, KEY)
    return lay


PICK_PARTS = {SLOT: part_slot, KEY: part_key}


def two_lines(label, limit=15):
    """A name for the space under a slot: longer than `limit` letters, it breaks at the
    space or underscore nearest its middle."""
    cuts = [i for i, ch in enumerate(label) if ch in " _"]
    if len(label) <= limit or not cuts:
        return label
    i = min(cuts, key=lambda i: abs(i - len(label) / 2))
    return label[:i] + "\n" + label[i + 1:]


def short_actor(name, limit=24):
    for prefix in ("Dm_", "Npc_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name[:limit]


class Picker:
    """The character list and where things sit. Character c is on sheet c // 18, slot c % 18
    (slots run left to right, top to bottom)."""

    def __init__(self):
        self.names = npc.ring()
        self.n = len(self.names)
        self.sheets = -(-self.n // PER_SHEET)
        n_main = len(build.CHARACTERS)
        assert self.names[:n_main] == CHARACTERS and n_main <= len(DETAILS)
        self.captions = [DETAILS[c][1] for c in range(n_main)]
        for c, ch in enumerate(npc.characters(), n_main):
            lines = ["Character number %d" % c, "Model: %s" % ch["stem"].split(".")[1][len("Npc_"):]]
            actors = sorted(ch["actors"], key=lambda a: (len(a), a))
            if actors:
                lines.append("Used by: %s%s" % (short_actor(actors[0]),
                                               " (+%d)" % (len(actors) - 1) if len(actors) > 1 else ""))
            self.captions.append("\n".join(lines))
        assert len(self.captions) == self.n
        self.slot_pos, self.label_pos = [], []
        for j in range(PER_SHEET):
            x = PICK_LEFT + PICK_PITCH_X * (j % PICK_COLS + 0.5)
            y = PICK_TOP - BOX / 2 - PICK_PITCH_Y * (j // PICK_COLS)
            self.slot_pos.append((x, y))
            self.label_pos.append((x, y - BOX / 2 - 12 - LABEL_H / 2))

    def on_sheet(self, s):
        return range(s * PER_SHEET, min(self.n, (s + 1) * PER_SHEET))

    def page_text(self, s):
        return "page %d of %d" % (s + 1, self.sheets)

    def moves(self, c):
        """{button: character} for the D-pad: inside the sheet, no wrapping."""
        s, j = divmod(c, PER_SHEET)
        col, row = j % PICK_COLS, j // PICK_COLS
        out = {}
        if col > 0:
            out["Left"] = c - 1
        if col < PICK_COLS - 1 and c + 1 in self.on_sheet(s):
            out["Right"] = c + 1
        if row > 0:
            out["Up"] = c - PICK_COLS
        if row < PICK_ROWS - 1 and c + PICK_COLS in self.on_sheet(s):
            out["Down"] = c + PICK_COLS
        return out

    def mark(self, s, chosen):
        """Mark's frame while sheet s shows and `chosen` is the saved number."""
        return chosen % PER_SHEET if chosen in self.on_sheet(s) else NO_MARK

    def after(self, c, button, chosen):
        """The model the graph is checked against: (state, chosen number) after `button`
        in state c; state = a character, or the name of the command the page ends with."""
        s = c // PER_SHEET
        if button in ("Left", "Right", "Up", "Down"):
            return self.moves(c).get(button, c), chosen
        if button == "ZR":
            return (s + 1) % self.sheets * PER_SHEET, chosen
        if button == "ZL":
            return (s - 1) % self.sheets * PER_SHEET, chosen
        if button == "A":
            return c, c
        return {"R": "Next", "Plus": "PouchMenu", "B": "Main", "Minus": "Main"}.get(button, c), chosen


def sparse_state(values):
    """uiedit.state_curve without the keys inside runs of zeros: the same value at every
    integer frame (a segment between two zero keys with slope 0 is exactly 0 everywhere;
    other values keep a key per frame, so each is read at a key, never between two)."""
    values = [float(v) for v in values]
    last = len(values) - 1
    keep = [i for i, v in enumerate(values)
            if i in (0, last) or v != 0.0 or values[i - 1] != 0.0 or values[i + 1] != 0.0]
    cv = uiedit.hermite([(i, values[i], 0.0) for i in keep])
    assert all(uiedit.curve_value(cv, float(i)) == v for i, v in enumerate(values))
    return cv


def ramp(frames, end=1.0, wrap=0):
    """A straight line from 0 at frame 0 to `end` at the last frame: a part animation's
    normalised frame while the screen animation that drives it plays."""
    return uiedit.hermite([(0, 0.0, end / frames), (frames, end, end / frames)], wrap=wrap)


def name_size(name):
    """Font size of the big name in the detail block: 84 as on the samples, smaller when
    the name would not fit (about 0.55 of the size per letter)."""
    return float(min(84, int(PICK_DETAIL_W / (0.55 * max(len(name), 1)))))


def make_picker_layout(pk):
    """CharacterMenu_00.buil for the picker and its messages {label: text}."""
    lay = build.page_layout()
    ed = uiedit.Editor(lay)
    ed.validate()
    P = Panes(ed)
    top = P.null("N_Sandbox_00", None)
    detail = P.null("N_DetailAll_00", top, PICK_DETAIL_POS)
    P.line("P_DetailLine_00", detail, (0, 0), (PICK_DETAIL_W - 60) / FOCUS_LINE_W)
    # the slots, each in a container (the last sheet has fewer)
    for j in range(PER_SHEET):
        holder = P.null("N_Slot_%02d" % j, top)
        P.part("Pa_Slot_%02d" % j, holder, SLOT, pk.slot_pos[j], scale=SLOT_SCALE, size=(380, 492),
               anims=[("ItemPattern", SLOT_PATTERNS[j])])
    # the line over the grid
    P.text("T_Title_00", top, (GRID_LEFT + PICK_TITLE_W / 2, PICK_HEAD_Y), (PICK_TITLE_W, 70), 46.0, PICK_TITLE,
           h_align=0)
    for button, x in sorted(KEY_X.items()):
        pane = "Pa_Key%s_00" % button
        P.part(pane, top, KEY, (x, PICK_HEAD_Y), scale=KEY_SCALE, size=(102, 102))
        P.msgs["%s-T_Icon_00" % pane] = key_glyph(button)
    dx, dy = PICK_DETAIL_POS
    for s in range(pk.sheets):
        sheet = P.null("N_Sheet_%02d" % s, top, visible=s == 0)
        P.text("T_Sheet_%02d" % s, sheet, (PAGE_X, PICK_HEAD_Y), (PAGE_W, 70), 46.0, pk.page_text(s))
        for c in pk.on_sheet(s):
            name = pk.names[c]
            P.text("T_Slot_%03d" % c, sheet, pk.label_pos[c % PER_SHEET], (LABEL_W, LABEL_H), LABEL_SIZE,
                   two_lines(name), v_align=0)
            P.text("T_CellName_%03d" % c, sheet, (dx, dy + 96), (PICK_DETAIL_W, 130), name_size(name), name,
                   big=True, visible=False)
            P.text("T_CellText_%03d" % c, sheet, (dx, dy - 150), (PICK_DETAIL_W, 220), 42.0, pk.captions[c],
                   v_align=0, visible=False)
    P.insert()

    n, sheets = pk.n, pk.sheets
    state = uiedit.state_curve
    slots = ["Pa_Slot_%02d" % j for j in range(PER_SHEET)]

    def merge(acc, drive):
        for a, d in zip(acc, drive):
            a.extend(d)

    def add(name, frames, acc, **kw):
        ed.add_anim(name, frames=frames, consts=acc[0], curves=acc[1], events=acc[2], **kw)

    # Sheet
    curves = [(ed.key("N_Sheet_%02d" % s, VISIBLE), state(onehot(sheets, s))) for s in range(sheets)]
    for j in range(PER_SHEET):
        have = [float(s * PER_SHEET + j < n) for s in range(sheets)]
        if min(have) == 0.0:
            curves.append((ed.key("N_Slot_%02d" % j, VISIBLE), state(have)))
    ed.add_anim("Sheet", frames=max(sheets - 1, 1), curves=curves)
    # Det
    curves = []
    for c in range(n):
        for pane in ("T_CellName_%03d" % c, "T_CellText_%03d" % c):
            curves.append((ed.key(pane, VISIBLE), sparse_state(onehot(PER_SHEET, c % PER_SHEET))))
    ed.add_anim("Det", frames=PER_SHEET - 1, curves=curves)
    # Cur
    acc = ([], [], [])
    for j, pane in enumerate(slots):
        merge(acc, ed.drive(pane, "Select", values=onehot(PER_SHEET, j)))
    add("Cur", PER_SHEET - 1, acc)
    # Mark: frames 0 .. 18
    acc = ([], [], [])
    for j, pane in enumerate(slots):
        merge(acc, ed.drive(pane, "IsChosen", values=onehot(PER_SHEET + 1, j)))
    add("Mark", PER_SHEET, acc)
    # In / Out / Dec per slot
    for tag, (part_anim, frames) in SLOT_ANIMS.items():
        for j, pane in enumerate(slots):
            slot = ed.part_anim_slot(part_anim)
            ed.add_anim("%s_%02d" % (tag, j), frames=frames, events=[(part_anim, pane)],
                        consts=[(ed.key(pane, uiedit.PART_ENABLE, part_anim=slot), 1.0)],
                        curves=[(ed.key(pane, uiedit.PART_FRAME, part_anim=slot), ramp(frames))])
    # Pulse: one period of CursorLoop, again and again
    part_anim, length, period = PULSE
    slot = ed.part_anim_slot(part_anim)
    ed.add_anim("Pulse", frames=period, loop=True, events=[(part_anim, pane) for pane in slots],
                consts=[(ed.key(pane, uiedit.PART_ENABLE, part_anim=slot), 1.0) for pane in slots],
                curves=[(ed.key(pane, uiedit.PART_FRAME, part_anim=slot),
                         ramp(period, period / length, wrap=uiedit.CURVE_REPEAT)) for pane in slots])

    uiedit.rename(lay, LAYOUT)
    out = layout.build(lay)
    check = layout.Layout(out)                  # what the game will read
    uiedit.Editor(check).validate()
    assert layout.build(check) == out
    return out, P.msgs


# ---------------------------------------------------------------- the left stick (test folder)
# A sequence graph sees buttons through SeqQueryControllerTrigMaskChecker: 16 outputs, each
# one bit of the controller's trigger mask, the D-pad among them and no stick. The mask
# itself is sead's (A 0, B 1, ZL 2, X 3, Y 4, ZR 5, RS 6, LS 7, Minus 9, Plus 10, L 13, R 14,
# Up / Down / Left / Right 16-19), where a stick pushed past its threshold counts as four
# more buttons: left stick up / down / left / right = bits 20-23. The game's menus read
# those in their screens' code; a class-less screen like ours has only the graph.
# So the stick needs code. The game has a third checker class that no stock graph uses,
# SeqQueryControllerReleaseMaskChecker (same shape: "ControllerReleaseMask" in, 16 bools
# "ReleaseA" ... out, the same bits). The patch makes its four direction outputs read
# "D-pad or left stick" (one instruction each, plus a 3 instruction routine), and the test
# graph feeds it the trigger mask and takes its directions from it. Nothing the game ships
# runs the patched code. Both live in the folder STICK_MOD, which sorts before
# "Characters page" and so overrides the page's graph while it is installed and switched
# on; "Characters page" keeps the proven D-pad graph (--no-stick installs only that).
STICK_MOD = "Character stick (test)"
assert STICK_MOD < "Characters page" and STICK_MOD not in build.MODS and STICK_MOD != npc.MOD
RELEASE_CHECKER = "SeqQueryControllerReleaseMaskChecker"
RELEASE_CALC = 0x8330f390          # fills the 16 outputs (called by the node's calc, 0x8330f350)
RELEASE_SET = 0x80854960           # set one bool output: x0 = its slot in the node, w1 = value
RELEASE_SLOTS = ["A", "B", "X", "Y", "L", "R", "ZL", "ZR", "Plus", "Minus", "Up", "Down", "Left", "Right",
                 "LSPush", "RSPush"]                       # node + 0x28 + 8 * i, the order of the class's setup
STICK_SITES = {"Up": 0x8330f4c8, "Down": 0x8330f4e4, "Left": 0x8330f500, "Right": 0x8330f51c}
# 48 bytes of padding between the last PLT stub and the emulator's built-in Dynamic FPS
# routine (0x83c66200); every other session's code is at 0x83c66280 and up.
STICK_CAVE = (0x83c661d0, 0x83c66200)
PAD_BITS = {"Up": 16, "Down": 17, "Left": 18, "Right": 19}
STICK_SHIFT = 4                    # left stick up / down / left / right = bits 20-23


def stick_patch():
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = (STICK_CAVE[0], region[1])
    try:
        p = exepatch.Patcher(cave=STICK_CAVE)
    finally:
        exepatch.CAVE_REGION = region
    # byte 2 of the mask = bits 16-23: fold the stick's four onto the D-pad's four
    p.cave("stick_fold", "ldrb w8, [x8, #2]; orr w8, w8, w8, lsr #%d; ret" % STICK_SHIFT)
    for site in STICK_SITES.values():
        p.asm(site, "bl {stick_fold}", expect="ldrb w8, [x8, #2]")
    return p


def check_stick_patch(p):
    """Runs the checker's routine in unicorn, stock and patched, for every single bit of
    the mask and for mixed masks: stock, an output follows exactly its own bit (the bits
    listed above); patched, the four directions also follow the stick bits, and nothing
    else changes. Returns the number of runs."""
    import random
    import unicorn
    from unicorn import arm64_const as A
    img = p.img
    size = 0x270                   # the routine and its out-of-line ends; it leaves by a tail call to RELEASE_SET
    heap, node = 0x10000000, 0x10000100
    back = heap + 0x4000           # where the routine returns to

    def run(patched, mask):
        uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
        pages = set()
        for addr, n in ((RELEASE_CALC, size), (STICK_CAVE[0], STICK_CAVE[1] - STICK_CAVE[0]),
                        (RELEASE_SET, 4)):
            for pg in range(addr & ~0xFFF, (addr + n + 0xFFF) & ~0xFFF, 0x1000):
                if pg not in pages:
                    pages.add(pg)
                    uc.mem_map(pg, 0x1000)
                    uc.mem_write(pg, img.data[pg - img.base:pg - img.base + 0x1000])
        if patched:
            for addr, data in p.patches:
                uc.mem_write(addr, data)
        uc.mem_write(RELEASE_SET, struct.pack("<I", 0xD65F03C0))        # ret
        uc.mem_map(heap, 0x10000)
        uc.mem_write(node + 0x20, struct.pack("<Q", heap + 0x800))      # the input: pointer to the mask's address
        uc.mem_write(heap + 0x800, struct.pack("<Q", heap + 0x900))
        uc.mem_write(heap + 0x900, struct.pack("<I", mask))
        out = {}

        def on_set(uc, address, size, data):
            slot = uc.reg_read(A.UC_ARM64_REG_X0) - node - 0x28
            assert slot % 8 == 0 and 0 <= slot // 8 < len(RELEASE_SLOTS) and RELEASE_SLOTS[slot // 8] not in out
            out[RELEASE_SLOTS[slot // 8]] = uc.reg_read(A.UC_ARM64_REG_X1) & 0xFFFFFFFF

        uc.hook_add(unicorn.UC_HOOK_CODE, on_set, begin=RELEASE_SET, end=RELEASE_SET)
        uc.reg_write(A.UC_ARM64_REG_SP, heap + 0x8000)
        uc.reg_write(A.UC_ARM64_REG_X0, node)
        uc.reg_write(A.UC_ARM64_REG_X30, back)
        uc.emu_start(RELEASE_CALC, back, count=2000)
        assert uc.reg_read(A.UC_ARM64_REG_PC) == back and uc.reg_read(A.UC_ARM64_REG_SP) == heap + 0x8000
        assert set(out) == set(RELEASE_SLOTS), sorted(out)
        return out

    sead = {"A": 0, "B": 1, "ZL": 2, "X": 3, "Y": 4, "ZR": 5, "Minus": 9, "Plus": 10, "L": 13, "R": 14}
    sead.update(PAD_BITS)
    random.seed(1337)
    masks = [1 << b for b in range(32)] + [0, 0xFFFFFFFF] + [random.getrandbits(32) for _ in range(200)]
    bit_of = {}
    for b in range(32):
        on = [k for k, v in run(False, 1 << b).items() if v]
        assert len(on) <= 1
        for k in on:
            bit_of[k] = b
    assert all(bit_of[k] == b for k, b in sead.items()) and len(bit_of) == len(RELEASE_SLOTS), bit_of
    assert not set(range(PAD_BITS["Up"] + STICK_SHIFT, PAD_BITS["Right"] + STICK_SHIFT + 1)) & set(bit_of.values())
    for mask in masks:
        stock, new = run(False, mask), run(True, mask)
        for k in RELEASE_SLOTS:
            want = (mask >> bit_of[k]) & 1
            assert stock[k] == want, (k, mask)
            if k in PAD_BITS:
                want |= (mask >> (bit_of[k] + STICK_SHIFT)) & 1
            assert new[k] == want, (k, hex(mask), new[k])
    return 2 * len(masks) + 32


def release_checker(g):
    """A controller mask query and, fed by its trigger mask, the checker class the patch
    changed: returns that node (outputs ReleaseUp / Down / Left / Right = D-pad or left
    stick, newly pressed). A mask query of its own, so that each query feeds one node as
    in every stock graph."""
    B = build
    mask = g.add(B.vanilla("ImportantMenu", 5, "SeqQueryControllerGetMask"))
    n = B.vanilla("ImportantMenu", 4, "SeqQueryControllerTrigMaskChecker")
    n["Name"] = RELEASE_CHECKER
    inp = n["Parameters"]["Inputs"]["Pointer"][0]
    assert inp["Name"] == "ControllerTrigMask" and inp["Output Index"] == 3
    inp["Name"] = "ControllerReleaseMask"
    outs = n["Parameters"]["Outputs"]["Bool"]
    assert sorted(o["Name"] for o in outs) == sorted("Trig" + k for k in RELEASE_SLOTS)
    for o in outs:
        o["Name"] = "Release" + o["Name"][len("Trig"):]
        o["Is Output"] = o["Name"][len("Release"):] in PAD_BITS
    rel = g.add(n)
    plug = g.nodes[mask]["Plugs"]["Generic"]
    assert plug == [{"Node Index": 4, "Name": "ControllerTrigMask"}]       # ImportantMenu: its checker
    plug[0]["Node Index"] = rel
    g.nodes[rel]["Queries"] = [mask]
    g.nodes[rel]["Parameters"]["Inputs"]["Pointer"][0]["Node Index"] = mask
    return rel


def picker_graph(mod, pk, stick=False):
    """Sequence/CharacterMenu.module.ainb for the picker: one button loop per character.
    Every way into a loop ends in that character's gate (two waited frames, the shape the
    sample pages proved); what a press changes runs before it, in the frame of the press.
    A stores the number and runs the swap; the swap's steps exist once (their first step,
    the store, is per character), and after them the saved number decides where the cursor
    goes, the same way as on entry. ZL / ZR ask for the saved number too: the new sheet
    shows its orange slot only if the chosen character is on it."""
    B = build
    S = SCREEN
    g, root, chk, common, go = page_graph(picker=True)
    moves_by = release_checker(g) if stick else chk       # which node the four directions come from
    n, sheets = pk.n, pk.sheets
    slots = range(PER_SHEET)

    def stops(anim, count):
        return [g.add(B.t_stop_anim(S, anim, float(i))) for i in range(count)]

    def per_slot(tag, frame=None):
        return [g.add(B.t_play_anim(S, "%s_%02d" % (tag, j)) if frame is None else
                      B.t_stop_anim(S, "%s_%02d" % (tag, j), float(frame))) for j in slots]

    stop_sheet, stop_det, stop_cur = stops("Sheet", sheets), stops("Det", PER_SHEET), stops("Cur", PER_SHEET)
    stop_mark = stops("Mark", PER_SHEET + 1)
    play_in, play_out, play_dec = per_slot("In"), per_slot("Out"), per_slot("Dec")
    # Out stopped at its end and In stopped at its start both leave the slot without a cursor
    end_out, drop_in = per_slot("Out", SLOT_ANIMS["Out"][1]), per_slot("In", 0)
    settle = g.add(B.t_simultaneous(end_out + drop_in, root=False))     # no In / Out left running
    snd_move, snd_decide = g.add(B.t_sound("mc_CursorMove")), g.add(B.t_sound("mc_NormalDecide"))
    snd_right, snd_left = g.add(B.t_sound("mc_TabToRight")), g.add(B.t_sound("mc_TabToLeft"))

    loops = [g.add(B.t_simultaneous([], root=True)) for _ in range(n)]
    gates = [g.add(B.t_sequential([g.add(B.t_wait_frames(1)), g.add(B.t_wait_frames(1))], then=loops[c],
                                  root=True, same_frame=False)) for c in range(n)]

    def arrive(c, *first):
        return g.add(B.t_sequential(list(first), then=gates[c], root=True))

    def place(s, j):
        """The nodes that show sheet s with the cursor on slot j at once."""
        return [settle, stop_sheet[s], stop_det[j], stop_cur[j]]

    starts = [arrive(c, stop_mark[c % PER_SHEET], *place(*divmod(c, PER_SHEET))) for c in range(n)]
    start_unknown = arrive(0, stop_mark[NO_MARK], *place(0, 0))

    # by the saved number: on entry and after a swap
    query = flag_query(g)
    sel = g.add(t_int_sel(g, query, [(c, go(starts[c])) for c in range(n)], go(start_unknown)))
    g.nodes[query]["Plugs"]["Generic"][0]["Node Index"] = sel
    init = g.add(B.t_simultaneous([sel], root=True))

    # ZL / ZR: the new sheet, the cursor on its first slot, then the orange slot by the saved
    # number (a query node feeds one selector, so each sheet has its own)
    turns = {}
    for s in range(sheets):
        first = s * PER_SHEET
        marked = {j: arrive(first, stop_mark[j]) for j in [c % PER_SHEET for c in pk.on_sheet(s)] + [NO_MARK]}
        q = flag_query(g)
        sel = g.add(t_int_sel(g, q, [(c, go(marked[c % PER_SHEET])) for c in pk.on_sheet(s)], go(marked[NO_MARK])))
        g.nodes[q]["Plugs"]["Generic"][0]["Node Index"] = sel
        ask = g.add(B.t_simultaneous([sel], root=True))
        for sound in (snd_right, snd_left):
            turns[(s, sound)] = g.add(B.t_sequential(place(s, 0) + [sound], then=ask, root=True))

    # A: the orange slot and the slot's Decide at once, the number a frame later, then the
    # normal build's swap
    steps = B._swap_step_list(g, 0)
    store = g.nodes[steps[0]]
    assert store["Name"] == "SeqOneShotSetNonStructGameDataInt", store["Name"]
    assert [x["Default Value"] for x in store["Parameters"]["Inputs"]["String"]] == [B.CHARACTER_FLAG]
    assert sum(g.nodes[i].get("Name") == store["Name"] for i in steps) == 1
    swap = g.add(B.t_sequential([snd_decide] + steps[1:], then=init, root=True, same_frame=False))
    decide = [g.add(B.t_simultaneous([stop_mark[j], play_dec[j]], root=False)) for j in slots]
    picks = []
    for c in range(n):
        store_c = steps[0] if c == 0 else g.add(B.vanilla("AfterEvent", 33, store["Name"], Value=c,
                                                           GameDataFullName=B.CHARACTER_FLAG))
        picks.append(g.add(B.t_sequential([decide[c % PER_SHEET], store_c], then=swap, root=True, same_frame=False)))

    for s in range(sheets):
        tail = g.add(B.t_bool_sel(chk, B.TRIG["ZL"], go(turns[((s - 1) % sheets, snd_left)]), common))
        tail = g.add(B.t_bool_sel(chk, B.TRIG["ZR"], go(turns[((s + 1) % sheets, snd_right)]), tail))
        for c in pk.on_sheet(s):
            a = c % PER_SHEET
            chain = g.add(B.t_bool_sel(chk, B.TRIG["A"], go(picks[c]), tail))
            for button, to in sorted(pk.moves(c).items()):
                b = to % PER_SHEET
                move = arrive(to, stop_det[b], end_out[b], play_out[a], play_in[b], snd_move)
                chain = g.add(B.t_bool_sel(moves_by, B.TRIG[button], go(move), chain))
            g.nodes[loops[c]]["Plugs"] = B._plugs([chain])
    finish_graph(g, root, init, also=[g.add(B.t_play_anim(S, "Pulse"))])
    check_graph(g, loops, PICK_ANIMS)
    presses = check_picker_graph(g, pk, loops)
    write_graph(g, mod)
    # the same run on the file as written
    back = ainbx.load(open(out_path(mod, "Sequence/CharacterMenu.module.ainb"), "rb").read()).as_dict()
    read = type("ReadBack", (), {"nodes": back["Nodes"], "d": back})
    assert len(read.nodes) == len(g.nodes) and check_picker_graph(read, pk, loops) == presses
    return g, presses


def check_picker_graph(g, pk, loops):
    """Runs the graph as the game would: on entry for every saved number, and for every
    character every button, then every button again. After each press it must be where
    Picker.after() says, with the number stored, the sheet, the detail text, the orange slot
    and the cursor that go with it: the cursor on exactly one slot, and no In / Out still
    running that would end up showing something else."""
    nodes = g.nodes
    trig = {v: k for k, v in build.TRIG.items()}
    loop_of = {r: c for c, r in enumerate(loops)}
    lengths = {tag: frames for tag, (_, frames) in SLOT_ANIMS.items()}

    def kids(i):
        return [c["Node Index"] for c in nodes[i].get("Plugs", {}).get("Child", [])]

    def request(st, play, anim, frame):
        tag, _, j = anim.partition("_")
        if anim == "Pulse":
            assert play
            st["pulse"] = True
        elif tag in lengths:
            j = int(j)
            if play:
                if tag == "In":     # an Out of this slot would outlast it
                    assert st["running"].get(j) != "Out", ("In over a running Out", j)
                st["running"][j] = tag
                st["cursor"][j] = tag != "Out"
            else:                   # only ever stopped where the slot has no cursor
                assert (tag, frame) in (("Out", lengths["Out"]), ("In", 0)), (anim, frame)
                if st["running"].get(j) == tag:
                    del st["running"][j]
                st["cursor"][j] = False
        elif anim == "Cur":
            assert not play and not st["running"], ("cursor placed while In / Out run", st["running"])
            st["cursor"] = [j == frame for j in range(PER_SHEET)]
        elif anim in ("Sheet", "Det", "Mark"):
            assert not play
            st[anim] = frame

    def run(i, st):
        """Runs node i; returns the root node it jumps to, a command name, or None."""
        n = nodes[i]
        t, name = n["Node Type"], n.get("Name")
        if t == "Element_StateEnd":
            return n["Properties"]["String"][0]["Default Value"]
        if t == "Element_BoolSelector":
            q = n["Parameters"]["Inputs"]["Bool"][0]
            qn = nodes[q["Node Index"]].get("Name")
            if qn in ("SeqQueryControllerTrigMaskChecker", RELEASE_CHECKER):
                out = nodes[q["Node Index"]]["Parameters"]["Outputs"]["Bool"][q["Output Index"]]
                assert out["Is Output"] and trig[q["Output Index"]] == out["Name"][len(qn) - len("SeqQueryControllerMaskChecker"):]
                yes = trig[q["Output Index"]] == st["button"]
            else:      # may the menu close: yes; the swap's own questions: either answer
                yes = True
            return run(kids(i)[0 if yes else 1], st)
        if t == "Element_S32Selector":
            plugs = n["Plugs"]["Child"]
            hit = [p for p in plugs if p.get("Condition") == st["flag"] and not p.get("Is Default")]
            assert len(hit) <= 1 and sum(bool(p.get("Is Default")) for p in plugs) == 1
            return run((hit or [p for p in plugs if p.get("Is Default")])[0]["Node Index"], st)
        if t in ("Element_Sequential", "Element_Simultaneous"):
            for c in kids(i):
                r = run(c, st)
                if r is not None:
                    return r
            tr = n.get("Plugs", {}).get("Transition", [])
            return tr[0]["Node Index"] if tr else None
        v = {p["Name"]: p.get("Default Value") for ps in n.get("Parameters", {}).get("Inputs", {}).values() for p in ps}
        if name == "SeqOneShotEnd":
            return n["Plugs"]["Transition"][0]["Node Index"]
        if name == "SeqOneShotRequestStopAnimationScreen":
            request(st, False, v["AnimationName"], v["Frame"])
        if name == "SeqOneShotRequestPlayAnimationScreen":
            assert not v["IsReverse"]
            request(st, True, v["AnimationName"], None)
        if name == "SeqOneShotSetNonStructGameDataInt":
            assert v["GameDataFullName"] == build.CHARACTER_FLAG
            st["flag"] = v["Value"]
        if name == "WaitEquip.module":      # the swap takes far longer than any In / Out / Dec
            st["running"].clear()
        return None

    def settle(i, st):
        """From root node i, no button held any more, to a button loop or the page's end."""
        st["button"] = None
        for _ in range(20):
            if isinstance(i, str):
                return i
            if i in loop_of:
                return loop_of[i]
            i = run(i, st)
            assert i is not None, "the graph stops outside a button loop"
        raise AssertionError("no button loop reached")

    def press(c, button, st):
        st["button"] = button
        want, want_chosen = pk.after(c, button, st["flag"])
        r = run(loops[c], st)
        end = c if r is None else settle(r, st)
        assert end == want and st["flag"] == want_chosen, (c, button, end, want, st)
        if not isinstance(end, str):
            check(end, st)
        return end

    def check(c, st):
        s, j = divmod(c, PER_SHEET)
        assert (st["Sheet"], st["Det"], st["Mark"]) == (s, j, pk.mark(s, st["flag"])), (c, st)
        assert st["cursor"] == [k == j for k in range(PER_SHEET)], (c, st["cursor"])
        for k, tag in st["running"].items():        # what is still running ends in this picture
            assert (tag != "Out") == (k == j), (c, st["running"])

    def fresh(flag, c=None):
        st = {"button": None, "flag": flag, "running": {}, "cursor": [False] * PER_SHEET, "pulse": c is not None}
        if c is not None:
            s, j = divmod(c, PER_SHEET)
            st.update(Sheet=s, Det=j, Mark=pk.mark(s, flag), cursor=[k == j for k in range(PER_SHEET)])
        return st

    count = 0
    cmd = g.d["Commands"][0]["Root Node Index"]
    for flag in list(range(pk.n)) + [pk.n, 1000, 1001, -1]:
        st = fresh(flag)
        c = settle(run(cmd, st), st)
        assert c == (flag if 0 <= flag < pk.n else 0) and st["pulse"], (flag, c)
        check(c, st)
        count += 1
    buttons = sorted(build.TRIG)
    for c in range(pk.n):
        for chosen in sorted({0, c, pk.n - 1}):
            for button in buttons:
                st = fresh(chosen, c)
                end = press(c, button, st)
                count += 1
                if isinstance(end, str) or (button not in ("Left", "Right", "Up", "Down") and chosen != c):
                    continue
                for second in buttons:              # a second press while the first one's animations run
                    st2 = dict(st, running=dict(st["running"]), cursor=list(st["cursor"]))
                    press(end, second, st2)
                    count += 1
    return count


def check_picker(data, pk):
    """What each state shows, from the layout as built: for every character the one sheet,
    its own detail texts and no others, its name under its slot; the cursor and the orange
    slot for every slot; the played animations run their part animation from start to end
    and the pulse one period; everything inside its area. Returns the grid's right edge."""
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    names = ed.names()
    idx = names.index
    table = [x.text for x in lay.root.ptr[0x1F8].ptr[0x0].items]
    n, sheets = pk.n, pk.sheets
    slots = ["Pa_Slot_%02d" % j for j in range(PER_SHEET)]

    def val(values, pane, group, part_anim=None):
        v = values[(idx(pane), group, table.index(part_anim) if part_anim else None)]
        return v[0] if len(v) == 1 else v

    def parent(name):
        return names[ed.pane(name).parent]

    def frames(anim):
        return lay.root.ptr[0x140].get(anim).get("<I", 0x28)[0]

    at = lambda anim, f: uiedit.anim_values(lay, anim, float(f))      # noqa: E731
    for s in range(sheets):
        v = at("Sheet", s)
        assert [val(v, "N_Sheet_%02d" % t, VISIBLE) for t in range(sheets)] == onehot(sheets, s)
        for j in range(PER_SHEET):
            key = (idx("N_Slot_%02d" % j), VISIBLE, None)
            has = v[key][0] if key in v else float(ed.pane("N_Slot_%02d" % j).visible)
            assert has == float(s * PER_SHEET + j < n), (s, j)
            assert parent(slots[j]) == "N_Slot_%02d" % j
    for j in range(PER_SHEET):
        v = at("Cur", j)
        for i, pane in enumerate(slots):
            assert val(v, pane, uiedit.PART_FRAME, "Select") == float(i == j)
            assert val(v, pane, uiedit.PART_ENABLE, "Select") == 1.0
        assert tuple(ed.pane(slots[j]).pos) == tuple(map(float, pk.slot_pos[j]))
    for k in range(PER_SHEET + 1):
        v = at("Mark", k)
        for i, pane in enumerate(slots):
            assert val(v, pane, uiedit.PART_FRAME, "IsChosen") == float(i == k)
    for c in range(n):
        s, j = divmod(c, PER_SHEET)
        v = at("Det", j)
        for pane in ("T_CellName_%03d", "T_CellText_%03d"):
            cells = [d for d in pk.on_sheet(s) if val(v, pane % d, VISIBLE) == 1.0]
            assert cells == [c] and not ed.pane(pane % c).visible, (c, cells)
            assert parent(pane % c) == "N_Sheet_%02d" % s
        label = ed.pane("T_Slot_%03d" % c)
        assert parent("T_Slot_%03d" % c) == "N_Sheet_%02d" % s and label.visible
        assert tuple(label.pos) == tuple(map(float, pk.label_pos[j]))
    for tag, (part_anim, length) in SLOT_ANIMS.items():
        for j, pane in enumerate(slots):
            anim = "%s_%02d" % (tag, j)
            assert frames(anim) == length
            for f in range(length + 1):
                v = at(anim, f)
                assert set(k[0] for k in v) == {idx(pane)}                  # this slot and no other
                assert abs(val(v, pane, uiedit.PART_FRAME, part_anim) - f / length) < 1e-6
                assert val(v, pane, uiedit.PART_ENABLE, part_anim) == 1.0
    part_anim, length, period = PULSE
    assert frames("Pulse") == period and lay.root.ptr[0x140].get("Pulse").ptr[0].get("<II", 0x58) == (1, period)
    for f in (0, period / 2, period):
        v = at("Pulse", f)
        for pane in slots:
            assert abs(val(v, pane, uiedit.PART_FRAME, part_anim) * length - f) < 1e-3
    known = set(x[:-5] for x in common_layouts()) | set(CUSTOM_BUILT)
    for k, rec, _ in lay.root.ptr[0xC0].items:
        assert rec.ptr[0x0].text in known, rec.ptr[0x0].text
    # bounds: the grid in the left half with equal margins, the detail texts right of the
    # character, nothing over the header or the footer
    edge = -1e9
    patterns = {k.text: [(e.ptr[0].text, e.get("<f", 8)[0]) for e in rec.ptr[0x28].items]
                for k, rec, _ in lay.root.ptr[0xC0].items}
    for j, (x, y) in enumerate(pk.slot_pos):
        assert patterns[slots[j]] == [("ItemPattern", float(SLOT_PATTERNS[j]))] and ed.pane(slots[j]).scale == (1.0, 1.0)
        w, h = SLOT_SHAPES[SLOT_PATTERNS[j]]
        assert w <= PICK_PITCH_X - 8 and h <= BOX, (j, w, h)             # clear of its neighbours and its name
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        assert PICK_LEFT <= box[0] and box[2] <= PICK_RIGHT and -780 <= box[1] and box[3] <= 560, box
        edge = max(edge, box[2])
    for name in names:
        if name.startswith("T_") and name not in ("T_Name_00", "T_Caption_00", "T_Message_00"):
            v = ed.pane(name)
            x0, x1 = v.pos[0] - v.size[0] / 2, v.pos[0] + v.size[0] / 2
            if name.startswith("T_Cell"):
                assert 940 <= x0 and x1 <= 1840, (name, v.pos, v.size)
            else:
                assert PICK_LEFT - 1 <= x0 and x1 <= PICK_RIGHT + 1, (name, v.pos, v.size)
            assert -880 <= v.pos[1] - v.size[1] / 2 and v.pos[1] + v.size[1] / 2 <= 690, (name, v.pos, v.size)
    # the line over the grid: title from the grid's left edge, then ZL, the page text, ZR up to its right edge
    assert ed.pane("T_Title_00").pos[0] - PICK_TITLE_W / 2 == GRID_LEFT and parent("T_Title_00") == "N_Sandbox_00"
    spans = [(ed.pane("T_Title_00").pos[0] - PICK_TITLE_W / 2, ed.pane("T_Title_00").pos[0] + PICK_TITLE_W / 2)]
    for pane, w in (("Pa_KeyZL_00", KEY_W), ("T_Sheet_00", PAGE_W), ("Pa_KeyZR_00", KEY_W)):
        spans.append((ed.pane(pane).pos[0] - w / 2, ed.pane(pane).pos[0] + w / 2))
        assert ed.pane(pane).pos[1] == PICK_HEAD_Y
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])) and abs(spans[-1][1] - GRID_RIGHT) < 1e-3, spans
    for s in range(sheets):
        assert ed.pane("T_Sheet_%02d" % s).pos == ed.pane("T_Sheet_00").pos
    return edge


# ---------------------------------------------------------------- offline checks
# Nodes that take at least one frame when they run. (SeqExecuteGenericDummyAction is not
# one of them: the normal build uses it as a branch that does nothing inside a sequence.)
TAKES_FRAMES = {"SeqExecuteGenericWaitFrame", "SeqExecuteWaitCreateActor", "SeqExecuteWaitScreenAnimEnd",
                "WaitEquip.module"}


def check_graph(g, loops, ours):
    """No path from a button loop (`loops`: their root nodes) back to a button loop inside
    one frame; every animation request names an animation of our layout and a frame it
    has, and every animation of ours (`ours`: the first word of their names) is used."""
    nodes = g.nodes

    def kids(i):
        return [c["Node Index"] for c in nodes[i].get("Plugs", {}).get("Child", [])]

    def instant(i, seen=()):
        """Can node i run to its end in the frame it starts in?"""
        n = nodes[i]
        t = n["Node Type"]
        if t == "UserDefined":
            return n.get("Name") not in TAKES_FRAMES
        if t == "Element_StateEnd":
            return False                     # leaves this module
        if t == "Element_Sequential":
            same = [p["Default Value"] for p in n["Properties"]["Bool"] if p["Name"] == "IsUpdateNextInFrame"][0]
            if not same and len(kids(i)) >= 2:
                return False
            return all(instant(c) for c in kids(i))
        if t == "Element_Simultaneous":
            return all(instant(c) for c in kids(i))
        return any(instant(c) for c in kids(i)) if kids(i) else True     # selectors: any branch

    def jumps(i, acc):
        n = nodes[i]
        if n.get("Name") == "SeqOneShotEnd":
            acc.add(n["Plugs"]["Transition"][0]["Node Index"])
        for c in kids(i):
            jumps(c, acc)
        return acc

    roots = [i for i, n in enumerate(nodes) if "Is Root Node" in n.get("Flags", [])]
    edges = {}
    for r in roots:
        e = jumps(r, set())
        tr = nodes[r].get("Plugs", {}).get("Transition", [])
        if tr and instant(r):
            e.add(tr[0]["Node Index"])
        for t in e:
            assert "Is Root Node" in nodes[t]["Flags"], (r, t)
        edges[r] = e
    state = {}

    def visit(r, path):
        if state.get(r) == 2:
            return
        assert state.get(r) != 1, "nodes %s can run for ever inside one frame" % (path + [r])
        state[r] = 1
        for t in edges[r]:
            visit(t, path + [r])
        state[r] = 2

    sys.setrecursionlimit(10000)
    for r in roots:
        visit(r, [])
    # every state can be reached from the entry, and every button loop can leave the page
    cmd = g.d["Commands"][0]["Root Node Index"]
    all_edges = {r: set(edges[r]) for r in roots}
    for r in roots:
        tr = nodes[r].get("Plugs", {}).get("Transition", [])
        if tr:
            all_edges[r].add(tr[0]["Node Index"])
    seen, todo = {cmd}, [cmd]
    while todo:
        for t in all_edges[todo.pop()]:
            if t not in seen:
                seen.add(t)
                todo.append(t)
    assert seen == set(roots), sorted(set(roots) - seen)[:10]
    ends = [i for i in roots if nodes[i]["Node Type"] == "Element_StateEnd"]
    for r in loops:
        out = set()
        todo2, seen2 = list(all_edges[r]), set()
        # one press away: the jump targets of the loop itself and what they run into at once
        for t in list(all_edges[r]):
            out |= all_edges[t] | {t}
        assert out & set(ends) or any(nodes[t]["Node Type"] == "Element_StateEnd" for t in out), r

    lay = layout.Layout(open(os.path.join(BUILD_UI, LAYOUT + ".buil"), "rb").read())
    frames = {it[0].text: it[1].get("<I", 0x28)[0] for it in lay.root.ptr[0x140].items}
    used = set()
    for n in nodes:
        if n.get("Name") in ("SeqOneShotRequestPlayAnimationScreen", "SeqOneShotRequestStopAnimationScreen"):
            v = {p["Name"]: p["Default Value"] for ps in n["Parameters"]["Inputs"].values() for p in ps}
            assert v["ScreenName"] == SCREEN and v["AnimationName"] in frames, v
            if "Frame" in v:    # the game ignores a stop beyond the last frame
                assert 0 <= v["Frame"] <= frames[v["AnimationName"]], v
            used.add(v["AnimationName"])
    made = {a for a in frames if a.split("_")[0] in ours}
    assert made and made <= used, made - used
    return len(roots)


def check_states(data, pages):
    """What each state shows, from the animations as authored (uiedit.anim_values): one
    page visible, one item selected, the right detail text, the list window on the
    focused row, and the diamonds for each choice."""
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    names = ed.names()
    idx = names.index
    slots = [x.text for x in lay.root.ptr[0x1F8].ptr[0x0].items]

    def val(values, pane, group, part_anim=None):
        v = values[(idx(pane), group, slots.index(part_anim) if part_anim else None)]
        return v[0] if len(v) == 1 else v

    count = 0
    for page in pages:
        v = uiedit.anim_values(lay, "Page", float(page.index))
        assert [val(v, "N_Page_%02d" % p.index, VISIBLE) for p in pages] == onehot(len(pages), page.index)
        assert val(v, "N_DetailAll_00", VISIBLE) == float(page.has_detail)
        for st in page.states():
            i = st[0]
            v = uiedit.anim_values(lay, page.cursor_anim, float(i))
            for j, it in enumerate(page.items):
                if has_anim(it.part, "Select"):
                    assert val(v, it.pane, uiedit.PART_FRAME, "Select") == float(j == i), (page.key, st, j)
                    assert val(v, it.pane, uiedit.PART_ENABLE, "Select") == 1.0
            if page.has_detail:
                assert [val(v, "N_Detail_%02d" % d, VISIBLE) for d in range(len(DETAILS))] == \
                    onehot(len(DETAILS), page.items[i].detail)
            ln = page.items[i].line
            got = val(v, page.focus_pane, TRANSLATE) + val(v, page.focus_pane, SCALE)
            assert all(abs(a - b) < 1e-3 for a, b in zip(got, (ln[0], ln[1], ln[2], 1.0))), (page.key, st, got, ln)
            if page.kind == "scroll":
                s = st[1]
                v = uiedit.anim_values(lay, "Scroll", float(s))
                assert val(v, page.scroll_pane, TRANSLATE)[1] == s * ROW_PITCH
                shown = [r for r, it in enumerate(page.items) if val(v, it.pane, VISIBLE) == 1.0]
                assert shown == list(range(s, s + LIST_VISIBLE)) and i in shown
                # the focused row is inside the scissor
                row = page.items[i]
                y = row.row_y + s * ROW_PITCH
                assert abs(y) <= (WIN_H - ROW_PITCH) / 2, (st, y)
            count += 1
    page = next(p for p in pages if p.kind == "scroll")
    for (frm, to), name in page.tweens.items():
        a, b = uiedit.anim_values(lay, name, 0.0), uiedit.anim_values(lay, name, float(SCROLL_FRAMES))
        end = uiedit.anim_values(lay, "Scroll", float(to))
        assert val(a, page.scroll_pane, TRANSLATE)[1] == frm * ROW_PITCH
        for it in page.items:                                   # a move ends in the state it goes to
            assert val(b, it.pane, VISIBLE) == val(end, it.pane, VISIBLE), (name, it.pane)
        assert val(b, page.scroll_pane, TRANSLATE) == val(end, page.scroll_pane, TRANSLATE)
        assert abs(val(b, page.bar_pane, uiedit.PART_FRAME, "Pos") - val(end, page.bar_pane, uiedit.PART_FRAME, "Pos")) < 1e-6
        mid = uiedit.anim_values(lay, name, SCROLL_FRAMES / 2)
        lo, hi = sorted((frm * ROW_PITCH, to * ROW_PITCH))
        assert lo < val(mid, page.scroll_pane, TRANSLATE)[1] < hi
    for c in range(len(CHARACTERS)):
        v = uiedit.anim_values(lay, "Chosen", float(c))
        for p in pages:
            for it in p.items:
                if it.dot_pane and it.char is not None:
                    assert val(v, it.dot_pane, uiedit.PART_FRAME, "IsChosen") == float(it.char == c)
    # every part we show exists, and every text we set has its pane and label form
    known = set(n[:-5] for n in common_layouts()) | set(CUSTOM_BUILT)
    for k, rec, _ in lay.root.ptr[0xC0].items:
        assert rec.ptr[0x0].text in known, rec.ptr[0x0].text
    return count


def check_bounds(data, pages):
    """Everything we place stays inside the UI area (left of Link) and clear of the
    header and the footer, by the parts' footprints; returns the rightmost edge."""
    ed = uiedit.Editor(layout.Layout(data))
    worst = -1e9
    for page in pages:
        for it in page.items:
            x0, y0, x1, y1 = footprint(it.part)
            px, py = (LIST_X, LIST_Y + it.row_y) if page.kind == "scroll" else it.pos
            box = (px + it.scale * x0, py + it.scale * y0, px + it.scale * x1, py + it.scale * y1)
            if page.kind == "scroll":      # rows outside the window are clipped
                box = (box[0], max(box[1], LIST_Y - WIN_H / 2), box[2], min(box[3], LIST_Y + WIN_H / 2))
            assert UI_LEFT - 20 <= box[0] and box[2] <= UI_RIGHT, (page.key, it.part, box)
            assert -900 <= box[1] and box[3] <= 700, (page.key, it.part, box)
            worst = max(worst, box[2])
            if it.dot:
                assert UI_LEFT - 20 <= it.dot[0] - 60 and it.dot[0] + 60 <= UI_RIGHT, (page.key, it.dot)
    for name in ed.names():
        if name.startswith("T_") and name not in ("T_Name_00", "T_Caption_00", "T_Message_00"):
            v = ed.pane(name)
            x = v.pos[0] + (DETAIL_POS[0] if name.startswith("T_Detail") else 0)
            assert UI_LEFT - 20 <= x - v.size[0] / 2 and x + v.size[0] / 2 <= UI_RIGHT, (name, v.pos, v.size)
            worst = max(worst, x + v.size[0] / 2)
    return worst


# ---------------------------------------------------------------- archives
def out_path(mod, rel):
    return os.path.join(BUILD_UI, mod, "romfs", rel)


def read_built(mod, rel):
    """A file as the normal build produced it (or the untouched romfs file)."""
    p = out_path(mod, rel)
    return open(p if os.path.exists(p) else os.path.join(nxfmt.ROMFS, rel), "rb").read()


def owner(rel):
    """The mod folder that already ships `rel` (each file must live in one folder)."""
    have = [m for m in build.MODS if os.path.exists(out_path(m, rel))]
    assert len(have) <= 1, (rel, have)
    return have[0] if have else "Characters page"


def sandbox(layouts, msgs):
    """`layouts` {name: .buil bytes} into the layout archive, the category list and the
    screen table; `msgs` into LayoutMsg/<our layout>.msbt."""
    sizes = build.SIZES
    mod = owner(COMMON)
    raw = read_built(mod, COMMON)
    sarc = nxfmt.unzs(raw, COMMON)
    have = set(n for n, _, _ in nxfmt.sarc_entries(sarc))
    assert not have & set(n + ".buil" for n in layouts)
    new = sarcedit.add(sarc, [(n + ".buil", d) for n, d in sorted(layouts.items()) for _ in range(LAYOUT_COPIES)])
    for n in layouts:
        assert [e for e, _, _ in nxfmt.sarc_entries(new)].count(n + ".buil") == LAYOUT_COPIES
    build.write(mod, COMMON, nxpack.zs_compress_like(new, raw), len(new), sizes, size=align32(len(new)) + 4144)
    # archive category: our layouts are in Common
    raw = open(os.path.join(nxfmt.ROMFS, CATEGORY), "rb").read()
    data = nxfmt.unzs(raw, CATEGORY)
    cat = byml.load(data)
    assert byml.dump(cat, byml.version_of(data)) == data
    lst = cat["Common"]
    for n in sorted(layouts):
        assert n not in lst
        lst.insert(bisect.bisect([s.lower() for s in lst], n.lower()), n)
    new = byml.dump(cat, byml.version_of(data))
    build.write(owner(CATEGORY), CATEGORY, nxpack.zs_compress_like(new, raw), len(new), sizes,
                size=align32(len(new)) + 264)
    # screen table: our screen shows our layout
    mod = owner(build.UISCREEN)
    raw = read_built(mod, build.UISCREEN)
    data = nxfmt.unzs(raw, build.UISCREEN)
    rows = byml.load(data)
    row = [r for r in rows if r["Name"] == SCREEN]
    assert len(row) == 1 and row[0]["BodyLayoutFile"] == build.PAGE_LAYOUT, row
    row[0]["BodyLayoutFile"] = LAYOUT
    new = byml.dump(rows, byml.version_of(data))
    build.write(mod, build.UISCREEN, nxpack.zs_compress_like(new, raw), len(new), sizes)
    # texts
    for rel in MALS:
        mod = owner(rel)
        raw = read_built(mod, rel)
        sarc = nxfmt.unzs(raw, rel)
        files = nxfmt.sarc_files(sarc)
        assert MSG not in files
        m = msbt.Msbt(files[MSG_BASE])
        for label, text in msgs.items():
            assert len(label) < 64, label
            m.add(label, text)
        new = sarcedit.add(sarc, [(MSG, m.to_bytes())])
        build.write(mod, rel, nxpack.zs_compress_like(new, raw), len(new), sizes, size=align32(len(new)) + 312)


# ---------------------------------------------------------------- main
def sandbox_emulator_running():
    """Only the sandbox copy keeps our mods folder open (the normal emulator may run)."""
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path } | "
                          "Select-Object -ExpandProperty Path"], capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if os.path.normcase(p).startswith(os.path.normcase(uienv.DST))]


def main():
    install = "--install" in sys.argv
    samples = "--samples" in sys.argv
    stick = not samples and "--no-stick" not in sys.argv
    if install:
        for _ in range(10):     # a just-closed emulator takes a moment to go away
            if not sandbox_emulator_running():
                break
            time.sleep(1)
        else:
            print("The sandbox emulator is running. Close it first: it keeps the mod files open.")
            sys.exit(1)
    build.BUILD = BUILD_UI          # the normal build's output stays untouched
    build.SIZES = {}
    build.EXE_PATCHES = {}
    folders = list(build.MODS)
    shutil.rmtree(os.path.join(BUILD_UI, npc.MOD), ignore_errors=True)
    shutil.rmtree(os.path.join(BUILD_UI, STICK_MOD), ignore_errors=True)
    if samples:
        for mod, fn in build.MODS.items():
            print(mod)
            shutil.rmtree(os.path.join(BUILD_UI, mod), ignore_errors=True)
            fn(mod)
    else:
        npc_build = npc.build_mods()        # the normal mods + the whole ring of characters
        folders.append(npc.MOD)
    print("UI sandbox:", "sample pages" if samples else "character picker")
    parts = build_custom_parts(CUSTOM_PARTS if samples else PICK_PARTS)
    if samples:
        pages = make_pages()
        data, msgs = make_layout(pages)
    else:
        pk = Picker()
        data, msgs = make_picker_layout(pk)
    with open(os.path.join(BUILD_UI, LAYOUT + ".buil"), "wb") as f:
        f.write(data)
    for name, d in parts.items():
        with open(os.path.join(BUILD_UI, name + ".buil"), "wb") as f:
            f.write(d)
    ed = uiedit.Editor(layout.Layout(data))
    head = "  layout %s: %d bytes, %d panes, %d animations, %d messages" \
        % (LAYOUT, len(data), len(ed.panes), len(ed.dict_items(0x140)), len(msgs))
    if samples:
        n_states = check_states(data, pages)
        right = check_bounds(data, pages)
        print("%s; %d states checked; right edge x = %d" % (head, n_states, right))
    else:
        right = check_picker(data, pk)
        print("%s; %d characters on %d sheets checked; grid x %d .. %d" % (head, pk.n, pk.sheets, PICK_LEFT, right))
    layouts = dict(parts)
    layouts[LAYOUT] = data
    sandbox(layouts, msgs)
    if samples:
        g = sandbox_graph("Characters page", pages)
        print("  graph: %d nodes" % len(g.nodes))
    else:
        g, presses = picker_graph("Characters page", pk)
        print("  graph: %d nodes; %d button presses and entries run offline" % (len(g.nodes), presses))
        if stick:
            print(STICK_MOD)
            size = build.SIZES["Sequence/CharacterMenu.module.ainb"]
            g2, presses2 = picker_graph(STICK_MOD, pk, stick=True)
            assert presses2 == presses and build.SIZES["Sequence/CharacterMenu.module.ainb"] >= size
            patch = stick_patch()
            runs = check_stick_patch(patch)
            build.write_ips(STICK_MOD, patch)
            folders.append(STICK_MOD)
            print("  graph: %d nodes, directions from %s; code patch run offline for %d masks" %
                  (len(g2.nodes), RELEASE_CHECKER, runs // 2))
    for mod in build.RSTB_MODS:
        print(mod, "(combined size table)")
        build.write_rstb(mod, build.SIZES)
    if not samples:
        print("checks of the NPC build")
        npc.check_build(*npc_build)
    if install:
        uienv.setup()
        uienv.check()
        for name in os.listdir(uienv.LOAD):
            shutil.rmtree(os.path.join(uienv.LOAD, name))
        for mod in folders:
            shutil.copytree(os.path.join(BUILD_UI, mod), os.path.join(uienv.LOAD, mod))
            print("  installed ->", os.path.join(uienv.LOAD, mod))


if __name__ == "__main__":
    main()
