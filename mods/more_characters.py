"""More characters: the models outside `Npc_*` that have Link's core bones, as characters to
pick (README, NPC section, "Models outside Npc_*": Dark Link, Skull Kid, the witches, the
Iron Knuckles, the undead, the Moblins, Wolfos, the cow, Lizalfos).

PART OF THE MAIN BUILD since 2026-11-13 (snaildev: "good to add to the main version"):
mods\\build.py's main() calls apply(), and its PICKER_ROSTER names their numbers. Run by
itself this file is that same build into a folder and an emulator copy of its own, for work
on new characters before they go to the main version:

    venv\\Scripts\\python.exe mods\\more_characters.py             build into build-more\\ and check
    venv\\Scripts\\python.exe mods\\more_characters.py --install   also install into its own emulator
                                                                copy (tools\\moreenv.py, PLAY-debug-more.bat)
    `--ganon`: with Ganon and larger heaps (see GANON)
    `--capture`: the portrait capture build instead (capture(); own folder and emulator copy)

What apply() hooks in, all from outside (mods\\npc_characters.py, tools\\npcrig.py,
tools\\bfres.py and mods\\ui_sandbox.py are not edited):
  - npc_characters.characters() gets the models of EXTRA appended. They follow the 98 NPCs,
    so every stored number of the ring of 100 keeps its character (the saves, the voices'
    and the cloth hook's tables go by them); the new ones are 100 and up, append only.
  - a hair stand-in for those that have none of npc_characters.HAIR_STAND_INS (HAIR), models
    made readable and nameable first (ADAPT), no cloth of their own yet (cloth_source).
  - the page zoom for bodies the Head bone says little about (ZOOM).
  - their portraits (build-more\\portraits\\cut-fit, made by tools\\moreshots.py and
    tools\\morecutout.py from a capture run) and adult Link's picture in Link's slot while
    the player is an adult (add_portraits, finish_graph).
The model cache (build-npc\\_cache, keyed by content) is shared: the 98 NPCs come out of it
unchanged. The test copies share the saves with the normal Lichii install.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build  # noqa: E402
import npc_characters as npc  # noqa: E402
import bfres  # noqa: E402
import npcrig  # noqa: E402
import moreenv  # noqa: E402
import nxpack  # noqa: E402
import ui_sandbox as _ui  # noqa: E402

BUILD_MORE = os.path.join(ROOT, "build-more")
BUILD_CAPTURE = os.path.join(ROOT, "build-more-capture")
PORTRAITS = os.path.join(BUILD_MORE, "portraits", "cut-fit")
# (model file stem, name), in the order of their stored numbers (ring 100, 101, ...).
# Append only: a number, once seen in a save, keeps its character.
EXTRA = [
    ("Player_Adult_Dark.Player_Adult_Dark", "Dark Link"),
    ("Enemy_SkullKid.Enemy_SkullKid", "Skull Kid"),
    ("Enemy_DungeonBoss_Kotake.Npc_DungeonBoss_Kotake", "Koume & Kotake"),
    ("Enemy_DungeonBoss_Kotake.Enemy_DungeonBoss_Kotake", "Koume & Kotake (boss)"),
    ("Enemy_DungeonBoss_Twinrova.Enemy_DungeonBoss_Twinrova", "Twinrova"),
    ("Enemy_MiddleBoss_IronKnuckle_Nabooru.Enemy_MiddleBoss_IronKnuckle_Nabooru", "Iron Knuckle (Nabooru)"),
    ("Enemy_MiddleBoss_IronKnuckle_Nabooru.Enemy_MiddleBoss_IronKnuckle_Nabooru_Demo", "Iron Knuckle (Nabooru, cutscene)"),
    ("Enemy_IronKnuckle_Silver.Enemy_IronKnuckle_Silver", "Iron Knuckle"),
    ("Enemy_Gibdo.Enemy_Gibdo", "Gibdo"),
    ("Enemy_ReDead.Enemy_ReDead", "ReDead"),
    ("Enemy_Moblin.Enemy_Moblin", "Moblin"),
    ("Enemy_Moblin_Club.Enemy_Moblin_Club", "Club Moblin"),
    ("Enemy_Wolfos.Enemy_Wolfos", "Wolfos"),
    ("Animal_Cow.Animal_Cow", "Cow"),
    ("Player_Child_Op.Player_Child_Op", "Link (opening)"),
    ("Enemy_Lizalfos.Enemy_Lizalfos", "Lizalfos"),
    # not a character to pick (HIDDEN): the adult model on the child's actor, as Dark Link is
    # shown, for the picture the picker shows in Link's slot while the player is an adult
    ("Player_Adult.Player_Adult", "Link (adult)"),
]
# Ganon (the beast) is not in the default batch: with 59 shapes and 1,077 bones as a player
# model his pre-actor render heap need is 465,692 bytes, more than the 0x60000 the build
# gives the four player actors (npc_characters.HEAPS, passed in game for up to 700 bones).
# `--ganon` appends him and raises those heaps for everybody (GANON_HEAPS: the pre-actor
# heap to 0x90000 = his need * 1.25; the instance heap factors times 1.5, a guess from his
# bone count). That is a change under every character, so it is its own round.
GANON = ("Enemy_DungeonBoss_Ganon.Enemy_DungeonBoss_Ganon", "Ganon")
GANON_HEAPS = {"Player_Child": (3.0, 0x90000), "Player_Adult": (2.4, 0x90000),
               "PouchPlayer_Child": (4.5, 0x90000), "PouchPlayer_Adult": (4.5, 0x90000)}
# The page's zoom goes by the Head bone's height (npc_characters.zoom_factors), which says
# little about these: the witches ride a broom 4.2 m long at a head height of 0.94 m, the
# cow is a four-legged body 4.3 m long on Link's two-legged pose. snaildev, 2026-11-13: the
# page does not zoom out enough to show them (the cow's head was not in view at all). The
# height the zoom is told is the Head bone's times this.
ZOOM = {
    "Enemy_DungeonBoss_Kotake.Npc_DungeonBoss_Kotake": 1.6,
    "Enemy_DungeonBoss_Kotake.Enemy_DungeonBoss_Kotake": 1.6,
    "Animal_Cow.Animal_Cow": 2.8,
}
ADULT_LINK_PORTRAIT = 100 + 16      # "Link (adult)" of EXTRA: his number = his picture's number
HIDDEN = [ADULT_LINK_PORTRAIT]      # in the ring, never in the picker (added to build.PICKER_HIDDEN)
# What the capture build's run shows after child Link (`--all`: everybody again). Second run,
# 2026-11-13: adult Link, and the three whose zoom changed; the others' pictures are cut
# from the first run's frames and stay with them.
CAPTURE_RUN = [ADULT_LINK_PORTRAIT, 102, 103, 113]
NO_CLOTH = ("Enemy_Gibdo.Enemy_Gibdo", "Enemy_ReDead.Enemy_ReDead", "Player_Adult.Player_Adult")
# npc_characters.CLOTH_MAX (the cloth hook's table: one bit per stored number) ends at 103.
CLOTH_MAX = 127
# The stand-in for Mt_Hairs where the model has none of npc_characters.HAIR_STAND_INS. The
# rigged files are cached by content without this table: after changing an entry, delete
# build-npc\_cache (or that model's entries).
HAIR = {
    "Enemy_IronKnuckle_Silver.Enemy_IronKnuckle_Silver": "Mt_Inner",
    "Enemy_Gibdo.Enemy_Gibdo": "Mt_Eye_Inner",
    "Enemy_ReDead.Enemy_ReDead": "Mt_Wood",
    "Enemy_Moblin.Enemy_Moblin": "Mt_Lens",
    "Enemy_Lizalfos.Enemy_Lizalfos": "Mt_Eye",
}

# Models that need a change before tools\bfres.py and the rig can take them (adapted()):
#   "mirror": the skeleton has a mirror table, which bfres.py does not read ("unknown
#             skeleton arrays"); it is dropped.
#   "rename": bone names, so that the rig finds Link's joint (a long neck of three bones:
#             the lowest one is driven as Link's neck, the other two keep their rest pose).
SKL_MIRROR = 0x80
ADAPT = {
    "Enemy_Lizalfos.Enemy_Lizalfos": {"mirror": True, "rename": {"Neck_1": "Neck"}},
}

_base_characters = npc.characters
_base_hair = npc.hair_material
_base_rigged = npc.rigged
_base_cloth_source = npc.cloth_source
_base_npc_data = npc.npc_data
_base_portrait_files = build.portrait_files
_base_portrait_textures = build.portrait_textures
_base_add_portraits = build.add_portraits
_base_finish_graph = _ui.finish_graph
_adapted = {}
_now = []
_capture = []      # non-empty in the capture build
_base_head_height = npc.head_height
_list = []


def characters():
    if _list:
        return _list
    base = list(_base_characters())
    assert len(base) == 98 and not any(c["stem"] in dict(EXTRA) for c in base)
    actors = npc.actor_names()
    out = list(base)
    for stem, label in EXTRA:
        assert npcrig.is_humanoid(bfres.Fres(npc.npc_data(stem)[1]).model()), stem
        out.append({"stem": stem, "label": label, "actors": actors.get(stem, [])})
    assert len({c["label"] for c in out}) == len(out)
    _list.extend(out)
    return _list


def rigged(stem, *args, **kw):
    _now.append(stem)
    try:
        return _base_rigged(stem, *args, **kw)
    finally:
        _now.pop()


def hair_material(m):
    names = m.material_names
    if npc.HAIR_MATERIAL not in names and not any(n in names for n in npc.HAIR_STAND_INS):
        mine = HAIR[_now[-1]]
        assert mine in names, (_now[-1], names)
        m.rename_material(mine, npc.HAIR_MATERIAL)
        return mine
    return _base_hair(m)


def cloth_source(stem):
    """npc_characters.cloth_source for ours too (snaildev, 2026-11-14: "prepare this with the
    cloth physics for each character"). Their actors keep cloth as the NPCs' do (a ClothList
    and a Cloth entry in the ControllerSetParam, the ClothParam in the pack), except:
      - NO_CLOTH: the ReDead's settings (the Gibdo uses them too) name a cloth file but no
        ClothParam, so there are no meshes to simulate; adult Link's model is not shipped.
    """
    if stem in NO_CLOTH:
        return None
    return _base_cloth_source(stem)


def adapted(stem, data):
    """A stock model file made readable for tools\bfres.py and nameable for the rig (ADAPT)."""
    how = ADAPT[stem]
    d = bytearray(data)
    skl = struct.unpack_from("<Q", d, struct.unpack_from("<Q", d, 0x28)[0] + 0x18)[0]
    assert d[skl:skl + 4] == b"FSKL"
    flags, table = struct.unpack_from("<I", d, skl + 4)[0], struct.unpack_from("<Q", d, skl + 0x30)[0]
    if how.get("mirror"):
        # the skeleton's mirror table (bit 7 of its flags; +0x30 -> one s16 per bone: its
        # mirror image's index, itself on the centre line, -1 none). Link's skeleton has
        # none, and a player model does not need one: flag off, pointer null (the
        # relocation table may list a null pointer).
        assert flags & SKL_MIRROR and table and struct.unpack_from("<Q", d, skl + 0x28)[0] == 0
        n = struct.unpack_from("<H", d, skl + 0x38)[0]
        mirror = struct.unpack_from("<%dh" % n, d, table)
        assert all(m == -1 or mirror[m] == i for i, m in enumerate(mirror)), "not a mirror table"
        struct.pack_into("<I", d, skl + 4, flags & ~SKL_MIRROR)
        struct.pack_into("<Q", d, skl + 0x30, 0)
    f = bfres.Fres(bytes(d))
    m = f.model()
    rename = how.get("rename", {})
    if rename:
        names = [b.name for b in m.bones]
        assert all(old in names and new not in names for old, new in rename.items()), (stem, rename)
        m.set_skeleton([b.copy(name=rename.get(b.name, b.name)) for b in m.bones], list(range(len(m.bones))))
    return bytes(f.build())


def npc_data(stem):
    if stem not in ADAPT:
        return _base_npc_data(stem)
    if stem not in _adapted:
        raw, data = _base_npc_data(stem)
        data = adapted(stem, data)
        # the compressed form is rigged()'s cache key: it changes with ADAPT
        _adapted[stem] = (nxpack.zs_compress_like(data, raw, 3), data)
    return _adapted[stem]


def head_height(stem):
    return _base_head_height(stem) * ZOOM.get(stem, 1.0)


def full_roster():
    """The picker's list for the full build (`--full`): everybody who can be picked, in the
    ring's order, but with Dark Link and Skull Kid as the last two of the first sheet (snaildev,
    2026-11-13: "with dark link and skull kid on the first page") and the other new ones at
    the end. As a list it also keeps adult Link's model out of the build (build.lite_roster)."""
    ring = len(npc.ring())
    first = [100, 101]
    rest = [n for n in range(ring) if n not in build.PICKER_HIDDEN and n not in first and n < 100]
    new = [n for n in numbers() if n not in first]
    at = _ui.PER_SHEET - len(first)
    out = rest[:at] + first + rest[at:] + new
    assert out[0] == 0 and len(set(out)) == len(out) == ring - len(build.PICKER_HIDDEN)
    return out


def numbers():
    """The stored numbers of EXTRA that can be picked."""
    first = len(build.CHARACTERS) + len(_base_characters())
    return [n for n in range(first, first + len(EXTRA)) if n not in HIDDEN]


def portrait_files():
    """build.portrait_files plus ours: build-more\portraits\cut-fit\<nnn>_<name>.png
    (tools\morecutout.py; three digits, which the normal build's two-digit list skips)."""
    out = dict(_base_portrait_files())
    for name in sorted(os.listdir(PORTRAITS)) if os.path.isdir(PORTRAITS) else []:
        if name.lower().endswith(".png") and name[:3].isdigit() and name[3] == "_":
            assert int(name[:3]) in numbers() + [ADULT_LINK_PORTRAIT] and int(name[:3]) not in out, name
            out[int(name[:3])] = os.path.join(PORTRAITS, name)
    return out


# ---------------------------------------------------------------- adult Link's picture
# snaildev, 2026-11-13: an avatar for adult Link, shown in place of young Link's while the
# player is an adult. Link's slot gets a second picture pane (ADULT_PANE, a copy of his
# P_Face pane with the adult's texture, at the end of the first sheet: same place, drawn
# last) and the layout an animation AGE_ANIM that shows one of the two: frame 0 the child's,
# frame 1 the adult's. The page's entry stops it at the frame the saved Bool
# Sequence_IsChildhood says (the game's own flag for the age: LoadBancImpl creates the
# player by it); the age cannot change while the menu is open.
ADULT_PANE = "P_FaceAdult"
AGE_ANIM = "Age"
AGE_TEMPLATE = "Mark"       # an animation of the picker layout that is stopped at whole frames


def _names(items):
    return [(x[0].rstrip(b"\0").decode() if isinstance(x[0], bytes) else x[0]) for x in items]


def adult_picture():
    return portrait_files().get(ADULT_LINK_PORTRAIT)


def portrait_textures(mod, numbers):
    """build.portrait_textures with adult Link's picture among the textures."""
    numbers = list(numbers)
    assert numbers[0] == 0 and ADULT_LINK_PORTRAIT not in numbers
    return _base_portrait_textures(mod, numbers + ([ADULT_LINK_PORTRAIT] if adult_picture() else []))


def add_portraits(ui, data, pk, names):
    data = _base_add_portraits(ui, data, pk, names)
    if not adult_picture():
        return data
    import layout
    import uiedit
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    child = ed.pane("P_Face_000")
    assert pk.number(0) == 0 and ADULT_PANE not in ed.names() and AGE_ANIM not in _names(ed.dict_items(0x140))
    mat = ed.dict_items(0x40)[child.material][1]
    first, count = ed._pass_runs()[child.material]
    assert count == 1
    passes = ed.passes()[first:first + count]
    typed = ed.dict_items(0x60)[child.type_index][1]
    adult = uiedit.NewPane(ADULT_PANE, uiedit.deep(child.node), None, uiedit.deep(mat),
                           [uiedit.deep(x) for x in passes], ed.arr208()[child.material], uiedit.deep(typed), None)
    assert adult.passes[0].ptr[0].items[0].ptr[8].text == names[0]
    adult.passes[0].ptr[0].items[0].ptr[8] = layout.cstr(names[ADULT_LINK_PORTRAIT])
    before = len(ed.panes)
    ed.insert("N_Sheet_00", [adult])
    ed.add_anim(AGE_ANIM, frames=1, template=AGE_TEMPLATE,
                curves=[(ed.key("P_Face_000", uiedit.VISIBLE), uiedit.state_curve([1.0, 0.0])),
                        (ed.key(ADULT_PANE, uiedit.VISIBLE), uiedit.state_curve([0.0, 1.0]))])
    ed.validate()
    out = layout.build(lay)
    check = layout.Layout(out)                  # what the game will read
    again = uiedit.Editor(check)
    again.validate()
    assert layout.build(check) == out and len(again.panes) == before + 1 < 512
    a, c = again.pane(ADULT_PANE), again.pane("P_Face_000")
    assert a.parent == c.parent and a.index > c.index and a.size == c.size and a.visible == c.visible
    assert a.node.get("<2f", 0x1C) == c.node.get("<2f", 0x1C)       # the same place
    f, k = again._pass_runs()[a.material]
    assert k == 1 and again.passes()[f].ptr[0].items[0].ptr[8].text == names[ADULT_LINK_PORTRAIT]
    f, k = again._pass_runs()[c.material]
    assert again.passes()[f].ptr[0].items[0].ptr[8].text == names[0]
    for frame, child_on in ((0, 1.0), (1, 0.0)):
        got = uiedit.anim_values(check, AGE_ANIM, frame)
        assert got == {(c.index, uiedit.VISIBLE, None): [child_on], (a.index, uiedit.VISIBLE, None): [1.0 - child_on]}, got
    stock = layout.Layout(data)                 # every other animation is what it was
    new_names, old_names = again.names(), uiedit.Editor(stock).names()
    assert [n for n in new_names if n != ADULT_PANE] == old_names
    assert AGE_ANIM in _names(again.dict_items(0x140)) and AGE_TEMPLATE in _names(again.dict_items(0x140))
    for name in _names(uiedit.Editor(stock).dict_items(0x140)):
        for frame in (0, 1, 5):         # by pane name: the new pane moved the indices after it
            new_v = {(new_names[k[0]],) + tuple(k[1:]): v for k, v in uiedit.anim_values(check, name, frame).items()}
            old_v = {(old_names[k[0]],) + tuple(k[1:]): v for k, v in uiedit.anim_values(stock, name, frame).items()}
            assert new_v == old_v, name
    return out


def finish_graph(g, root, init, also=()):
    """ui_sandbox.finish_graph with one more node on the page's entry: AGE_ANIM stopped at
    the frame for the player's age."""
    also = list(also)
    if adult_picture():
        S = _ui.SCREEN
        is_child = g.add(build.vanilla("LoadBancImpl", 43, "SeqQueryGetNonStructGameDataBool"))
        assert g.nodes[is_child]["Parameters"]["Inputs"]["String"][0]["Default Value"] == "Sequence_IsChildhood"
        also.append(g.add(build.t_bool_sel(is_child, 0, g.add(build.t_stop_anim(S, AGE_ANIM, 0.0)),
                                           g.add(build.t_stop_anim(S, AGE_ANIM, 1.0)))))
    return _base_finish_graph(g, root, init, also=also)


def apply():
    """Hook the list, the hair stand-ins and the adapted models into npc_characters, the
    portraits and adult Link's picture into the picker. mods\build.py's main() calls this
    (the main version, since 2026-11-13); calling it again changes nothing."""
    if npc.characters is characters:
        return
    assert EXTRA[ADULT_LINK_PORTRAIT - 100][1] == "Link (adult)"
    build.PICKER_HIDDEN = list(build.PICKER_HIDDEN) + [h for h in HIDDEN if h not in build.PICKER_HIDDEN]
    assert npc.CLOTH_MAX <= CLOTH_MAX
    npc.CLOTH_MAX = CLOTH_MAX
    npc.characters = characters
    npc.hair_material = hair_material
    npc.rigged = rigged
    npc.cloth_source = cloth_source
    npc.npc_data = npc_data
    build.portrait_files = portrait_files
    npc.head_height = head_height
    build.portrait_textures = portrait_textures
    build.add_portraits = add_portraits
    _ui.finish_graph = finish_graph


def capture():
    """The portrait capture build: mods\npc_characters.py's own main() (its capture build:
    no backdrop behind the character, frozen idle, no gear, D-pad down on the Characters
    page = the portrait run) with our list, into BUILD_CAPTURE and our capture copy
    (moreenv.CAPTURE). The run shows child Link first (the cut-out tool's scale) and then
    the characters of EXTRA; tools\moreshots.py saves the frames meanwhile."""
    assert "--display" not in sys.argv
    _capture.append(True)
    apply()
    assert npc.ring()[ADULT_LINK_PORTRAIT] == "Link (adult)"
    npc.BUILD_NPC = BUILD_CAPTURE
    npc.npcenv = moreenv.CAPTURE
    run = [0] + (numbers() + [ADULT_LINK_PORTRAIT] if "--all" in sys.argv else CAPTURE_RUN)
    npc.PORTRAIT_TRIAL = tuple(run)
    os.makedirs(BUILD_CAPTURE, exist_ok=True)
    print("capture build: the portrait run (D-pad down) shows %s" % ", ".join(npc.ring()[n] for n in npc.PORTRAIT_TRIAL))
    npc.main()


def main():
    if "--ganon" in sys.argv:
        EXTRA.append(GANON)         # number 117
        build.PICKER_ROSTER = list(build.PICKER_ROSTER) + [100 + len(EXTRA) - 1]
        assert set(GANON_HEAPS) == set(npc.HEAPS)
        npc.HEAPS = dict(GANON_HEAPS)
    if "--capture" in sys.argv:
        return capture()
    apply()
    build.BUILD = BUILD_MORE
    if "--install" in sys.argv:         # into our own copy only; the normal mods folder is not touched
        if moreenv.running():
            print("The more characters emulator copy is running. Close it first.")
            sys.exit(1)
        moreenv.setup()
        moreenv.check()
        build.EDEN_LOAD = moreenv.LOAD
        assert os.path.normcase(build.EDEN_LOAD).startswith(os.path.normcase(moreenv.DST))
    os.makedirs(BUILD_MORE, exist_ok=True)
    assert "--full" in sys.argv or all(n in build.PICKER_ROSTER for n in numbers())
    ring = npc.ring()
    print("more characters: %d, stored numbers %d .. %d: %s" % (len(EXTRA), numbers()[0], numbers()[-1],
                                                                ", ".join(ring[n] for n in numbers())))
    build.main()


if __name__ == "__main__":
    import more_characters      # run as the library module: build.main() imports it by that name
    more_characters.main()
