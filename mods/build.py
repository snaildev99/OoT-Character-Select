"""Build the mods into build\\<mod>\\romfs and optionally install them.

    venv\\Scripts\\python.exe mods\\build.py             build only
    venv\\Scripts\\python.exe mods\\build.py --install   build, then copy each mod
        folder into %APPDATA%\\eden\\load\\0400486024DEC000 (only our own folders
        are replaced; folders of retired mods are removed)

Every mod starts from the untouched romfs and checks the original values it
expects, so a game update makes the build fail instead of shipping a bad file.
"""
import copy
import os
import shutil
import struct
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ainbx  # noqa: E402
import buil  # noqa: E402
import byml  # noqa: E402
import bympatch  # noqa: E402
import exepatch  # noqa: E402
import gamedata  # noqa: E402
import msbt  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402
import rstb  # noqa: E402
import sarcedit  # noqa: E402
import uiedit  # noqa: E402
import layout  # noqa: E402

ROMFS = nxfmt.ROMFS
SEQ = os.path.join(ROMFS, "Sequence")
BUILD = os.path.join(ROOT, "build")
EDEN_LOAD = os.path.join(os.environ["APPDATA"], "eden", "load", "0400486024DEC000")


def guid(key):
    """Stable GUID for a node we add (rebuilds give identical files). Our signature:
    every GUID we make starts with 00001337, so our nodes stand out in dumps."""
    u = uuid.uuid5(uuid.NAMESPACE_URL, "oot-modding:" + key)
    return str(uuid.UUID(int=(0x00001337 << 96) | (u.int & ((1 << 96) - 1))))


# ---------------------------------------------------------------- output + size table

_stock_rstb = {}


def stock_table():
    if "t" not in _stock_rstb:
        _stock_rstb["t"] = rstb.Rstb(nxfmt.read(os.path.join(ROMFS, rstb.PATH)))
    return _stock_rstb["t"]


def size_needed(rel, n):
    """Resource size table value the game would use for a decompressed file of n bytes."""
    if rel.endswith(".ainb"):
        return rstb.ainb_size(n)
    if rel.startswith("RSDB/") and rel.endswith(".rstbl.byml"):
        return ((n + 31) & ~31) + 264  # 19 of 20 RSDB tables (ActorInfo needs more)
    if rel.endswith(".bntx") or rel == gamedata.PATH[:-3]:
        return ((n + 31) & ~31) + 264  # all player textures; the GameDataList
    if rel.startswith("Pack/Actor/") and rel.endswith(".pack"):
        return ((n + 31) & ~31) + 312  # 3DUICamera, PouchPlayer_Child, Player_Child
    raise ValueError("no size rule for " + rel)


# Size table entries every mod's files need. One combined table is written into
# each mod in RSTB_MODS (identical copies, so whichever the emulator picks has all
# entries, and either mod still works when the other one is switched off).
SIZES = {}
RSTB_MODS = ["Characters page", "Character models"]


def write(mod, rel, data, decompressed_size, sizes=None, size=None):
    """Write a file into the mod. Without `sizes` the file must fit the stock
    size table; with it (a dict, normally SIZES) the needed entry is recorded
    there (`size` overrides the rule, e.g. for models) and goes into the
    shipped size table (write_rstb)."""
    key = rel[:-3] if rel.endswith(".zs") else rel
    limit = stock_table().get(key)
    if sizes is None:
        assert limit is not None and decompressed_size <= limit, \
            "%s is %d bytes, size table allows %s" % (rel, decompressed_size, limit)
    else:
        need = size if size is not None else size_needed(key, decompressed_size)
        assert need >= decompressed_size
        sizes[key] = max(sizes.get(key, 0), need)
    _write_raw(mod, rel, data, "(%d bytes, stock table %s)" % (decompressed_size, limit))


def write_rstb(mod, sizes):
    raw = open(os.path.join(ROMFS, rstb.PATH), "rb").read()
    t = rstb.Rstb(nxfmt.unzs(raw))
    for path, size in sorted(sizes.items()):
        is_new = not any(os.path.exists(os.path.join(ROMFS, path + z)) for z in ("", ".zs"))
        if is_new:  # a stock entry under the same crc32 would belong to another file
            assert t.get(path) is None, "size table crc32 collision for new file " + path
        old, new = t.ensure(path, size)
        print("  size table: %s %s -> %s" % (path, old, new))
    _write_raw(mod, rstb.PATH, nxpack.zs_compress_like(t.to_bytes(), raw))


def _write_raw(mod, rel, data, note=""):
    out = os.path.join(BUILD, mod, "romfs", rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(data)
    print("  %s  %s" % (rel, note))


# ---------------------------------------------------------------- graph helpers

class Graph:
    """An AINB as an editable dict (ainb package dict form)."""

    def __init__(self, src_path=None, like=None, filename=None, exact=True):
        """`exact`: the file must rebuild byte for byte (required for files we
        write; templates may skip it, see vanilla())."""
        if src_path is not None:
            self.src = open(src_path, "rb").read()
            self.orig = ainbx.load(self.src)
            if exact:
                assert ainbx.dump(self.orig) == self.src, "round trip not exact: " + src_path
            self.d = self.orig.as_dict()
            self.new_file = False
        else:
            # new file: header sections cloned from `like` (a Graph), no nodes
            self.orig = like.orig
            self.d = copy.deepcopy(like.d)
            self.d.update(Filename=filename, Modules=[], Nodes=[])
            self.new_file = True
        self.nodes = self.d["Nodes"]

    def node(self, i, name=None, type_=None):
        n = self.nodes[i]
        if name is not None:
            assert n.get("Name") == name, (i, n.get("Name"), name)
        if type_ is not None:
            assert n["Node Type"] == type_, (i, n["Node Type"], type_)
        return n

    def add(self, n):
        n = copy.deepcopy(n)
        n["Node Index"] = len(self.nodes)
        n["GUID"] = guid("%s:node%d" % (self.d["Filename"], n["Node Index"]))
        self.nodes.append(n)
        return n["Node Index"]

    def build(self):
        return ainbx.dump(ainbx.from_dict(self.d, like=self.orig, keep_query_order=not self.new_file))


_vanilla_graphs = {}


def vanilla(file, index, name, **inputs):
    """Copy of a vanilla Sequence node as a template, with input values
    changed (keyword = input name; its type is looked up).

    The package's "Is Output" input flag (bit 0x1000000) marks a value left at
    the node type's declared default (Sequence/NodeDefinition): in vanilla it
    is only ever set when the value equals that default, and the game then
    uses the declared default. So any value we set must drop the flag, or it
    is ignored (this made IsDirect=true act as false).

    Template files need not rebuild exactly (LoadBancImpl comes out 4 bytes
    shorter, in a section after the nodes, 0x50ac); expression nodes are not
    taken from them."""
    if file not in _vanilla_graphs:
        _vanilla_graphs[file] = Graph(os.path.join(SEQ, file + ".module.ainb"), exact=file != "LoadBancImpl")
    n = copy.deepcopy(_vanilla_graphs[file].node(index, name))
    assert n["Node Type"] != "Element_Expression", (file, index)
    for key, value in inputs.items():
        hit = [p for ps in n["Parameters"]["Inputs"].values() for p in ps if p["Name"] == key]
        assert len(hit) == 1, (file, index, key)
        hit[0]["Default Value"] = value
        hit[0]["Flags"] = [f for f in hit[0]["Flags"] if f != "Is Output"]
    return n


def _plugs(children, then=None):
    p = {"Child": [{"Node Index": c, "Name": ""} for c in children]}
    if then is not None:
        p["Transition"] = [{"Node Index": then, "Transition Type": 1, "Update Post Calc": True}]
    return p


# Controller trigger outputs of SeqQueryControllerTrigMaskChecker (Bool output index).
TRIG = {"A": 0, "B": 1, "Down": 2, "L": 3, "Left": 4, "Minus": 6, "Plus": 7, "R": 8,
        "Right": 9, "Up": 11, "X": 12, "Y": 13, "ZL": 14, "ZR": 15}


def t_simultaneous(children, then=None, root=True):
    """Element_Simultaneous; `root` marks it as a transition target / command root."""
    n = vanilla("ImportantMenu", 26, "")
    n["Flags"] = ["Is Root Node"] if root else []
    n["Plugs"] = _plugs(children, then)
    return n


def t_sequential(children, then=None, root=True, same_frame=True):
    """Element_Sequential; same_frame=False starts each child on a new frame
    (IsUpdateNextInFrame false, as in LoadBancImpl nodes 36 and 68)."""
    n = vanilla("ImportantMenu", 46, "")
    n["Flags"] = ["Is Root Node"] if root else []
    n["Plugs"] = _plugs(children, then)
    prop = [p for p in n["Properties"]["Bool"] if p["Name"] == "IsUpdateNextInFrame"]
    assert len(prop) == 1 and prop[0]["Default Value"] is True
    prop[0]["Default Value"] = same_frame
    return n


def t_bool_sel(query, output, true, false):
    n = vanilla("ImportantMenu", 15, "")
    n["Queries"] = [query]
    inp = n["Parameters"]["Inputs"]["Bool"][0]
    inp["Node Index"], inp["Output Index"] = query, output
    n["Plugs"]["Generic"][0]["Node Index"] = query
    n["Plugs"]["Child"] = [{"Node Index": true, "Name": "True"}, {"Node Index": false, "Name": "False"}]
    return n


def t_end(target):
    """SeqOneShotEnd: transition to `target` (a root node)."""
    n = vanilla("ImportantMenu", 18, "SeqOneShotEnd")
    n["Plugs"]["Transition"][0]["Node Index"] = target
    return n


def t_state_end(command):
    n = vanilla("ImportantMenu", 19, "")
    n["Properties"]["String"][0]["Default Value"] = command
    return n


def t_sound(key):
    return vanilla("ImportantMenu", 69, "SeqOneShotStartSound", SLinkKey=key)


def t_camera(state):
    """SeqOneShotSetUICameraState. Vanilla pages: 4 = leave to the right-hand tab,
    5 = leave to the left, 1/2 = enter from the left/right, 3 = settled. With
    Link shown (display 0), setting 0 or 2 on entry blacked the view out."""
    return vanilla("ImportantMenu", 47, "SeqOneShotSetUICameraState", UICameraState=state)


def t_display(display_type):
    """SeqOneShotSetPouchActorDisplayType: 0 = 3D Link, 2 = tapestry, 3 = Key
    Items stones, 1/4 = nothing, 5 = menu closed."""
    return vanilla("ImportantMenu", 33, "SeqOneShotSetPouchActorDisplayType", DisplayType=display_type)


def t_env(name):
    """SeqExecuteApplyEnvSet (3D menu scene): "ImportantMenu", "Tapestry", "Pouch" (+ menu)."""
    return vanilla("ImportantMenu", 35, "SeqExecuteApplyEnvSet", ModelSceneType=1, EnvSetName=name)


def t_load_screen(screen):
    return vanilla("PlusMenu", 117, "SeqOneShotLoadScreen", ScreenName=screen)


def t_open_screen(screen, option=2):
    return vanilla("ImportantMenu", 30, "SeqOneShotOpenScreen", ScreenName=screen, Option=option)


def t_close_unload_screen(screen):
    return vanilla("AfterEvent", 20, "SeqExecuteCloseAndUnloadScreen", ScreenName=screen, IsDirect=True)


def t_play_anim(screen, anim, reverse=False):
    return vanilla("ImportantMenu", 59, "SeqOneShotRequestPlayAnimationScreen",
                   ScreenName=screen, AnimationName=anim, IsReverse=reverse)


def t_stop_anim(screen, anim, frame=0.0):
    return vanilla("ImportantMenu", 60, "SeqOneShotRequestStopAnimationScreen",
                   ScreenName=screen, AnimationName=anim, Frame=frame)


def t_overlay(preset):
    """OpenAppMenuOverlay.module: header + footer preset (see README)."""
    return vanilla("ImportantMenu", 21, "OpenAppMenuOverlay.module", Preset=preset)


def t_screen_state_sel(screen, when_open, when_closed):
    """SeqSelectorScreenOpenCloseState: Opening/Opened/Closing -> when_open,
    Closed -> when_closed (Key Items uses it for EventFade_01)."""
    n = vanilla("ImportantMenu", 56, "SeqSelectorScreenOpenCloseState", ScreenName=screen)
    n["Plugs"]["Child"] = [{"Node Index": when_open, "Name": s} for s in ("Opening", "Opened", "Closing")] + \
                          [{"Node Index": when_closed, "Name": "Closed"}]
    return n


def t_set_flag(name, on):
    """SeqOneShotSetNonStructGameDataBool (template: ChangeBanc node 22)."""
    return vanilla("ChangeBanc", 22, "SeqOneShotSetNonStructGameDataBool", Value=on, GameDataFullName=name)


def t_set_int(name, value):
    """SeqOneShotSetNonStructGameDataInt (template: AfterEvent node 33)."""
    return vanilla("AfterEvent", 33, "SeqOneShotSetNonStructGameDataInt", Value=value, GameDataFullName=name)


def t_add_int(name, value):
    """SeqOneShotAddNonStructGameDataInt (template: ChangeGamePause node 2; the NPC build's
    next / previous buttons use it)."""
    return vanilla("ChangeGamePause", 2, "SeqOneShotAddNonStructGameDataInt", Value=value, GameDataFullName=name)


def t_compare_int(query, value, equal, other):
    """SeqSelectorMathCompareInt (template: WaitEquip node 6): child `equal` when the
    Int query's value is `value`, else `other`."""
    n = vanilla("WaitEquip", 6, "SeqSelectorMathCompareInt", ValueB=value)
    a = n["Parameters"]["Inputs"]["Int"][0]
    assert a["Name"] == "ValueA" and n["Queries"] == [7] and a["Node Index"] == 7
    n["Queries"] = [query]
    a["Node Index"] = query
    assert [c["Name"] for c in n["Plugs"]["Child"]] == ["Bigger", "Equal", "Smaller"]
    n["Plugs"] = {"Child": [{"Node Index": other, "Name": "Bigger"}, {"Node Index": equal, "Name": "Equal"},
                            {"Node Index": other, "Name": "Smaller"}],
                  "Int": [{"Node Index": query, "Name": "ValueA"}]}
    return n


def t_wait_frames(frames):
    """SeqExecuteGenericWaitFrame (template: FastLoadOn node 4; Frame is a property)."""
    n = vanilla("FastLoadOn", 4, "SeqExecuteGenericWaitFrame")
    prop = [p for ps in n["Properties"].values() for p in ps if p["Name"] == "Frame"]
    assert len(prop) == 1 and prop[0]["Default Value"] == 1, prop
    prop[0]["Default Value"] = frames
    return n


def t_wait_anim(screen, anim):
    return vanilla("ImportantMenu", 72, "SeqExecuteWaitScreenAnimEnd", ScreenName=screen, AnimName=anim)


# ---------------------------------------------------------------- Characters page

PAGE_SCREEN = "CharacterMenu_00"
PAGE_CATEGORY = 7   # our − menu category (vanilla has 0-6; see header_four_tabs)
PAGE_PRESET = 1337  # our header preset (vanilla has 0-19; any value is safe, see header_four_tabs)
PAGE_LABEL = "Characters"  # header text label; must be an existing string in the executable
PAGE_LAYOUT = "ImportantMenu_01"  # placeholder: the Key Items layout (vignette + in/out animations)
UISCREEN = "RSDB/UIScreen.Product.111.rstbl.byml.zs"

# Characters. The menu stores the choice in a GameData Int of our own (saved in the
# save slot, like the rest of Link's progress); the model hook (character_models)
# reads it whenever the game builds a model path. Index = stored value; 0 is the
# game's own Link. A character's "models" map a player model to the model mod in
# assets\models it comes from and the texture names to give its own copies (same
# length as the original: names are changed in place inside the files).
CHARACTER_FLAG = "CharacterMenu_SelectedCharacter"
# Set by the Characters page around its "delete the menu Link" step: the exe patch
# then keeps the menu's 3D camera (see character_swap_steps).
KEEP_CAMERA_FLAG = "CharacterMenu_KeepUICamera"
# Set while the Characters page is shown: the exe patch (view_hook) then puts Link on
# the right third, leaving room for UI.
PAGE_VIEW_FLAG = "CharacterMenu_IsPageOpen"
# Character transition (snaildev, 2026-11-10; the README's mods list has every version and
# why the early ones failed): the old character slides off to the right while the new
# one slides in from the left; the backdrop stays. Both characters exist meanwhile and
# are moved themselves:
#   1. SLIDE_FLAG 0 -> SLIDE_STASH, then the game's "delete the menu character" node:
#      the exe patch parks the character in menu slot STASH_SLOT instead (a slot no
#      display type touches) and deletes nothing. From that frame the page view patch
#      moves it away, counting its frames in SLIDE_OUT_FLAG.
#   2. the new character is created into slot 0 the usual way (asleep and hidden, as
#      the game makes it); the patches in "character created" and "create the menu
#      character" keep the camera block and the shadow range meanwhile.
#   3. display type 0 from the sequence: the game wakes and shows slot 0 (the page
#      view patch cannot: during the actors' update the game refuses handle access to
#      other actors). While SLIDE_FLAG is still SLIDE_STASH the patch keeps the new one
#      hidden (the actor's "shown" bit) where its way in starts.
#   4. SLIDE_FLAG -> 1: the patch shows the new one and counts SLIDE_FLAG up, one per
#      camera update; that count is the frame of its way in (and of the zoom's glide).
#      Positions go through the game's own setter and are applied on the actor's next
#      update. The page's vignette is drawn over the 3D picture and is dark at the
#      sides: that is the fade.
#   5. the delete node again: with SLIDE_FLAG neither 0 nor SLIDE_STASH the patch
#      deletes slot STASH_SLOT, the old character. The page view patch then finds a
#      count but nobody parked: the transition is over, it puts SLIDE_FLAG and
#      SLIDE_OUT_FLAG back to 0 and makes the stored character the view character.
# The sequence changes SLIDE_FLAG only by adding (t_add_int: +SLIDE_STASH, then
# 1 - SLIDE_STASH), because the UI session's picker graph, whose swap this is in the
# full build, checks that its only "set an Int" node is the one that stores the
# character. A flag that is out of step puts itself right: see step 5.
# The view (the page zoom, zoom_blend_hook) follows VIEW_CHARACTER_FLAG = the character
# that is shown: copied from the stored one when a menu character is created outside
# a transition and when a transition ends, never just because the stored number
# changed (the picker stores it a few frames before the swap starts).
SLIDE_FLAG = "CharacterMenu_Slide"
VIEW_CHARACTER_FLAG = "CharacterMenu_ViewCharacter"
SLIDE_OUT_FLAG = "CharacterMenu_SlideOut"
SLIDE_STASH = 200
STASH_SLOT = 4              # menu slots 4 and 5 are empty in the − menu (live, 2026-11-10)
SLIDE_FRAMES = 16           # the zoom's glide, frames at 60 a second, from the start of the way in
SLIDE_OUT = 2.0             # metres to the right where the old one stops (the screen edge is 0.94 m from Link, the dark 0.73 m)
SLIDE_IN = 1.6              # metres to the left where the new character starts
# The slide's shape is a table (style_row): the two distances, whether the old character
# moves "together" with the new one (its frame = the slide count, so it waits for the
# new one to load) or leaves at once (its frame = SLIDE_OUT_FLAG), and for each of the
# two a fraction of the way per frame (STYLE_FRAMES values, STYLE_ONE = all of it,
# [0] = 0). snaildev compared six in game (2026-11-10: an exponential fling out with a
# cubic ease-out in; carousels, both moving as one strip once the new one has loaded,
# with ease-in-out, ease-out, CSS ease and linear; and this one) and chose this one,
# "a clear winner": the old one leaves at once with an ease-in, the new one comes in
# with an ease-out, 0.3 s each. The fling had "a slight stutter", the carousels wait
# for the load (0.6 s).
STYLE_FRAMES = 40
STYLE_ONE_BITS = 12         # fractions are 4.12 fixed point: steps of half a millimetre
STYLE_ONE = 1 << STYLE_ONE_BITS
STYLE_ROW = 12 + 4 * STYLE_FRAMES       # floats out / in distance, u8 together, pad, u16 out[], u16 in[]


def _style_table(frames, curve):
    assert 0 < frames < STYLE_FRAMES
    out = [min(STYLE_ONE, max(0, round(STYLE_ONE * curve(i / frames)))) if i < frames else STYLE_ONE
           for i in range(STYLE_FRAMES)]
    assert out[0] == 0 and all(b >= a for a, b in zip(out, out[1:]))
    return out


SLIDE_STYLE = {"together": False, "out": SLIDE_OUT, "in": SLIDE_IN,
               "out_frames": _style_table(18, lambda t: t * t),                 # ease-in (quadratic)
               "in_frames": _style_table(18, lambda t: 1 - (1 - t) ** 2)}       # ease-out


def style_row(st):
    assert len(st["out_frames"]) == len(st["in_frames"]) == STYLE_FRAMES
    row = struct.pack("<2fB3x", st["out"], st["in"], st["together"]) + \
        struct.pack("<%dH" % STYLE_FRAMES, *st["out_frames"]) + struct.pack("<%dH" % STYLE_FRAMES, *st["in_frames"])
    assert len(row) == STYLE_ROW
    return row


def slide_wait(st=None):
    """Frames the swap waits after starting the way in, before it drops the old character."""
    st = st or SLIDE_STYLE
    return 3 + max(max(i for i, f in enumerate(st[k]) if f < STYLE_ONE) + 1
                   for k in (("in_frames", "out_frames") if st["together"] else ("in_frames",)))


# The menu character's pose (snaildev, 2026-11-10): the + menu's equipment page stores the
# category and the name of the item under its cursor ([0x84e98e28] + 0x7c8 / + 0x6c8,
# written by 0x80a89ab0 on a hover) and nothing ever resets them. The menu character's
# AI turns him by it (ExecuteMenuActorRotate: a shield = ShieldRotate, his back),
# picks his animation by it (ExecuteSetPouchPlayerASBBParameter: a sword = the draw)
# and the manager picks the camera by it, so our page showed every character in the
# pose of whatever was hovered last. A tunic gives the neutral pose: category
# POSE_NEUTRAL. The page view patch writes that category while one of the − menu's
# pages shows (display types 2 and 3, where no character is seen, so that it is in
# place before ours wakes him, and our own page) and keeps the + menu's own in
# POSE_FLAG (category + 1; 0 = nothing kept), which it gives back the first time a +
# menu view shows (display type 0 off our page, or 1), unless the category was changed
# meanwhile. Only the category is written, not the item's name.
POSE_FLAG = "CharacterMenu_PoseCategory"
POSE_NEUTRAL = 4            # armour (live, 2026-11-10: category 4 with "Obj_Armor_Child_Normal"; 9 = a shield)
POUCH_HOVER = 0x84e98e28    # [this]: + 0x6c8 the hovered item's name (string), + 0x7c8 its category
POUCH_HOVER_CATEGORY = 0x7C8
# D-pad Left / Right on the Characters page: previous / next of these stored numbers,
# with the slide. `build.py --cycle` fills it from CYCLE_NAMES (the page without the picker).
CYCLE = []
# The full build: every swap is the slide, also for callers that ask for the plain one
# (the UI session's picker; see _swap_step_list).
SWAP_SLIDES = False
CYCLE_NAMES = ["Link", "Saria", "Mido", "Zelda"]
GAMEDATA_FLAGS = [  # (type, full name, entry); entries are appended to the GameDataList
    ("Int", CHARACTER_FLAG, {"DefaultValue": 0, "ResetTypeValue": 8, "SaveFileIndex": 0}),
    ("Bool", KEEP_CAMERA_FLAG, {"ResetTypeValue": 0, "SaveFileIndex": -1}),
    ("Bool", PAGE_VIEW_FLAG, {"ResetTypeValue": 0, "SaveFileIndex": -1}),
    ("Int", SLIDE_FLAG, {"DefaultValue": 0, "ResetTypeValue": 0, "SaveFileIndex": -1}),
    ("Int", VIEW_CHARACTER_FLAG, {"DefaultValue": 0, "ResetTypeValue": 0, "SaveFileIndex": -1}),
    ("Int", SLIDE_OUT_FLAG, {"DefaultValue": 0, "ResetTypeValue": 0, "SaveFileIndex": -1}),
    ("Int", POSE_FLAG, {"DefaultValue": 0, "ResetTypeValue": 0, "SaveFileIndex": -1}),
]
# Characters page composition (snaildev, 2026-11-09): Link on the right-third line,
# leaving the left side for UI. The Link view (display type 0) has the camera at
# (0.07, 1.15, 4.5) aiming at (0.07, 0.95, 0), fovy 20 degrees: half the screen width
# at Link is tan(10 deg) * 4.504 * 16/9 = 1.412 m, and Link (x = 0) sits at
# -0.07 / 1.412 = -0.05 half widths from the centre in the game's own view.
#   VIEW = "lens" (snaildev's idea; passed in game with offsets -1/6 and +1/6): the
#   camera stays where the game puts it and the picture is offset. The 8 bytes after
#   fovy in the camera block are the projection offset (x, y): an offset of o moves
#   the picture by -2 * o half widths (+1/6 put Link on the left third, -1/6 a
#   little left of the right third), so Link looks exactly as in the game's view.
#   VIEW = "camera" (passed in game first): the camera and its aim point move by
#   VIEW_SHIFT_X instead; Link is then seen from 7 degrees to the side.
# Tried and dropped: shifting the menu 3D layer's viewport from the 3D UI camera's
# AI with OneShotLayerViewport (213,0-1493,720): Link was not on the third (looked
# further away and tilted), and the + menu and the other 3D views stayed shifted.
VIEW = "lens"
LINK_SCREEN_X = 1.0 / 3     # where Link stands: half screen widths right of the centre
VIEW_HALF_WIDTH = 1.412     # metres, at Link
VIEW_CAMERA_X = 0.07
VIEW_OFFSET_X = -(LINK_SCREEN_X + VIEW_CAMERA_X / VIEW_HALF_WIDTH) / 2    # -0.1915
VIEW_SHIFT_X = -(LINK_SCREEN_X * VIEW_HALF_WIDTH + VIEW_CAMERA_X)         # -0.541
CHARACTERS = [
    {"name": "Link"},
    {"name": "Retro Link", "tag": "Retro", "models": {
        "Player_Child": {"assets": "N64 Young Link", "textures": ("Mt_Vest_Player_Child_", "Mt_Vest_Player_Retro_")},
        "Player_Adult": {"assets": "N64 Link", "textures": ("Mt_Angin_Acs_Player_Adult_", "Mt_Angin_Acs_Player_Retro_")},
    }},
]
PLAYER_MODELS = ["Player_Child", "Player_Adult"]  # the hook's "age" index 0 / 1
CHARACTER_BUTTONS = {"Y": 0, "X": 1}  # Characters page button -> character index
# Tried 2026-11-09: the same swap without hiding the 3D view (display 4) on D-pad
# Left/Right; it showed the same dark overlay during the swap, so it was dropped.
CHARACTER_BUTTONS_NO_HIDE = {}
ASSETS = os.path.join(ROOT, "assets", "models")


def add_page_screen(mod, sizes):
    """New row in the UI screen table: our page's 2D layer. No screen class
    (generic layout, like MenuBG_00); menu draw unit and sort key like the
    other − menu pages; loaded on demand like EventFade_01."""
    raw = open(os.path.join(ROMFS, UISCREEN), "rb").read()
    data = nxfmt.unzs(raw, UISCREEN)
    rows = byml.load(data)
    assert byml.dump(rows, byml.version_of(data)) == data, "UIScreen table round trip not exact"
    names = [r["Name"] for r in rows]
    assert names == sorted(names, key=str.encode) and PAGE_SCREEN not in names
    assert any(r.get("BodyLayoutFile") == PAGE_LAYOUT for r in rows)
    row = {"ArchiveCategory": ["Common"], "BodyLayoutFile": PAGE_LAYOUT, "DrawUnitId": 1,
           "IsHeapReverse": True, "Name": PAGE_SCREEN, "SortKey": 5000}
    rows.append(row)
    rows.sort(key=lambda r: r["Name"].encode())
    new = byml.dump(rows, byml.version_of(data))
    write(mod, UISCREEN, nxpack.zs_compress_like(new, raw), len(new), sizes)


def characters_page(mod):
    """Characters page left of Key Items in the − menu.

    Key Items L/ZL: Key Items' own exit animation (the one used towards
    Threads of Time) + "tab left" sound, and starts loading our screen -> Prev.
    MinusMenu routes Prev to CharacterMenu.module. The page opens its 2D
    screen with the InFromChallenge animation and shows the 3D Link with the
    + menu lighting (env "Pouch", display type 0). It never sets a camera
    state (0 and 2 turn the Link view black). R/ZR: exit animation, unload,
    -> Key Items (its "from Threads of Time" entry). B/− close the menu.
    Y/X pick the character (CHARACTER_BUTTONS, see character_swap).
    """
    sizes = SIZES
    add_page_screen(mod, sizes)

    # 1. ImportantMenu.module: after the ZR check (node 17), check L then ZL.
    rel = "Sequence/ImportantMenu.module.ainb"
    g = Graph(os.path.join(ROMFS, rel))
    outs = g.node(4, "SeqQueryControllerTrigMaskChecker")["Parameters"]["Outputs"]["Bool"]
    zr = g.node(17, "", "Element_BoolSelector")
    assert zr["Parameters"]["Inputs"]["Bool"][0]["Output Index"] == TRIG["ZR"]
    assert zr["Plugs"]["Child"][1] == {"Node Index": 16, "Name": "False"}
    g.node(16, "SeqExecuteGenericDummyAction")
    for k in ("L", "ZL"):
        assert outs[TRIG[k]]["Name"] == "Trig" + k
        outs[TRIG[k]]["Is Output"] = True
    close = g.node(45, "CloseAllMinusMenuContentScreen.module")  # template: IsCloseImportantToChallenge
    flags = {p["Name"]: p["Default Value"] for p in close["Parameters"]["Inputs"]["Bool"]}
    assert flags["IsCloseImportantToChallenge"] and sum(flags.values()) == 1, flags
    prev = g.add(t_state_end("Prev"))
    leave = g.add(t_simultaneous([g.add(close), g.add(t_sound("mc_TabToLeft")),
                                  g.add(t_load_screen(PAGE_SCREEN))], then=prev))
    end = g.add(t_end(leave))
    zl = g.add(t_bool_sel(4, TRIG["ZL"], end, 16))
    zr["Plugs"]["Child"][1]["Node Index"] = g.add(t_bool_sel(4, TRIG["L"], end, zl))
    # Main loop (node 3, runs after the entry): if our page's screen is still open
    # (overlap exit), wait for its fade-out and close + unload it.
    main = g.node(3, "", "Element_Simultaneous")
    assert [c["Node Index"] for c in main["Plugs"]["Child"]] == [65, 48, 7, 38]
    close_ours = g.add(t_sequential([g.add(t_wait_anim(PAGE_SCREEN, "InChallenge")),
                                     g.add(t_close_unload_screen(PAGE_SCREEN))], root=False))
    main["Plugs"]["Child"].append({"Node Index": g.add(t_screen_state_sel(PAGE_SCREEN, close_ours, g.add(
        vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction")))), "Name": ""})
    out = g.build()
    write(mod, rel, out, len(out), sizes)

    # 2. MinusMenu.module: both ImportantMenu nodes get Prev -> CharacterMenu.module;
    #    the page's Next -> ImportantMenu node 20 (IsFromChallengeMenu=true: plays
    #    InFromChallenge, camera 2 -> 3), Main -> close menu (4), Event -> 12, PouchMenu -> 24.
    rel = "Sequence/MinusMenu.module.ainb"
    g = Graph(os.path.join(ROMFS, rel))
    imp = [g.node(i, "ImportantMenu.module") for i in (17, 20)]
    assert [p["Default Value"] for p in g.node(20)["Parameters"]["Inputs"]["Bool"]] == [True]
    for i in (4, 12, 24):
        assert "Is Root Node" in g.node(i)["Flags"]

    def tr(target, name):
        return {"Node Index": target, "Transition Type": 0, "Update Post Calc": True, "Transition Name": name}

    page = copy.deepcopy(imp[0])
    page["Name"] = "CharacterMenu.module"
    page["Parameters"] = {"Inputs": {}, "Outputs": {}}
    page["Plugs"] = {"Transition": [tr(20, "Next"), tr(4, "Main"), tr(24, "PouchMenu"), tr(12, "Event")]}
    cm = g.add(page)
    for n in imp:
        assert all(p["Transition Name"] != "Prev" for p in n["Plugs"]["Transition"])
        n["Plugs"]["Transition"].append(tr(cm, "Prev"))
    g.d["Modules"].append({"Path": "CharacterMenu.module.ainb", "Category": "Sequence", "Instance Count": 1})
    out = g.build()
    write(mod, rel, out, len(out), sizes)

    # 3. CharacterMenu.module (new file).
    rel = "Sequence/CharacterMenu.module.ainb"
    assert not os.path.exists(os.path.join(ROMFS, rel))
    base = Graph(os.path.join(SEQ, "ImportantMenu.module.ainb"))
    g = Graph(like=base, filename="CharacterMenu.module")
    g.d["Blackboard ID"] = 69420  # signature value; no vanilla Sequence graph uses it
    S = PAGE_SCREEN
    root = g.add(t_sequential([], root=True))  # 0: entry, filled below
    mask = g.add(vanilla("ImportantMenu", 5, "SeqQueryControllerGetMask"))
    chk = g.add(vanilla("ImportantMenu", 4, "SeqQueryControllerTrigMaskChecker"))
    g.nodes[mask]["Plugs"]["Generic"][0]["Node Index"] = chk
    g.nodes[chk]["Queries"] = [mask]
    g.nodes[chk]["Parameters"]["Inputs"]["Pointer"][0]["Node Index"] = mask
    for o in g.nodes[chk]["Parameters"]["Outputs"]["Bool"]:
        o["Is Output"] = o["Name"] in ["TrigB", "TrigMinus", "TrigR", "TrigZR"] + \
            ["Trig" + b for b in list(CHARACTER_BUTTONS) + list(CHARACTER_BUTTONS_NO_HIDE) +
             (["Left", "Right"] if CYCLE else [])]
    closable = g.add(vanilla("ImportantMenu", 31, "SeqQueryIsClosableMinusMenuByBbutton"))
    dummy = g.add(vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))

    # B/−: straight to Main like Key Items; MinusMenuToMain closes our screen
    # together with the header and the rest (see close_page_with_menu)
    end_main = g.add(t_end(g.add(t_state_end("Main"))))
    # R/ZR: start our exit animation (as Key Items does towards Threads of Time) and
    # go to Key Items at once; Key Items' main loop closes + unloads our screen when
    # the animation has finished (see the ImportantMenu edit). The two fades overlap,
    # so there is no frame without a vignette (the old "wait, close, then Next" exit
    # blinked; camera states 4/5 on the exit made no difference, 2026-11-09).
    st_next = g.add(t_state_end("Next"))
    overlap = g.add(t_simultaneous([g.add(t_sound("mc_TabToRight")), g.add(t_stop_anim(S, "InFromChallenge")),
                                    g.add(t_play_anim(S, "InChallenge")),
                                    g.add(t_set_flag(PAGE_VIEW_FLAG, False))], then=st_next))
    end_next = g.add(t_end(overlap))

    # Y/X: character choice (after every vanilla-like input, so those keep priority)
    chain = dummy
    cycle = {}      # stored number -> its swap sequence (with the slide), filled once `main` exists
    if CYCLE:
        current = g.add(vanilla("ChangeGamePause", 17, "SeqQueryGetNonStructGameDataInt",
                                GameDataFullName=CHARACTER_FLAG))
        for value in CYCLE:
            cycle[value] = g.add(t_sequential([], root=True, same_frame=False))
        go = {value: g.add(t_end(seq)) for value, seq in cycle.items()}
        for button, step in (("Right", 1), ("Left", -1)):
            node = go[CYCLE[0]]                     # any other stored number: the first one
            for i in reversed(range(len(CYCLE))):
                node = g.add(t_compare_int(current, CYCLE[i], go[CYCLE[(i + step) % len(CYCLE)]], node))
            chain = g.add(t_bool_sel(chk, TRIG[button], node, chain))
    swaps = []
    buttons = [(b, v, True) for b, v in CHARACTER_BUTTONS.items()] +         [(b, v, False) for b, v in CHARACTER_BUTTONS_NO_HIDE.items()]
    for button, value, hide in reversed(buttons):
        swap = g.add(t_sequential([], root=True, same_frame=False))  # filled once `main` exists
        swaps.append((swap, value, hide))
        chain = g.add(t_bool_sel(chk, TRIG[button], g.add(t_end(swap)), chain))
    zr2 = g.add(t_bool_sel(chk, TRIG["ZR"], end_next, chain))
    r2 = g.add(t_bool_sel(chk, TRIG["R"], end_next, zr2))
    can_close = g.add(t_bool_sel(closable, 0, end_main, r2))  # not closable: go on to R
    minus2 = g.add(t_bool_sel(chk, TRIG["Minus"], can_close, r2))
    b2 = g.add(t_bool_sel(chk, TRIG["B"], can_close, minus2))
    main = g.add(t_simultaneous([b2], root=True))
    for value, seq in cycle.items():
        g.nodes[seq]["Plugs"] = _plugs(_swap_step_list(g, value, True, slide=True), then=main)
    for swap, value, hide in swaps:
        g.nodes[swap]["Plugs"] = _plugs(_swap_step_list(g, value, hide), then=main)

    # entry: open the 2D screen and play its in-animation (as Key Items does when
    # coming from Threads of Time; the "In" detail/guide/message animations held
    # at frame 0 = hidden), apply the + menu scene, then show Link.
    # The header preset PAGE_PRESET highlights our tab when the "Header four tabs"
    # exe patch is installed; without it the game ignores the unknown preset
    # (keeps the current highlight and footer).
    g.d["Modules"] = [m for m in base.d["Modules"] if m["Path"] == "OpenAppMenuOverlay.module.ainb"]
    assert len(g.d["Modules"]) == 1
    # used by character_swap_steps; one swap runs at a time (vanilla counts are
    # not node counts: ChallengeLog has 11 OpenAppMenuOverlay nodes, count 1)
    g.d["Modules"].append({"Path": "WaitEquip.module.ainb", "Category": "Sequence", "Instance Count": 1})
    enter = g.add(t_simultaneous(
        [g.add(t_open_screen(S)), g.add(t_stop_anim(S, "InChallenge")),
         g.add(t_stop_anim(S, "DetailIn")), g.add(t_stop_anim(S, "GuideIn")),
         g.add(t_stop_anim(S, "DemoMessageIn")), g.add(t_play_anim(S, "InFromChallenge")),
         g.add(t_overlay(PAGE_PRESET)), g.add(t_env("Pouch")),
         g.add(t_set_flag(PAGE_VIEW_FLAG, True))] +
        ([g.add(t_set_int(SLIDE_FLAG, 0))] if CYCLE else []), root=False))
    g.nodes[root]["Plugs"] = _plugs([enter, g.add(t_display(0))], then=main)
    g.d["Commands"] = [{"Name": "Root", "GUID": guid("CharacterMenu.module:Root"), "Root Node Index": root}]
    out = g.build()
    write(mod, rel, out, len(out), sizes)

    close_page_with_menu(mod, sizes)


def character_swap_steps(g, value, hide=True):
    """The character swap as ONE child node for any sequence (the UI sandbox uses
    it): a nested sequence that runs _swap_step_list one step per frame, whatever
    the caller's own sequence does. The steps need the frames: GameData sets are
    queued (the keep-camera flag must be applied before the delete), and the new
    Link can only be created once the old one is gone."""
    return [g.add(t_sequential(_swap_step_list(g, value, hide), root=False, same_frame=False))]


def _swap_step_list(g, value, hide=True, slide=False):
    """Children of the sequence a character button runs on the Characters page
    (that sequence must have same_frame=False: one step per frame):
    store the choice, then rebuild the menu's 3D Link the way the game builds it
    when an area loads (LoadBancImpl: delete, wait, create the child or adult
    one, give him his equipment; MinusMenuToMain: WaitEquip), hidden meanwhile.
    The new pouch Link loads its model through the model hook
    (character_models), which reads the stored choice.

    The camera: the game's "delete the menu Link" (0x80c35c30) also deletes the
    3D UI camera, and a camera created while the menu is open only becomes the
    active view once the menu pause ends (seen 2026-11-09: Link drawn from a
    default camera, boots at the top of the screen, fixed by closing and
    reopening the menu or by a door). So KEEP_CAMERA_FLAG is set around the
    delete and the exe patch (keep_camera_hook) leaves the camera alone; it
    takes its numbers from the menu manager, not from the Link actor. If the
    camera is gone anyway (Character models mod off), it is re-created like
    LoadBancImpl does (if the area uses one), which shows after a menu reopen."""
    is_child = g.add(vanilla("LoadBancImpl", 43, "SeqQueryGetNonStructGameDataBool"))
    assert g.nodes[is_child]["Parameters"]["Inputs"]["String"][0]["Default Value"] == "Sequence_IsChildhood"
    create_child = g.add(vanilla("LoadBancImpl", 41, "SeqOneShotCreatePouchPlayer"))
    create_adult = g.add(vanilla("LoadBancImpl", 44, "SeqOneShotCreatePouchPlayer"))
    wait_actor = lambda: g.add(vanilla("LoadBancImpl", 0, "SeqExecuteWaitCreateActor"))  # noqa: E731
    # LoadBancImpl 46-48: create the camera if the area's banc parameter says so
    banc = g.add(vanilla("LoadBancImpl", 46, "SeqQueryGetCurrentGameBancParam"))
    uses_cam = vanilla("LoadBancImpl", 47, "SeqQueryGetBoolFromGameBancParam")
    assert uses_cam["Parameters"]["Inputs"]["String"][0]["Default Value"] == "IsUse3DUICameraActor"
    assert uses_cam["Queries"] == [46] and uses_cam["Parameters"]["Inputs"]["Pointer"][0]["Node Index"] == 46
    uses_cam["Queries"] = [banc]
    uses_cam["Parameters"]["Inputs"]["Pointer"][0]["Node Index"] = banc
    uses_cam = g.add(uses_cam)
    assert g.nodes[banc]["Plugs"]["Generic"] == [{"Node Index": 47, "Name": "GameBancParam"}]
    g.nodes[banc]["Plugs"]["Generic"][0]["Node Index"] = uses_cam
    create_cam = g.add(vanilla("LoadBancImpl", 45, "SeqOneShotCreateUICamera"))
    no_cam = g.add(vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))
    keep_cam = lambda on: g.add(t_set_flag(KEEP_CAMERA_FLAG, on))  # noqa: E731
    # One step per frame (the swap's sequence has same_frame=False), and the keep
    # flag stays set until Link is shown again: with set / delete / clear in one
    # frame the camera was still deleted (2026-11-09, live: new camera actor and id
    # after a swap, patch and flag present), so the delete's effect evidently
    # does not happen in node order within the frame.
    def slide_steps(store):
        """The crossfade (see SLIDE_FLAG): no hiding, the old character stays until the end."""
        delete = lambda: g.add(vanilla("LoadBancImpl", 65, "SeqOneShotDeletePouchPlayer"))  # noqa: E731
        return [
            # both flags in one frame, the delete in the next: a queued set is there one frame
            # later (the plain swap without hiding: keep flag, then the delete, 2026-11-09)
            g.add(t_simultaneous([g.add(t_add_int(SLIDE_FLAG, SLIDE_STASH)),    # 0 -> SLIDE_STASH
                                  keep_cam(True)], root=False)),
            delete(),                                           # patched: slot 0 is parked in STASH_SLOT
        ] + ([g.add(t_set_int(CHARACTER_FLAG, value)), g.add(t_wait_frames(1))] if store else []) + [
            g.add(t_bool_sel(is_child, 0, create_child, create_adult)),
            wait_actor(),
            g.add(vanilla("LoadBancImpl", 69, "SeqSyncPlayerEquipmentAndGameData")),
            g.add(vanilla("MinusMenuToMain", 1, "WaitEquip.module")),
            g.add(t_display(0)),                # wakes and shows slot 0; the view patch holds it hidden
            g.add(t_add_int(SLIDE_FLAG, 1 - SLIDE_STASH)),      # -> 1: the way in starts (its hold was applied
            g.add(t_wait_frames(slide_wait())),                 # on the actor's update before the patch shows it)
            delete(),                                           # patched: deletes STASH_SLOT; the patch ends the transition
            g.add(t_wait_frames(1)),
            keep_cam(False),
        ]

    if slide:
        return slide_steps(True)
    if SWAP_SLIDES:
        # as the plain list for its callers: the store first (the picker replaces it by one per
        # character and runs it before the rest), then everything else as one step
        return [g.add(vanilla("AfterEvent", 33, "SeqOneShotSetNonStructGameDataInt",
                              Value=value, GameDataFullName=CHARACTER_FLAG)),
                g.add(t_sequential(slide_steps(False), root=False, same_frame=False))]
    return [
        g.add(vanilla("AfterEvent", 33, "SeqOneShotSetNonStructGameDataInt",
                      Value=value, GameDataFullName=CHARACTER_FLAG)),
        keep_cam(True),
    ] + ([g.add(t_display(4))] if hide else []) + [
        g.add(vanilla("LoadBancImpl", 65, "SeqOneShotDeletePouchPlayer")),
        wait_actor(),
        g.add(t_bool_sel(is_child, 0, create_child, create_adult)),
        wait_actor(),
        g.add(vanilla("LoadBancImpl", 69, "SeqSyncPlayerEquipmentAndGameData")),
        g.add(vanilla("MinusMenuToMain", 1, "WaitEquip.module")),
        g.add(t_bool_sel(uses_cam, 0, create_cam, no_cam)),  # no-op while the camera exists
        wait_actor(),
        g.add(t_display(0)),
        keep_cam(False),
    ]


def close_page_with_menu(mod, sizes):
    """MinusMenuToMain.module: in the step that closes the menu screens together
    with the pause background (node 3, the CloseScreenWithPauseMenuBG child of
    PlusMinusPauseOff, same frame as the header), also close + unload our
    screen when it is open."""
    rel = "Sequence/MinusMenuToMain.module.ainb"
    g = Graph(os.path.join(ROMFS, rel))
    step = g.node(3, "", "Element_Simultaneous")
    assert g.node(2, "PlusMinusPauseOff.module")["Plugs"]["Child"] == \
        [{"Node Index": 3, "Name": "CloseScreenWithPauseMenuBG"}]
    assert g.node(4, "CloseAllMinusMenuContentScreen.module")
    close = g.add(t_close_unload_screen(PAGE_SCREEN))
    idle = g.add(vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))
    step["Plugs"]["Child"].append({"Node Index": g.add(t_screen_state_sel(PAGE_SCREEN, close, idle)), "Name": ""})
    # same step as display type 5 (3D view hidden): end the Characters page camera shift
    assert any(g.node(c["Node Index"]).get("Name") == "SeqOneShotSetPouchActorDisplayType" for c in step["Plugs"]["Child"])
    step["Plugs"]["Child"].append({"Node Index": g.add(t_set_flag(PAGE_VIEW_FLAG, False)), "Name": ""})
    out = g.build()
    write(mod, rel, out, len(out), sizes)


# ---------------------------------------------------------------- exe patches

EXE_PATCHES = {}  # mod -> [(start, end)]; all our .ips files are applied together


def write_ips(mod, p):
    for other, ranges in EXE_PATCHES.items():
        for a0, a1 in p.ranges():
            for b0, b1 in ranges:
                assert a1 <= b0 or b1 <= a0, "%s and %s patch the same bytes at %x" % (mod, other, a0)
    EXE_PATCHES[mod] = p.ranges()
    out = os.path.join(BUILD, mod, "exefs", exepatch.BUILD_ID + ".ips")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(p.ips32())
    with open(os.path.join(BUILD, mod + ".listing.txt"), "w") as f:
        f.write(p.listing())
    print("  exefs/%s.ips  (%d patches, cave %x-%x)" % (exepatch.BUILD_ID, len(p.patches),
                                                        p.cave_start, p.cave_ptr))


# ---------------------------------------------------------------- four-tab − header

# UIMenuOverlayScreen (header/footer of the + and − menus), addresses in image.bin:
SET_PRESET_MAP = 0x80a88f0c   # in setPreset (0x80a88a40): "cmp w1, #0x13" of the preset -> category map
SET_CATEGORY_STORE = 0x80a88ed0  # "str w8, [x19, #0x358]" then footer + header rebuild
PRESET_IN_RANGE = 0x80a88f14  # next instruction after the map's range check
PRESET_UNKNOWN = 0x80a88ed4   # where unknown presets go (no category change)
HEADER_MINUS_PATH = 0x80a89498  # header builder (0x80a89220): start of the − menu branch
SLOT_SET_CATEGORY = 0x80a8ab50  # (slot, category, enabled): label + − / + style
SLOT_HIGHLIGHT = 0x802ecf60     # (slot, is_current, animate)
SLOT_SHOW = 0x80228350          # (slot, 0)


def header_four_tabs(mod):
    """− menu header with four tabs: Characters, Key Items, Threads of Time, Map.

    Vanilla: the header builder fills the 4 Pa_Category slots for the + menu
    (Collect 0, Equipment 3, Item 4, System 5) or for the − menu (Important 6,
    Challenge 1, Map 2, and hides slot 3). Category labels are the category
    enum names looked up in LayoutMsg/MenuOverlay_00; − style (anim "Type")
    is for categories in mask 0x46. Patches:
      1. − branch -> code cave: slot 0 = category 7 (always enabled), then
         Key Items / Threads of Time / Map in slots 1-3 with their vanilla
         enabled checks; all four shown.
      2. category > 6 label: "" -> "Characters" (existing string in .rodata),
         plus a "Characters" message in MenuOverlay_00 (US/EU English).
      3. − style mask 0x46 -> 0xc6 and its range check 6 -> 7.
      4. preset 1337 -> category 7 (the Characters page opens the overlay with it).
    """
    p = exepatch.Patcher()
    img = p.img
    label_addr = img.strings(PAGE_LABEL)
    assert len(label_addr) == 1 and label_addr[0] >= 0x83c67000, label_addr  # one copy, in .rodata
    label_addr = label_addr[0]

    # 2. label of out-of-range categories
    p.asm(0x80a8ab8c, "adrp x8, #%#x; add x8, x8, #%#x" % (label_addr & ~0xFFF, label_addr & 0xFFF),
          expect="adrp x8, #0x83cfd000; add x8, x8, #0xd43")
    assert img.cstr(0x83cfdd43) == ""
    # 3. − style for category 7 too
    p.asm(0x80a8ac20, "cmp w20, #7", expect="cmp w20, #6")
    p.asm(0x80a8ac2c, "mov w9, #0xc6", expect="mov w9, #0x46")

    # 4. preset 20 -> category 7
    p.cave("preset_cave", """
        cmp w21, #0x13
        b.hi preset_high
        b #%#x
    preset_high:
        cmp w21, #%d
        b.eq preset_ours
        b #%#x
    preset_ours:
        mov w8, #%d
        b #%#x
    """ % (PRESET_IN_RANGE, PAGE_PRESET, PRESET_UNKNOWN, PAGE_CATEGORY, SET_CATEGORY_STORE))
    p.asm(SET_PRESET_MAP, "b {preset_cave}", expect="cmp w1, #0x13")
    assert img.dis(SET_PRESET_MAP - 4, 3) == [
        "80a88f08: mov      x21, x1", "80a88f0c: cmp      w1, #0x13", "80a88f10: b.hi     #0x80a88ed4"]

    # 1. − branch with four slots
    def slot(index, category, enabled_reg):
        """setCategory + highlight + show for slot `index` (bounds-checked like vanilla)."""
        sel = "ldr w9, [x19, #0x328]\n cmp w9, #%d\n mov w21, #%d\n csel x9, x21, xzr, hi\n" \
              " ldr x10, [x19, #0x330]\n ldr x0, [x10, x9]\n" % (index, index * 8) if index else \
              "ldr x10, [x19, #0x330]\n ldr x0, [x10]\n"
        return """
        and w2, {en}, #1
        mov w1, #{cat}
        {sel}
        bl #{set:#x}
        ldr w8, [x19, #0x358]
        mov w2, w20
        cmp w8, #{cat}
        cset w1, eq
        {sel}
        bl #{hi:#x}
        mov w1, wzr
        {sel}
        bl #{show:#x}
        """.format(en=enabled_reg, cat=category, sel=sel, set=SLOT_SET_CATEGORY, hi=SLOT_HIGHLIGHT,
                   show=SLOT_SHOW)

    p.cave("minus_header_cave", """
        mov w12, #1
        """ + slot(0, PAGE_CATEGORY, "w12") + """
        adrp x8, #0x84c9e000
        mov w1, #4
        ldr x8, [x8, #0xd0]
        ldr x0, [x8]
        bl #0x80a8f690
        ldr w22, [x0, #8]
        cmp w22, #1
        b.lt ki_none
        mov x21, x0
        mov x23, xzr
    ki_loop:
        ldr x8, [x21, #0x10]
        ldr x0, [x8, x23, lsl #3]
        bl #0x8002a520
        mov w1, wzr
        bl #0x80d666a0
        bl #0x8002a4c0
        bl #0x801a1160
        tbnz w0, #0, ki_found
        add x23, x23, #1
        cmp x22, x23
        b.ne ki_loop
    ki_none:
        mov w23, wzr
        b ki_done
    ki_found:
        ldrb w23, [x19, #0x35d]
        eor w23, w23, #1
    ki_done:
        """ + slot(1, 6, "w23") + """
        adrp x8, #0x84e9c000
        add x8, x8, #0xfa8
        ldr x8, [x8]
        ldr x0, [x8, #0x4f8]
        bl #0x833a5f50
        ldrb w8, [x19, #0x35e]
        and w8, w0, w8
        eor w23, w0, w8
        """ + slot(2, 1, "w23") + """
        bl #0x83406710
        mov w23, w0
        """ + slot(3, 2, "w23") + """
        ldp x20, x19, [sp, #0x40]
        ldp x22, x21, [sp, #0x30]
        ldr x23, [sp, #0x20]
        ldp x29, x30, [sp, #0x10]
        ldp d9, d8, [sp], #0x50
        retab
    """)
    p.asm(HEADER_MINUS_PATH, "b {minus_header_cave}", expect="adrp x8, #0x84c9e000")
    # the vanilla − branch we replace (entry checked above; the rest becomes dead code)
    assert img.dis(0x80a894f8, 3) == [
        "80a894f8: mov      w1, #6", "80a894fc: ldr      x0, [x9]", "80a89500: bl       #0x80a8ab50"]
    assert img.dis(0x80a8965c, 1) == ["80a8965c: b        #0x802edef0"]
    assert img.dis(0x80a89410, 6)[-1] == "80a89424: retab    "

    # 5. tab sound set: setPreset writes SLink property 8 from a per-preset table
    #    (0x83f3669c: − pages 2, + pages 1, System 3); presets > 19 got 1 (+ sounds).
    #    Give preset 20 the − set like Key Items.
    p.cave("sound_cave", """
        cmp w21, #%d
        b.ne sound_other
        mov w2, #2
        b #0x80a88c08
    sound_other:
        cmp w21, #0x13
        b.hi sound_high
        b #0x80a88eec
    sound_high:
        b #0x80a88c04
    """ % PAGE_PRESET)
    assert img.dis(0x80a88ee4, 7) == [
        "80a88ee4: cmp      w21, #0x13", "80a88ee8: b.hi     #0x80a88c04", "80a88eec: and      x8, x21, #0x1f",
        "80a88ef0: adrp     x9, #0x83f36000", "80a88ef4: add      x9, x9, #0x69c",
        "80a88ef8: ldr      w2, [x9, x8, lsl #2]", "80a88efc: b        #0x80a88c08"]
    assert struct.unpack_from("<10I", img.data, 0x83f3669c - img.base) == (2, 2, 2, 2, 1, 1, 1, 1, 1, 3)
    p.asm(0x80a88ee4, "b {sound_cave}", expect="cmp w21, #0x13")

    write_ips(mod, p)

    # Layout edits (same size, in place). The UI layout archives store every layout 4
    # times under the same name (identical copies) and the game uses more than the
    # first, so each edited layout is written into all of its entries.
    rel = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs"
    raw = open(os.path.join(ROMFS, rel), "rb").read()
    sarc = bytearray(nxfmt.unzs(raw, rel))

    def entry(name):
        s0, e0 = nxpack.sarc_file_range(sarc, name)
        return bytearray(sarc[s0:e0])

    def expect(data, off, fmt, value):
        have = struct.unpack_from(fmt, data, off)
        assert list(have) == list(value), (hex(off), have, value)

    # Header layout: the − menu layout is the end of animation PlusToMinus, whose
    # constants put tabs 0-2 at x -520, 0, 520 and hide tab 3 (visibility 0). Use the
    # + menu positions (-690, -230, 230; tab 3 stays at its base x 690), show tab 3.
    ov = entry("MenuOverlay_00.buil")
    layout = buil.Buil(ov)
    _, targets, vp, consts = layout.anim("PlusToMinus")
    by_const = {t["const"]: (t["pane"], t["group"], t["prop"]) for t in targets if t["const"] is not None}
    assert consts == [1.0, -520.0, 0.0, 520.0, 0.0, 1.0, 1.0, 1.0, 1.0], consts
    assert [by_const[i] for i in (1, 2, 3, 4)] == [(8, 1, 0xE), (9, 1, 0xE), (10, 1, 0xE), (11, 2, 0)], by_const
    _, plus_targets, _, plus_consts = layout.anim("MinusToPlus")
    assert plus_consts[1:5] == [-690.0, -230.0, 230.0, 1.0], plus_consts
    struct.pack_into("<4f", ov, vp + 4, -690.0, -230.0, 230.0, 1.0)

    # Tab highlight shapes. Each tab (part PaMenuCategory_00) has P_MinusMenuBg_00
    # (pane 4, orange material, textures MenuHeaderSelect_04-06: left/middle/right of
    # a 3-tab bar) and P_PlusMenuBg_00 (pane 5, beige material, MenuHeaderSelect_00-03:
    # 4-tab bar); "Type" shows one of them, and the state animations
    # MinusMenuHeaderState / PlusMenuHeaderState (texture pattern, frame = shape index)
    # pick the shape. The textures are masks (colour comes from the material), so give
    # the − background the + shapes: its material's texture list -> the + list,
    # the − state animation 0..3 like the + one, and tab 3's − frame 0 -> 3.
    #   header layout: per-tab part settings, tab 3 (record at 0x28b8)
    expect(ov, 0x28b8, "<Q", [layout.string_offset("MinusMenuHeaderState")])
    expect(ov, 0x28c0, "<f", [0.0])
    expect(ov, 0x28c8, "<Q", [layout.string_offset("PlusMenuHeaderState")])
    expect(ov, 0x28d0, "<f", [3.0])
    struct.pack_into("<f", ov, 0x28c0, 3.0)
    #   tab part
    pt = entry("PaMenuCategory_00.buil")
    part = buil.Buil(pt)
    names = lambda lst, n: [part.cstr(part.u64(lst + 8 * i)) for i in range(n)]
    assert names(0x7d0, 3) == ["MenuHeaderSelect_04", "MenuHeaderSelect_05", "MenuHeaderSelect_06"]
    assert names(0x860, 4) == ["MenuHeaderSelect_00", "MenuHeaderSelect_01", "MenuHeaderSelect_02",
                               "MenuHeaderSelect_03"]
    expect(pt, 0x7a0, "<QII", [0x7d0, 3, 8])   # − material: texture list
    expect(pt, 0x830, "<QII", [0x860, 4, 8])   # + material: texture list
    _, mt, _, _ = part.anim("MinusMenuHeaderState")
    assert [(t["pane"], t["group"], t["curve"]) for t in mt] == [(4, 28, 0)], mt
    expect(pt, 0x2ad0, "<Q", [part.string_offset("MinusMenuHeaderState")])
    expect(pt, 0x2ac8, "<I", [2])              # animation length (frames)
    expect(pt, 0x2b2c, "<I", [2])
    expect(pt, 0x2b7c, "<f", [2.0])            # curve end frame
    expect(pt, 0x2b80, "<QII", [0x2b90, 5, 4])  # key frames
    expect(pt, 0x2b90, "<5f", [0, 1, 1, 2, 2])
    expect(pt, 0x2bb0, "<QII", [0x2bc0, 10, 4])  # key (value, slope) pairs
    expect(pt, 0x2bc0, "<10f", [0, 0, 0, 0, 1, 0, 1, 0, 2, 0])
    expect(pt, 0x2910, "<I", [3])              # the + animation, for comparison
    # The − material needs its OWN 4-entry list: the game relocates pointers by
    # walking every reference, so a list shared by two materials gets relocated twice
    # (seen in memory: entries 2*base+offset) and both menus lost their highlights.
    # No free room in place, so append the list after the string section (the last
    # section), and grow that section and the file size to cover it.
    expect(pt, 0x08, "<I", [len(pt)])                  # BFFH file size
    expect(pt, 0xd0, "<4sII", [b"BFSI", 0x3be0, len(pt) - 0x3be0])  # StringPool section
    assert pt[0xe8:0xf2] == b"StringPool"
    plus_list = bytes(pt[0x860:0x880])                 # 4 name pointers (file offsets)
    new_list = (len(pt) + 7) & ~7
    pt += b"\0" * (new_list - len(pt)) + plus_list
    struct.pack_into("<I", pt, 0x08, len(pt))
    struct.pack_into("<I", pt, 0xd8, len(pt) - 0x3be0)
    struct.pack_into("<QI", pt, 0x7a0, new_list, 4)
    struct.pack_into("<I", pt, 0x2ac8, 3)
    struct.pack_into("<I", pt, 0x2b2c, 3)
    struct.pack_into("<f", pt, 0x2b7c, 3.0)
    struct.pack_into("<5f", pt, 0x2b90, 0, 1, 2, 3, 3)
    struct.pack_into("<10f", pt, 0x2bc0, 0, 0, 1, 0, 2, 0, 3, 0, 3, 0)

    assert nxpack.sarc_patch_all(sarc, "MenuOverlay_00.buil", ov) == 4
    assert len(nxpack.sarc_file_ranges(sarc, "PaMenuCategory_00.buil")) == 4
    sarc = nxpack.sarc_replace(bytes(sarc), {"PaMenuCategory_00.buil": bytes(pt)}, align=16)  # grew: repack
    write(mod, rel, nxpack.zs_compress_like(sarc, raw), len(sarc))

    # header text for the new label
    for lang in ("USen", "EUen"):
        rel = "Mals/%s.Product.110.sarc.zs" % lang
        raw = open(os.path.join(ROMFS, rel), "rb").read()
        sarc = nxfmt.unzs(raw, rel)
        name = "LayoutMsg/MenuOverlay_00.msbt"
        m = msbt.Msbt(nxfmt.sarc_files(sarc)[name])
        m.add(PAGE_LABEL, "Characters")
        new = nxpack.sarc_replace(sarc, {name: m.to_bytes()})
        write(mod, rel, nxpack.zs_compress_like(new, raw), len(new))


# ---------------------------------------------------------------- character models

MODEL_PATH_FMT = 0x83cafff3     # "Model/%s.%s.bfres" (model project name, fmdb name)
STRING_FORMAT = 0x80062b70      # printf into a FixedSafeString: (string, format, ...)
# Every call that formats MODEL_PATH_FMT (12 references, 11 calls; found again on
# each build and compared, so a game update fails the build).
MODEL_FORMAT_CALLS = [0x80034810, 0x801a2410, 0x80383a80, 0x80972840, 0x831dd3fc, 0x831de438,
                      0x83b18a44, 0x83b197a8, 0x83b35b14, 0x83b35ec0, 0x83b3a070]
# GameData manager: the game reads it as [[GOT entry]] (e.g. 0x8019de20 for an Int
# flag). Int flags: hash map at +0xe0 ({u32 hash, u32 record}, open addressing from
# hash % capacity, capacity at +0xec, free slot = hash 0), records at +0xd8 (count
# +0xd0, 0x28 bytes each, current value at +0x1c).
GAMEDATA_MGR_GOT = 0x84c9e180


def model_format_calls(img):
    """The first call after each reference to the format string (following an
    unconditional branch on the way, as at 0x801a2318); each must be STRING_FORMAT."""
    calls = set()
    for ref in img.xrefs(MODEL_PATH_FMT):
        at, found = ref, None
        for _ in range(3):
            for line in img.dis(at, 40):
                op = line.split(":", 1)[1].split()
                if op[0] in ("bl", "b"):
                    break
            if op[0] == "bl":
                found = line
                break
            at = int(op[1].lstrip("#"), 16)
        assert found and found.endswith("#%#x" % STRING_FORMAT), (hex(ref), found)
        calls.add(int(found.split(":")[0], 16))
    return sorted(calls)


def character_models(mod):
    """The other characters' player models, and the switch the menu drives.

    1. GameDataList: an Int flag CHARACTER_FLAG (saved in progress.sav, reset with
       the rest of a save's progress, 0 = the game's Link) and a Bool
       KEEP_CAMERA_FLAG (not saved), plus the save size bookkeeping (see
       tools/gamedata.py).
    2. Each character's model under its own name, Model/<Player_X>_<tag>.<Player_X>
       .bfres, with its own copies of the textures the model mod replaced (renamed
       inside the model and the texture files), so the game's Link stays intact.
    3. Exe patch: each call that formats "Model/%s.%s.bfres" goes through a hook
       that, when the project name is Player_Child / Player_Adult and the flag
       names a character that has that model, formats Player_X_<tag> instead.
       Everything else passes through unchanged.
    """
    # 1. the flags
    rel = gamedata.PATH
    raw = open(os.path.join(ROMFS, rel), "rb").read()
    data = nxfmt.unzs(raw, rel)
    old = byml.load(data)
    taken = {e["Hash"] for t, lst in old["Data"].items() if t != "Bool64bitKey" for e in lst}
    p = bympatch.Patch(data)
    expect = copy.deepcopy(old)
    grow = []
    per_kind = {}  # kind -> (template index, [values]); one array move per kind
    for kind, name, entry in GAMEDATA_FLAGS:
        h = gamedata.murmur3_32(name)
        assert h not in taken
        taken.add(h)
        values = dict(entry, Hash=h)
        full = dict(values)
        if kind == "Bool":  # DefaultValue false comes from the template (bool values are not ints)
            full["DefaultValue"] = False
        tmpl = next(i for i, e in enumerate(old["Data"][kind]) if set(e) == set(full) and
                    e["DefaultValue"] == full["DefaultValue"])
        per_kind.setdefault(kind, (tmpl, []))[1].append(values)
        expect["Data"][kind].append(full)
        grow += [["MetaData", "AllDataSaveOffset"], ["MetaData", "AllDataSaveSize"]]
        if entry["SaveFileIndex"] >= 0:
            f = entry["SaveFileIndex"]
            grow += [["MetaData", "SaveDataOffsetPos", f], ["MetaData", "SaveDataSize", f]]
    for kind, (tmpl, values_list) in per_kind.items():
        p.append_dicts(["Data", kind], tmpl, values_list)
    for path in grow:
        p.add_int(path, 8)
        node = expect
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] += 8
    new = p.bytes()
    assert byml.load(new) == expect, "GameDataList edit check failed"
    write(mod, rel, nxpack.zs_compress_like(new, raw), len(new), SIZES)

    # 2. models and textures
    for ch in CHARACTERS[1:]:
        for base, m in ch["models"].items():
            assert base in PLAYER_MODELS
            src = os.path.join(ASSETS, m["assets"], "romfs")
            old_prefix, new_prefix = (s.encode() for s in m["textures"])
            assert len(old_prefix) == len(new_prefix)
            textures = sorted(f for f in os.listdir(os.path.join(src, "Tex")) if f.encode().startswith(old_prefix))
            rel = "Model/%s.%s.bfres.zs" % (base, base)
            raw = open(os.path.join(src, rel), "rb").read()
            model = nxfmt.unzs(raw, rel)
            assert model.count(old_prefix) == len(textures) > 0, (base, model.count(old_prefix), textures)
            assert new_prefix not in model
            model = model.replace(old_prefix, new_prefix)
            write(mod, "Model/%s_%s.%s.bfres.zs" % (base, ch["tag"], base), nxpack.zs_compress_like(model, raw),
                  len(model), SIZES, size=stock_table().get(rel[:-3]))  # as much as the game's model
            for f in textures:
                raw = open(os.path.join(src, "Tex", f), "rb").read()
                tex = nxfmt.unzs(raw, f)
                assert tex.count(old_prefix) == 1, f
                write(mod, "Tex/" + (new_prefix + f.encode()[len(old_prefix):]).decode(),
                      nxpack.zs_compress_like(tex.replace(old_prefix, new_prefix), raw), len(tex), SIZES)

    # 3. the hooks
    h = gamedata.murmur3_32(CHARACTER_FLAG)
    p = exepatch.Patcher(cave=exepatch.CAVE_MODELS)
    img = p.img
    assert img.cstr(MODEL_PATH_FMT) == "Model/%s.%s.bfres"
    calls = model_format_calls(img)
    assert calls == MODEL_FORMAT_CALLS, [hex(c) for c in calls]
    base_names = p.rodata("base_names", b"".join(n.encode().ljust(16, b"\0") for n in PLAYER_MODELS))
    table = b""
    for ch in CHARACTERS[1:]:  # 32-byte slot per (character - 1, age)
        for base in PLAYER_MODELS:
            name = ("%s_%s" % (base, ch["tag"])).encode() if base in ch["models"] else b""
            assert len(name) < 32
            table += name.ljust(32, b"\0")
    model_names = p.rodata("model_names", table)
    assert base_names >> 12 == model_names >> 12  # one adrp page for both
    got = GAMEDATA_MGR_GOT
    p.cave("model_hook", """
        adrp x9, #{fmt_page:#x}
        add x9, x9, #{fmt_off:#x}
        cmp x1, x9
        b.ne mh_out
        cbz x2, mh_out
        adrp x10, #{names_page:#x}
        add x10, x10, #{base_off:#x}
        mov w13, wzr
    mh_try:
        mov x11, xzr
    mh_cmp:
        ldrb w12, [x2, x11]
        ldrb w14, [x10, x11]
        cmp w12, w14
        b.ne mh_next
        add x11, x11, #1
        cbnz w12, mh_cmp
        b mh_match
    mh_next:
        cbnz w13, mh_out
        add x10, x10, #16
        mov w13, #1
        b mh_try
    mh_match:
        adrp x9, #{got_page:#x}
        ldr x9, [x9, #{got_off:#x}]
        ldr x9, [x9]
        cbz x9, mh_out
        ldr x10, [x9, #0xe0]
        cbz x10, mh_out
        ldr w11, [x9, #0xec]
        cbz w11, mh_out
        movz w12, #{hlo:#x}
        movk w12, #{hhi:#x}, lsl #16
        udiv w14, w12, w11
        msub w14, w14, w11, w12
        mov w15, w14
    mh_probe:
        add x16, x10, w15, uxtw #3
        ldr w17, [x16]
        cmp w17, w12
        b.eq mh_found
        cbz w17, mh_out
        add w15, w15, #1
        cmp w15, w11
        csel w15, wzr, w15, hs
        cmp w15, w14
        b.ne mh_probe
        b mh_out
    mh_found:
        ldr w16, [x16, #4]
        ldr w17, [x9, #0xd0]
        cmp w16, w17
        b.hs mh_out
        ldr x17, [x9, #0xd8]
        mov w15, #0x28
        umaddl x17, w16, w15, x17
        ldr w16, [x17, #0x1c]
        sub w16, w16, #1
        cmp w16, #{others}
        b.hs mh_out
        adrp x10, #{names_page:#x}
        add x10, x10, #{model_off:#x}
        lsl w16, w16, #6
        add x10, x10, w16, uxtw
        lsl w13, w13, #5
        add x10, x10, w13, uxtw
        ldrb w11, [x10]
        cbz w11, mh_out
        mov x2, x10
    mh_out:
        b #{fmt_fn:#x}
    """.format(fmt_page=MODEL_PATH_FMT & ~0xFFF, fmt_off=MODEL_PATH_FMT & 0xFFF, got_page=got & ~0xFFF,
               got_off=got & 0xFFF, hlo=h & 0xFFFF, hhi=h >> 16, others=len(CHARACTERS) - 1,
               fmt_fn=STRING_FORMAT, names_page=base_names & ~0xFFF, base_off=base_names & 0xFFF,
               model_off=model_names & 0xFFF))
    for site in calls:
        p.asm(site, "bl {model_hook}", expect="bl #%#x" % STRING_FORMAT)
    check_model_hook(p)
    keep_camera_hook(p)
    view_hook(p)
    write_ips(mod, p)


# 3D UI camera update (0x8289d110, ExecuteUpdate3DUICamera): loads the menu
# manager's camera block for the current display type (+0x22c + 0x38 * type:
# position, aim point, up, near, far, fovy, 8 more bytes) into s8 s10 s9 / s0 s15
# s14 / s13 s12 s11 / s5 s4 s3 / x20, then jumps on (0x8289d1b8); the settled path
# (0x8289d454) stores them into the camera (+0x60.., x20 at +0xcc after the aspect).
CAMERA_BLOCK_LOADED = 0x8289d1b8
CAMERA_BLOCK_NEXT = 0x8289d1f0


ACTOR_SET_POS = 0x80964e70      # (actor, &vec3, 1): what the menu character's rotate node uses
MENU_SET_VISIBLE = 0x80d2a3c0   # (menu actor manager, visible, slot); refuses during the actors' update
ACTOR_POS = 0x31C               # the actor's position now (a request is in it after the actor's next update)
ACTOR_SHOWN = 0x4AC             # actor flags word: bit 0x10000 = shown (what set-visible sets and clears)
ACTOR_SHOWN_BIT = 0x10000
LDSET_W13_W14_X12 = exepatch.PSEUDO["ldset w13, w14, [x12]"][1]   # atomic bit set / clear
LDCLR_W13_W14_X12 = exepatch.PSEUDO["ldclr w13, w14, [x12]"][1]
MENU_DELETE_SLOT = 0x80c35e30   # (menu actor manager, slot)
# Menu actor manager ([[0x84c9e2a8] + 0x4e8]): 6 slots of 0x20 from +0x28 (0 the menu
# character, 1 the + menu's item preview, 2 tapestry, 3 collection background): +0x10
# link, +0x18 id (-1 = empty), +0x1c a flag byte; the actor is [link + 0x40] while
# [link + 0x48] == id; actor state [actor + 0x2c] in 7..11 = going away. +0x37a: the
# menu character is the child one.
MENU_SLOT = 0x28
MENU_SLOT_SIZE = 0x20


def menu_pivots():
    """Where the menu character stands, per age (ActorBasePos of PouchPlayer_<Age>)."""
    out = []
    for age in ("Child", "Adult"):
        pack = nxfmt.sarc_files(nxfmt.read(os.path.join(ROMFS, "Pack", "Actor", "PouchPlayer_%s.pack.zs" % age)))
        param = byml.load(pack["GameParameter/MenuActorParam/PouchPlayer_%s.game__ui__MenuActorParam.bgyml" % age])
        out.append(tuple(float(param["ActorBasePos"][k]) for k in "XYZ"))
    return out


def gamedata_subs(p):
    """Two small routines for our hooks, placed once per patch in the spare padding
    (exepatch.CAVE_EXTRA). In: x11 = GameData manager, w14 = flag hash.
      gd_bool -> w12 = the Bool's value (0 if unknown)
      gd_int  -> x12 = the Int's record (value at +0x1c), 0 if unknown
    They change x12, x13, x15, x16, x17 only. Layout as the game reads it: Bool map
    +0x20 (capacity +0x2c), records +0x18 (count +0x10, 0x18 each, value byte +0x16);
    Int map +0xe0 (capacity +0xec), records +0xd8 (count +0xd0, 0x28 each)."""
    if "gd_bool" in p.symbols:
        return
    body = """
    {name}:
        ldr x12, [x11, #{map:#x}]
        cbz x12, {name}_no
        ldr w13, [x11, #{cap:#x}]
        cbz w13, {name}_no
        udiv w15, w14, w13
        msub w15, w15, w13, w14
        mov w16, w15
    {name}_probe:
        ldr x17, [x12, w16, uxtw #3]
        cmp w17, w14
        b.eq {name}_found
        cbz w17, {name}_no
        add w16, w16, #1
        cmp w16, w13
        csel w16, wzr, w16, hs
        cmp w16, w15
        b.ne {name}_probe
    {name}_no:
        mov x12, xzr
        ret
    {name}_found:
        lsr x17, x17, #32
        ldr w12, [x11, #{count:#x}]
        cmp w17, w12
        b.hs {name}_no
        ldr x12, [x11, #{recs:#x}]
        mov w13, #{size:#x}
        umaddl x12, w17, w13, x12
        {last}
        ret
    """
    with p.extra():
        p.cave("gd_bool", body.replace("{name}", "gd_bool").format(map=0x20, cap=0x2C, count=0x10, recs=0x18,
                                                                    size=0x18, last="ldrb w12, [x12, #0x16]"))
        p.cave("gd_int", body.replace("{name}", "gd_int").format(map=0xE0, cap=0xEC, count=0xD0, recs=0xD8,
                                                                  size=0x28, last="nop"))


def _hash_asm(name):
    h = gamedata.murmur3_32(name)
    return "movz w14, #%#x\n movk w14, #%#x, lsl #16" % (h & 0xFFFF, h >> 16)


def _mgr_asm(fail):
    return ("adrp x11, #%#x\n ldr x11, [x11, #%#x]\n ldr x11, [x11]\n cbz x11, %s"
            % (GAMEDATA_MGR_GOT & ~0xFFF, GAMEDATA_MGR_GOT & 0xFFF, fail))


def _view_copy(p):
    """vh_copy: view character := stored character (x11 = GameData manager; changes
    x12-x17). Used by the page view patch and by "character created"."""
    if "vh_copy" in p.symbols:
        return
    gamedata_subs(p)
    with p.extra(exepatch.CAVE_EXTRA2):
        p.cave("vh_copy", """
    vh_copy:
        sub sp, sp, #0x20
        str x30, [sp]
        {h_char}
        bl #{gd_int:#x}
        cbz x12, vc_out
        ldr w13, [x12, #0x1c]
        str w13, [sp, #8]
        {h_view}
        bl #{gd_int:#x}
        cbz x12, vc_out
        ldr w13, [sp, #8]
        str w13, [x12, #0x1c]
    vc_out:
        ldr x30, [sp]
        add sp, sp, #0x20
        ret
        """.format(h_char=_hash_asm(CHARACTER_FLAG), h_view=_hash_asm(VIEW_CHARACTER_FLAG), gd_int=p.symbols["gd_int"]))


def view_hook(p):
    """While PAGE_VIEW_FLAG is set and the display type is 0 (the menu character):
    put him at LINK_SCREEN_X with the projection offset (x20's first float; the +
    menu's view, same block with the flag clear, is unchanged) and run the character
    transition (SLIDE_FLAG; nothing else at rest)."""
    img = p.img
    assert VIEW == "lens"
    assert img.dis(0x8289d16c, 2) == ["8289d16c: ldr      w9, [x10, #0x228]", "8289d170: cmp      w9, #3"]
    assert img.dis(CAMERA_BLOCK_LOADED - 8, 3) == [
        "8289d1b0: add      x11, x11, #0x25c", "8289d1b4: ldr      x20, [x11]", "8289d1b8: b        #0x8289d1f0"]
    assert img.dis(CAMERA_BLOCK_NEXT, 2) == ["8289d1f0: adrp     x11, #0x84c9e000", "8289d1f4: ldr      w10, [x10, #0x30c]"]
    assert img.dis(0x8289d4d0, 1) == ["8289d4d0: stur     x20, [x19, #0xcc]"]
    assert img.dis(ACTOR_SET_POS, 1) == ["80964e70: pacibsp  "]
    # the game's set-visible (0x80d2a3c0), slot 0: the "shown" bit of the actor
    assert img.dis(0x80d2a4c4, 4) == ["80d2a4c4: add      x8, x22, #0x4ac", "80d2a4c8: mov      w9, #0x10000",
                                      "80d2a4cc: tbnz     w24, #0, #0x80d2a8b4", "80d2a4d0: ldclr    w9, w8, [x8]"]
    assert img.dis(0x80d2a8b4, 1) == ["80d2a8b4: ldset    w9, w8, [x8]"]
    for word, text in ((LDSET_W13_W14_X12, "ldset w13, w14, [x12]"), (LDCLR_W13_W14_X12, "ldclr w13, w14, [x12]")):
        ins = list(img.cs.disasm(struct.pack("<I", word), 0))
        assert len(ins) == 1 and "%s %s" % (ins[0].mnemonic, ins[0].op_str) == text, (hex(word), text)
    assert img.dis(0x80591788, 9)[-1] == "805917a8: bl       #0x80964e70"       # the rotate node: (actor, sp + 0x10, 1)
    assert STYLE_FRAMES <= SLIDE_STASH and SLIDE_FRAMES < STYLE_FRAMES
    # the hovered item's category: where the + menu stores it and where the menu character's
    # rotate node and the manager read it
    assert img.dis(0x80a89c98, 3) == ["80a89c98: ldr      x8, [x24]", "80a89c9c: ldr      w9, [sp, #0x50]",
                                      "80a89ca0: str      w9, [x8, #0x7c8]"]
    assert img.dis(0x80a89ae8, 1) == ["80a89ae8: ldr      x24, [x24, #0xd0]"]
    assert struct.unpack_from("<Q", img.data, 0x84c9e0d0 - img.base)[0] == POUCH_HOVER
    assert img.dis(0x80590ef4, 1) == ["80590ef4: ldr      w28, [x9, #0x7c8]"]
    assert img.dis(0x80b9aa60, 5) == ["80b9aa60: adrp     x28, #0x84e98000", "80b9aa64: add      x28, x28, #0xe28",
                                      "80b9aa68: ldr      w23, [x8, #0x320]", "80b9aa6c: ldr      x25, [x28]",
                                      "80b9aa70: ldr      w22, [x25, #0x7c8]"]
    assert img.dis(0x80b9ac90, 2) == ["80b9ac90: ldr      w8, [x8, #0x320]", "80b9ac94: cmp      w8, #4"]
    gamedata_subs(p)
    _view_copy(p)
    pivots = menu_pivots()
    assert [tuple(round(v, 3) for v in piv) for piv in pivots] == [(0.0, 0.3, 0.0), (0.1, -0.2, 0.0)], pivots
    blob = struct.pack("<4f", VIEW_OFFSET_X, 0.0, 0.0, 0.0) + b"".join(struct.pack("<3f", *piv) for piv in pivots)
    table = p.rodata("view_values", blob)
    off = table & 0xFFF
    assert off % 4 == 0 and off + len(blob) <= 0x1000
    row = p.rodata("slide_style", style_row(SLIDE_STYLE))
    assert row % 4 == 0 and (row & 0xFFF) + STYLE_ROW <= 0x1000
    with p.extra(exepatch.CAVE_EXTRA2):
        # vh_move: x0 = a menu slot, s16 = metres right of where the character stands; w1 = 1
        # show / 2 hide the actor first (its "shown" bit, as the game's set-visible does with
        # ldset / ldclr), 0 leave that alone. Uses the hook's frame ([sp + 0x18] menu manager,
        # 0x40 the position, 0x50 lr).
        p.cave("vh_move", """    vh_move:
        str x30, [sp, #0x50]
        ldr w12, [x0, #0x18]
        cmn w12, #1
        b.eq vm_out
        ldr x13, [x0, #0x10]
        cbz x13, vm_out
        ldr w14, [x13, #0x48]
        cmp w14, w12
        b.ne vm_out
        ldr x0, [x13, #0x40]
        cbz x0, vm_out
        ldr w14, [x0, #0x2c]
        sub w14, w14, #7
        cmp w14, #5
        b.lo vm_out
        cbz w1, vm_place
        add x12, x0, #{shown:#x}
        mov w13, #{shown_bit:#x}
        tbnz w1, #1, vm_hide
        ldset w13, w14, [x12]
        b vm_place
    vm_hide:
        ldclr w13, w14, [x12]
    vm_place:
        ldr x12, [sp, #0x18]
        ldrb w12, [x12, #0x37a]
        adrp x13, #{t_page:#x}
        add x13, x13, #{off_child:#x}
        cbnz w12, vm_age
        add x13, x13, #12
    vm_age:
        ldr s17, [x13]
        fadd s17, s17, s16
        str s17, [sp, #0x40]
        ldr w14, [x13, #4]
        str w14, [sp, #0x44]
        ldr w14, [x13, #8]
        str w14, [sp, #0x48]
        add x1, sp, #0x40
        mov w2, #1
        bl #{set_pos:#x}
    vm_out:
        ldr x30, [sp, #0x50]
        ret
        """.format(t_page=table & ~0xFFF, off_child=off + 16, set_pos=ACTOR_SET_POS, shown=ACTOR_SHOWN,
                   shown_bit=ACTOR_SHOWN_BIT))
    slot0, old = MENU_SLOT, MENU_SLOT + MENU_SLOT_SIZE * STASH_SLOT
    # At the hook: x0 x1 x9 (display type) x10 (menu actor manager) x19 x20 and the
    # camera floats s0 s3 s4 s5 s8-s15 are live; x11-x17 and v16+ are free; the
    # function has its return address in its frame, so bl is fine. In a transition:
    # [sp + 0x30] the new character's frame, 0x34 show / hide.
    p.cave("view_hook", """
        cmp w9, #3
        b.hi vh_out
        {mgr}
        {h_page}
        bl #{gd_bool:#x}
        cbnz w9, vh_other
        cbnz w12, vh_neutral
    vh_plus:
        {h_pose}
        bl #{gd_int:#x}
        cbz x12, vh_out
        ldr w13, [x12, #0x1c]
        cbz w13, vh_out
        str wzr, [x12, #0x1c]
        adrp x14, #{hover_page:#x}
        ldr x14, [x14, #{hover_off:#x}]
        cbz x14, vh_out
        ldr w15, [x14, #{hover_cat:#x}]
        cmp w15, #{neutral}
        b.ne vh_out
        sub w13, w13, #1
        str w13, [x14, #{hover_cat:#x}]
        b vh_out
    vh_other:
        cmp w9, #1
        b.eq vh_plus
    vh_neutral:
        {h_pose}
        bl #{gd_int:#x}
        cbz x12, vh_posed
        adrp x14, #{hover_page:#x}
        ldr x14, [x14, #{hover_off:#x}]
        cbz x14, vh_posed
        ldr w15, [x14, #{hover_cat:#x}]
        cmp w15, #{neutral}
        b.eq vh_posed
        add w15, w15, #1
        str w15, [x12, #0x1c]
        mov w15, #{neutral}
        str w15, [x14, #{hover_cat:#x}]
    vh_posed:
        cbnz w9, vh_out
        adrp x13, #{t_page:#x}
        ldr w20, [x13, #{off:#x}]
        {h_slide}
        bl #{gd_int:#x}
        cbz x12, vh_out
        ldr w13, [x12, #0x1c]
        cbz w13, vh_out
        sub sp, sp, #0x60
        stp x0, x1, [sp]
        stp x9, x10, [sp, #0x10]
        stp s0, s3, [sp, #0x20]
        stp s4, s5, [sp, #0x28]
        ldr w14, [x10, #{old_id:#x}]
        cmn w14, #1
        b.ne vh_parked
        cmp w13, #{stash}
        b.eq vh_done
        str wzr, [x12, #0x1c]
        {h_out}
        bl #{gd_int:#x}
        cbz x12, vh_view
        str wzr, [x12, #0x1c]
    vh_view:
        bl #{vh_copy:#x}
        b vh_done
    vh_parked:
        cmp w13, #{stash}
        b.ne vh_count
        mov w15, wzr
        mov w16, #2
        b vh_both
    vh_count:
        mov w14, #{cap}
        cmp w13, w14
        csel w15, w13, w14, lo
        b.hs vh_shown
        add w14, w13, #1
        str w14, [x12, #0x1c]
    vh_shown:
        mov w16, #1
    vh_both:
        stp w15, w16, [sp, #0x30]
        {h_out}
        bl #{gd_int:#x}
        mov w14, wzr
        cbz x12, vh_old
        ldr w14, [x12, #0x1c]
        cmp w14, #{cap}
        b.hs vh_old
        add w14, w14, #1
        str w14, [x12, #0x1c]
    vh_old:
        adrp x13, #{row_page:#x}
        add x13, x13, #{row_off:#x}
        ldrb w16, [x13, #8]
        cbz w16, vh_oi
        ldr w14, [sp, #0x30]
    vh_oi:
        add x16, x13, #12
        ldrh w14, [x16, w14, uxtw #1]
        ucvtf s16, w14, #{one_bits}
        ldr s17, [x13]
        fmul s16, s16, s17
        ldr x10, [sp, #0x18]
        mov w1, wzr
        add x0, x10, #{old:#x}
        bl #{vh_move:#x}
        adrp x13, #{row_page:#x}
        add x13, x13, #{row_off:#x}
        ldr w14, [sp, #0x30]
        add x16, x13, #{in_off}
        ldrh w14, [x16, w14, uxtw #1]
        ucvtf s16, w14, #{one_bits}
        fmov s17, #1.0
        fsub s16, s17, s16
        ldr s17, [x13, #4]
        fnmul s16, s16, s17
        ldr x10, [sp, #0x18]
        ldr w1, [sp, #0x34]
        add x0, x10, #{slot0:#x}
        bl #{vh_move:#x}
    vh_done:
        ldp x0, x1, [sp]
        ldp x9, x10, [sp, #0x10]
        ldp s0, s3, [sp, #0x20]
        ldp s4, s5, [sp, #0x28]
        add sp, sp, #0x60
    vh_out:
        b #{next:#x}

    """.format(mgr=_mgr_asm("vh_out"), h_page=_hash_asm(PAGE_VIEW_FLAG), h_slide=_hash_asm(SLIDE_FLAG),
               h_out=_hash_asm(SLIDE_OUT_FLAG), h_pose=_hash_asm(POSE_FLAG), hover_page=POUCH_HOVER & ~0xFFF,
               hover_off=POUCH_HOVER & 0xFFF, hover_cat=POUCH_HOVER_CATEGORY, neutral=POSE_NEUTRAL,
               vh_copy=p.symbols["vh_copy"], vh_move=p.symbols["vh_move"],
               gd_bool=p.symbols["gd_bool"], gd_int=p.symbols["gd_int"], t_page=table & ~0xFFF, off=off,
               stash=SLIDE_STASH, cap=STYLE_FRAMES - 1, row_page=row & ~0xFFF, row_off=row & 0xFFF,
               in_off=12 + 2 * STYLE_FRAMES, one_bits=STYLE_ONE_BITS, slot0=slot0, old=old, old_id=old + 0x18,
               next=CAMERA_BLOCK_NEXT))
    p.asm(CAMERA_BLOCK_LOADED, "b {view_hook}", expect="b #%#x" % CAMERA_BLOCK_NEXT)
    assert "keep_camera" in p.symbols, "keep_camera_hook goes first"
    check_menu_hooks(p, table, pivots)


# Menu actor manager's "delete the menu Link" (0x80c35c30, only called by the
# SeqOneShotDeletePouchPlayer node 0x80c35be0): delete slot 0 (pouch Link), then
# if the 3D UI camera handle (+0x10) is valid, delete the camera too.
DELETE_POUCH_SLOT_CALL = 0x80c35c60      # "bl 0x80c35e30" with x0 = x19 = manager, x1 = 0
DELETE_POUCH_CAMERA_CHECK = 0x80c35c64   # "add x0, x19, #0x10" before the camera handle check
DELETE_POUCH_CAMERA_NEXT = 0x80c35c68
DELETE_POUCH_RETURN = 0x80c35cc0         # epilogue
CREATED_BLOCK = 0x80c36924               # "character created": first instruction of the camera block reset
CREATED_BLOCK_DONE = 0x80c369b0          # first instruction after it
CREATE_SHADOW = 0x80c38654               # "create the menu character": start of the shadow range override
CREATE_SHADOW_DONE = 0x80c386b0          # its epilogue
MENU_SCENES = 0x84eb50a8                 # [this]: scenes, count +0x50, array +0x58 (0x60 each); [1] = the menu's 3D scene
SCENE_SHADOW_FLAGS = 0xD14               # u16; bit 2: depth shadow range overridden
SCENE_SHADOW = 0xD60                     # -> +0x250 cascade count n, +0x330 -> floats: [0] near, [n] far


def keep_camera_hook(p):
    """The two patches in "delete the menu Link":
    - its "delete slot 0" call: while SLIDE_FLAG is SLIDE_STASH, the character is moved
      to slot STASH_SLOT instead (emptied first) and stays alive for the transition;
      with any other value but 0, slot STASH_SLOT is deleted instead of slot 0;
    - the camera part is skipped while KEEP_CAMERA_FLAG is set (only the Characters
      page sets it). A camera created while the menu is open only becomes the active
      view after the menu pause ends, so the page must keep the existing one; the
      camera takes its numbers from the menu manager, not from the character.
    And one in "character created": while SLIDE_FLAG is not 0 the camera block is not
    reset to the new character's default view (the game then glides back to the
    equipment view over half a second; seen as a dip at the start of a transition);
    while it is 0, the view character becomes the stored one (see VIEW_CHARACTER_FLAG)."""
    img = p.img
    assert img.dis(0x80c35c48, 1) == ["80c35c48: mov      x19, x0"]
    assert img.dis(0x80c35c58, 6) == [
        "80c35c58: mov      x0, x19", "80c35c5c: mov      x1, xzr", "80c35c60: bl       #0x80c35e30",
        "80c35c64: add      x0, x19, #0x10", "80c35c68: bl       #0x80813ea0", "80c35c6c: tbz      w0, #0, #0x80c35cd0"]
    assert img.dis(DELETE_POUCH_RETURN, 4)[-1] == "80c35ccc: retab    "
    assert img.calls_to(0x80c35c30) == [0x80c35c08]
    assert img.dis(0x80c35e20, 2) == ["80c35e20: mov      w1, #1", "80c35e24: b        #0x80c35e30"]   # the game's "delete slot 1"
    assert img.dis(0x80c35e64, 4) == ["80c35e64: add      x9, x8, x9, lsl #5", "80c35e68: csel     x22, x9, x8, lo",
                                      "80c35e6c: ldr      w8, [x22, #0x18]", "80c35e70: cmn      w8, #1"]
    # "character created" (0x80c36870, from the manager's update when slot 0's actor
    # arrives): camera block 0 := the actor's default view, then sleep + hide, then the
    # backdrop if there is none
    assert img.calls_to(0x80c36870) == [0x833b5840]
    assert img.dis(CREATED_BLOCK, 4) == [
        "80c36924: mov      x0, x20", "80c36928: bl       #0x80c367a0", "80c3692c: mov      x21, x0",
        "80c36930: add      x22, x19, #0x22c"]
    assert img.dis(CREATED_BLOCK_DONE - 4, 2) == ["80c369ac: str      s0, [x19, #0x258]", "80c369b0: ldr      x8, [x20, #0x270]"]
    assert img.dis(0x80c369fc, 1) == ["80c369fc: ldr      w22, [x19, #0x228]"]      # x21 / x22 are set again after
    assert img.dis(0x80c369f0, 1) == ["80c369f0: mov      w21, #0x10000"]
    assert 0 < STASH_SLOT < 6 and STASH_SLOT not in (1, 2, 3)
    gamedata_subs(p)
    s0, s1 = MENU_SLOT, MENU_SLOT + MENU_SLOT_SIZE * STASH_SLOT
    with p.extra():
        # entered with bl; ends with an explicit branch back, so x30 is free
        p.cave("stash_slot", """
            {mgr}
            {h_slide}
            bl #{gd_int:#x}
            cbz x12, ss_delete
            ldr w13, [x12, #0x1c]
            cbz w13, ss_delete
            cmp w13, #{stash}
            b.ne ss_drop
            mov x0, x19
            mov w1, #{slot}
            bl #{delete:#x}
            ldp x11, x12, [x19, #{s0:#x}]
            stp x11, x12, [x19, #{s1:#x}]
            ldp x11, x12, [x19, #{s0b:#x}]
            stp x11, x12, [x19, #{s1b:#x}]
            str xzr, [x19, #{s0:#x}]
            mov w11, #-1
            str w11, [x19, #{s0id:#x}]
            strb wzr, [x19, #{s0flag:#x}]
            b #{after:#x}
        ss_drop:
            mov w1, #{slot}
            b ss_go
        ss_delete:
            mov x1, xzr
        ss_go:
            mov x0, x19
            bl #{delete:#x}
            b #{after:#x}
        """.format(mgr=_mgr_asm("ss_delete"), h_slide=_hash_asm(SLIDE_FLAG), gd_int=p.symbols["gd_int"],
                   stash=SLIDE_STASH, slot=STASH_SLOT, delete=MENU_DELETE_SLOT, s0=s0, s1=s1,
                   s0b=s0 + 0x10, s1b=s1 + 0x10, s0id=s0 + 0x18, s0flag=s0 + 0x1C, after=DELETE_POUCH_CAMERA_CHECK))
    with p.extra(exepatch.CAVE_EXTRA2):
        p.cave("keep_camera", """
            {mgr}
            {h_keep}
            bl #{gd_bool:#x}
            cbz w12, kc_delete
            b #{ret:#x}
        kc_delete:
            add x0, x19, #0x10
            b #{next:#x}
        """.format(mgr=_mgr_asm("kc_delete"), h_keep=_hash_asm(KEEP_CAMERA_FLAG), gd_bool=p.symbols["gd_bool"],
                   ret=DELETE_POUCH_RETURN, next=DELETE_POUCH_CAMERA_NEXT))
    # during a transition the camera block stays as it is; otherwise the character that
    # has just arrived is the one the view is for (x11-x17 and x30 are free here: the next
    # instructions are a call's arguments, the function has its return address in its frame)
    assert "stp      x29, x30, [sp, #0x150]" in img.dis(0x80c36878, 1)[0]
    _view_copy(p)
    p.cave("keep_block", """
        {mgr}
        {h_slide}
        bl #{gd_int:#x}
        cbz x12, kb_view
        ldr w13, [x12, #0x1c]
        cbz w13, kb_view
        b #{done:#x}
    kb_view:
        bl #{vh_copy:#x}
    kb_reset:
        mov x0, x20
        b #{next:#x}
    """.format(mgr=_mgr_asm("kb_reset"), h_slide=_hash_asm(SLIDE_FLAG), gd_int=p.symbols["gd_int"],
               vh_copy=p.symbols["vh_copy"], done=CREATED_BLOCK_DONE, next=CREATED_BLOCK + 4))
    p.asm(CREATED_BLOCK, "b {keep_block}", expect="mov x0, x20")
    # "create the menu character" (0x80c38510) ends by overriding the menu scene's depth
    # shadow range (flag 2 in scene +0xd14, near 1.7, far 6.0 in place of the scene
    # config's 2.0 .. 8.4) until the next display mode request, which comes when the
    # actor has arrived: the far part of the shown character's shadow is cut meanwhile
    # (snaildev, 2026-11-10). Not during a transition.
    assert img.dis(CREATE_SHADOW - 4, 4) == [
        "80c38650: strb     w20, [x19, #0x37a]", "80c38654: adrp     x8, #0x84eb5000", "80c38658: add      x8, x8, #0xa8",
        "80c3865c: ldr      x8, [x8]"]
    assert img.dis(0x80c3867c, 4) == ["80c3867c: ldrh     w9, [x8, #0xd14]", "80c38680: mov      w10, #0x999a",
                                      "80c38684: movk     w10, #0x3fd9, lsl #16", "80c38688: orr      w9, w9, #2"]
    assert img.dis(CREATE_SHADOW_DONE, 5)[-1] == "80c386c0: retab    "
    assert "stp      x29, x30, [sp, #0x130]" in img.dis(0x80c38518, 1)[0]
    with p.extra(exepatch.CAVE_EXTRA2):
        p.cave("keep_shadow", """
            {mgr}
            {h_slide}
            bl #{gd_int:#x}
            cbz x12, ks_set
            ldr w13, [x12, #0x1c]
            cbz w13, ks_set
            b #{done:#x}
        ks_set:
            adrp x8, #0x84eb5000
            b #{next:#x}
        """.format(mgr=_mgr_asm("ks_set"), h_slide=_hash_asm(SLIDE_FLAG), gd_int=p.symbols["gd_int"],
                   done=CREATE_SHADOW_DONE, next=CREATE_SHADOW + 4))
    p.asm(CREATE_SHADOW, "b {keep_shadow}", expect="adrp x8, #0x84eb5000")
    p.asm(DELETE_POUCH_SLOT_CALL, "bl {stash_slot}", expect="bl #%#x" % MENU_DELETE_SLOT)
    p.asm(DELETE_POUCH_CAMERA_CHECK, "b {keep_camera}", expect="add x0, x19, #0x10")


def check_menu_hooks(p, table, pivots):
    """Run the patched code in unicorn (game functions replaced by stubs that record
    their arguments and scribble over every register a real call may change): the
    page view at rest, off the page, in other views; a whole transition frame by
    frame; "delete the menu Link" with and without the stash, the drop and the keep
    flag; the camera block patch in "character created" and the shadow range patch
    in "create the menu character"."""
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
        if addr >= 0x83c66000:                      # everything we placed in the padding
            need(addr, len(data))
            mu.mem_write(addr, bytes(data))
    RET = b"\xc0\x03\x5f\xd6"
    stubs = {ACTOR_SET_POS: "set_pos", MENU_SET_VISIBLE: "set_visible", MENU_DELETE_SLOT: "delete_slot"}
    stops = [CAMERA_BLOCK_NEXT, DELETE_POUCH_CAMERA_CHECK, DELETE_POUCH_RETURN, DELETE_POUCH_CAMERA_NEXT,
             CREATED_BLOCK + 4, CREATED_BLOCK_DONE, CREATE_SHADOW + 4, CREATE_SHADOW_DONE]
    for a in list(stubs) + stops:
        need(a, 4)
        mu.mem_write(a, RET)
    STACK, GD, MENU, HEAP = 0x20000000, 0x10000000, 0x11000000, 0x12000000
    for base in (STACK, GD, MENU, HEAP):
        mu.mem_map(base, 0x10000)
    need(GAMEDATA_MGR_GOT, 8)
    mu.mem_write(GAMEDATA_MGR_GOT, struct.pack("<Q", GD + 0x800))
    mu.mem_write(GD + 0x800, struct.pack("<Q", GD))
    cap = 11
    MAPB, RECB, MAPI, RECI = GD + 0x1000, GD + 0x2000, GD + 0x3000, GD + 0x4000
    mu.mem_write(GD + 0x10, struct.pack("<I", 8))
    mu.mem_write(GD + 0x18, struct.pack("<QQ", RECB, MAPB))
    mu.mem_write(GD + 0x2C, struct.pack("<I", cap))
    mu.mem_write(GD + 0xD0, struct.pack("<I", 8))
    mu.mem_write(GD + 0xD8, struct.pack("<QQ", RECI, MAPI))
    mu.mem_write(GD + 0xEC, struct.pack("<I", cap))
    where = {}

    def add_flag(name, kind, index):
        h = gamedata.murmur3_32(name)
        m = MAPB if kind == "Bool" else MAPI
        i = h % cap
        while struct.unpack("<I", bytes(mu.mem_read(m + 8 * i, 4)))[0]:
            i = (i + 1) % cap
        mu.mem_write(m + 8 * i, struct.pack("<II", h, index))
        where[name] = (RECB + 0x18 * index + 0x16) if kind == "Bool" else (RECI + 0x28 * index + 0x1C)

    for i, (kind, name) in enumerate([("Bool", PAGE_VIEW_FLAG), ("Bool", KEEP_CAMERA_FLAG), ("Bool", "Other_Flag")]):
        add_flag(name, kind, i + 1)
    for i, (kind, name) in enumerate([("Int", SLIDE_FLAG), ("Int", CHARACTER_FLAG), ("Int", VIEW_CHARACTER_FLAG),
                                      ("Int", SLIDE_OUT_FLAG), ("Int", POSE_FLAG)]):
        add_flag(name, kind, i + 2)

    def setb(name, v):
        mu.mem_write(where[name], bytes([v]))

    def seti(name, v):
        mu.mem_write(where[name], struct.pack("<i", v))

    def geti(name):
        return struct.unpack("<i", bytes(mu.mem_read(where[name], 4)))[0]

    # menu actor manager: two actors with a link each, in slot 0 and the stash slot
    ACT = [HEAP + 0x1000, HEAP + 0x2000]
    OLD = STASH_SLOT
    SHOWN = 0x01FE0000                  # the flags word of a hidden actor, as seen live

    def shown(actor):
        v = struct.unpack("<I", bytes(mu.mem_read(actor + ACTOR_SHOWN, 4)))[0]
        assert v & ~ACTOR_SHOWN_BIT == SHOWN, hex(v)
        return bool(v & ACTOR_SHOWN_BIT)

    def set_shown(actor, on):
        mu.mem_write(actor + ACTOR_SHOWN, struct.pack("<I", SHOWN | (ACTOR_SHOWN_BIT if on else 0)))
    LINK = [HEAP + 0x100, HEAP + 0x200]

    def set_slot(i, actor, ident, state=3):
        base = MENU + MENU_SLOT + MENU_SLOT_SIZE * i
        if actor is None:
            mu.mem_write(base, struct.pack("<QQQiB", 0, 0x77770000 + i, LINK[i % 2], -1, 0) + b"\0\0\0")
            return
        link = LINK[ident & 1]
        mu.mem_write(link + 0x40, struct.pack("<QI", actor, ident))
        mu.mem_write(actor + 0x2C, struct.pack("<I", state))
        mu.mem_write(base, struct.pack("<QQQiB", 0, 0x77770000 + i, link, ident, 1) + b"\0\0\0")

    def slot(i):
        return bytes(mu.mem_read(MENU + MENU_SLOT + MENU_SLOT_SIZE * i, MENU_SLOT_SIZE))

    calls = []
    scratch = [A.UC_ARM64_REG_X0 + i for i in range(18)]

    def on_code(uc, address, size, data):
        if address in stubs:
            x0, x1, x2 = (uc.reg_read(A.UC_ARM64_REG_X0 + i) for i in range(3))
            if stubs[address] == "set_pos":
                calls.append(("set_pos", x0, struct.unpack("<3f", bytes(uc.mem_read(x1, 12))), x2 & 0xFFFFFFFF))
                uc.mem_write(x0 + ACTOR_POS, bytes(uc.mem_read(x1, 12)))     # as if the actor had its next update
            else:
                calls.append((stubs[address], x0, x1 & 0xFFFFFFFF, x2 & 0xFFFFFFFF))
            for r in scratch:                       # what a real call may destroy
                uc.reg_write(r, 0xDEAD0000DEAD0000)
            for r in list(range(A.UC_ARM64_REG_V0, A.UC_ARM64_REG_V0 + 8)) + \
                    list(range(A.UC_ARM64_REG_V0 + 16, A.UC_ARM64_REG_V0 + 32)):
                uc.reg_write(r, 0x7FC000007FC00000)

    for a in stubs:
        mu.hook_add(unicorn.UC_HOOK_CODE, on_code, begin=a, end=a)
    import collections
    trail = collections.deque(maxlen=24)        # the last instructions, for the message if a run stops
    mu.hook_add(unicorn.UC_HOOK_CODE, lambda uc, address, size, data: trail.append(address))
    # The two atomic bit instructions (ARMv8.1): unicorn's default CPU does not know
    # them and its newest one stops after them, so in the emulated copy each is a call
    # to a stub that does the same to memory and to w14 (and, unlike the real
    # instruction, changes x30: vh_move has saved it by then and reloads it at its end).
    lse_stub = {LDSET_W13_W14_X12: 0x83c65000, LDCLR_W13_W14_X12: 0x83c65010}
    found = []
    for addr, data in p.patches:
        if addr >= 0x83c66000 and addr not in p.data_at and addr != p.cave_start:
            for i in range(0, len(data) - 3, 4):
                word = struct.unpack_from("<I", data, i)[0]
                if word in lse_stub:
                    found.append(word)
                    mu.mem_write(addr + i, exepatch.assemble("bl #%#x" % lse_stub[word], addr + i))
    assert sorted(found) == sorted(lse_stub), found

    def on_lse(uc, address, size, set_bits):
        at = uc.reg_read(A.UC_ARM64_REG_X12)
        bits = uc.reg_read(A.UC_ARM64_REG_X13) & 0xFFFFFFFF
        was = struct.unpack("<I", bytes(uc.mem_read(at, 4)))[0]
        uc.mem_write(at, struct.pack("<I", was | bits if set_bits else was & ~bits))
        uc.reg_write(A.UC_ARM64_REG_X14, was)

    for word, a in lse_stub.items():
        need(a, 4)
        mu.mem_write(a, RET)
        mu.hook_add(unicorn.UC_HOOK_CODE, on_lse, word == LDSET_W13_W14_X12, begin=a, end=a)
    mu.reg_write(A.UC_ARM64_REG_CPACR_EL1, 3 << 20)
    live_f = {A.UC_ARM64_REG_S0: 1.25, A.UC_ARM64_REG_S3: 2.5, A.UC_ARM64_REG_S4: 3.5, A.UC_ARM64_REG_S5: 4.5,
              A.UC_ARM64_REG_S8: 5.5, A.UC_ARM64_REG_S15: 6.5}
    fbits = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]  # noqa: E731

    def view(display=0, x20=0x1111111122222222):
        """One camera update through the page view patch -> (offset float or None, calls)."""
        del calls[:]
        sp = STACK + 0x8000
        mu.reg_write(A.UC_ARM64_REG_SP, sp)
        regs = {A.UC_ARM64_REG_X0: 0xA0, A.UC_ARM64_REG_X1: 0xA1, A.UC_ARM64_REG_X9: display,
                A.UC_ARM64_REG_X10: MENU, A.UC_ARM64_REG_X19: 0xA19, A.UC_ARM64_REG_X20: x20}
        for r, v in regs.items():
            mu.reg_write(r, v)
        for r, v in live_f.items():
            mu.reg_write(r, fbits(v))
        try:
            mu.emu_start(p.symbols["view_hook"], CAMERA_BLOCK_NEXT, count=5000)
        except unicorn.UcError as e:
            pc = mu.reg_read(A.UC_ARM64_REG_PC)
            raise AssertionError("emulation stopped at %#x (%s); last: %s" % (pc, e, " ".join("%x" % a for a in trail)))
        assert mu.reg_read(A.UC_ARM64_REG_PC) == CAMERA_BLOCK_NEXT and mu.reg_read(A.UC_ARM64_REG_SP) == sp
        for r, v in regs.items():
            if r != A.UC_ARM64_REG_X20:
                assert mu.reg_read(r) == v, ("register lost", r)
        for r, v in live_f.items():
            assert mu.reg_read(r) & 0xFFFFFFFF == fbits(v), ("float lost", r)
        out = mu.reg_read(A.UC_ARM64_REG_X20)
        if out == x20:
            return None, list(calls)
        assert out >> 32 == 0
        return struct.unpack("<f", struct.pack("<I", out))[0], list(calls)

    near = lambda a, b: abs(a - b) < 1e-5  # noqa: E731
    mu.mem_write(MENU + 0x37A, b"\x01")
    set_slot(0, ACT[0], 0x100)
    set_slot(OLD, None, -1)
    set_shown(ACT[0], True), set_shown(ACT[1], False)
    # the hovered item's category (see POSE_FLAG)
    HOVER = HEAP + 0x6000
    need(POUCH_HOVER, 8)
    mu.mem_write(POUCH_HOVER, struct.pack("<Q", HOVER))
    hover = lambda: struct.unpack("<i", bytes(mu.mem_read(HOVER + POUCH_HOVER_CATEGORY, 4)))[0]  # noqa: E731
    set_hover = lambda v: mu.mem_write(HOVER + POUCH_HOVER_CATEGORY, struct.pack("<i", v))  # noqa: E731
    shield = 9
    assert shield != POSE_NEUTRAL
    seti(SLIDE_FLAG, 5), seti(CHARACTER_FLAG, 8), seti(VIEW_CHARACTER_FLAG, 2)
    for page in (0, 1):
        setb(PAGE_VIEW_FLAG, page)
        # the + menu's views with nothing kept, and views that are not the menu's: nothing happens
        for display in ([0] if not page else []) + [1, 4, 5, 7]:
            set_hover(shield), seti(POSE_FLAG, 0)
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (shield, 0), (page, display)
        # the − menu's other pages: the neutral category, the + menu's kept; nothing else
        for display in (2, 3):
            set_hover(shield), seti(POSE_FLAG, 0)
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (POSE_NEUTRAL, shield + 1)
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (POSE_NEUTRAL, shield + 1)
            set_hover(POSE_NEUTRAL), seti(POSE_FLAG, 0)             # a tunic was hovered: nothing to keep
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (POSE_NEUTRAL, 0)
        # back in the + menu: its own category again, once; not if it was changed meanwhile
        for display in ([0] if not page else []) + [1]:
            set_hover(POSE_NEUTRAL), seti(POSE_FLAG, shield + 1)
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (shield, 0)
            set_hover(0), seti(POSE_FLAG, shield + 1)
            assert view(display=display) == (None, []) and (hover(), geti(POSE_FLAG)) == (0, 0)
        assert geti(SLIDE_FLAG) == 5 and geti(VIEW_CHARACTER_FLAG) == 2
    # our page: the neutral category too
    set_hover(shield), seti(POSE_FLAG, 0), seti(SLIDE_FLAG, 0)
    o, c = view()
    assert near(o, VIEW_OFFSET_X) and c == [] and (hover(), geti(POSE_FLAG)) == (POSE_NEUTRAL, shield + 1)
    mu.mem_write(POUCH_HOVER, struct.pack("<Q", 0))                 # no hover object: only the view
    o, c = view()
    assert near(o, VIEW_OFFSET_X) and c == [] and geti(POSE_FLAG) == shield + 1
    mu.mem_write(POUCH_HOVER, struct.pack("<Q", HOVER))
    seti(SLIDE_FLAG, 5)
    # at rest: the offset; the view character is left alone (the picker stores the number
    # a few frames before its swap starts)
    setb(PAGE_VIEW_FLAG, 1), seti(SLIDE_FLAG, 0), seti(SLIDE_OUT_FLAG, 0)
    o, c = view()
    assert near(o, VIEW_OFFSET_X) and c == [] and geti(VIEW_CHARACTER_FLAG) == 2 and geti(SLIDE_FLAG) == 0

    # a transition, frame by frame
    home = pivots[0]
    f32 = lambda v: struct.unpack("<f", struct.pack("<f", v))[0]  # noqa: E731
    at = lambda actor: struct.unpack("<3f", bytes(mu.mem_read(actor + ACTOR_POS, 12)))  # noqa: E731
    st, last = SLIDE_STYLE, STYLE_FRAMES - 1
    seti(CHARACTER_FLAG, 3), seti(VIEW_CHARACTER_FLAG, 8), seti(SLIDE_FLAG, SLIDE_STASH)
    o, c = view()                               # asked for, not parked yet: nothing moves, nothing is counted
    assert near(o, VIEW_OFFSET_X) and c == [] and geti(SLIDE_OUT_FLAG) == 0 and shown(ACT[0])
    assert geti(SLIDE_FLAG) == SLIDE_STASH and geti(VIEW_CHARACTER_FLAG) == 8
    set_slot(OLD, ACT[0], 0x100), set_slot(0, None, -1)
    parked = [0]

    def frame(k, new, hidden, piv=home):
        """One camera update with the new character at frame k of its way in."""
        o, c = view()
        parked[0] += 1
        assert near(o, VIEW_OFFSET_X) and geti(SLIDE_OUT_FLAG) == min(parked[0], last)
        i = k if st["together"] else min(parked[0], last)
        want = [(ACT[0], f32(piv[0]) + st["out"] * st["out_frames"][i] / STYLE_ONE)]
        if new:
            want.append((ACT[1], f32(piv[0]) - st["in"] * (1 - st["in_frames"][k] / STYLE_ONE)))
        assert [x[:2] for x in c] == [("set_pos", w[0]) for w in want], (k, c)
        for x, w in zip(c, want):
            assert abs(x[2][0] - w[1]) < 1e-4 and x[2][1:] == (f32(piv[1]), f32(piv[2])) and x[3] == 1, (k, x, w)
        assert shown(ACT[0]) and (not new or shown(ACT[1]) != hidden), k
        assert geti(VIEW_CHARACTER_FLAG) == 8           # the old one's until the transition is over

    for _ in range(3):
        frame(0, False, True)                   # the old one leaves from the frame it is parked
    assert at(ACT[0])[0] > home[0]
    set_slot(0, ACT[1], 0x101)
    for game_showed_it in (False, True, False):
        set_shown(ACT[1], game_showed_it)
        frame(0, True, True)                    # the new one: held where its way in starts, hidden
        assert geti(SLIDE_FLAG) == SLIDE_STASH
    v = 1
    for _ in range(STYLE_FRAMES + 4):
        seti(SLIDE_FLAG, v)
        frame(min(v, last), True, False)
        assert geti(SLIDE_FLAG) == min(v + 1, last)
        v = geti(SLIDE_FLAG)
    assert at(ACT[1])[0] == f32(home[0]) and abs(at(ACT[0])[0] - (home[0] + st["out"])) < 1e-5
    # adult pivot; an actor that is going away is left alone
    mu.mem_write(MENU + 0x37A, b"\x00")
    frame(last, True, False, pivots[1])
    set_slot(0, ACT[1], 0x101, state=8)
    set_shown(ACT[1], False)
    o, c = view()
    assert [x[1] for x in c] == [ACT[0]] and not shown(ACT[1])
    set_slot(0, ACT[1], 0x101)
    set_shown(ACT[1], True)
    mu.mem_write(MENU + 0x37A, b"\x01")
    # the old one is dropped: the transition is over, the view is the stored character's
    set_slot(OLD, None, -1)
    o, c = view()
    assert near(o, VIEW_OFFSET_X) and c == []
    assert (geti(SLIDE_FLAG), geti(SLIDE_OUT_FLAG), geti(VIEW_CHARACTER_FLAG)) == (0, 0, 3)
    seti(CHARACTER_FLAG, 7)
    o, c = view()
    assert near(o, VIEW_OFFSET_X) and c == [] and geti(VIEW_CHARACTER_FLAG) == 3
    # a count with nobody parked (the flag out of step): put right at once
    for v in (5, last, 1 - SLIDE_STASH, 2 * SLIDE_STASH):
        seti(SLIDE_FLAG, v), seti(SLIDE_OUT_FLAG, 9), seti(VIEW_CHARACTER_FLAG, 1)
        o, c = view()
        assert near(o, VIEW_OFFSET_X) and c == [] and shown(ACT[1])
        assert (geti(SLIDE_FLAG), geti(SLIDE_OUT_FLAG), geti(VIEW_CHARACTER_FLAG)) == (0, 0, 7), v

    def delete(expect_stop):
        del calls[:]
        mu.reg_write(A.UC_ARM64_REG_SP, STACK + 0x8000)
        mu.reg_write(A.UC_ARM64_REG_X19, MENU)
        mu.reg_write(A.UC_ARM64_REG_X0, MENU)
        mu.reg_write(A.UC_ARM64_REG_X1, 0)
        mu.emu_start(p.symbols["stash_slot"], DELETE_POUCH_CAMERA_CHECK, count=5000)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == DELETE_POUCH_CAMERA_CHECK and mu.reg_read(A.UC_ARM64_REG_X19) == MENU
        first = list(calls)
        mu.emu_start(p.symbols["keep_camera"], expect_stop, count=5000)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == expect_stop
        return first

    # plain delete (Y / X swap, the game's own uses): slot 0 deleted; camera by the keep flag
    set_slot(0, ACT[0], 0x100)
    set_slot(OLD, None, -1)
    others = lambda: [slot(i) for i in range(6) if i not in (0, OLD)]  # noqa: E731
    before = (slot(0), slot(OLD), others())
    seti(SLIDE_FLAG, 0), setb(KEEP_CAMERA_FLAG, 0)
    assert [x[:3] for x in delete(DELETE_POUCH_CAMERA_NEXT)] == [("delete_slot", MENU, 0)]
    assert mu.reg_read(A.UC_ARM64_REG_X0) == MENU + 0x10 and (slot(0), slot(OLD), others()) == before
    setb(KEEP_CAMERA_FLAG, 1)
    assert [x[:3] for x in delete(DELETE_POUCH_RETURN)] == [("delete_slot", MENU, 0)]
    # stash: the stash slot emptied, slot 0 moved there, slot 0 empty, nothing else deleted
    seti(SLIDE_FLAG, SLIDE_STASH)
    assert [x[:3] for x in delete(DELETE_POUCH_RETURN)] == [("delete_slot", MENU, OLD)]
    assert slot(OLD) == before[0], "the character did not arrive in the stash slot intact"
    s0 = slot(0)
    assert struct.unpack_from("<Q", s0, 0)[0] == 0 and struct.unpack_from("<i", s0, 0x18)[0] == -1 and s0[0x1C] == 0
    assert s0[8:0x18] == before[0][8:0x18] and others() == before[2]
    # drop: the stash slot is deleted, slot 0 (the new character) is not touched
    set_slot(0, ACT[1], 0x101)
    now = (slot(0), slot(OLD), others())
    for v in (1, SLIDE_FRAMES, STYLE_FRAMES - 1, 1 - SLIDE_STASH, 2 * SLIDE_STASH):
        seti(SLIDE_FLAG, v)
        assert [x[:3] for x in delete(DELETE_POUCH_RETURN)] == [("delete_slot", MENU, OLD)]
        assert (slot(0), slot(OLD), others()) == now

    # "character created": the camera block reset runs at rest, is skipped in a transition
    def created(expect):
        mu.reg_write(A.UC_ARM64_REG_SP, STACK + 0x8000)
        mu.reg_write(A.UC_ARM64_REG_X19, MENU)
        mu.reg_write(A.UC_ARM64_REG_X20, ACT[1])
        mu.reg_write(A.UC_ARM64_REG_X0, 0xBAD)
        mu.emu_start(p.symbols["keep_block"], expect, count=200)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == expect, hex(mu.reg_read(A.UC_ARM64_REG_PC))
        assert mu.reg_read(A.UC_ARM64_REG_X19) == MENU and mu.reg_read(A.UC_ARM64_REG_X20) == ACT[1]
        assert mu.reg_read(A.UC_ARM64_REG_SP) == STACK + 0x8000
        return mu.reg_read(A.UC_ARM64_REG_X0)

    seti(SLIDE_FLAG, 0), seti(CHARACTER_FLAG, 4), seti(VIEW_CHARACTER_FLAG, 9)
    assert created(CREATED_BLOCK + 4) == ACT[1] and geti(VIEW_CHARACTER_FLAG) == 4      # what is shown from now on
    for v in (SLIDE_STASH, 1, SLIDE_FRAMES, STYLE_FRAMES - 1):
        seti(SLIDE_FLAG, v), seti(VIEW_CHARACTER_FLAG, 9)
        created(CREATED_BLOCK_DONE)
        assert geti(VIEW_CHARACTER_FLAG) == 9

    # "create the menu character": the shadow range override runs at rest only
    def create(expect):
        mu.reg_write(A.UC_ARM64_REG_SP, STACK + 0x8000)
        mu.reg_write(A.UC_ARM64_REG_X19, MENU)
        mu.reg_write(A.UC_ARM64_REG_X8, 0xBAD)
        mu.emu_start(p.symbols["keep_shadow"], expect, count=200)
        assert mu.reg_read(A.UC_ARM64_REG_PC) == expect, hex(mu.reg_read(A.UC_ARM64_REG_PC))
        assert mu.reg_read(A.UC_ARM64_REG_X19) == MENU and mu.reg_read(A.UC_ARM64_REG_SP) == STACK + 0x8000
        return mu.reg_read(A.UC_ARM64_REG_X8)

    seti(SLIDE_FLAG, 0)
    assert create(CREATE_SHADOW + 4) == MENU_SCENES & ~0xFFF
    for v in (SLIDE_STASH, 1, SLIDE_FRAMES, STYLE_FRAMES - 1):
        seti(SLIDE_FLAG, v)
        create(CREATE_SHADOW_DONE)
    print("  menu hooks: page view, a whole transition frame by frame, the delete and the created patches emulated")


# ---------------------------------------------------------------- page zoom (build.py --npcs)

UI_SCENE_CONFIG = "gsys/ModelSceneConfigParam/UI.gsys__ModelSceneConfigParam.bgyml"


def ui_shadow_range():
    """(near, far) of the menu scene's depth shadow, from the scene config: metres from
    the camera between which shadows are drawn (one cascade)."""
    cfg = byml.load(open(os.path.join(ROMFS, UI_SCENE_CONFIG), "rb").read())
    assert int(cfg["DepthShadowCascadeNum0"]) == 1
    near, far = float(cfg["DepthShadowNear00"]), float(cfg["DepthShadowFar0"])
    assert (round(near, 3), round(far, 3)) == (2.0, 8.4), (near, far)
    return near, far


def zoom_blend_hook(p, n_npc, npc):
    """The full build's zoom of the menu character's view (display type 0), in place of
    the NPC session's zoom_hook (same place, same table: its zoom_factors() and
    menu_pivots(); the camera position and aim point are scaled about the character's
    feet by head height / Link's head height, so every character fills the frame as Link
    does). On the Characters page and, since 2026-11-10 (snaildev: tall characters were cut
    off on the equipment screen), on the + menu's equipment page too: there every
    category's camera pair is scaled the same way; off our page a factor of 1 touches
    nothing at all. Two differences to the NPC session's:
    - the factor follows VIEW_CHARACTER_FLAG and, while SLIDE_FLAG counts (any value but
      0 and SLIDE_STASH; 1 .. SLIDE_FRAMES), glides from that character's factor to the
      stored character's with a smoothstep of the count: the picture zooms during the slide;
    - the menu scene's depth shadow range is scaled with it. The range is metres from the
      camera (scene config: 2.0 .. 8.4), so with the camera pulled back a tall character's
      shadow was cut off (snaildev, 2026-11-10: Zelda's shadow stays cut). The game's own
      override is used: flag 2 in the scene's +0xd14 and the two floats, as "create the
      menu character" does; a display mode request ends an override. With factor 1 the
      scene is not touched unless the override flag is set (then: the config's numbers,
      flag off).
    Returns the factors, as the NPC session's zoom_hook does."""
    img = p.img
    assert img.dis(CAMERA_BLOCK_NEXT, 2) == ["8289d1f0: adrp     x11, #0x84c9e000", "8289d1f4: ldr      w10, [x10, #0x30c]"]
    assert img.dis(0x8289d180, 6) == [
        "8289d180: ldr      s8, [x11, #0x22c]", "8289d184: ldr      s10, [x11, #0x230]",
        "8289d188: ldr      s9, [x11, #0x234]", "8289d18c: ldr      s0, [x11, #0x238]",
        "8289d190: ldr      s15, [x11, #0x23c]", "8289d194: ldr      s14, [x11, #0x240]"]
    # the scene and its shadow numbers as "create the menu character" reaches them
    assert img.dis(0x80c38660, 7) == [
        "80c38660: ldr      w9, [x8, #0x50]", "80c38664: ldr      x8, [x8, #0x58]", "80c38668: cmp      w9, #1",
        "80c3866c: mov      w9, #0x60", "80c38670: csel     x9, x9, xzr, hi", "80c38674: ldr      x8, [x8, x9]",
        "80c38678: cbz      x8, #0x80c386b0"]
    assert img.dis(0x80c3868c, 9) == [
        "80c3868c: strh     w9, [x8, #0xd14]", "80c38690: ldr      x9, [x8, #0xd60]", "80c38694: ldr      x9, [x9, #0x330]",
        "80c38698: str      w10, [x9]", "80c3869c: mov      w10, #0x40c00000", "80c386a0: ldr      x8, [x8, #0xd60]",
        "80c386a4: ldrsw    x9, [x8, #0x250]", "80c386a8: ldr      x8, [x8, #0x330]", "80c386ac: str      w10, [x8, x9, lsl #2]"]
    gamedata_subs(p)
    total = len(CHARACTERS) + n_npc
    factors = npc.zoom_factors()
    assert len(factors) == total + len(npc.EXPERIMENTS)
    table = p.rodata("zoom_factors", b"".join(struct.pack("<2H", *(round(f * 256) for f in row)) for row in factors))
    pivots = p.rodata("zoom_pivots", b"".join(struct.pack("<3f", *piv) for piv in npc.menu_pivots()))
    near, far = ui_shadow_range()
    consts = p.rodata("zoom_consts", struct.pack("<3f", near, far, 1.0 / SLIDE_FRAMES))
    assert (consts & 0xFFF) % 4 == 0 and (consts & 0xFFF) + 12 <= 0x1000
    scale = "\n".join("fsub {r}, {r}, {p}\n fmul {r}, {r}, s19\n fadd {r}, {r}, {p}".format(r=r, p=piv) for r, piv in (
        ("s8", "s18"), ("s10", "s20"), ("s9", "s21"), ("s0", "s18"), ("s15", "s20"), ("s14", "s21")))
    # At the hook (every path of the camera update): x9 display type, x10 menu actor
    # manager, x20 and the camera floats s0, s3-s5, s8-s15 are live; x11-x17 and v16-v31
    # are free (the NPC session's zoom_hook checks that); the function keeps its return
    # address in its frame, so bl is fine. Our GameData routines change x12, x13, x15-x17.
    p.cave("zoom_hook", """
        cbnz w9, zb_out
        {mgr}
        {h_page}
        bl #{gd_bool:#x}
        fmov s22, w12
        {h_child}
        bl #{gd_bool:#x}
        cmp w12, #0
        cset w12, eq
        fmov s17, w12
        {h_view}
        bl #{gd_int:#x}
        cbz x12, zb_out
        ldr w12, [x12, #0x1c]
        bl zb_factor
        fmov s19, s16
        {h_slide}
        bl #{gd_int:#x}
        cbz x12, zb_apply
        ldr w13, [x12, #0x1c]
        cbz w13, zb_apply
        cmp w13, #{stash}
        b.eq zb_apply
        mov w14, #{frames}
        cmp w13, w14
        csel w13, w13, w14, lo
        scvtf s18, w13
        adrp x13, #{consts_page:#x}
        ldr s20, [x13, #{inv_n:#x}]
        fmul s18, s18, s20
        {h_char}
        bl #{gd_int:#x}
        cbz x12, zb_apply
        ldr w12, [x12, #0x1c]
        bl zb_factor
        fmov s20, #3.0
        fadd s21, s18, s18
        fsub s20, s20, s21
        fmul s18, s18, s18
        fmul s18, s18, s20
        fsub s16, s16, s19
        fmadd s19, s16, s18, s19
    zb_apply:
        fmov s16, #1.0
        fcmp s19, s16
        b.eq zb_shadow
        fmov w15, s17
        adrp x13, #{pivot_page:#x}
        add x13, x13, #{pivot_off:#x}
        mov w14, #12
        umaddl x13, w15, w14, x13
        ldr s18, [x13]
        ldr s20, [x13, #4]
        ldr s21, [x13, #8]
        {scale}
    zb_shadow:
        adrp x12, #{scenes_page:#x}
        ldr x12, [x12, #{scenes_off:#x}]
        cbz x12, zb_out
        ldr w13, [x12, #0x50]
        cmp w13, #1
        b.ls zb_out
        ldr x12, [x12, #0x58]
        ldr x12, [x12, #0x60]
        cbz x12, zb_out
        ldrh w13, [x12, #{flags:#x}]
        fmov s16, #1.0
        fcmp s19, s16
        b.ne zb_on
        fmov w15, s22
        cbz w15, zb_out
        tbz w13, #1, zb_out
        and w13, w13, #0xfffffffd
        b zb_write
    zb_on:
        orr w13, w13, #2
    zb_write:
        strh w13, [x12, #{flags:#x}]
        ldr x12, [x12, #{shadow:#x}]
        cbz x12, zb_out
        ldrsw x14, [x12, #0x250]
        ldr x12, [x12, #0x330]
        cbz x12, zb_out
        adrp x13, #{consts_page:#x}
        ldr s16, [x13, #{near:#x}]
        fmul s16, s16, s19
        str s16, [x12]
        ldr s16, [x13, #{far:#x}]
        fmul s16, s16, s19
        str s16, [x12, x14, lsl #2]
    zb_out:
        adrp x11, #0x84c9e000
        b #{resume:#x}
    zb_factor:
        sub w14, w12, #{exp_base}
        cmp w14, #{n_exp}
        b.hs zf_ring
        add w12, w14, #{total}
        b zf_index
    zf_ring:
        mov w13, #{total}
        sdiv w14, w12, w13
        msub w12, w14, w13, w12
        add w14, w12, w13
        cmp w12, #0
        csel w12, w14, w12, lt
    zf_index:
        fmov w15, s17
        add w12, w15, w12, lsl #1
        adrp x13, #{table_page:#x}
        add x13, x13, #{table_off:#x}
        ldrh w14, [x13, w12, uxtw #1]
        ucvtf s16, w14, #8
        ret
    """.format(mgr=_mgr_asm("zb_out"), h_page=_hash_asm(PAGE_VIEW_FLAG), h_child=_hash_asm("Sequence_IsChildhood"),
               h_view=_hash_asm(VIEW_CHARACTER_FLAG), h_slide=_hash_asm(SLIDE_FLAG), h_char=_hash_asm(CHARACTER_FLAG),
               gd_bool=p.symbols["gd_bool"], gd_int=p.symbols["gd_int"], stash=SLIDE_STASH, frames=SLIDE_FRAMES,
               consts_page=consts & ~0xFFF, near=consts & 0xFFF, far=(consts & 0xFFF) + 4, inv_n=(consts & 0xFFF) + 8,
               pivot_page=pivots & ~0xFFF, pivot_off=pivots & 0xFFF, table_page=table & ~0xFFF, table_off=table & 0xFFF,
               scenes_page=MENU_SCENES & ~0xFFF, scenes_off=MENU_SCENES & 0xFFF, flags=SCENE_SHADOW_FLAGS,
               shadow=SCENE_SHADOW, scale=scale, exp_base=npc.EXPERIMENT_BASE, n_exp=len(npc.EXPERIMENTS), total=total,
               resume=CAMERA_BLOCK_NEXT + 4))
    p.asm(CAMERA_BLOCK_NEXT, "b {zoom_hook}", expect="adrp x11, #0x84c9e000")
    return factors


def check_zoom_blend(p, n_npc, factors, npc):
    """Run zoom_blend_hook in unicorn: at rest for every table index and both ages (the
    camera scaled about the feet by the table's factor, or bit for bit untouched), off the
    page and in other views, through slides between characters of different sizes, and
    what it does to the scene's shadow range. Returns (runs, zoomed runs)."""
    import unicorn
    from unicorn import arm64_const as A
    hook = p.symbols["zoom_hook"]
    uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    uc.reg_write(A.UC_ARM64_REG_CPACR_EL1, uc.reg_read(A.UC_ARM64_REG_CPACR_EL1) | (3 << 20))
    mapped = set()

    def need(addr, size=1):
        for pg in range(addr & ~0xFFF, (addr + size + 0xFFF) & ~0xFFF, 0x1000):
            if pg not in mapped:
                uc.mem_map(pg, 0x1000)
                mapped.add(pg)

    for addr, data in p.patches:
        if addr >= 0x83c66000:
            need(addr, len(data))
            uc.mem_write(addr, bytes(data))
    for a in (GAMEDATA_MGR_GOT, CAMERA_BLOCK_NEXT, MENU_SCENES):
        need(a, 8)
    heap = 0x10000000
    uc.mem_map(heap, 0x20000)
    holder, mgr = heap, heap + 0x100
    bool_map, bool_rec, int_map, int_rec = heap + 0x1000, heap + 0x2000, heap + 0x3000, heap + 0x4000
    uc.mem_write(GAMEDATA_MGR_GOT, struct.pack("<Q", holder))
    uc.mem_write(holder, struct.pack("<Q", mgr))
    bcap, icap = 53, 61
    uc.mem_write(mgr + 0x20, struct.pack("<Q", bool_map))
    uc.mem_write(mgr + 0x2C, struct.pack("<I", bcap))
    uc.mem_write(mgr + 0x10, struct.pack("<I", 30))
    uc.mem_write(mgr + 0x18, struct.pack("<Q", bool_rec))
    uc.mem_write(mgr + 0xE0, struct.pack("<Q", int_map))
    uc.mem_write(mgr + 0xEC, struct.pack("<I", icap))
    uc.mem_write(mgr + 0xD0, struct.pack("<I", 40))
    uc.mem_write(mgr + 0xD8, struct.pack("<Q", int_rec))
    h = {n: gamedata.murmur3_32(n) for n in (PAGE_VIEW_FLAG, "Sequence_IsChildhood", VIEW_CHARACTER_FLAG,
                                             CHARACTER_FLAG, SLIDE_FLAG)}

    def put(table, cap, key, index, stranger=False):
        at = key % cap
        while struct.unpack("<I", bytes(uc.mem_read(table + 8 * at, 4)))[0]:
            at = (at + 1) % cap
        uc.mem_write(table + 8 * at, struct.pack("<II", key ^ 0x77 if stranger else key, index))

    put(bool_map, bcap, h[PAGE_VIEW_FLAG], 1, stranger=True)    # a stranger in our flag's slot: probing is exercised
    put(bool_map, bcap, h[PAGE_VIEW_FLAG], 5)
    put(bool_map, bcap, h["Sequence_IsChildhood"], 9)
    put(int_map, icap, h[SLIDE_FLAG], 3, stranger=True)
    put(int_map, icap, h[VIEW_CHARACTER_FLAG], 7)
    put(int_map, icap, h[CHARACTER_FLAG], 11)
    put(int_map, icap, h[SLIDE_FLAG], 13)
    # the scenes: two, the second one with its shadow numbers
    scenes, array, scene, shadow, floats = heap + 0x8000, heap + 0x8100, heap + 0x9000, heap + 0xB000, heap + 0xC000
    uc.mem_write(MENU_SCENES, struct.pack("<Q", scenes))
    uc.mem_write(scenes + 0x50, struct.pack("<I", 2))
    uc.mem_write(scenes + 0x58, struct.pack("<Q", array))
    uc.mem_write(array, struct.pack("<Q", heap + 0x1F000))       # scene 0: must not be touched
    uc.mem_write(array + 0x60, struct.pack("<Q", scene))
    uc.mem_write(scene + SCENE_SHADOW, struct.pack("<Q", shadow))
    uc.mem_write(shadow + 0x250, struct.pack("<i", 1))
    uc.mem_write(shadow + 0x330, struct.pack("<Q", floats))
    near, far = ui_shadow_range()
    pivots = npc.menu_pivots()
    total = len(CHARACTERS) + n_npc
    fregs = [A.UC_ARM64_REG_S8, A.UC_ARM64_REG_S10, A.UC_ARM64_REG_S9, A.UC_ARM64_REG_S0, A.UC_ARM64_REG_S15,
             A.UC_ARM64_REG_S14, A.UC_ARM64_REG_S13, A.UC_ARM64_REG_S12, A.UC_ARM64_REG_S11, A.UC_ARM64_REG_S5,
             A.UC_ARM64_REG_S4, A.UC_ARM64_REG_S3]
    camera = [0.07, 1.15, 4.5, 0.07, 0.95, 0.0, 0.0, 1.0, 0.0, 0.1, 10.0, 0.349]

    def f32(x):
        return struct.unpack("<f", struct.pack("<f", x))[0]

    def run(display, page, child, view, stored=None, slide=0, flags=0x4, numbers=None):
        """-> (camera floats, scene flags, [near, far, the float after])"""
        numbers = numbers or (near, far)
        uc.mem_write(bool_rec + 0x18 * 5 + 0x16, bytes([page]))
        uc.mem_write(bool_rec + 0x18 * 9 + 0x16, bytes([child]))
        uc.mem_write(int_rec + 0x28 * 7 + 0x1C, struct.pack("<i", view))
        uc.mem_write(int_rec + 0x28 * 11 + 0x1C, struct.pack("<i", view if stored is None else stored))
        uc.mem_write(int_rec + 0x28 * 13 + 0x1C, struct.pack("<i", slide))
        uc.mem_write(scene + SCENE_SHADOW_FLAGS, struct.pack("<H", flags))
        uc.mem_write(floats, struct.pack("<3f", numbers[0], numbers[1], 1234.5))
        for reg, v in zip(fregs, camera):
            uc.reg_write(reg, struct.unpack("<I", struct.pack("<f", v))[0])
        uc.reg_write(A.UC_ARM64_REG_X9, display)
        uc.reg_write(A.UC_ARM64_REG_X10, 0xABCD0000)
        uc.reg_write(A.UC_ARM64_REG_X20, 0x1234)
        uc.reg_write(A.UC_ARM64_REG_X19, 0x4321)
        uc.reg_write(A.UC_ARM64_REG_SP, heap + 0x1E000)
        uc.emu_start(hook, CAMERA_BLOCK_NEXT + 4, count=4000)
        assert uc.reg_read(A.UC_ARM64_REG_PC) == CAMERA_BLOCK_NEXT + 4 and uc.reg_read(A.UC_ARM64_REG_SP) == heap + 0x1E000
        assert [uc.reg_read(r) for r in (A.UC_ARM64_REG_X9, A.UC_ARM64_REG_X10, A.UC_ARM64_REG_X20, A.UC_ARM64_REG_X19)] == \
            [display, 0xABCD0000, 0x1234, 0x4321]
        assert uc.reg_read(A.UC_ARM64_REG_X11) == 0x84c9e000
        assert bytes(uc.mem_read(int_rec + 0x28 * 13 + 0x1C, 4)) == struct.pack("<i", slide)       # the count is the view patch's
        cam = [struct.unpack("<f", struct.pack("<I", uc.reg_read(r) & 0xFFFFFFFF))[0] for r in fregs]
        return cam, struct.unpack("<H", bytes(uc.mem_read(scene + SCENE_SHADOW_FLAGS, 2)))[0], \
            list(struct.unpack("<3f", bytes(uc.mem_read(floats, 12))))

    def index_of(value):
        e = value - npc.EXPERIMENT_BASE
        return total + e if 0 <= e < len(npc.EXPERIMENTS) else value % total

    def factor(value, age):
        return round(factors[index_of(value)][age] * 256) / 256

    plain = [f32(c) for c in camera]

    def scaled(f, age):
        want = list(plain)
        for k in range(6):
            want[k] = f32(f32(f32(plain[k] - f32(pivots[age][k % 3])) * f32(f)) + f32(pivots[age][k % 3]))
        return want

    untouched = (0x4, [f32(near), f32(far), 1234.5])
    runs = zoomed = 0
    values = list(range(-3, total + 3)) + [npc.EXPERIMENT_BASE + i for i in range(-1, len(npc.EXPERIMENTS) + 1)] + \
        [-total * 3 - 1]
    for value in values:
        for child in (1, 0):
            age = 0 if child else 1
            f = factor(value, age)
            for display, page in ((0, 1), (0, 0), (1, 1), (3, 1), (5, 1)):
                cam, flags, numbers = run(display, page, child, value)
                if display == 0 and f != 1.0:              # our page and the + menu's equipment page alike
                    assert cam == scaled(f, age), (value, child, cam)
                    assert flags == 0x6 and numbers == [f32(f32(near) * f32(f)), f32(f32(far) * f32(f)), 1234.5], (value, numbers)
                    zoomed += 1
                else:
                    assert cam == plain and (flags, numbers) == untouched, (value, child, display, page)   # bit for bit
                runs += 1
    # factor 1 with an override left on (ours from the last character, or the game's): the config's numbers, flag off
    one = next(v for v in range(total) if factor(v, 0) == 1.0)
    big = max(range(total), key=lambda v: factor(v, 0))
    small = min(range(total), key=lambda v: factor(v, 0))
    assert factor(big, 0) > 1.2, "no tall character to test with"
    cam, flags, numbers = run(0, 1, 1, one, flags=0x2006, numbers=(1.7, 6.0))
    assert cam == plain and flags == 0x2004 and numbers == [f32(near), f32(far), 1234.5]
    assert run(1, 1, 1, one, flags=0x6, numbers=(1.7, 6.0))[1:] == (0x6, [f32(1.7), f32(6.0), 1234.5])     # other views: not ours
    # a slide: the old character's factor while the new one loads, then a smoothstep to the new one's
    for a, b in ((one, big), (big, one), (big, small), (one, one)):
        fa, fb = factor(a, 0), factor(b, 0)
        cam, flags, numbers = run(0, 1, 1, a, stored=b, slide=SLIDE_STASH)
        assert cam == (scaled(fa, 0) if fa != 1.0 else plain), (a, b)
        last = fa
        for k in list(range(1, SLIDE_FRAMES + 1)) + [SLIDE_FRAMES + 5, STYLE_FRAMES - 1]:
            t = min(k, SLIDE_FRAMES) / SLIDE_FRAMES
            f = fa + (fb - fa) * t * t * (3 - 2 * t)
            cam, flags, numbers = run(0, 1, 1, a, stored=b, slide=k)
            want = scaled(f, 0)
            assert all(abs(x - y) <= 2e-5 * max(1.0, abs(y)) for x, y in zip(cam, want)), (a, b, k, cam, want)
            got = (cam[2] - pivots[0][2]) / (plain[2] - pivots[0][2])
            assert min(fa, fb) - 1e-4 <= got <= max(fa, fb) + 1e-4 and (got - last) * (fb - fa) >= -1e-6, (a, b, k, got)
            last = got
            if abs(got - 1.0) > 1e-6:
                assert flags == 0x6 and abs(numbers[0] - near * got) < 1e-4 and abs(numbers[1] - far * got) < 1e-4
            runs += 1
        assert abs(last - fb) < 1e-5, (a, b, last)
        if fb == 1.0:
            assert cam == plain, "the slide does not end bit for bit at Link's view"
    # off our page a factor of 1 leaves the game's own override alone
    assert run(0, 0, 1, one, flags=0x6, numbers=(1.7, 6.0)) == (plain, 0x6, [f32(1.7), f32(6.0), 1234.5])
    # the view character unknown to the game: untouched
    uc.mem_write(int_map, bytes(8 * icap))
    assert run(0, 1, 1, big) == (plain,) + untouched
    return runs, zoomed


def check_model_hook(p):
    """Disassemble the hook and check what the assembler made of the
    address-forming instructions (keystone takes absolute targets)."""
    img = p.img
    hook = p.symbols["model_hook"]
    code = dict(p.patches)[hook]
    ins = list(img.cs.disasm(code, hook))
    assert len(ins) * 4 == len(code)
    page = lambda a: "#%#x" % (a & ~0xFFF)  # noqa: E731
    adrp = [i.op_str for i in ins if i.mnemonic == "adrp"]
    assert adrp == ["x9, " + page(MODEL_PATH_FMT), "x10, " + page(p.symbols["base_names"]),
                    "x9, " + page(GAMEDATA_MGR_GOT), "x10, " + page(p.symbols["model_names"])], adrp
    adds = [ins[k + 1].op_str for k, i in enumerate(ins) if i.mnemonic == "adrp" and ins[k + 1].mnemonic == "add"]
    assert adds == ["x9, x9, #%#x" % (MODEL_PATH_FMT & 0xFFF), "x10, x10, #%#x" % (p.symbols["base_names"] & 0xFFF),
                    "x10, x10, #%#x" % (p.symbols["model_names"] & 0xFFF)], adds
    assert ins[-1].mnemonic == "b" and ins[-1].op_str == "#%#x" % STRING_FORMAT
    # the game's own Int read that the hook copies (manager via the same GOT entry,
    # map +0xe0 / capacity +0xec, records +0xd8 / count +0xd0, stride 0x28, value +0x1c)
    seen = "\n".join(img.dis(0x8019de5c, 60))
    for part in ("adrp     x20, #0x84c9e000", "ldr      x20, [x20, #0x180]", "ldr      x23, [x20]",
                 "ldr      x9, [x23, #0xe0]", "ldr      w10, [x23, #0xec]", "ldr      w10, [x23, #0xd0]",
                 "ldr      x11, [x23, #0xd8]", "mov      w10, #0x28", "ldr      w8, [x8, #0x1c]"):
        assert part in seen, part


# ---------------------------------------------------------------- page layout (test folder)

COMMON_LAYOUTS = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.blarc.zs"
LAYOUT_CATEGORY = "UI/LayoutArchiveCategory.Product.100.byml.zs"
MALS = ["Mals/USen.Product.110.sarc.zs", "Mals/EUen.Product.110.sarc.zs"]
LAYOUT_COPIES = 4                   # every layout is stored 4 times in its archive
PAGE_OWN_LAYOUT = "CharacterMenu_00"
# The light behind Link. The "light circle" of the placeholder page is the clear
# middle of Key Items' vignette: window pane W_Window_00 (4080 x 2160 layout units,
# screen 3840 x 2160, colour palette 4 = near black) drawn as four mirrored
# quadrants of the alpha mask ImportantFade_00 (1360 x 720, shown at 2040 x 1080 per
# quadrant; clear at the screen centre, half dark 1080 units out sideways and 648
# up and down, 5 % at 912 / 482, 98 % at 1344 / 894). It is 2D, so no camera or
# picture offset moves it, and it is drawn over the 3D Link (it darkens his boots).
# Here the window is put on Link. Link (child) on screen, from the camera block: soles
# 877 units below the screen centre, top of the head about 610 above.
#   2026-11-09 first cut: centre (640, 0), half dark 700 x 648: passed in game
#   ("looks perfect"), but the lower part of his boots and his shadow on the floor
#   (cast to his right) were in the dark -> slightly larger, taller, and centred
#   lower so the extra reach goes to his feet rather than above his head.
SPOT_X = LINK_SCREEN_X * 1920       # layout units right of the screen centre (640)
SPOT_Y = -150.0                     # layout units above the screen centre (negative = lower)
SPOT_HALF_WIDTH = 800.0             # half darkness, sideways (Key Items: 1080)
SPOT_HALF_HEIGHT = 850.0            # half darkness, up and down (Key Items: 648)
MASK_QUADRANT = (2040.0, 1080.0)    # one quadrant of the window, layout units
MASK_HALF_DARK_X = 1080.0           # from the hole centre, in an unscaled quadrant
MASK_HALF_DARK_Y = 648.0


def page_layout():
    """The Characters page's own layout as a layout.Layout: Key Items' layout
    (ImportantMenu_01: vignette, fade and slide animations) under the name
    PAGE_OWN_LAYOUT, with the vignette's hole on Link (SPOT_X, SPOT_Y) and
    SPOT_HALF_WIDTH x SPOT_HALF_HEIGHT large. Only values change, no structure:
      pane W_Window_00: translate; scale x so the window still covers the screen;
      scale y = the height wanted (the whole window grows, which also covers the
      screen for any height the vanilla size or taller, centred this low).
      its mask texture map (centre pivot, u' = sx * (u - 0.5) + 0.5 + tx, clamp to
      edge; vanilla sx = -1 mirrors the mask into the top-left quadrant): sx = -k
      and tx = (k - 1) / 2 give u' = k * (1 - u), the same picture squeezed towards
      the hole by k, the rest of the quadrant filled with the mask's opaque edge.
    (Convention checked on vanilla maps: flips and rotations with zero translate
    need the centre pivot; ChallengeMenuMask_00 with scale 2, translate -0.5 only
    makes sense with + tx, clamping on its uniform left edge.)"""
    files = nxfmt.sarc_files(nxfmt.read(os.path.join(ROMFS, COMMON_LAYOUTS)))
    lay = layout.Layout(files[PAGE_LAYOUT + ".buil"])
    assert layout.build(lay) == files[PAGE_LAYOUT + ".buil"], "layout rebuild not exact"
    ed = uiedit.Editor(lay)
    ed.validate()
    win = ed.pane("W_Window_00")
    assert (win.pos, win.scale, win.size, win.rotation) == ((0.0, 0.0), (1.0, 1.0), (4080.0, 2160.0), 0.0)
    assert win.size == (2 * MASK_QUADRANT[0], 2 * MASK_QUADRANT[1])
    sx = (1920.0 + abs(SPOT_X) + 40.0) / MASK_QUADRANT[0]      # covers both screen edges, 40 units spare
    sy = SPOT_HALF_HEIGHT / MASK_HALF_DARK_Y                    # height by pane scale (v map untouched)
    assert sy * MASK_QUADRANT[1] >= 1080.0 + abs(SPOT_Y) + 40.0, "window would not cover the screen top/bottom"
    win.pos = (SPOT_X, SPOT_Y)
    win.scale = (sx, sy)
    maps = lay.root.ptr[0x40].get("W_Window_00").ptr[0x0].items
    assert len(maps) == 2
    mask = maps[1]                                              # [0] is the noise (DecoNoise_01, tiled)
    assert mask.get("<5fI", 0x10) == (-1.0, 1.0, 0.0, 0.0, 0.0, 0), mask.get("<5fI", 0x10)
    k = MASK_HALF_DARK_X * sx / SPOT_HALF_WIDTH
    assert k >= 1.0
    mask.set("<f", 0x10, -k)
    mask.set("<f", 0x18, (k - 1.0) / 2)
    uiedit.rename(lay, PAGE_OWN_LAYOUT)
    return lay


def built(owner, rel):
    """A file as mod `owner` ships it in this build (or the untouched romfs file)."""
    path = os.path.join(BUILD, owner, "romfs", rel)
    return open(path if os.path.exists(path) else os.path.join(ROMFS, rel), "rb").read()


def character_layout_test(mod):
    """Test folder: the Characters page with its own layout (page_layout).

    Mod folders are layered in name order and the first one that has a file wins
    (yuzu's LayeredFS), so this folder, which sorts before the others, carries
    its own versions of the files it changes and nothing else changes when it is
    switched off in the emulator's Add-Ons list:
      layout archive (Header four tabs' version + our layout, 4 copies),
      UI/LayoutArchiveCategory (our layout listed under Common),
      RSDB/UIScreen (Characters page's version, our screen -> our layout),
      Mals (Header four tabs' versions + LayoutMsg/<our layout>.msbt, a copy of
      Key Items' messages: text panes look their text up by layout name).
    Recipe from the UI sandbox (mods\\ui_sandbox.py, tools\\sarcedit.py)."""
    assert mod < min(MODS), "the test folder must sort before the mods it overrides"
    lay = page_layout()
    data = layout.build(lay)
    check = layout.Layout(data)
    uiedit.Editor(check).validate()
    assert layout.build(check) == data and check.name == PAGE_OWN_LAYOUT
    with open(os.path.join(BUILD, PAGE_OWN_LAYOUT + ".buil"), "wb") as f:
        f.write(data)

    raw = built("Header four tabs", COMMON_LAYOUTS)
    sarc = nxfmt.unzs(raw, COMMON_LAYOUTS)
    name = PAGE_OWN_LAYOUT + ".buil"
    assert sarcedit.build(*sarcedit.parse(sarc)) == sarc, "layout archive rebuild not exact"
    assert not any(n == name for n, _, _ in nxfmt.sarc_entries(sarc))
    new = sarcedit.add(sarc, [(name, data)] * LAYOUT_COPIES)
    assert [n for n, _, _ in nxfmt.sarc_entries(new)].count(name) == LAYOUT_COPIES
    write(mod, COMMON_LAYOUTS, nxpack.zs_compress_like(new, raw), len(new), SIZES, size=((len(new) + 31) & ~31) + 4144)

    raw = open(os.path.join(ROMFS, LAYOUT_CATEGORY), "rb").read()
    cat_data = nxfmt.unzs(raw, LAYOUT_CATEGORY)
    cat = byml.load(cat_data)
    assert byml.dump(cat, byml.version_of(cat_data)) == cat_data
    names = cat["Common"]
    assert PAGE_LAYOUT in names and PAGE_OWN_LAYOUT not in names
    # the list is in file-name order ("_" before digits and letters, case ignored);
    # ours goes before the first name that is greater
    at = next(i for i, n in enumerate(names) if n.lower() > PAGE_OWN_LAYOUT.lower())
    assert at > 0 and names[at - 1].lower() < PAGE_OWN_LAYOUT.lower()
    names.insert(at, PAGE_OWN_LAYOUT)
    new = byml.dump(cat, byml.version_of(cat_data))
    write(mod, LAYOUT_CATEGORY, nxpack.zs_compress_like(new, raw), len(new), SIZES, size=((len(new) + 31) & ~31) + 264)

    raw = built("Characters page", UISCREEN)
    table = nxfmt.unzs(raw, UISCREEN)
    rows = byml.load(table)
    row = [r for r in rows if r["Name"] == PAGE_SCREEN]
    assert len(row) == 1 and row[0]["BodyLayoutFile"] == PAGE_LAYOUT, row
    row[0]["BodyLayoutFile"] = PAGE_OWN_LAYOUT
    new = byml.dump(rows, byml.version_of(table))
    write(mod, UISCREEN, nxpack.zs_compress_like(new, raw), len(new), SIZES)

    for rel in MALS:
        raw = built("Header four tabs", rel)
        sarc = nxfmt.unzs(raw, rel)
        files = nxfmt.sarc_files(sarc)
        msg = "LayoutMsg/%s.msbt" % PAGE_OWN_LAYOUT
        assert msg not in files
        new = sarcedit.add(sarc, [(msg, files["LayoutMsg/%s.msbt" % PAGE_LAYOUT])])
        write(mod, rel, nxpack.zs_compress_like(new, raw), len(new), SIZES, size=((len(new) + 31) & ~31) + 312)


# ---------------------------------------------------------------- main

MODS = {
    "Characters page": characters_page,
    "Header four tabs": header_four_tabs,  # exe patch, kept separate so it can be switched off alone
    "Character models": character_models,  # flag + models + exe patch, kept separate the same way
}
# Test folders: built after MODS from their output, installed like them, but each
# only overrides files of the regular mods while it is enabled (see the function).
EXPERIMENTS = {
    "Character layout (test)": character_layout_test,
}
RETIRED = ["Test 1 - Characters tab name", "Test 2 - L on Key Items", "Test 3 - Characters page",
           "Test 4 - Characters page 3D", "Test 5 - Characters page Link", "Test 6 - Characters camera 0",
           "Test 7 - Characters no camera", "Test 8 - Characters fade"]
EMULATORS = ("lichii.exe", "eden.exe", "yuzu.exe")


def emulator_running():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout.lower()
    return [e for e in EMULATORS if '"%s"' % e in out]


# ---------------------------------------------------------------- portraits in the picker's slots
# One head-and-shoulders picture per character, made by the NPC session from in-game
# captures (build-npc\portraits\cut-fit\<nn>_<name>.png, nn = the stored character number;
# README, mods list, "NPC characters", "Portraits, full run"). They are read at build time,
# never copied. Each becomes a texture of its own in the layout archive's texture file
# (ASTC 4x4 sRGB, the format of the game's own colour textures there; tools\bntxwrite.py)
# and a picture pane in its sheet's container of the picker layout: drawn after the slot
# parts, so over the slot's background and its cursor, which is where the inventory slot
# draws its item icon too (PaItemMenuBtn_00: N_Item_00 comes after the cursor panes). The
# pane's material and shader pass are the game's icon picture's (PaReplaceIcon_00:
# P_ReplaceIcon_00, a plain texture with alpha), its texture name ours. The game's icons
# store straight alpha with the colour carried on into the transparent part; so do ours.
# Stored character numbers the picker does not offer (snaildev, 2026-11-10): 1 = Retro Link,
# somebody else's model mod, not credited yet; it is to come back under a section of its
# own for mods. The numbers themselves stay as they are (the saves, the model hook, the
# NPC session's portraits all go by them): the picker's list just leaves these out, so a
# place in the list and the stored number are two things (Picker.number). A save that
# has such a number shows that character and the picker without an orange slot.
PICKER_HIDDEN = [1]
PORTRAIT_DIR = os.path.join(ROOT, "build-npc", "portraits", "cut-fit")
PORTRAIT_PIXELS = 256               # a slot is about 260 layout units = 260 pixels at 4K
PORTRAIT_SIZE = 232.0               # layout units; the slot's shape is 259 x 263
PORTRAIT_TEXTURE = "CharacterPortrait_%03d"
PORTRAIT_NONE = "CharacterPortrait_None"    # for a character without a picture
COMMON_TEXTURES = "UI/Layout2Archive/Common.Product.100.Nin_Ounce_NVN2.bntx.zs"


def portrait_files():
    """{character number: path} of the pictures that exist."""
    out = {}
    for name in sorted(os.listdir(PORTRAIT_DIR)) if os.path.isdir(PORTRAIT_DIR) else []:
        if name.lower().endswith(".png") and name[:2].isdigit() and name[2] == "_":
            assert int(name[:2]) not in out, name
            out[int(name[:2])] = os.path.join(PORTRAIT_DIR, name)
    return out


def _carry_colour(rgba, rounds=16):
    """Give fully transparent pixels the colour of their nearest neighbours that have one
    (what the game's icons do), so that filtering does not darken the edge."""
    import numpy as np
    rgb = rgba[:, :, :3].astype(np.float32)
    known = rgba[:, :, 3] > 0
    if not known.any():
        return rgba
    for _ in range(rounds):
        if known.all():
            break
        total = np.zeros_like(rgb)
        count = np.zeros(known.shape, np.float32)
        padded_rgb = np.pad(rgb * known[:, :, None], ((1, 1), (1, 1), (0, 0)))
        padded_known = np.pad(known.astype(np.float32), 1)
        h, w = known.shape
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                total += padded_rgb[dy:dy + h, dx:dx + w]
                count += padded_known[dy:dy + h, dx:dx + w]
        fill = ~known & (count > 0)
        rgb[fill] = total[fill] / count[fill][:, None]
        known = known | fill
    rgb[~known] = rgb[known].mean(axis=0)
    out = rgba.copy()
    out[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    return out


def portrait_pixels(path):
    """A picture file as (PORTRAIT_PIXELS, PORTRAIT_PIXELS, 4) straight-alpha numbers."""
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGBA")
        assert im.size[0] == im.size[1], (path, im.size)
        im = im.convert("RGBa").resize((PORTRAIT_PIXELS, PORTRAIT_PIXELS), Image.LANCZOS).convert("RGBA")
        rgba = np.array(im, dtype=np.uint8)
    assert rgba[:, :, 3].max() > 200, "nothing opaque in " + path
    return _carry_colour(rgba)


def portrait_placeholder():
    """A plain head-and-shoulders shape for a character without a picture."""
    import numpy as np
    n = PORTRAIT_PIXELS
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n
    head = ((x - 0.5) / 0.17) ** 2 + ((y - 0.40) / 0.20) ** 2
    body = ((x - 0.5) / 0.36) ** 2 + ((y - 1.02) / 0.36) ** 2
    shape = np.clip((1.0 - np.minimum(head, body)) * 14.0, 0.0, 1.0)      # soft edge
    rgba = np.zeros((n, n, 4), np.uint8)
    rgba[:, :, :3] = 200
    rgba[:, :, 3] = np.rint(shape * 110).astype(np.uint8)
    return rgba


def portrait_textures(mod, numbers):
    """The layout archive's texture file with our textures added, for the characters
    `numbers` -> {character number: texture name}. The result is kept in build\\_cache by
    what went into it (encoding and packing 250 MB takes a while)."""
    import hashlib
    import bntxwrite
    files = {c: path for c, path in portrait_files().items() if c in numbers}
    assert files, "no portraits in " + PORTRAIT_DIR
    names = {c: PORTRAIT_TEXTURE % c if c in files else PORTRAIT_NONE for c in numbers}
    stock = os.path.join(ROMFS, COMMON_TEXTURES)
    key = hashlib.sha1()
    key.update(repr((PORTRAIT_PIXELS, PORTRAIT_TEXTURE, PORTRAIT_NONE, sorted(files), os.path.getsize(stock))).encode())
    for path in (bntxwrite.__file__,) + tuple(files[c] for c in sorted(files)):
        key.update(open(path, "rb").read())
    cache = os.path.join(BUILD, "_cache", "portraits-%s.bntx.zs" % key.hexdigest()[:16])
    raw = open(stock, "rb").read()
    if os.path.exists(cache):
        packed = open(cache, "rb").read()
        size = len(nxfmt.unzs(packed, COMMON_TEXTURES))
    else:
        pictures = [(PORTRAIT_TEXTURE % c, portrait_pixels(files[c])) for c in sorted(files)]
        pictures.append((PORTRAIT_NONE, portrait_placeholder()))
        data = nxfmt.unzs(raw, COMMON_TEXTURES)
        new = bntxwrite.add_textures(data, pictures)
        # read back: the game's textures untouched, ours decode to what went in
        before, after = bntxwrite.Parsed(data), bntxwrite.Parsed(new)
        assert [(t["name"], t["data"]) for t in after.textures[:len(before.textures)]] == \
            [(t["name"], t["data"]) for t in before.textures]
        for (name, rgba), t in zip(pictures, after.textures[len(before.textures):]):
            n = PORTRAIT_PIXELS
            got = bntxwrite.astc_decode(bntxwrite.deswizzle(t["data"], n // 4, n // 4, 16, struct.unpack_from(
                "<I", t["record"], 0x34)[0]), n, n)
            shown = rgba[:, :, 3] > 8
            err = float(abs(got.astype(int) - rgba.astype(int))[shown].mean())
            assert t["name"] == name and err < 4.0, (name, err)
            # colour with alpha, as the game's own colour textures (the first version took the
            # selectors of a grey + alpha texture: the portraits came out grey; snaildev, 2026-11-10)
            assert t["record"][0x58:0x5C] == bntxwrite.RGBA and struct.unpack_from("<I", t["record"], 0x1C)[0] == 0x2D06
            assert struct.unpack_from("<II", t["record"], 0x24) == (n, n)
        size = len(new)
        packed = nxpack.zs_compress_like(new, raw, level=6)
        assert nxfmt.unzs(packed, COMMON_TEXTURES) == new
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as f:
            f.write(packed)
    write(mod, COMMON_TEXTURES, packed, size, SIZES, size=((size + 31) & ~31) + 8264)
    return names, len(files)


def add_portraits(ui, data, pk, names):
    """The picker layout `data` with one picture pane per character (see the head)."""
    import layout
    import uiedit
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    ed.validate()
    files = nxfmt.sarc_files(nxfmt.unzs(open(os.path.join(ROMFS, COMMON_LAYOUTS), "rb").read(), COMMON_LAYOUTS))
    icon_lay = layout.Layout(files["PaReplaceIcon_00.buil"])
    icon = uiedit.Editor(icon_lay)
    v = icon.pane("P_ReplaceIcon_00")
    mat = icon.dict_items(0x40)[v.material][1]
    first, count = icon._pass_runs()[v.material]
    passes = icon.passes()[first:first + count]
    typed = icon.dict_items(0x60)[v.type_index][1]
    assert count == 1 and passes[0].ptr[0].items[0].ptr[8].text == "FBLayout_00_ASTC4x4"
    assert v.ext == 0xFFFF and v.type == 1 and len(mat.ptr[0].items) == 1       # no window extension, one texture
    assert passes[0].ptr[0x78].text == "ui"
    # the material's colours are palette indices: white and transparent black in both layouts
    white, black = mat.get("<6I", 0x18)[4:6]
    for index in set(mat.get("<6I", 0x18)):
        assert tuple(uiedit.palette(icon_lay)[index]) == tuple(uiedit.palette(lay)[index]), index
    assert tuple(uiedit.palette(lay)[white]) == (1.0, 1.0, 1.0, 1.0) and tuple(uiedit.palette(lay)[black]) == (0.0, 0.0, 0.0, 0.0)
    before = len(ed.panes)
    for s in range(pk.sheets):
        block = []
        for c in pk.on_sheet(s):
            # everything from the icon picture, its pane record too: a plain picture. (The
            # first version copied the page's P_Element_00, which is a window: the texture
            # as a stretched frame, four times too wide; snaildev, 2026-11-10.)
            np_ = uiedit.NewPane("P_Face_%03d" % c, uiedit.deep(v.node), None, uiedit.deep(mat),
                                 [uiedit.deep(p) for p in passes], icon.arr208()[v.material], uiedit.deep(typed), None)
            np_.passes[0].ptr[0].items[0].ptr[8] = layout.cstr(names[pk.number(c)])
            np_.node.set("<2f", 0x10, 1.0, 1.0)
            np_.node.set("<2f", 0x1C, *pk.slot_pos[c % ui.PER_SHEET])
            np_.node.set("<2f", 0x2C, PORTRAIT_SIZE, PORTRAIT_SIZE)
            np_.node.set("<I", 0x24, 0)                             # alpha table entry 0 = 1.0
            np_.node.set("<I", 0x38, np_.node.get("<I", 0x38)[0] | 1)
            block.append(np_)
        ed.insert("N_Sheet_%02d" % s, block)
    ed.validate()
    assert len(ed.panes) == before + pk.n < 512
    out = layout.build(lay)
    check = layout.Layout(out)                  # what the game will read
    again = uiedit.Editor(check)
    again.validate()
    assert layout.build(check) == out
    for c in range(pk.n):
        face = again.pane("P_Face_%03d" % c)
        sheet = again.pane("N_Sheet_%02d" % (c // ui.PER_SHEET))
        slot = again.pane("Pa_Slot_%02d" % (c % ui.PER_SHEET))
        assert face.parent == sheet.index and face.index > slot.index and face.visible
        assert face.ext == 0xFFFF and face.size == (PORTRAIT_SIZE, PORTRAIT_SIZE) and face.node.get("<2f", 0x10) == (1.0, 1.0)
        assert face.node.get("<I", 0x38)[0] == v.node.get("<I", 0x38)[0] and face.node.get("<I", 0x24)[0] == 0
        f, k = again._pass_runs()[face.material]
        assert k == 1 and again.passes()[f].ptr[0].items[0].ptr[8].text == names[pk.number(c)]
    return out


def picker_graph(ui, mod, pk):
    """Sequence/CharacterMenu.module.ainb for the picker. This is the UI session's
    ui_sandbox.picker_graph (stick=True) with one change (snaildev, 2026-11-10): ZL / ZR keep
    the cursor's slot instead of going to the sheet's first one (pk.turn_to). Everything
    else is its text line for line, so its notes apply: one button loop per character;
    every way into a loop ends in that character's gate (two waited frames); what a
    press changes runs before it, in the frame of the press; A stores the number and runs
    the swap, whose steps exist once.
    A turn now is one sequence per target character (and per sound): the sheet with the
    cursor on the target slot at once, the sound, then the orange slot by the saved
    number, then the target's gate. The orange slot is one saved-number query and
    selector per sheet, as before, but as a step inside those sequences with the Mark
    stops as its children, where the sandbox had it under a root node of its own with
    jumps to one "arrive" sequence per slot (which could only arrive at one character)."""
    S = ui.SCREEN
    PER = ui.PER_SHEET
    g, root, chk, common, go = ui.page_graph(picker=True)
    moves_by = ui.release_checker(g)        # the four directions: D-pad or left stick
    n, sheets = pk.n, pk.sheets
    slots = range(PER)

    def stops(anim, count):
        return [g.add(t_stop_anim(S, anim, float(i))) for i in range(count)]

    def per_slot(tag, frame=None):
        return [g.add(t_play_anim(S, "%s_%02d" % (tag, j)) if frame is None else
                      t_stop_anim(S, "%s_%02d" % (tag, j), float(frame))) for j in slots]

    stop_sheet, stop_det, stop_cur = stops("Sheet", sheets), stops("Det", PER), stops("Cur", PER)
    stop_mark = stops("Mark", PER + 1)
    play_in, play_out, play_dec = per_slot("In"), per_slot("Out"), per_slot("Dec")
    # Out stopped at its end and In stopped at its start both leave the slot without a cursor
    end_out, drop_in = per_slot("Out", ui.SLOT_ANIMS["Out"][1]), per_slot("In", 0)
    settle = g.add(t_simultaneous(end_out + drop_in, root=False))       # no In / Out left running
    snd_move, snd_decide = g.add(t_sound("mc_CursorMove")), g.add(t_sound("mc_NormalDecide"))
    snd_right, snd_left = g.add(t_sound("mc_TabToRight")), g.add(t_sound("mc_TabToLeft"))

    loops = [g.add(t_simultaneous([], root=True)) for _ in range(n)]
    gates = [g.add(t_sequential([g.add(t_wait_frames(1)), g.add(t_wait_frames(1))], then=loops[c],
                                root=True, same_frame=False)) for c in range(n)]

    def arrive(c, *first):
        return g.add(t_sequential(list(first), then=gates[c], root=True))

    def place(s, j):
        """The nodes that show sheet s with the cursor on slot j at once."""
        return [settle, stop_sheet[s], stop_det[j], stop_cur[j]]

    starts = [arrive(c, stop_mark[c % PER], *place(*divmod(c, PER))) for c in range(n)]
    start_unknown = arrive(0, stop_mark[ui.NO_MARK], *place(0, 0))

    # by the saved number: on entry and after a swap
    query = ui.flag_query(g)
    sel = g.add(ui.t_int_sel(g, query, [(c, go(starts[c])) for c in range(n)], go(start_unknown)))
    g.nodes[query]["Plugs"]["Generic"][0]["Node Index"] = sel
    init = g.add(t_simultaneous([sel], root=True))

    # ZL / ZR: the new sheet with the cursor on the same slot (the nearest one the sheet has),
    # then the orange slot by the saved number
    marks = []
    for s in range(sheets):
        q = ui.flag_query(g)
        marks.append(g.add(ui.t_int_sel(g, q, [(c, stop_mark[c % PER]) for c in pk.on_sheet(s)],
                                        stop_mark[ui.NO_MARK])))
        g.nodes[q]["Plugs"]["Generic"][0]["Node Index"] = marks[s]
    turns = {}

    def turn(c, sound):
        """The sequence that arrives at character c by a page turn (made when first asked for:
        not every slot can be reached from both sides)."""
        if (c, sound) not in turns:
            turns[(c, sound)] = arrive(c, *place(c // PER, c % PER), sound, marks[c // PER])
        return turns[(c, sound)]

    # A: the orange slot and the slot's Decide at once, the number a frame later, then the swap
    steps = _swap_step_list(g, 0)
    store = g.nodes[steps[0]]
    assert store["Name"] == "SeqOneShotSetNonStructGameDataInt", store["Name"]
    assert [x["Default Value"] for x in store["Parameters"]["Inputs"]["String"]] == [CHARACTER_FLAG]
    assert sum(g.nodes[i].get("Name") == store["Name"] for i in steps) == 1
    swap = g.add(t_sequential([snd_decide] + steps[1:], then=init, root=True, same_frame=False))
    decide = [g.add(t_simultaneous([stop_mark[j], play_dec[j]], root=False)) for j in slots]
    picks = []
    for c in range(n):
        store_c = steps[0] if c == 0 else g.add(vanilla("AfterEvent", 33, store["Name"], Value=c,
                                                         GameDataFullName=CHARACTER_FLAG))
        picks.append(g.add(t_sequential([decide[c % PER], store_c], then=swap, root=True, same_frame=False)))

    for s in range(sheets):
        for c in pk.on_sheet(s):
            a = c % PER
            tail = g.add(t_bool_sel(chk, TRIG["ZL"], go(turn(pk.turn_to(c, -1), snd_left)), common))
            tail = g.add(t_bool_sel(chk, TRIG["ZR"], go(turn(pk.turn_to(c, 1), snd_right)), tail))
            chain = g.add(t_bool_sel(chk, TRIG["A"], go(picks[c]), tail))
            for button, to in sorted(pk.moves(c).items()):
                b = to % PER
                move = arrive(to, stop_det[b], end_out[b], play_out[a], play_in[b], snd_move)
                chain = g.add(t_bool_sel(moves_by, TRIG[button], go(move), chain))
            g.nodes[loops[c]]["Plugs"] = _plugs([chain])
    ui.finish_graph(g, root, init, also=[g.add(t_play_anim(S, "Pulse"))])
    ui.check_graph(g, loops, ui.PICK_ANIMS)
    presses = ui.check_picker_graph(g, pk, loops)
    # Up to here a character is its place in the list, which is what the UI session's model
    # runs on. The game stores and asks for the character's number: put those in.
    place_of = {pk.number(c): c for c in range(n)}
    changed = _renumber_characters(g.nodes, pk.number)
    assert changed == (n, sum(len(list(pk.on_sheet(s))) for s in range(sheets)) + n), changed
    ui.write_graph(g, mod)
    # the same run on the file as written, with the numbers taken back to places
    back = ainbx.load(open(ui.out_path(mod, "Sequence/CharacterMenu.module.ainb"), "rb").read()).as_dict()
    read = type("ReadBack", (), {"nodes": back["Nodes"], "d": back})
    assert _renumber_characters(read.nodes, place_of.__getitem__) == changed
    assert len(read.nodes) == len(g.nodes) and ui.check_picker_graph(read, pk, loops) == presses
    return g, presses


def _renumber_characters(nodes, f):
    """In a picker graph, every character value v becomes f(v): the value of the nodes that
    store CHARACTER_FLAG and the cases of the selectors that ask for it. -> (stores, cases)."""
    stores = cases = 0
    for n in nodes:
        inputs = n.get("Parameters", {}).get("Inputs", {})
        if n.get("Name") == "SeqOneShotSetNonStructGameDataInt":
            assert [x["Default Value"] for x in inputs["String"]] == [CHARACTER_FLAG]
            value = [x for x in inputs["Int"] if x["Name"] == "Value"]
            assert len(value) == 1
            value[0]["Default Value"] = f(value[0]["Default Value"])
            stores += 1
        elif n["Node Type"] == "Element_S32Selector":
            query = nodes[n["Parameters"]["Inputs"]["Int"][0]["Node Index"]]
            assert query.get("Name") == "SeqQueryGetNonStructGameDataInt" and \
                [x["Default Value"] for x in query["Parameters"]["Inputs"]["String"]] == [CHARACTER_FLAG]
            for plug in n["Plugs"]["Child"]:
                if not plug.get("Is Default"):
                    plug["Condition"] = f(plug["Condition"])
                    cases += 1
    return stores, cases


def picker_page(mod="Characters page"):
    """The UI session's character picker as the Characters page (mods\\ui_sandbox.py as a
    library, the way its own main() uses it; README, `UI sandbox`, "For a merge into the
    normal build"): its layout and two parts into the layout archive, the category list,
    the screen table and the messages, its graph in place of ours (the four directions
    from the D-pad or the left stick; our copy of it, picker_graph, for the page turns),
    and its small code patch for the stick as this mod's own .ips. Run after the mods of MODS (it edits their files) with SWAP_SLIDES
    set: the picker's A then runs our slide. Returns the lines to print."""
    import ui_sandbox as ui
    import layout
    import uiedit
    assert SWAP_SLIDES
    ui.BUILD_UI = BUILD                         # where it reads and writes the mods' files
    parts = ui.build_custom_parts(ui.PICK_PARTS)

    class Picker(ui.Picker):
        """The UI session's picker model with page turns that keep the cursor's slot, and
        without the characters of PICKER_HIDDEN: character c is the c-th one of the list,
        number(c) its stored number."""

        def __init__(self):
            super().__init__()
            ring = self.n
            assert all(0 < h < ring for h in PICKER_HIDDEN)             # Link stays, in the first slot
            self.numbers = [c for c in range(ring) if c not in PICKER_HIDDEN]
            self.names = [self.names[c] for c in self.numbers]
            self.captions = [self.captions[c] for c in self.numbers]    # their texts name the stored number
            self.n = len(self.numbers)
            self.sheets = -(-self.n // ui.PER_SHEET)

        def number(self, c):
            return self.numbers[c]

        def turn_to(self, c, step):
            """Where ZR (step 1) / ZL (-1) put the cursor from character c: the same slot of
            the next / previous sheet, or, where that sheet ends earlier, the same column
            in its last row that has it."""
            s, j = divmod(c, ui.PER_SHEET)
            to = (s + step) % self.sheets * ui.PER_SHEET + j
            while to not in self.on_sheet((s + step) % self.sheets):
                to -= ui.PICK_COLS
            return to

        def after(self, c, button, chosen):
            if button in ("ZR", "ZL"):
                return self.turn_to(c, 1 if button == "ZR" else -1), chosen
            return super().after(c, button, chosen)

    pk = Picker()
    last = pk.n - 1
    assert pk.turn_to(0, 1) == ui.PER_SHEET and pk.turn_to(ui.PER_SHEET + 7, -1) == 7
    assert pk.turn_to(last, 1) == last % ui.PER_SHEET and pk.turn_to(last % ui.PER_SHEET, -1) == last
    assert all(pk.turn_to(c, d) in pk.on_sheet((c // ui.PER_SHEET + d) % pk.sheets) and
               pk.turn_to(c, d) % ui.PICK_COLS == c % ui.PICK_COLS for c in range(pk.n) for d in (1, -1))
    data, msgs = ui.make_picker_layout(pk)
    right = ui.check_picker(data, pk)           # the UI session's check, on its own layout
    names, n_pictures = portrait_textures(mod, pk.numbers)
    data = add_portraits(ui, data, pk, names)
    layouts = dict(parts)
    layouts[ui.LAYOUT] = data
    for name, d in layouts.items():             # kept beside the mod folders, for the editor tools (its checks read ours)
        with open(os.path.join(BUILD, name + ".buil"), "wb") as f:
            f.write(d)
    ed = uiedit.Editor(layout.Layout(data))
    out = ["  layout %s: %d bytes, %d panes, %d animations, %d messages; %d characters on %d sheets checked; grid x %d .. %d"
           % (ui.LAYOUT, len(data), len(ed.panes), len(ed.dict_items(0x140)), len(msgs), pk.n, pk.sheets, ui.PICK_LEFT, right)]
    out.append("  portraits: %d pictures of %d characters as textures of %d pixels, a placeholder for the rest;"
               " one picture pane per character" % (n_pictures, pk.n, PORTRAIT_PIXELS))
    ui.sandbox(layouts, msgs)
    g, presses = picker_graph(ui, mod, pk)
    patch = ui.stick_patch()
    runs = ui.check_stick_patch(patch)
    write_ips(mod, patch)
    out.append("  graph: %d nodes, directions from %s; %d button presses and entries run offline; the stick's code"
               " patch run offline for %d masks" % (len(g.nodes), ui.RELEASE_CHECKER, presses, runs // 2))
    return out, [ui.STICK_MOD]


def field_swap_hooks():
    """The field swap session's work as a library (mods\\field_swap.py, passed in game in its
    own emulator copy, snaildev 2026-11-10; merged on his word the same day): the character
    picked on the Characters page becomes the field Link when the menu closes. This does
    for our build what that module's own main() does for its build-field\\ build, with its
    code unchanged: its unsaved flag, its three wrappers around our functions (the picker's
    A also sets the flag; the screen table gets the fade screen's row; the menu-closing
    graph gets the swap, written to its own folder) and its folder in the list of size
    tables. That folder, "Character field swap", stays what it was tested as: an override
    folder that sorts before ours and holds the swap's graph and the code patch together;
    switched off in the Add-Ons list, the rest is the build without the swap.
    -> the module, or None when mods\\field_swap.py itself is running this build (it has
    hooked in already; main() then leaves the patch and the checks to it)."""
    global RSTB_MODS, GAMEDATA_FLAGS, close_page_with_menu, add_page_screen, _swap_step_list
    if close_page_with_menu.__module__ != __name__:
        return None
    import field_swap as fs
    assert fs.KEEP_CAMERA                                   # the version that passed
    assert fs.MOD < min(MODS) and fs.MOD not in RSTB_MODS, "its folder must sort before ours"
    assert not any(name == fs.PENDING_FLAG for _kind, name, _attrs in GAMEDATA_FLAGS)
    shutil.rmtree(os.path.join(BUILD, fs.MOD), ignore_errors=True)
    RSTB_MODS = RSTB_MODS + [fs.MOD]
    GAMEDATA_FLAGS = GAMEDATA_FLAGS + [("Bool", fs.PENDING_FLAG, {"ResetTypeValue": 0, "SaveFileIndex": -1})]
    close_page_with_menu, add_page_screen, _swap_step_list = \
        fs.close_page_with_menu, fs.add_page_screen, fs._swap_step_list
    return fs


def main():
    """The full build: the Characters page is the UI session's picker (picker_page), every
    character of the NPC session's ring can be picked (mods\\npc_characters.py as a library:
    its model hook and the folder "NPC characters"), A runs the slide, and the picked
    character becomes the field Link when the menu closes (field_swap_hooks;
    `--no-field-swap` leaves that out and removes its folder on install).
    `--plain`: the first version instead (our page, Y / X = Link / Retro Link, no NPCs).
    `--cycle`: our page with the NPCs and D-pad Left / Right through CYCLE_NAMES with the
    slide (for work on the transition). `--install`: also copy into the emulator's mods
    folder and remove retired folders. (`--npcs`, the full build's old name, is accepted.)"""
    global CYCLE, SWAP_SLIDES
    install = "--install" in sys.argv
    plain, cycle = "--plain" in sys.argv, "--cycle" in sys.argv
    assert not (plain and cycle)
    if install and emulator_running():
        print("The emulator is running (%s). Close it first: it keeps the mod files open."
              % ", ".join(emulator_running()))
        sys.exit(1)
    extra, retired, notes = [], list(RETIRED), []
    experiments = dict(EXPERIMENTS)
    field, field_hooked = None, close_page_with_menu.__module__ != __name__
    if plain:
        for mod, fn in MODS.items():
            print(mod)
            shutil.rmtree(os.path.join(BUILD, mod), ignore_errors=True)
            fn(mod)
    else:
        import npc_characters as npc
        # the page zoom: ours (the NPC session's factors and pivots; glides during a slide)
        npc.zoom_hook = lambda p, n_npc: zoom_blend_hook(p, n_npc, npc)
        npc.check_zoom_hook = lambda p, n_npc, factors: check_zoom_blend(p, n_npc, factors, npc)
        if cycle:
            names = npc.ring()
            CYCLE = [names.index(n) for n in CYCLE_NAMES]
            print("D-pad cycle:", ", ".join("%s = %d" % (n, v) for n, v in zip(CYCLE_NAMES, CYCLE)))
        else:
            SWAP_SLIDES = True
            if "--no-field-swap" not in sys.argv:
                field = field_swap_hooks()      # before anything is built: it wraps what builds
        built_npc = npc.build_mods()
        extra = [npc.MOD]
        if not cycle:
            # the picker's layout is the page's own: the layout test folder would override it
            retired += list(experiments)
            experiments = {}
            print("Characters page (the picker)")
            notes, gone = picker_page()
            retired += gone
            print("\n".join(notes))
    for mod, fn in experiments.items():
        print(mod, "(test folder)")
        shutil.rmtree(os.path.join(BUILD, mod), ignore_errors=True)
        fn(mod)
    for mod in RSTB_MODS + list(experiments):
        print(mod, "(combined size table)")
        write_rstb(mod, SIZES)
    if extra:
        print("checks")
        npc.check_build(*built_npc)
    if field:
        print(field.MOD)
        patch = field.field_patch()
        write_ips(field.MOD, patch)             # also: no byte shared with our patches
        print("checks (field swap)")
        print("  code patch: %d runs emulated" % field.check_patch(patch))
        for i, step in enumerate(field.check_graphs()):
            print("  menu close, step %d: %s" % (i + 1, step))
        extra.append(field.MOD)
    elif not field_hooked:
        # not built here: an installed copy would override the menu's closing with a graph
        # that asks for a flag this build does not have
        import field_swap
        retired.append(field_swap.MOD)
        shutil.rmtree(os.path.join(BUILD, field_swap.MOD), ignore_errors=True)
    if install:
        for mod in list(MODS) + extra + list(experiments):
            dst = os.path.join(EDEN_LOAD, mod)
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(os.path.join(BUILD, mod), dst)
            print("  installed ->", dst)
        for mod in retired:
            dst = os.path.join(EDEN_LOAD, mod)
            if os.path.isdir(dst):
                shutil.rmtree(dst)
                print("removed retired mod", dst)


if __name__ == "__main__":
    sys.path.insert(0, HERE)    # python -I leaves the script's folder out
    import build        # run as the library module, so that helpers importing `build` share its state
    build.main()
