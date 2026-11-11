"""NPC characters: every humanoid NPC model of the game as a character to pick and play.

    venv\\Scripts\\python.exe mods\\npc_characters.py             build into build-npc\\
    venv\\Scripts\\python.exe mods\\npc_characters.py --install   build, then install into the NPC
                                                                emulator copy (tools\\npcenv.py,
                                                                launcher PLAY-debug-npc.bat),
                                                                never the normal one
    venv\\Scripts\\python.exe mods\\npc_characters.py --list      the character list only

Builds every mod of mods\\build.py exactly as the normal build does (output redirected to
build-npc\\; build\\ and the normal mods folder are never touched), then adds:
- mod folder "NPC characters": Model/Player_<Age>_C<nnn>.Player_<Age>.bfres for every
  humanoid NPC model (CHARACTERS order, both ages), each the NPC's own file with the
  skeleton the player actor needs (tools\\npcrig.py) and the model renamed; and
  RSDB/ActorInfo with larger heaps for the four player actors (the rigged skeletons have
  about twice Link's bone count, and those heaps are sized to the byte for Link's model).
- "Character models": the same flags and Retro Link, but its exe patch gets a model hook
  that maps the stored character number to those files (model_hook below).
- "Characters page": debug buttons (BUTTONS): Y Link, X Retro Link, D-pad right / left =
  next / previous character, up / down = 10 forward / back, left stick click = the first
  experiment (then D-pad right for the next ones).
The stored number is CharacterMenu_SelectedCharacter as in the normal build (saved with the
slot): 0 Link, 1 Retro Link, then the NPCs; the hook takes it modulo the list length, so the
list is a ring and any number is valid. The normal build shows Link for numbers it does not know.
"""
import hashlib
import json
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
import bfres  # noqa: E402
import byml  # noqa: E402
import bympatch  # noqa: E402
import exepatch  # noqa: E402
import gamedata  # noqa: E402
import npcenv  # noqa: E402
import npcrig  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402

BUILD_NPC = os.path.join(ROOT, "build-npc")
CACHE = os.path.join(BUILD_NPC, "_cache")
MOD = "NPC characters"
MODEL_DIR = os.path.join(nxfmt.ROMFS, "Model")
AGES = ["Child", "Adult"]                       # Player_<age>, the hook's age index
assert build.PLAYER_MODELS == ["Player_" + a for a in AGES]
MODEL_FILE = "Model/Player_%s_C%03d.Player_%s.bfres.zs"   # age, NPC number, age
ZS_LEVEL = 16

# ---------------------------------------------------------------- the character list
# Named characters first (the order of the ring after Link and Retro Link); every other
# humanoid NPC model follows in file order. Key = model file stem "<project>.<fmdb>".
FEATURED = [
    ("Npc_Kokiri_WC_Saria", "Saria"),
    ("Npc_Kokiri_MC_Mido", "Mido"),
    ("Npc_Hylia_WU_Zelda_Child", "Zelda (child)"),
    ("Npc_Hylia_WC_Malon_Child", "Malon (child)"),
    ("Npc_Zora_ZU_Ruto_Child", "Ruto (child)"),
    ("Npc_Kokiri_WC_Fado", "Fado"),
    ("Npc_Hylia_WU_Zelda_Adult", "Zelda"),
    ("Npc_Hylia_WU_Sheik", "Sheik"),
    ("Npc_Hylia_WU_Impa", "Impa"),
    ("Npc_Hylia_WN_Malon_Adult", "Malon"),
    ("Npc_Zora_ZU_Ruto_Adult", "Ruto"),
    ("Npc_Gerudo_GEU_Nabooru", "Nabooru"),
    ("Npc_Gerudo_GEU_Ganondorf_Child", "Ganondorf (child era)"),
    ("Npc_Gerudo_GEU_Ganondorf_Adult", "Ganondorf"),
    ("Npc_Goron_GOU_Darunia", "Darunia"),
    ("Npc_Zora_ZU_KingZora", "King Zora"),
    ("Npc_Hylia_MU_Rauru", "Rauru"),
    ("Npc_U_GreatFairy", "Great Fairy"),
    ("Npc_Hylia_MU_Talon", "Talon"),
    ("Npc_Hylia_MU_Ingo_Child", "Ingo"),
    ("Npc_Hylia_MU_Ingo_Adult", "Ingo (adult era)"),
    ("Npc_Hylia_MU_Dampe", "Dampe"),
    ("Npc_Goron_GOU_DaruniaJr", "Darunia's son"),
    ("Npc_Goron_GOU_Biggoron", "Biggoron"),
    ("Npc_Goron_GOU_Medigoron", "Medigoron"),
    ("Npc_DungeonBoss_PhantomGanon", "Phantom Ganon"),
]
# Experiments: not part of the ring. Stored number EXPERIMENT_BASE + i.
#   ("lean", stem): rigged, but without the added Link bones (fewer bones than Link has).
#   ("raw", stem): the NPC's file with only the model (and the hair material) renamed.
#   ("stock", stem): no file of ours at all; the hook names the game's own NPC file.
#                    Not built any more: its answer is known (it cannot work, HAIR_MATERIAL).
# They answer what the player actor really needs (may crash, hence off the ring).
EXPERIMENT_BASE = 1000
EXPERIMENT_MODEL = "Npc_Kokiri_WC_Saria.Npc_Kokiri_WC_Saria"
EXPERIMENTS = [("lean", EXPERIMENT_MODEL), ("raw", EXPERIMENT_MODEL)]

# The player's code looks up the material "Mt_Hairs" and its shader parameter "ns_tex_mtx0"
# without checking the result (0x804d8240 on every equipment sync, 0x804d84b0 with head
# gear): a player model without that material crashes the game on the first frame (round 1,
# 2026-11-09: array[-1], then a call through null). Only child Zelda has the name; 87 NPC
# models call their hair "Mt_Hair", 10 have no hair material. So one material of every
# model is renamed: the first of HAIR_STAND_INS it has. The game then writes that
# material's texture matrix 0: the identity at rest (a no-op, checked in rigged(): every
# stand-in has the identity as its default), head gear's hair values while a mask is worn.
# The legs (2026-11-11; README, NPC section, "The ankles"): the foot IK takes the ankle's
# parent and grandparent for knee and hip, so no offset bone may sit between hip, knee and
# ankle (npcrig step 5, LEGS). The leg bones then carry the character's own lengths, which
# the game only keeps when the animation is retargeted: the folder MOD also has
#   - the player's AnimationParam with IsRetarget on every animation set, in the actor packs
#     of the field and the menu Link of both ages (RETARGET_PACKS), and
#   - the reference skeleton each set then needs, Model/<set>.Skeleton.bfres: the game has
#     it for one set per age (= Link's skeleton), copied here under the other sets' names.
# Passed in game as a test folder with Saria and Zelda (snaildev, 2026-11-11: both fixed on
# slopes and ledges, Link and the lighting normal, a character of the first rig as before).
LEGS = "retarget"
RETARGET_PACKS = {      # age: (the AnimationParam file in the packs, the packs)
    "Child": ("Component/AnimationParam/Player_Child.engine__component__AnimationParam.bgyml",
              ("Player_Child", "PouchPlayer_Child")),
    "Adult": ("Component/AnimationParam/Player.engine__component__AnimationParam.bgyml",
              ("Player_Adult", "PouchPlayer_Adult")),
}
RETARGET_SKELETON = "Model/%s.Skeleton.bfres.zs"
LEGS_AS_THEY_ARE = ("Npc_Hylia_MM_003.Npc_Hylia_MM_003",)     # sits: no leg to fit, keeps the first rig
# The faces (2026-11-11; README, NPC section, "Faces"): the characters' own face bones are
# bound to Link's face clips (npcrig step 6). Passed in game as a test folder with Saria,
# Mido and adult Zelda as child Link (snaildev, 2026-11-11: "everything is as expected", they
# have facial animations).
FACE = True
RETIRED = ["Character leg retarget (test)", "Character faces (test)"]   # our earlier test folders: the installing build removes them
EXTRA_MODS = []                 # folders build_mods() makes besides MOD; the caller installs them too

HAIR_MATERIAL = "Mt_Hairs"
HAIR_STAND_INS = ("Mt_Hair", "Mt_Teeth", "Mt_Item_DekuStick")
TEX_MTX0_KEY = 0x5E0F8E359DEB7624      # shader parameter "ns_tex_mtx0" as model files store it
TEX_MTX_IDENTITY = (0, 1.0, 1.0, 0.0, 0.0, 0.0)   # mode, scale x y, rotation, translation x y


class Step(int):
    """A relative choice for a character button: add this to the stored number."""


class Backdrop(int):
    """A button that puts a flat colour behind the 3D character (portrait captures); see the
    backdrop section. 0: black. 1: white. 2: off."""


class PortraitRun(int):
    """A button that shows the first <value> characters of the ring in turn, without gear, first on black,
    then on white, while tools\\npcshots.py saves the frames (see the portrait section)."""


build.TRIG.setdefault("LSPush", 5)      # output TrigLSPush (left stick click); the normal build has no use for it
BUTTONS = {"Y": 0, "X": 1, "Right": Step(1), "Left": Step(-1), "Up": Step(10), "Down": Step(-10),
           "LSPush": EXPERIMENT_BASE, "A": Backdrop(0), "L": Backdrop(1), "ZL": Backdrop(2)}
BUTTONS["Down"] = PortraitRun(-1)       # a trial run: PORTRAIT_TRIAL (10 back is given up for it)
BUTTONS["LSPush"] = PortraitRun(100)    # the whole ring (the model experiments are answered and off the buttons)

_models = {}


def npc_data(stem):
    """(compressed file bytes, decompressed bytes) of a stock model."""
    if stem not in _models:
        raw = open(os.path.join(MODEL_DIR, stem + ".bfres.zs"), "rb").read()
        _models[stem] = (raw, nxfmt.unzs(raw, stem))
    return _models[stem]


def link_model(age):
    return bfres.Fres(npc_data("Player_%s.Player_%s" % (age, age))[1]).model()


def actor_names():
    """{model file stem: [actor names]} from the actor table (for the list's descriptions)."""
    rows = byml.load(nxfmt.read(os.path.join(nxfmt.ROMFS, ACTOR_INFO)))
    out = {}
    for r in rows:
        if "ModelProjectName" in r and "FmdbName" in r:
            out.setdefault("%s.%s" % (r["ModelProjectName"], r["FmdbName"]), []).append(r["__RowId"])
    return out


_characters = []


def characters():
    """[{"stem", "label", "actors"}]: every humanoid NPC model, FEATURED first."""
    if _characters:
        return _characters
    stems = sorted(f[:-len(".bfres.zs")] for f in os.listdir(MODEL_DIR)
                   if f.startswith("Npc_") and f.endswith(".bfres.zs") and ".anim." not in f and ".Skeleton." not in f)
    humanoid = [s for s in stems if npcrig.is_humanoid(bfres.Fres(npc_data(s)[1]).model())]
    actors = actor_names()
    labels = {}
    for name, label in FEATURED:
        stem = "%s.%s" % (name, name)
        assert stem in humanoid, ("featured model is missing or not humanoid", stem)
        labels[stem] = label
    order = list(labels) + [s for s in humanoid if s not in labels]
    for stem in order:
        fmdb = stem.split(".")[1]
        _characters.append({"stem": stem, "label": labels.get(stem, fmdb[len("Npc_"):]),
                            "actors": actors.get(stem, [])})
    return _characters


def ring():
    """Display names by stored number: the normal build's characters, then the NPCs."""
    return [c["name"] for c in build.CHARACTERS] + [c["label"] for c in characters()]


# ---------------------------------------------------------------- models

def _source_hash():
    h = hashlib.sha1()
    for name in ("bfres.py", "npcrig.py"):
        h.update(open(os.path.join(ROOT, "tools", name), "rb").read())
    return h.hexdigest()


def rigged(stem, age, mode="full", legs=None, face=False):
    """The NPC model as a player model: (compressed bytes, decompressed size, growth in
    bytes, report). Modes: full (the real thing), lean, raw (EXPERIMENTS); legs = npcrig's
    legs_mode, face = its step 6 (the face test). Every result is parsed again and checked against the stock
    model (npcrig.check_rig); cached by content."""
    raw, data = npc_data(stem)
    link_raw = npc_data("Player_%s.Player_%s" % (age, age))[0]
    key = hashlib.sha1(b"|".join([raw, link_raw, mode.encode(), age.encode(), _source_hash().encode(),
                                 b"%d" % ZS_LEVEL, repr((HAIR_MATERIAL, HAIR_STAND_INS)).encode(),
                                 repr((GEAR_AWAY, GEAR_BONES, GEAR_OFFSET)).encode()]
                                + ([b"legs " + legs.encode()] if legs else [])
                                + ([b"face"] if face else []))).hexdigest()
    path = os.path.join(CACHE, key)
    if os.path.exists(path + ".zs") and os.path.exists(path + ".json"):
        info = json.load(open(path + ".json"))
        return open(path + ".zs", "rb").read(), info["size"], info["growth"], info["report"]
    link = link_model(age)
    f = bfres.Fres(data)
    m = f.model()
    before = bfres.Fres(data).model()
    report = {"bones": (len(m.bones), len(m.bones))}
    if mode != "raw":
        bones, remap, inverse, report = npcrig.player_rig(m, link, add_missing=(mode == "full"),
                                                          legs_mode=legs, face=face)
        assert not legs or set(report["legs"]) == (set() if stem in LEGS_AS_THEY_ARE else set(npcrig.SIDES)), \
            (stem, "legs", report["legs"])
        missing = [n for n in npcrig.CORE if n != "Root" and n not in report["joints"]]
        assert not missing, (stem, "core joints not bound", missing)
        m.set_skeleton(bones, remap)
        for smooth, xf in inverse.items():
            m.set_inverse_bind(smooth, xf)
    f.rename_model("Player_" + age)
    report["hair"] = hair_material(m)
    out = f.build()
    after = bfres.Fres(out).model()
    assert after.name == "Player_" + age
    assert after.material_names == [HAIR_MATERIAL if n == report["hair"] else n for n in before.material_names]
    check_hair_material(after, stem)
    if mode != "raw":
        report["error"] = npcrig.check_rig(before, after, link, report["fixed"], complete=(mode == "full"),
                                           follow=report["follow"], legs=report["legs"], face=report["face"])
    else:
        assert [(b.name, b.parent) for b in after.bones] == [(b.name, b.parent) for b in before.bones]
    if GEAR_AWAY and mode == "full":
        out = parked(out, stem)
    zs = nxpack.zs_compress_like(out, raw, ZS_LEVEL)
    assert nxfmt.unzs(zs, stem) == out
    os.makedirs(CACHE, exist_ok=True)
    with open(path + ".zs", "wb") as fh:
        fh.write(zs)
    report = json.loads(json.dumps(report))
    json.dump({"size": len(out), "growth": len(out) - len(data), "report": report, "stem": stem, "age": age,
               "mode": mode}, open(path + ".json", "w"))
    return zs, len(out), len(out) - len(data), report


def hair_material(m):
    """Make sure the model has a material named HAIR_MATERIAL; returns the name of the
    material that was renamed (None: the model has its own)."""
    if HAIR_MATERIAL in m.material_names:
        return None
    for name in HAIR_STAND_INS:
        if name in m.material_names:
            m.rename_material(name, HAIR_MATERIAL)
            return name
    raise AssertionError("no stand-in for %s among %s" % (HAIR_MATERIAL, m.material_names))


def check_hair_material(m, what):
    """What the player's code assumes about a model: the game's own dictionary lookup finds
    HAIR_MATERIAL, and that material has the texture matrix parameter, at rest the identity."""
    names, nodes = m.f.read_dict(m.f.u64(m.off + 0x40))
    assert names == m.material_names
    at = bfres.resdic_find(nodes, [n.encode() for n in names], HAIR_MATERIAL.encode())
    assert at and names[at - 1] == HAIR_MATERIAL, (what, "the game would not find", HAIR_MATERIAL)
    value = m.shader_param(HAIR_MATERIAL, TEX_MTX0_KEY, "<I5f")
    assert value == TEX_MTX_IDENTITY, (what, HAIR_MATERIAL, "ns_tex_mtx0", value)


def model_size_entry(stem, growth):
    """Size table value for a rigged copy: what the game reserves for the NPC's own file
    (it knows what that model needs), plus what we added, plus a margin."""
    stock = build.stock_table().get("Model/%s.bfres" % stem)
    assert stock is not None, stem
    return stock + growth + 0x10000


def file_experiments():
    return [(mode, stem) for mode, stem in EXPERIMENTS if mode != "stock"]


def direct_experiments():
    return [stem for mode, stem in EXPERIMENTS if mode == "stock"]


def npc_models(mod):
    """Model files C000.. for the ring's NPCs, then one per file experiment."""
    chars = characters()
    jobs = [(c["stem"], "full") for c in chars] + [(stem, mode) for mode, stem in file_experiments()]
    reports = []
    t0 = time.time()
    for number, (stem, mode) in enumerate(jobs):
        for age in AGES:
            zs, size, growth, report = rigged(stem, age, mode, legs=LEGS if mode == "full" else None,
                                              face=FACE and mode == "full")
            build.write(mod, MODEL_FILE % (age, number, age), zs, size, build.SIZES,
                        size=model_size_entry(stem, growth))
            reports.append((number, stem, age, mode, report))
    print("  %d model files in %.0f s" % (len(reports), time.time() - t0))
    return reports


# ---------------------------------------------------------------- retargeting

def retarget(mod):
    """The player's animation sets retargeted (see LEGS). Returns lines for the build's output."""
    notes = []
    for age, (param_file, packs) in RETARGET_PACKS.items():
        projects = None
        for pack in packs:
            rel = "Pack/Actor/%s.pack.zs" % pack
            raw = open(os.path.join(nxfmt.ROMFS, rel), "rb").read()
            data = nxfmt.unzs(raw, rel)
            old = bytes(nxfmt.sarc_files(data)[param_file])
            param = byml.load(old)
            assert byml.dump(param, byml.version_of(old)) == old, "the parameter file does not round trip"
            assert list(param) == ["AnimationResources"] and all(list(r) == ["ModelProjectName"]
                                                                 for r in param["AnimationResources"]), param
            assert projects in (None, [r["ModelProjectName"] for r in param["AnimationResources"]])
            projects = [r["ModelProjectName"] for r in param["AnimationResources"]]
            for r in param["AnimationResources"]:
                r["IsRetarget"] = True
            new = byml.dump(param, byml.version_of(old))
            assert byml.load(new) == param
            out = nxpack.sarc_replace(data, {param_file: new})
            files = nxfmt.sarc_files(out)
            assert bytes(files[param_file]) == new and all(
                bytes(v) == bytes(files[k]) for k, v in nxfmt.sarc_files(data).items() if k != param_file)
            build.write(mod, rel, nxpack.zs_compress_like(out, raw), len(out), build.SIZES)
            notes.append("%s: IsRetarget on %d animation sets (%d -> %d bytes)" % (pack, len(projects), len(old), len(new)))
        have = [q for q in projects if os.path.exists(os.path.join(nxfmt.ROMFS, RETARGET_SKELETON % q))]
        assert len(have) == 1, have
        raw = open(os.path.join(nxfmt.ROMFS, RETARGET_SKELETON % have[0]), "rb").read()
        skeleton = nxfmt.unzs(raw, have[0])
        ref, link = bfres.Fres(skeleton).model(), link_model(age)
        assert ref.name == "Skeleton" and [(b.name, b.parent, b.scale, b.rotate, b.translate) for b in ref.bones] == \
            [(b.name, b.parent, b.scale, b.rotate, b.translate) for b in link.bones], "not Link's skeleton"
        entry = build.stock_table().get((RETARGET_SKELETON % have[0])[:-3])
        assert entry is not None and entry >= len(skeleton)
        for q in projects:
            if q != have[0]:
                assert not os.path.exists(os.path.join(nxfmt.ROMFS, "Model", q + ".Skeleton.bfres"))
                build.write(mod, RETARGET_SKELETON % q, raw, len(skeleton), build.SIZES, size=entry)
        notes.append("%s: reference skeleton %s copied for %d sets" % (age, have[0], len(projects) - 1))
    return notes


# ---------------------------------------------------------------- actor heaps

ACTOR_INFO = "RSDB/ActorInfo.Product.111.rstbl.byml.zs"
# RSDB/ActorInfo gives every actor two heap sizes that fit its stock model to the byte:
#   PreActorRenderInfoHeapSize = 3714 * shapes + 1491 * materials + 179 * bones + 10 * meshes
#                                - 21 * smooth matrices + 1953  (mean error 0.9 KB over 555
#                                actors; Player_Child 185,384)
#   InstanceHeapSize: the actor's own heap (animation buffers per bone and slot, physics, ...;
#                                Player_Child 5.7 MB, Player_Adult 10.2 MB, the menu Links 1.1 / 1.2 MB)
# A rigged NPC has up to 700 bones (Link 309 / 327), so the four actors that load a player
# model get room: (factor for InstanceHeapSize, absolute PreActorRenderInfoHeapSize).
# The numbers stay near what the game itself uses elsewhere (largest stock values: instance
# heap 10.2 MB for Player_Adult, pre-actor heap 464,504 for a castle room).
HEAPS = {"Player_Child": (2.0, 0x60000), "Player_Adult": (1.6, 0x60000),
         "PouchPlayer_Child": (3.0, 0x60000), "PouchPlayer_Adult": (3.0, 0x60000)}


def pre_actor_need(model):
    """The formula above for a parsed model (bfres.Model)."""
    meshes = sum(s.meshes for s in model.shapes)
    return int(3714 * model.shape_count + 1491 * model.material_count + 179 * len(model.bones) + 10 * meshes
               - 21 * model.smooth_count + 1953)


def actor_heaps(mod):
    raw = open(os.path.join(nxfmt.ROMFS, ACTOR_INFO), "rb").read()
    data = nxfmt.unzs(raw, ACTOR_INFO)
    rows = byml.load(data)
    p = bympatch.Patch(data)
    want = {}
    for i, r in enumerate(rows):
        if r["__RowId"] in HEAPS:
            factor, pre = HEAPS[r["__RowId"]]
            old = (p.get_int([i, "InstanceHeapSize"]), p.get_int([i, "PreActorRenderInfoHeapSize"]))
            assert old == (r["InstanceHeapSize"], r["PreActorRenderInfoHeapSize"]) and old[1] < pre
            new = ((int(old[0] * factor) + 0xFFFF) & ~0xFFFF, pre)
            p.set_int([i, "InstanceHeapSize"], new[0])
            p.set_int([i, "PreActorRenderInfoHeapSize"], new[1])
            want[r["__RowId"]] = new
            print("  %-18s InstanceHeapSize %d -> %d, PreActorRenderInfoHeapSize %d -> %d"
                  % (r["__RowId"], old[0], new[0], old[1], new[1]))
    assert set(want) == set(HEAPS)
    new = p.bytes()
    assert len(new) == len(data)
    for a, b in zip(rows, byml.load(new)):      # nothing else changed (rows are not shared)
        if a["__RowId"] in want:
            a = dict(a, InstanceHeapSize=want[a["__RowId"]][0], PreActorRenderInfoHeapSize=want[a["__RowId"]][1])
        assert a == b, a["__RowId"]
    build.write(mod, ACTOR_INFO, nxpack.zs_compress_like(new, raw), len(new))
    return {k: v[1] for k, v in want.items()}


# ---------------------------------------------------------------- the model hook

# Our part of the padding after .text: below the normal build's Character models part
# (exepatch.CAVE_MODELS, 0x83c66800-0x83c66c00) and clear of the emulator's built-in Dynamic
# FPS routine (0x83c66200-0x83c66278). This build's patch for the "Character models" folder
# uses both parts as one cave: NPC_CAVE[0] .. CAVE_MODELS[1].
NPC_CAVE = (0x83c66400, exepatch.CAVE_MODELS[0])
NUMBERED_FORMAT = "Model/%s_C%03d.%s.bfres"     # project, NPC number, fmdb


def model_hook(p, n_npc):
    """Replaces the 11 calls that format "Model/%s.%s.bfres" (build.MODEL_FORMAT_CALLS).
    For the projects Player_Child / Player_Adult it reads the stored character number v:
      EXPERIMENT_BASE <= v < + file experiments: numbered file n_npc + i
      ... < + stock experiments: project and fmdb both replaced by the NPC's own name
      else c = v modulo (normal characters + n_npc), never negative:
        c = 0: Link (unchanged); c < normal characters: the normal build's name table
        (Retro Link); else numbered file c - normal characters.
    A numbered file is formatted with NUMBERED_FORMAT (the format function is variadic: the
    number goes in w3, the fmdb name moves to x4). Everything else passes through."""
    img = p.img
    assert img.cstr(build.MODEL_PATH_FMT) == "Model/%s.%s.bfres"
    calls = build.model_format_calls(img)
    assert calls == build.MODEL_FORMAT_CALLS, [hex(c) for c in calls]
    # the format function saves x2-x7 for its va_list before anything else
    assert img.dis(build.STRING_FORMAT + 0x18, 2) == ["80062b88: stp      x2, x3, [x29, #-0x70]",
                                                     "80062b8c: stp      x4, x5, [x29, #-0x60]"]
    n_main = len(build.CHARACTERS)
    files, direct = file_experiments(), direct_experiments()
    base_names = p.data("base_names", b"".join(n.encode().ljust(16, b"\0") for n in build.PLAYER_MODELS))
    table = b""
    for ch in build.CHARACTERS[1:]:     # 32-byte slot per (character - 1, age), as in the normal build
        for base in build.PLAYER_MODELS:
            name = ("%s_%s" % (base, ch["tag"])).encode() if base in ch["models"] else b""
            assert len(name) < 32
            table += name.ljust(32, b"\0")
    model_names = p.data("model_names", table)
    fmt = p.data("numbered_format", NUMBERED_FORMAT.encode() + b"\0")
    names = b""
    for stem in direct:
        project, fmdb = stem.split(".")
        assert project == fmdb and len(project) < 32, stem
        names += project.encode().ljust(32, b"\0")
    direct_names = p.data("direct_names", names or b"\0" * 4)
    page = base_names & ~0xFFF
    assert all(a & ~0xFFF == page for a in (model_names, fmt, direct_names))
    h = gamedata.murmur3_32(build.CHARACTER_FLAG)
    got = build.GAMEDATA_MGR_GOT
    assert n_main + n_npc < 4096 and EXPERIMENT_BASE < 4096 and n_npc + len(files) <= 1000
    p.cave("model_hook", """
        adrp x9, #{fmt_page:#x}
        add x9, x9, #{fmt_off:#x}
        cmp x1, x9
        b.ne mh_out
        cbz x2, mh_out
        adrp x10, #{page:#x}
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
        sub w17, w16, #{exp_base}
        cmp w17, #{n_exp}
        b.lo mh_experiment
        mov w11, #{total}
        sdiv w12, w16, w11
        msub w12, w12, w11, w16
        add w14, w12, w11
        cmp w12, #0
        csel w12, w14, w12, lt
        cbz w12, mh_out
        cmp w12, #{n_main}
        b.hs mh_npc
        sub w12, w12, #1
        adrp x10, #{page:#x}
        add x10, x10, #{model_off:#x}
        lsl w12, w12, #6
        add x10, x10, w12, uxtw
        lsl w14, w13, #5
        add x10, x10, w14, uxtw
        ldrb w11, [x10]
        cbz w11, mh_out
        mov x2, x10
        b mh_out
    mh_npc:
        sub w12, w12, #{n_main}
    mh_numbered:
        mov x4, x3
        mov w3, w12
        adrp x1, #{page:#x}
        add x1, x1, #{numfmt_off:#x}
        b mh_out
    mh_experiment:
        cmp w17, #{n_files}
        b.hs mh_direct
        add w12, w17, #{n_npc}
        b mh_numbered
    mh_direct:
        sub w17, w17, #{n_files}
        adrp x10, #{page:#x}
        add x10, x10, #{direct_off:#x}
        lsl w17, w17, #5
        add x10, x10, w17, uxtw
        mov x2, x10
        mov x3, x10
    mh_out:
        b #{fmt_fn:#x}
    """.format(fmt_page=build.MODEL_PATH_FMT & ~0xFFF, fmt_off=build.MODEL_PATH_FMT & 0xFFF, page=page,
               base_off=base_names & 0xFFF, model_off=model_names & 0xFFF, numfmt_off=fmt & 0xFFF,
               direct_off=direct_names & 0xFFF, got_page=got & ~0xFFF, got_off=got & 0xFFF,
               hlo=h & 0xFFFF, hhi=h >> 16, exp_base=EXPERIMENT_BASE, n_exp=len(files) + len(direct),
               n_files=len(files), n_npc=n_npc, n_main=n_main, total=n_main + n_npc, fmt_fn=build.STRING_FORMAT))
    for site in calls:
        p.asm(site, "bl {model_hook}", expect="bl #%#x" % build.STRING_FORMAT)


def expected_path(v, project, fmdb, n_npc):
    """What the hook must make of a format call, worked out from the lists (not the code)."""
    stock = "Model/%s.%s.bfres" % (project, fmdb)
    if project not in build.PLAYER_MODELS:
        return stock
    files, direct = file_experiments(), direct_experiments()
    e = v - EXPERIMENT_BASE
    if 0 <= e < len(files):
        return NUMBERED_FORMAT % (project, n_npc + e, fmdb)
    if len(files) <= e < len(files) + len(direct):
        return "Model/%s.bfres" % direct[e - len(files)]
    c = v % (len(build.CHARACTERS) + n_npc)
    if c == 0:
        return stock
    if c < len(build.CHARACTERS):
        ch = build.CHARACTERS[c]
        return "Model/%s_%s.%s.bfres" % (project, ch["tag"], fmdb) if project in ch["models"] else stock
    return NUMBERED_FORMAT % (project, c - len(build.CHARACTERS), fmdb)


def check_model_hook(p, n_npc):
    """Run the assembled hook in an ARM64 emulator against a GameData manager laid out as
    the game's (README: GameData in memory) for many stored numbers, and compare the path
    it would format with expected_path(). Also: every path it can produce is a file of
    this build or of the game."""
    import unicorn
    from unicorn import arm64_const as A
    hook = p.symbols["model_hook"]
    uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    cave_page = hook & ~0xFFF
    uc.mem_map(cave_page, 0x1000)
    for addr, data in p.patches:
        if cave_page <= addr < cave_page + 0x1000:
            uc.mem_write(addr, data)
    got = build.GAMEDATA_MGR_GOT
    uc.mem_map(got & ~0xFFF, 0x1000)
    uc.mem_map(build.STRING_FORMAT & ~0xFFF, 0x1000)
    heap = 0x10000000
    uc.mem_map(heap, 0x10000)
    holder, mgr, table, records, strings = heap, heap + 0x100, heap + 0x1000, heap + 0x2000, heap + 0x8000
    capacity, count, slot_index = 61, 40, 7
    h = gamedata.murmur3_32(build.CHARACTER_FLAG)
    uc.mem_write(got, struct.pack("<Q", holder))
    uc.mem_write(holder, struct.pack("<Q", mgr))
    uc.mem_write(mgr + 0xE0, struct.pack("<Q", table))
    uc.mem_write(mgr + 0xEC, struct.pack("<I", capacity))
    uc.mem_write(mgr + 0xD0, struct.pack("<I", count))
    uc.mem_write(mgr + 0xD8, struct.pack("<Q", records))
    # two other flags sit in the probe path before ours (open addressing)
    at = h % capacity
    for k, entry in enumerate([(h ^ 0x5555, 3), (h ^ 0x1234, 4), (h, slot_index)]):
        uc.mem_write(table + 8 * ((at + k) % capacity), struct.pack("<II", *entry))

    def cstr(addr):
        out = b""
        while True:
            c = bytes(uc.mem_read(addr + len(out), 1))
            if c == b"\0":
                return out.decode()
            out += c

    def run(v, fmt_addr, project, fmdb):
        uc.mem_write(records + 0x28 * slot_index + 0x1C, struct.pack("<i", v))
        uc.mem_write(strings, project.encode() + b"\0")
        uc.mem_write(strings + 0x100, fmdb.encode() + b"\0")
        uc.reg_write(A.UC_ARM64_REG_X0, 0xDEAD0000)
        uc.reg_write(A.UC_ARM64_REG_X1, fmt_addr)
        uc.reg_write(A.UC_ARM64_REG_X2, strings)
        uc.reg_write(A.UC_ARM64_REG_X3, strings + 0x100)
        uc.reg_write(A.UC_ARM64_REG_X4, 0x4444)
        uc.emu_start(hook, build.STRING_FORMAT, count=2000)
        assert uc.reg_read(A.UC_ARM64_REG_PC) == build.STRING_FORMAT, "the hook did not reach the format function"
        assert uc.reg_read(A.UC_ARM64_REG_X0) == 0xDEAD0000
        x1, x2, x3, x4 = (uc.reg_read(r) for r in (A.UC_ARM64_REG_X1, A.UC_ARM64_REG_X2, A.UC_ARM64_REG_X3,
                                                   A.UC_ARM64_REG_X4))
        if x1 == fmt_addr:
            assert fmt_addr != build.MODEL_PATH_FMT or x4 == 0x4444
            return ("Model/%s.%s.bfres" if fmt_addr == build.MODEL_PATH_FMT else "other %s %s") % (cstr(x2), cstr(x3))
        assert cstr(x1) == NUMBERED_FORMAT
        return NUMBERED_FORMAT % (cstr(x2), x3 & 0xFFFFFFFF, cstr(x4))

    total = len(build.CHARACTERS) + n_npc
    values = sorted(set(list(range(-total - 3, total + 4)) + [EXPERIMENT_BASE + i for i in range(-2, len(EXPERIMENTS) + 3)]
                        + [-2 ** 31, 2 ** 31 - 1, 5 * total + 3, -7 * total - 1]))
    paths = set()
    for v in values:
        for project, fmdb in [("Player_Child", "Player_Child"), ("Player_Adult", "Player_Adult"),
                              ("Player_Child_Op", "Player_Child_Op"), ("Player_Adult_Dark", "Player_Adult_Dark"),
                              ("Npc_Kokiri_WC_Saria", "Npc_Kokiri_WC_Saria"), ("Player_Chil", "x"), ("", "")]:
            got_path = run(v, build.MODEL_PATH_FMT, project, fmdb)
            assert got_path == expected_path(v, project, fmdb, n_npc), (v, project, got_path)
            if project in build.PLAYER_MODELS:
                paths.add(got_path)
    assert run(5, build.MODEL_PATH_FMT + 1, "Player_Child", "Player_Child") == "other Player_Child Player_Child"
    # flag unknown to the game (Character models' GameDataList not loaded): pass through
    uc.mem_write(table + 8 * ((at + 2) % capacity), struct.pack("<II", 0, 0))
    assert run(5, build.MODEL_PATH_FMT, "Player_Child", "Player_Child") == "Model/Player_Child.Player_Child.bfres"
    for path in sorted(paths):
        rel = path + ".zs"
        found = os.path.exists(os.path.join(nxfmt.ROMFS, rel)) or \
            any(os.path.exists(os.path.join(build.BUILD, m, "romfs", rel)) for m in os.listdir(build.BUILD))
        assert found, "the hook can ask for a file nobody ships: " + rel
    return len(values), len(paths)


# ---------------------------------------------------------------- the Characters page view

# The menu's camera is set up for Link: on the Characters page it shows 1.44 m above the
# character's feet (child; MenuActorParam / the camera block, README), so an adult NPC would
# lose its head. While the page is open (build.PAGE_VIEW_FLAG) and the 3D Link is shown
# (display type 0), zoom_hook scales the camera position and its aim point about the
# character's feet by (NPC head height / Link head height): the character fills the frame
# exactly as Link does, whatever its size. The + menu's views are left alone for now.
CAMERA_NEXT = build.CAMERA_BLOCK_NEXT       # 0x8289d1f0: every path of the camera update passes here
ZOOM_DEADBAND = 0.05                        # closer to Link's height than this: no zoom at all
ZOOM_MAX = 8.0
HEAD = "Head"


def menu_pivots():
    """Where the menu Link stands, per age (ActorBasePos of PouchPlayer_<Age>)."""
    out = []
    for age in AGES:
        pack = nxfmt.sarc_files(nxfmt.read(os.path.join(nxfmt.ROMFS, "Pack", "Actor", "PouchPlayer_%s.pack.zs" % age)))
        param = byml.load(pack["GameParameter/MenuActorParam/PouchPlayer_%s.game__ui__MenuActorParam.bgyml" % age])
        out.append(tuple(param["ActorBasePos"][k] for k in "XYZ"))
    assert [tuple(round(v, 3) for v in piv) for piv in out] == [(0.0, 0.3, 0.0), (0.1, -0.2, 0.0)], out
    return out


def head_height(stem):
    m = bfres.Fres(npc_data(stem)[1]).model()
    return npcrig.rest_world(m.bones)[[b.name for b in m.bones].index(HEAD)].t[1]


def zoom_factors():
    """[[child factor, adult factor]] by table index: the ring, then the experiments."""
    link = [head_height("Player_%s.Player_%s" % (a, a)) for a in AGES]
    stems = [None] * len(build.CHARACTERS) + [c["stem"] for c in characters()] + [stem for _mode, stem in EXPERIMENTS]
    out = []
    for stem in stems:
        row = []
        for h in link:
            f = 1.0 if stem is None else head_height(stem) / h
            row.append(1.0 if abs(f - 1.0) < ZOOM_DEADBAND else min(max(f, 0.5), ZOOM_MAX))
        out.append(row)
    return out


def zoom_hook(p, n_npc):
    import re
    img = p.img
    assert img.dis(0x8289d16c, 1) == ["8289d16c: ldr      w9, [x10, #0x228]"]       # display type
    assert img.dis(0x8289d180, 6) == [
        "8289d180: ldr      s8, [x11, #0x22c]", "8289d184: ldr      s10, [x11, #0x230]",    # position x y z
        "8289d188: ldr      s9, [x11, #0x234]", "8289d18c: ldr      s0, [x11, #0x238]",     # aim point x y z
        "8289d190: ldr      s15, [x11, #0x23c]", "8289d194: ldr      s14, [x11, #0x240]"]
    assert img.dis(CAMERA_NEXT, 2) == ["8289d1f0: adrp     x11, #0x84c9e000", "8289d1f4: ldr      w10, [x10, #0x30c]"]
    # the function keeps its return address in its frame (we use bl inside the hook) and
    # has no use for v16-v31 and x14-x17 (x11-x13 are set again before their next use)
    start, end = img.func(CAMERA_NEXT)
    body = img.dis(start, (end - start) // 4)
    assert (start, end) == (0x8289d110, 0x8289d80c) and "stp      x29, x30, [sp, #0x60]" in body[6]
    rets = [i for i, l in enumerate(body) if l.split()[1] in ("ret", "retab")]
    assert len(rets) == 1 and "ldp      x29, x30, [sp, #0x60]" in body[rets[0] - 2]
    used = {int(m.group(1)) for l in body for m in re.finditer(r"\b[svdq](\d+)\b", l.split(":", 1)[1])}
    assert max(used) == 15, sorted(used)
    total = len(build.CHARACTERS) + n_npc
    factors = zoom_factors()
    assert len(factors) == total + len(EXPERIMENTS)
    table = p.data("zoom_factors", b"".join(struct.pack("<2H", *(round(f * 256) for f in row)) for row in factors))
    pivots = p.data("zoom_pivots", b"".join(struct.pack("<3f", *piv) for piv in menu_pivots()))
    got = build.GAMEDATA_MGR_GOT
    hp, hc, hs = (gamedata.murmur3_32(n) for n in (build.PAGE_VIEW_FLAG, "Sequence_IsChildhood", build.CHARACTER_FLAG))
    p.cave("zoom_hook", """
        cbnz w9, zh_out
        adrp x11, #{got_page:#x}
        ldr x11, [x11, #{got_off:#x}]
        ldr x11, [x11]
        cbz x11, zh_out
        movz w14, #{plo:#x}
        movk w14, #{phi:#x}, lsl #16
        bl zh_bool
        cbz w17, zh_out
        cbz w12, zh_out
        movz w14, #{clo:#x}
        movk w14, #{chi:#x}, lsl #16
        bl zh_bool
        cbz w17, zh_out
        cmp w12, #0
        cset w12, eq
        fmov s17, w12
        ldr x12, [x11, #0xe0]
        cbz x12, zh_out
        ldr w13, [x11, #0xec]
        cbz w13, zh_out
        movz w14, #{slo:#x}
        movk w14, #{shi:#x}, lsl #16
        udiv w15, w14, w13
        msub w15, w15, w13, w14
        mov w16, w15
    zh_probe:
        ldr x17, [x12, w16, uxtw #3]
        cmp w17, w14
        b.eq zh_found
        cbz w17, zh_out
        add w16, w16, #1
        cmp w16, w13
        csel w16, wzr, w16, hs
        cmp w16, w15
        b.ne zh_probe
        b zh_out
    zh_found:
        lsr x17, x17, #32
        ldr w12, [x11, #0xd0]
        cmp w17, w12
        b.hs zh_out
        ldr x12, [x11, #0xd8]
        mov w13, #0x28
        umaddl x12, w17, w13, x12
        ldr w12, [x12, #0x1c]
        sub w14, w12, #{exp_base}
        cmp w14, #{n_exp}
        b.hs zh_ring
        add w12, w14, #{total}
        b zh_index
    zh_ring:
        mov w13, #{total}
        sdiv w14, w12, w13
        msub w12, w14, w13, w12
        add w14, w12, w13
        cmp w12, #0
        csel w12, w14, w12, lt
    zh_index:
        fmov w15, s17
        add w12, w15, w12, lsl #1
        adrp x13, #{table_page:#x}
        add x13, x13, #{table_off:#x}
        ldrh w14, [x13, w12, uxtw #1]
        cmp w14, #256
        b.eq zh_out
        ucvtf s16, w14, #8
        adrp x13, #{pivot_page:#x}
        add x13, x13, #{pivot_off:#x}
        mov w14, #12
        umaddl x13, w15, w14, x13
        ldr s18, [x13]
        ldr s19, [x13, #4]
        ldr s20, [x13, #8]
        fsub s8, s8, s18
        fmul s8, s8, s16
        fadd s8, s8, s18
        fsub s10, s10, s19
        fmul s10, s10, s16
        fadd s10, s10, s19
        fsub s9, s9, s20
        fmul s9, s9, s16
        fadd s9, s9, s20
        fsub s0, s0, s18
        fmul s0, s0, s16
        fadd s0, s0, s18
        fsub s15, s15, s19
        fmul s15, s15, s16
        fadd s15, s15, s19
        fsub s14, s14, s20
        fmul s14, s14, s16
        fadd s14, s14, s20
    zh_out:
        adrp x11, #0x84c9e000
        b #{resume:#x}
    zh_bool:
        mov w17, wzr
        ldr x12, [x11, #0x20]
        cbz x12, zb_ret
        ldr w13, [x11, #0x2c]
        cbz w13, zb_ret
        udiv w15, w14, w13
        msub w15, w15, w13, w14
        mov w16, w15
    zb_probe:
        ldr x17, [x12, w16, uxtw #3]
        cmp w17, w14
        b.eq zb_found
        cbz w17, zb_none
        add w16, w16, #1
        cmp w16, w13
        csel w16, wzr, w16, hs
        cmp w16, w15
        b.ne zb_probe
    zb_none:
        mov w17, wzr
        ret
    zb_found:
        lsr x17, x17, #32
        ldr w12, [x11, #0x10]
        cmp w17, w12
        b.hs zb_none
        ldr x12, [x11, #0x18]
        mov w13, #0x18
        umaddl x12, w17, w13, x12
        ldrb w12, [x12, #0x16]
        mov w17, #1
    zb_ret:
        ret
    """.format(got_page=got & ~0xFFF, got_off=got & 0xFFF, plo=hp & 0xFFFF, phi=hp >> 16, clo=hc & 0xFFFF,
               chi=hc >> 16, slo=hs & 0xFFFF, shi=hs >> 16, exp_base=EXPERIMENT_BASE, n_exp=len(EXPERIMENTS),
               total=total, table_page=table & ~0xFFF, table_off=table & 0xFFF, pivot_page=pivots & ~0xFFF,
               pivot_off=pivots & 0xFFF, resume=CAMERA_NEXT + 4))
    p.asm(CAMERA_NEXT, "b {zoom_hook}", expect="adrp x11, #0x84c9e000")
    return factors


def check_zoom_hook(p, n_npc, factors):
    """Run zoom_hook in the emulator for every table index, both ages, page open / closed and
    other display types: the camera numbers must come out scaled about the feet by the
    table's factor, or untouched; x9, x10 and the replaced instruction's x11 as the game expects."""
    import unicorn
    from unicorn import arm64_const as A
    hook = p.symbols["zoom_hook"]
    uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    # enable FP / SIMD (CPACR_EL1.FPEN)
    uc.reg_write(A.UC_ARM64_REG_CPACR_EL1, uc.reg_read(A.UC_ARM64_REG_CPACR_EL1) | (3 << 20))
    cave_page = hook & ~0xFFF
    uc.mem_map(cave_page, 0x1000)
    for addr, data in p.patches:
        if cave_page <= addr < cave_page + 0x1000:
            uc.mem_write(addr, data)
    got = build.GAMEDATA_MGR_GOT
    uc.mem_map(got & ~0xFFF, 0x1000)
    uc.mem_map(CAMERA_NEXT & ~0xFFF, 0x1000)
    heap = 0x10000000
    uc.mem_map(heap, 0x10000)
    holder, mgr = heap, heap + 0x100
    bool_map, bool_rec, int_map, int_rec = heap + 0x1000, heap + 0x2000, heap + 0x3000, heap + 0x4000
    uc.mem_write(got, struct.pack("<Q", holder))
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
    hp, hc, hs = (gamedata.murmur3_32(n) for n in (build.PAGE_VIEW_FLAG, "Sequence_IsChildhood", build.CHARACTER_FLAG))

    def put(table, cap, at, h, index):
        while struct.unpack("<I", uc.mem_read(table + 8 * at, 4))[0]:
            at = (at + 1) % cap
        uc.mem_write(table + 8 * at, struct.pack("<II", h, index))
    put(bool_map, bcap, hp % bcap, hp ^ 0x77, 1)    # a stranger in our flag's slot, so that probing is exercised
    put(bool_map, bcap, hp % bcap, hp, 5)
    put(bool_map, bcap, hc % bcap, hc, 9)
    put(int_map, icap, hs % icap, hs, 7)
    pivots = menu_pivots()
    total = len(build.CHARACTERS) + n_npc
    fregs = [A.UC_ARM64_REG_S8, A.UC_ARM64_REG_S10, A.UC_ARM64_REG_S9, A.UC_ARM64_REG_S0, A.UC_ARM64_REG_S15,
             A.UC_ARM64_REG_S14, A.UC_ARM64_REG_S13, A.UC_ARM64_REG_S12, A.UC_ARM64_REG_S11, A.UC_ARM64_REG_S5,
             A.UC_ARM64_REG_S4, A.UC_ARM64_REG_S3]
    camera = [0.07, 1.15, 4.49, 0.07, 0.95, 0.0, 0.0, 1.0, 0.0, 0.1, 1000.0, 20.0]

    def f32(x):
        return struct.unpack("<f", struct.pack("<f", x))[0]

    def run(display, page, child, value):
        uc.mem_write(bool_rec + 0x18 * 5 + 0x16, bytes([page]))
        uc.mem_write(bool_rec + 0x18 * 9 + 0x16, bytes([child]))
        uc.mem_write(int_rec + 0x28 * 7 + 0x1C, struct.pack("<i", value))
        for reg, v in zip(fregs, camera):
            uc.reg_write(reg, struct.unpack("<I", struct.pack("<f", v))[0])
        uc.reg_write(A.UC_ARM64_REG_X9, display)
        uc.reg_write(A.UC_ARM64_REG_X10, 0xABCD0000)
        uc.reg_write(A.UC_ARM64_REG_X20, 0x1234)
        uc.reg_write(A.UC_ARM64_REG_X30, 0x5555)
        uc.emu_start(hook, CAMERA_NEXT + 4, count=4000)
        assert uc.reg_read(A.UC_ARM64_REG_PC) == CAMERA_NEXT + 4
        assert (uc.reg_read(A.UC_ARM64_REG_X9), uc.reg_read(A.UC_ARM64_REG_X10), uc.reg_read(A.UC_ARM64_REG_X20)) == \
            (display, 0xABCD0000, 0x1234)
        assert uc.reg_read(A.UC_ARM64_REG_X11) == 0x84c9e000
        return [struct.unpack("<f", struct.pack("<I", uc.reg_read(r) & 0xFFFFFFFF))[0] for r in fregs]

    def index_of(value):
        e = value - EXPERIMENT_BASE
        return total + e if 0 <= e < len(EXPERIMENTS) else value % total

    runs = zoomed = 0
    values = list(range(-3, total + 3)) + [EXPERIMENT_BASE + i for i in range(-1, len(EXPERIMENTS) + 1)] + [-total * 3 - 1]
    plain = [f32(c) for c in camera]
    for value in values:
        for child in (1, 0):
            for display, page in ((0, 1), (0, 0), (1, 1), (3, 1), (5, 1)):
                got_cam = run(display, page, child, value)
                age = 0 if child else 1
                f = round(factors[index_of(value)][age] * 256) / 256
                if display == 0 and page and f != 1.0:
                    piv = pivots[age]
                    want = list(plain)
                    for k in range(6):
                        want[k] = f32(f32(f32(plain[k] - f32(piv[k % 3])) * f32(f)) + f32(piv[k % 3]))
                    assert all(abs(a - b) <= 1e-6 * max(1.0, abs(b)) for a, b in zip(got_cam, want)), \
                        (value, child, got_cam, want)
                    zoomed += 1
                else:
                    assert got_cam == plain, (value, child, display, page)      # bit for bit untouched
                runs += 1
    # flags unknown to the game: untouched
    uc.mem_write(bool_map, bytes(8 * bcap))
    assert run(0, 1, 1, 15) == plain
    return runs, zoomed


def npc_character_models(mod, n_npc):
    """The normal build's Character models (flags, Retro Link, its patch), then the exe patch
    again with our model hook in place of its own, its two other hooks as they are, and the
    Characters page zoom."""
    build.character_models(mod)
    del build.EXE_PATCHES[mod]
    region = exepatch.CAVE_REGION
    exepatch.CAVE_REGION = (NPC_CAVE[0], region[1])
    try:
        p = exepatch.Patcher(cave=(NPC_CAVE[0], exepatch.CAVE_MODELS[1]))
    finally:
        exepatch.CAVE_REGION = region
    model_hook(p, n_npc)
    build.keep_camera_hook(p)
    build.view_hook(p)
    factors = zoom_hook(p, n_npc)
    build.write_ips(mod, p)
    return p, factors


# ---------------------------------------------------------------- debug buttons

_normal_swap_steps = build._swap_step_list


def _swap_step_list(g, value, hide=True, **kw):
    """The normal build's swap; for a Step the "store the number" node becomes "add to it";
    a Backdrop button gets its own steps."""
    if isinstance(value, Backdrop):
        return backdrop_steps(g, value)
    if isinstance(value, PortraitRun):
        return portrait_run_steps(g, int(value))
    steps = _normal_swap_steps(g, 0 if isinstance(value, Step) else value, hide, **kw)
    if isinstance(value, Step):
        first = g.nodes[steps[0]]
        assert first["Name"] == "SeqOneShotSetNonStructGameDataInt", first["Name"]
        assert [x["Default Value"] for x in first["Parameters"]["Inputs"]["String"]] == [build.CHARACTER_FLAG]
        assert sum(g.nodes[i].get("Name") == first["Name"] for i in steps) == 1
        node = build.vanilla("ChangeGamePause", 2, "SeqOneShotAddNonStructGameDataInt", Value=int(value),
                             GameDataFullName=build.CHARACTER_FLAG)
        node["Node Index"], node["GUID"] = first["Node Index"], first["GUID"]
        g.nodes[steps[0]] = node
    return steps


# ---------------------------------------------------------------- portrait backdrop (debug)
#
# A flat black or white picture BEHIND the 3D character of the Characters page, for cut-out
# portraits (the same frame on black and on white gives exact transparency).
# - The − menu's own background is the screen MenuBG_00: no class, draw unit 0, sort key
#   4000, IsPauseLower (the only unit 0 screens shown under the pause menus have it). The
#   page layouts with their vignettes are draw unit 1, over the 3D character. Our screen
#   BACKDROP_SCREEN copies MenuBG_00's place one sort key higher, with the game's fade layout
#   EventFade_00 (already used by three screens): animation "Color" stopped at frame 0 =
#   black pane, 1 = white panes (Key Items does exactly this with EventFade_01), "VeryShortIn"
#   stopped at its last frame = fully faded in.
# - The page's own layout (the vignette, drawn over the character) gets one animation,
#   PAGE_HIDE_ANIM: frame 1 hides everything under N_All_00.
# - Buttons: A black, L white, ZL off. Leaving the page (R / ZR, B / −) closes the screen.
# Every graph shape used here has a vanilla or in-game-proven twin: a screen state selector
# under a Simultaneous with one-shot children (ImportantMenu node 56), load / open on demand
# (our page screen), close + unload behind a selector (MinusMenuToMain, our page screen).
# Round 1 (snaildev, 2026-11-10): A / L hid the vignette (so PAGE_HIDE_ANIM and stop-animation
# requests work) but no colour appeared: our draw unit 0 screen did not show. Round 2 tells
# the causes apart on separate buttons:
#   A    MenuBG_00's own animation "IsMask" at frame 1: its near-black pane replaces the
#        captured picture (the + menu does this for its equipment view: PlusMenu node 143).
#   L    our unit 0 screen again, white, now also made visible the way the game does for
#        MenuBG_00 right after opening it (SeqOneShotSetScreenVisible, MinusMenu node 22).
#   Down a second screen of ours in the MENUS' draw unit under the page (sort key 4999),
#        white: expected to cover the character; proves a screen of ours with this layout shows.
#   ZL   everything off.
BACKDROP_SCREEN = "CharacterBackdrop_00"        # draw unit 0, over MenuBG_00
BACKDROP_SCREEN_MENU = "CharacterBackdrop_01"   # draw unit 1, under the page
BACKDROP_ROWS = {BACKDROP_SCREEN: {"IsPauseLower": True, "SortKey": 4001}}
# Round 2 (snaildev, 2026-11-10): A and L changed nothing but the vignette; the draw unit 1 screen
# turned everything white, character included (so a screen of ours with this layout works).
# What is behind the character is not 2D at all: the menu character has a child actor
# (PouchPlayerParam.BGActor = Obj_PlusMenuBackground, found by the main session), one cylinder
# with one material around and under him, part of the 3D picture; it covers draw unit 0.
# Round 3: this build ships that model moved out of sight (MENU_BACKDROP_*), so the buttons
# show what the 3D picture lets through: A = MenuBG_00 made visible (Key Items hides it) with
# its near-black pane, L = our white unit 0 screen, ZL = both off (MenuBG_00 hidden again).
MENU_BACKDROP_MODEL = "Obj_PlusMenuBackground.Obj_PlusMenuBackground"
MENU_BACKDROP_AWAY = (0.0, -2000.0, 0.0)       # metres: the root bone's new place
BACKDROP_LAYOUT = "EventFade_00"
BACKDROP_LOAD_FRAMES = 15
MENU_BG = "MenuBG_00"
PAGE_HIDE_ANIM = "PortraitHide"
PAGE_HIDE_PANE = "N_All_00"


def _dummy(g):
    return g.add(build.vanilla("ImportantMenu", 16, "SeqExecuteGenericDummyAction"))


def _when(g, screen, when_open, when_closed):
    """One step: Simultaneous -> screen state selector -> a one-shot (the vanilla shape)."""
    return g.add(build.t_simultaneous([g.add(build.t_screen_state_sel(screen, when_open, when_closed))], root=False))


def _visible(g, screen, on=True):
    return g.add(build.vanilla("MinusMenu", 22, "SeqOneShotSetScreenVisible", ScreenName=screen, IsVisible=on))


def _all_off(g):
    """Children for one Simultaneous: our screen closed if it is open."""
    return [g.add(build.t_screen_state_sel(sc, g.add(build.t_close_unload_screen(sc)), _dummy(g))) for sc in BACKDROP_ROWS]


def _backdrop_on(g, colour):
    """Steps that show our backdrop screen in a colour (0 black, 1 white) and hide the page's 2D layer."""
    S = BACKDROP_SCREEN
    return [_when(g, S, _dummy(g), g.add(build.t_load_screen(S))),
            g.add(build.t_wait_frames(BACKDROP_LOAD_FRAMES)),
            _when(g, S, _dummy(g), g.add(build.t_open_screen(S))),
            g.add(build.t_simultaneous([_visible(g, S),
                                        g.add(build.t_stop_anim(S, "VeryShortIn", 15.0)),
                                        g.add(build.t_stop_anim(S, "Color", float(colour))),
                                        g.add(build.t_stop_anim(build.PAGE_SCREEN, PAGE_HIDE_ANIM, 1.0))], root=False))]


def backdrop_steps(g, value):
    """Steps (one per frame) of a Backdrop button. The first call per graph also makes the
    page's R / ZR exit switch everything off."""
    P = build.PAGE_SCREEN
    if not getattr(g, "_backdrop_exit", False):
        exits = []
        for n in g.nodes:
            if n["Node Type"] != "Element_Simultaneous":
                continue
            for c in n.get("Plugs", {}).get("Child", []):
                k = g.nodes[c["Node Index"]]
                keys = [x["Default Value"] for x in k.get("Parameters", {}).get("Inputs", {}).get("String", [])]
                if k.get("Name") == "SeqOneShotStartSound" and "mc_TabToRight" in keys:
                    exits.append(n)
        assert len(exits) == 1, "the page's R / ZR exit step was not found"
        exits[0]["Plugs"]["Child"] += [{"Node Index": i, "Name": ""} for i in _all_off(g)]
        g._backdrop_exit = True
    if value == 2:
        return [g.add(build.t_stop_anim(P, PAGE_HIDE_ANIM, 0.0)), g.add(build.t_simultaneous(_all_off(g), root=False))]
    assert value in (0, 1)
    return _backdrop_on(g, int(value))


# ---------------------------------------------------------------- portrait run (debug)
#
# One button press shows every character for the capture tool (tools\npcshots.py, which only
# reads memory and asks the emulator for screenshots; it presses nothing):
#   backdrop on (black); then per character of PORTRAIT_RING: the normal build's swap to that
#   number, but without the step that gives the menu character his equipment (the new
#   character exists without it for a frame in every normal swap too), PORTRAIT_SETTLE frames
#   on black, PORTRAIT_HOLD frames on white, back to black; at the end everything off.
# The run is unrolled (a sequence of one nested sequence per character, made of the step
# kinds the buttons already use), so it needs no loop construct in the graph; nothing can
# stop it but closing the game.
# For the two pictures of a character to match pixel for pixel he must not move: this build
# ships the player's idle and face clips cut to one frame (freeze_animations).
PORTRAIT_SETTLE = 150       # frames on black: the swap's own fades, then at least one screenshot
PORTRAIT_HOLD = 120         # frames on white
SYNC_NODE = "SeqSyncPlayerEquipmentAndGameData"
# Trial 1 (snaildev, 2026-11-10): the run works, but leaving the equipment step out did not remove
# sword and shield (the menu character gets them some other way too), and the header covers
# the top of tall hair (Mido). The game has its own switch for a menu character without gear:
# AfterLoadSaveData asks the GameData Bool NO_GEAR_FLAG and then either deletes the pouch
# target's equipment (SeqOneShotPouchTargetRequestDeleteEquipment) or syncs it. The run sets
# the flag, puts that delete in the place of each swap's equipment step, and hides the
# header / footer screen; at the end it undoes all three and swaps once more the normal way.
NO_GEAR_FLAG = "Sequence_IsDisablePouchSyncEquipment"
# Trial 2 (snaildev, 2026-11-10): header hidden and back, the slide shows, but the gear was STILL
# there: neither the flag nor the delete request takes it off the menu character. So the run
# uses the normal swap again and the gear is dealt with in the models (GEAR_AWAY): the bones
# equipment hangs on become children of a new bone 2 km below the character. They are leaves
# without weights in every model; Link's animation still writes their own offsets, but those
# are relative to the parent. Test builds only: gear is out of sight everywhere.
GEAR_AWAY = False       # main() of this test build switches it on; builds that use us as a library keep the gear
GEAR_BONES = ("Pod_A", "Pod_B", "Weapon_Root", "Weapon_L", "Weapon_R")
GEAR_PARENT = "Gx_Away"
GEAR_OFFSET = (0.0, -2000.0, 0.0)


def park_gear(m):
    """Re-parent the gear bones of a model (bfres.Model) under a far-away bone."""
    names = [b.name for b in m.bones]
    assert GEAR_PARENT not in names
    gear = [names.index(n) for n in GEAR_BONES if n in names]
    assert gear, "no gear bones"
    for i in gear:
        b = m.bones[i]
        assert b.smooth == -1 and b.rigid == -1 and not any(x.parent == i for x in m.bones), ("not a free leaf", b.name)
        assert not any(sh.bone == i for sh in m.shapes), b.name
    keep = [i for i in range(len(m.bones)) if i not in gear]
    new_index = {old: new for new, old in enumerate(keep)}
    far = len(keep)
    for k, old in enumerate(gear):
        new_index[old] = far + 1 + k
    bones = []
    for old in keep:
        b = m.bones[old]
        bones.append(b.copy(parent=new_index[b.parent] if b.parent >= 0 else -1))
    root = names.index("Root") if "Root" in names else 0
    away = bfres.Bone(GEAR_PARENT, parent=new_index[root])
    away.set_local(translate=GEAR_OFFSET)
    bones.append(away)
    for old in gear:
        bones.append(m.bones[old].copy(parent=far))
    m.set_skeleton(bones, [new_index[i] for i in range(len(names))])
    return [names[i] for i in gear]


def parked(data, what):
    """A model file's bytes with the gear parked; checked by reading it back."""
    f = bfres.Fres(data)
    m = f.model()
    before = {b.name: (b.smooth, b.rigid) for b in m.bones}
    moved = park_gear(m)
    out = f.build()
    after = bfres.Fres(out).model()
    names = [b.name for b in after.bones]
    far = names.index(GEAR_PARENT)
    assert tuple(after.bones[far].translate) == GEAR_OFFSET
    assert all(after.bones[names.index(n)].parent == far for n in moved), what
    assert {b.name: (b.smooth, b.rigid) for b in after.bones if b.name != GEAR_PARENT} == before, what
    assert [x.name for x in after.shapes] == [x.name for x in m.shapes] and after.material_names == m.material_names
    return out


def park_link_gear(mod):
    """Link's own models (the game's files, shipped again from our folder) and the built
    Retro Link models with the gear parked."""
    for age in AGES:
        stem = "Player_%s.Player_%s" % (age, age)
        raw, data = npc_data(stem)
        out = parked(data, stem)
        zs = nxpack.zs_compress_like(out, raw, ZS_LEVEL)
        assert nxfmt.unzs(zs, stem) == out
        build.write(mod, "Model/%s.bfres.zs" % stem, zs, len(out), build.SIZES, size=model_size_entry(stem, len(out) - len(data)))
    folder = os.path.join(build.BUILD, "Character models", "romfs", "Model")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".bfres.zs"):
            continue
        path = os.path.join(folder, name)
        raw = open(path, "rb").read()
        data = nxfmt.unzs(raw, name)
        try:
            out = parked(data, name)
        except AssertionError as e:
            # (Retro Link, made with Switch Toolbox: its file keeps a block where ours puts new
            # data, tools\bfres.py does not handle that layout yet)
            print("  %s: gear NOT parked (%s)" % (name, repr(e)[:80]))
            continue
        key = "Model/" + name[:-3]
        assert key in build.SIZES and build.SIZES[key] >= len(out) + 0x1000, (key, build.SIZES.get(key), len(out))
        zs = nxpack.zs_compress_like(out, raw, ZS_LEVEL)
        assert nxfmt.unzs(zs, name) == out
        open(path, "wb").write(zs)
        print("  %s: gear parked (%d bytes)" % (name, len(out)))
OVERLAY_SCREEN = "MenuOverlay_00"
FROZEN_CLIPS = {            # animation file -> {clip: its stock length in frames}
    "Player_Child_04_Animation": {"Wait": 600, "Wait_Sword": 600},
    "Player_Child_01_Animation": {"Face_Blink": 15, "Face_Eye_Random": 600},
    "Player_Adult_04_Animation": {"Wait": 600, "Wait_Sword": 600},
    "Player_Adult_03_Animation": {"Pouch_Wait_LSword": 600},
    "Player_Adult_01_Animation": {"Face_Blink": 15, "Face_Eye_Random": 600},
}


PORTRAIT_TRIAL = (0, 1, 2, 3, 8)        # Link, Retro Link, Saria, Mido, adult Zelda (a tall one)


def portrait_ring(count):
    """Stored numbers a PortraitRun shows: the first <count>, or the trial list for -1."""
    if count < 0:
        return list(PORTRAIT_TRIAL)
    assert 0 < count <= len(build.CHARACTERS) + len(characters())
    return list(range(count))


def portrait_run_steps(g, count):
    S = BACKDROP_SCREEN
    steps = [g.add(build.t_simultaneous([_visible(g, OVERLAY_SCREEN, False)], root=False)),
             g.add(build.t_wait_frames(1))] + _backdrop_on(g, 0)
    for number in portrait_ring(count):
        one = _normal_swap_steps(g, number, True)
        one += [g.add(build.t_wait_frames(PORTRAIT_SETTLE)),
                g.add(build.t_stop_anim(S, "Color", 1.0)),
                g.add(build.t_wait_frames(PORTRAIT_HOLD)),
                g.add(build.t_stop_anim(S, "Color", 0.0))]
        # one nested sequence per character (a node has at most 255 children); nested one-step-
        # per-frame sequences are what build.character_swap_steps hands other graphs
        steps.append(g.add(build.t_sequential(one, root=False, same_frame=False)))
    return steps + [g.add(build.t_wait_frames(30)),
                    g.add(build.t_simultaneous([g.add(build.t_stop_anim(build.PAGE_SCREEN, PAGE_HIDE_ANIM, 0.0)),
                                                _visible(g, OVERLAY_SCREEN, True)], root=False)),
                    g.add(build.t_simultaneous(_all_off(g), root=False))]


def freeze_animations(mod):
    """The clips the menu character idles in, cut to one frame: he stands still (within
    what one frame of the idle moves), blinks and eye darts included. Only the frame count
    in each clip's header changes (FSKA +0x40); test builds only (the field idle freezes too)."""
    for stem, clips in FROZEN_CLIPS.items():
        rel = "Model/%s.anim.bfres.zs" % stem
        raw = open(os.path.join(nxfmt.ROMFS, rel), "rb").read()
        d = bytearray(nxfmt.unzs(raw, rel))
        assert d[:8] == b"FRES    "
        arr, = struct.unpack_from("<Q", d, 0x58)
        count, = struct.unpack_from("<H", d, 0xE2)
        found = {}
        for i in range(count):
            o = arr + i * 0x50
            assert d[o:o + 4] == b"FSKA"
            name_ptr, = struct.unpack_from("<Q", d, o + 8)
            n, = struct.unpack_from("<H", d, name_ptr)
            name = d[name_ptr + 2:name_ptr + 2 + n].decode()
            if name in clips:
                frames, = struct.unpack_from("<i", d, o + 0x40)
                assert frames == clips[name], (stem, name, frames)
                struct.pack_into("<i", d, o + 0x40, 1)
                found[name] = frames
        assert found == clips, (stem, found)
        zs = nxpack.zs_compress_like(bytes(d), raw, ZS_LEVEL)
        assert nxfmt.unzs(zs, rel) == bytes(d)
        build.write(mod, rel, zs, len(d))


def check_portrait_run():
    """Read the built graph back: the run's sequence has, per character, one store of its
    number, no equipment step, and the black / white switches in order."""
    import ainbx
    path = os.path.join(build.BUILD, "Characters page", "romfs", "Sequence", "CharacterMenu.module.ainb")
    nodes = ainbx.load(open(path, "rb").read()).as_dict()["Nodes"]
    trig = {v: k for k, v in build.TRIG.items()}
    runs = {b: int(v) for b, v in BUTTONS.items() if isinstance(v, PortraitRun)}
    found = {}
    for n in nodes:
        if n["Node Type"] != "Element_BoolSelector":
            continue
        q = n["Parameters"]["Inputs"]["Bool"][0]
        b = trig[q["Output Index"]]
        if nodes[q["Node Index"]].get("Name") == "SeqQueryControllerTrigMaskChecker" and b in runs:
            end = nodes[n["Plugs"]["Child"][0]["Node Index"]]
            assert b not in found
            found[b] = nodes[end["Plugs"]["Transition"][0]["Node Index"]]
    assert set(found) == set(runs) and all(x["Node Type"] == "Element_Sequential" for x in found.values())

    def flat(i):
        n = nodes[i]
        if n["Node Type"] in ("Element_Sequential", "Element_Simultaneous"):
            return [x for c in n["Plugs"]["Child"] for x in flat(c["Node Index"])]
        return [n]

    def val(n, name):
        for kind in ("String", "Float", "Int", "Bool"):
            for x in n.get("Parameters", {}).get("Inputs", {}).get(kind, []):
                if x["Name"] == name:
                    return x.get("Default Value")
        return None

    out = {}
    for b, seq in found.items():
        order = [x for c in seq["Plugs"]["Child"] for x in flat(c["Node Index"])]
        stores = [val(n, "Value") for n in order if n.get("Name") == "SeqOneShotSetNonStructGameDataInt"
                  and val(n, "GameDataFullName") == build.CHARACTER_FLAG]
        assert stores == portrait_ring(runs[b]), stores[:5]
        assert sum(n.get("Name") == SYNC_NODE for n in order) == len(stores)    # the normal swap, untouched
        shown = [val(n, "IsVisible") for n in order if n.get("Name") == "SeqOneShotSetScreenVisible"
                 and val(n, "ScreenName") == OVERLAY_SCREEN]
        assert shown == [False, True], shown
        colours = [val(n, "Frame") for n in order if n.get("Name") == "SeqOneShotRequestStopAnimationScreen"
                   and val(n, "ScreenName") == BACKDROP_SCREEN and val(n, "AnimationName") == "Color"]
        assert colours == [0.0] + [1.0, 0.0] * len(stores), colours[:6]
        waits = [x for n in order if n.get("Name") == "SeqExecuteGenericWaitFrame"
                 for ps in n["Properties"].values() for x in ps if x["Name"] == "Frame"]
        out[b] = (len(stores), len(seq["Plugs"]["Child"]), sum(x["Default Value"] for x in waits))
    return out, len(nodes), os.path.getsize(path)


def hide_menu_backdrop(mod):
    """The menu character's 3D backdrop (one rigid shape on the model's only bone) with that
    bone moved far below the scene: nothing of it is in the picture. Test builds only."""
    stem = MENU_BACKDROP_MODEL
    raw, data = npc_data(stem)
    f = bfres.Fres(data)
    m = f.model()
    assert [b.name for b in m.bones] == ["Root"] and len(m.shapes) == 1 and m.shapes[0].weights == 0 \
        and m.shapes[0].bone == 0 and m.smooth_count == 0 and m.rigid_count == 0
    assert m.bones[0].translate == (0.0, 0.0, 0.0) and m.bones[0].scale == (1.0, 1.0, 1.0)
    root = m.bones[0].copy()
    root.set_local(translate=MENU_BACKDROP_AWAY)
    m.set_skeleton([root], [0])
    out = f.build()
    after = bfres.Fres(out).model()
    assert tuple(after.bones[0].translate) == MENU_BACKDROP_AWAY and after.name == m.name
    assert after.material_names == m.material_names and [x.name for x in after.shapes] == [x.name for x in m.shapes]
    zs = nxpack.zs_compress_like(out, raw, ZS_LEVEL)
    assert nxfmt.unzs(zs, stem) == out
    build.write(mod, "Model/%s.bfres.zs" % stem, zs, len(out), build.SIZES, size=model_size_entry(stem, len(out) - len(data)))


_normal_add_page_screen = build.add_page_screen


def add_page_screen(mod, sizes):
    """The normal build's screen table, plus the backdrop's row (MenuBG_00's place)."""
    _normal_add_page_screen(mod, sizes)
    path = os.path.join(build.BUILD, mod, "romfs", build.UISCREEN)
    raw = open(path, "rb").read()
    data = nxfmt.unzs(raw, build.UISCREEN)
    rows = build.byml.load(data)
    assert build.byml.dump(rows, build.byml.version_of(data)) == data
    bg = [r for r in rows if r["Name"] == "MenuBG_00"]
    assert bg == [{"ArchiveCategory": ["Common"], "BodyLayoutFile": "MenuBG_00", "IsPauseLower": True,
                   "LifetimeGroup": "Resident", "Name": "MenuBG_00", "SortKey": 4000}], bg
    assert sum(r["BodyLayoutFile"] == BACKDROP_LAYOUT for r in rows) == 3
    page = [r for r in rows if r["Name"] == build.PAGE_SCREEN]
    assert len(page) == 1 and (page[0]["DrawUnitId"], page[0]["SortKey"]) == (1, 5000)
    for name, extra in BACKDROP_ROWS.items():
        assert not any(r["Name"] == name for r in rows)
        assert not any(r.get("DrawUnitId", 0) == extra.get("DrawUnitId", 0) and r["SortKey"] == extra["SortKey"] for r in rows)
        rows.append(dict({"ArchiveCategory": ["Common"], "BodyLayoutFile": BACKDROP_LAYOUT, "IsHeapReverse": True,
                          "Name": name}, **extra))
    rows.sort(key=lambda r: r["Name"].encode())
    new = build.byml.dump(rows, build.byml.version_of(data))
    assert build.byml.load(new) == rows
    build.write(mod, build.UISCREEN, nxpack.zs_compress_like(new, raw), len(new), sizes)


_normal_page_layout = build.page_layout


def page_layout():
    """The normal build's page layout plus PAGE_HIDE_ANIM (frame 0 shown, frame 1 hidden)."""
    lay = _normal_page_layout()
    ed = build.uiedit.Editor(lay)
    assert PAGE_HIDE_PANE in ed.names() and ed.names().index(PAGE_HIDE_PANE) == 1
    ed.add_anim(PAGE_HIDE_ANIM, frames=1, template="IsOpen",
                curves=[(ed.key(PAGE_HIDE_PANE, build.uiedit.VISIBLE), build.uiedit.state_curve([1.0, 0.0]))])
    ed.validate()
    check = build.layout.Layout(build.layout.build(lay))
    for frame, want in ((0, 1.0), (1, 0.0)):
        got = build.uiedit.anim_values(check, PAGE_HIDE_ANIM, frame)
        assert got == {(1, build.uiedit.VISIBLE, None): [want]}, (frame, got)
    # the animations the page plays must be what they were
    stock = _normal_page_layout()
    for name in ("InFromChallenge", "InChallenge", "DetailIn", "GuideIn", "DemoMessageIn"):
        for frame in (0, 8, 24):
            assert build.uiedit.anim_values(check, name, frame) == build.uiedit.anim_values(stock, name, frame), name
    return lay


_normal_close_page_with_menu = build.close_page_with_menu


def close_page_with_menu(mod, sizes):
    """The normal build's menu close step, which then also closes the backdrop when it is open."""
    _normal_close_page_with_menu(mod, sizes)
    rel = "Sequence/MinusMenuToMain.module.ainb"
    g = build.Graph(os.path.join(build.BUILD, mod, "romfs", rel))
    step = g.node(3, "", "Element_Simultaneous")
    assert any(g.nodes[c["Node Index"]].get("Name") == "SeqSelectorScreenOpenCloseState" for c in step["Plugs"]["Child"])
    step["Plugs"]["Child"] += [{"Node Index": i, "Name": ""} for i in _all_off(g)]
    out = g.build()
    build.write(mod, rel, out, len(out), sizes)


def check_backdrop():
    """Read the built files back: the screen row, the page layout's animation in the shipped
    archive, the three buttons' steps and the two exits."""
    import ainbx
    out = {}
    for mod in ("Characters page", "Character layout (test)"):
        rows = build.byml.load(nxfmt.unzs(open(os.path.join(build.BUILD, mod, "romfs", build.UISCREEN), "rb").read(),
                                         build.UISCREEN))
        for name, extra in BACKDROP_ROWS.items():
            row = [r for r in rows if r["Name"] == name]
            assert len(row) == 1 and all(row[0][k] == v for k, v in extra.items()) and \
                row[0].get("DrawUnitId", 0) == extra.get("DrawUnitId", 0), (mod, row)
        assert [r["Name"] for r in rows] == sorted((r["Name"] for r in rows), key=str.encode)
    arc = nxfmt.unzs(open(os.path.join(build.BUILD, "Character layout (test)", "romfs", build.COMMON_LAYOUTS), "rb").read(),
                     build.COMMON_LAYOUTS)
    copies = [d for n, d in ((n, arc[start:end]) for n, start, end in nxfmt.sarc_entries(arc))
              if n == build.PAGE_OWN_LAYOUT + ".buil"]
    assert len(copies) == build.LAYOUT_COPIES and len(set(bytes(c) for c in copies)) == 1
    lay = build.layout.Layout(bytes(copies[0]))
    assert build.uiedit.anim_values(lay, PAGE_HIDE_ANIM, 1) == {(1, build.uiedit.VISIBLE, None): [0.0]}
    stock = build.layout.Layout(nxfmt.sarc_files(arc)[BACKDROP_LAYOUT + ".buil"])
    assert build.uiedit.anim_values(stock, "Color", 0)[(3, build.uiedit.VISIBLE, None)] == [1.0]      # P_Black_00 shown
    assert build.uiedit.anim_values(stock, "Color", 1)[(3, build.uiedit.VISIBLE, None)] == [0.0]
    assert build.uiedit.anim_values(stock, "Color", 1)[(5, build.uiedit.VISIBLE, None)] == [1.0]      # P_White_00 shown
    assert build.uiedit.anim_values(stock, "VeryShortIn", 15)[(2, build.uiedit.ALPHA, None)] == [1.0]  # N_In_00 opaque

    def strings(n):
        return {x["Name"]: x["Default Value"] for kind in ("String", "Float", "Bool", "Int")
                for x in n.get("Parameters", {}).get("Inputs", {}).get(kind, []) if "Default Value" in x}

    def tree(nodes, i, depth=0):
        n = nodes[i]
        par = strings(n)
        text = (n.get("Name") or n["Node Type"]) + ("(%s)" % ", ".join("%s" % par[k] for k in ("ScreenName", "AnimationName", "Frame") if k in par) if par else "")
        kids = [tree(nodes, c["Node Index"], depth + 1) for c in n.get("Plugs", {}).get("Child", [])] if depth < 4 else []
        return text + ("[" + "; ".join(kids) + "]" if kids else "")

    path = os.path.join(build.BUILD, "Characters page", "romfs", "Sequence", "CharacterMenu.module.ainb")
    nodes = ainbx.load(open(path, "rb").read()).as_dict()["Nodes"]
    trig = {v: k for k, v in build.TRIG.items()}
    for n in nodes:
        if n["Node Type"] != "Element_BoolSelector":
            continue
        q = n["Parameters"]["Inputs"]["Bool"][0]
        if nodes[q["Node Index"]].get("Name") != "SeqQueryControllerTrigMaskChecker":
            continue
        button = trig[q["Output Index"]]
        if not isinstance(BUTTONS.get(button), Backdrop):
            continue
        end = nodes[n["Plugs"]["Child"][0]["Node Index"]]
        seq = nodes[end["Plugs"]["Transition"][0]["Node Index"]]
        assert seq["Node Type"] == "Element_Sequential"
        out[button] = [tree(nodes, c["Node Index"]) for c in seq["Plugs"]["Child"]]
    assert set(out) == {b for b, v in BUTTONS.items() if isinstance(v, Backdrop)}, out
    assert all(len(steps) >= 2 for steps in out.values()), "a one-step button sequence never leaves the frame of the press"
    for sc in BACKDROP_ROWS:
        closes = sum(1 for n in nodes if n.get("Name") == "SeqExecuteCloseAndUnloadScreen" and strings(n).get("ScreenName") == sc)
        want = 2 + sum(isinstance(v, PortraitRun) for v in BUTTONS.values())
        assert closes == want, (sc, closes)    # the ZL button, the R / ZR exit and the end of each portrait run
    path = os.path.join(build.BUILD, "Characters page", "romfs", "Sequence", "MinusMenuToMain.module.ainb")
    nodes = ainbx.load(open(path, "rb").read()).as_dict()["Nodes"]
    kids = [tree(nodes, c["Node Index"]) for c in nodes[3]["Plugs"]["Child"]]
    for sc in BACKDROP_ROWS:
        assert sum(sc in k and "SeqExecuteCloseAndUnloadScreen" in k for k in kids) == 1, kids
    return out


def check_buttons():
    """Read the built Characters page graph back: each button of BUTTONS leads to a swap
    whose first step stores / adds the right number; the other steps are the normal build's."""
    import ainbx
    path = os.path.join(BUILD_NPC, "Characters page", "romfs", "Sequence", "CharacterMenu.module.ainb")
    nodes = ainbx.load(open(path, "rb").read()).as_dict()["Nodes"]
    trig = {v: k for k, v in build.TRIG.items()}
    found = {}
    for n in nodes:
        if n["Node Type"] != "Element_BoolSelector":
            continue
        q = n["Parameters"]["Inputs"]["Bool"][0]
        if nodes[q["Node Index"]].get("Name") != "SeqQueryControllerTrigMaskChecker":
            continue
        button = trig[q["Output Index"]]
        assert nodes[q["Node Index"]]["Parameters"]["Outputs"]["Bool"][q["Output Index"]]["Is Output"], button
        end = nodes[n["Plugs"]["Child"][0]["Node Index"]]
        if end.get("Name") != "SeqOneShotEnd" or button not in BUTTONS or isinstance(BUTTONS[button], (Backdrop, PortraitRun)):
            continue
        seq = nodes[end["Plugs"]["Transition"][0]["Node Index"]]
        steps = [nodes[c["Node Index"]] for c in seq["Plugs"]["Child"]]
        first = steps[0]
        ints = {x["Name"]: x["Default Value"] for x in first["Parameters"]["Inputs"]["Int"]}
        names = [x["Default Value"] for x in first["Parameters"]["Inputs"]["String"]]
        assert names == [build.CHARACTER_FLAG], names
        found[button] = (first["Name"], ints["Value"], [s.get("Name") or s["Node Type"] for s in steps[1:]])
    characters_buttons = {b: v for b, v in BUTTONS.items() if not isinstance(v, (Backdrop, PortraitRun))}
    assert set(found) == set(characters_buttons), (sorted(found), sorted(characters_buttons))
    tails = {tuple(v[2]) for v in found.values()}
    assert len(tails) == 1, "the buttons' swaps differ after the first step"
    for button, value in characters_buttons.items():
        want = ("SeqOneShotAddNonStructGameDataInt" if isinstance(value, Step) else "SeqOneShotSetNonStructGameDataInt",
                int(value))
        assert found[button][:2] == want, (button, found[button][:2], want)
    return found


# ---------------------------------------------------------------- build

def write_list(reports, pre_heap):
    """build-npc\\characters.txt: stored number -> character, and what the rig did."""
    by = {}
    for number, stem, age, mode, report in reports:
        by.setdefault(number, {})[age] = (stem, mode, report)
    n_main = len(build.CHARACTERS)
    chars = characters()
    lines = ["Stored number (CharacterMenu_SelectedCharacter) -> character. The list is a ring of %d."
             % (n_main + len(chars)), ""]
    for i, c in enumerate(build.CHARACTERS):
        lines.append("%4d  %s" % (i, c["name"]))
    worst_pre = 0
    for number in sorted(by):
        stem, mode, report = by[number]["Child"]
        if number < len(chars):
            c = chars[number]
            head = "%4d  %-24s C%03d  %s" % (n_main + number, c["label"], number, stem.split(".")[1])
        else:
            head = "%4d  experiment %-13s C%03d  %s" % (EXPERIMENT_BASE + number - len(chars), mode, number,
                                                         stem.split(".")[1])
        detail = []
        for age in AGES:
            r = by[number][age][2]
            detail.append("%s %d->%d bones" % (age.lower(), r["bones"][0], r["bones"][1]))
        r = by[number]["Child"][2]
        notes = []
        if r.get("turned"):
            big = {k: v for k, v in r["turned"].items() if v > 15}
            if big:
                notes.append("turned " + ", ".join("%s %g" % kv for kv in sorted(big.items())))
        if r.get("absolute"):
            notes.append("arms " + ", ".join("%s %g" % kv for kv in sorted(r["absolute"].items())))
        far = {age.lower(): max(by[number][age][2].get("follow", {}).values(), default=0) * 1000 for age in AGES}
        if max(far.values()) > 20:      # the ankle helper turns the shin about an axis this far from its own
            notes.append("ankle helper off axis " + ", ".join("%s %.0f mm" % kv for kv in far.items()))
        legs = {age.lower(): max((v[k] for v in by[number][age][2].get("legs", {}).values() for k in ("hip", "knee")),
                                 default=0) * 1000 for age in AGES}
        if mode == "full" and not r.get("legs"):
            notes.append("legs: first rig (offset bones, the foot IK misses the knee)")
        elif max(legs.values()) > 20:      # the hip or knee pivot is this far from the model's own
            notes.append("leg pivots off " + ", ".join("%s %.0f mm" % kv for kv in legs.items()))
        if mode == "full" and FACE and not r.get("face"):
            notes.append("face: no face bones, static")
        elif r.get("face static"):
            notes.append("face: static " + ", ".join(r["face static"]))
        if r.get("not following"):
            notes.append("ankle helpers left static (another rest pose): " + ", ".join(r["not following"]))
        extra = [x for x in r.get("fixed", []) if x not in npcrig.ABSOLUTE]
        if extra:
            notes.append("kept own frame: " + ", ".join(extra))
        if r.get("hair") != "Mt_Hair":
            notes.append("%s: %s" % (HAIR_MATERIAL, "%s renamed" % r["hair"] if r.get("hair") else "its own"))
        if number < len(chars) and chars[number]["actors"]:
            a = chars[number]["actors"]
            notes.append("actors: " + ", ".join(a[:4]) + (" (+%d)" % (len(a) - 4) if len(a) > 4 else ""))
        lines.append("%s  | %s%s" % (head, ", ".join(detail), "".join("  | " + n for n in notes)))
    for i, stem in enumerate(direct_experiments()):
        lines.append("%4d  experiment stock                the game's own Model/%s.bfres"
                     % (EXPERIMENT_BASE + len(file_experiments()) + i, stem))
    with open(os.path.join(BUILD_NPC, "characters.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return lines


def check_heaps(pre_heap):
    """Every model file we ship fits the pre-actor render heap we give the player actors."""
    worst = (0, "")
    folder = os.path.join(build.BUILD, MOD, "romfs", "Model")
    for name in sorted(os.listdir(folder)):
        if not name.startswith("Player_") or ".anim." in name:      # (test builds add other files)
            continue
        m = bfres.Fres(nxfmt.read(os.path.join(folder, name))).model()
        worst = max(worst, (pre_actor_need(m), name))
    assert worst[0] * 1.25 < min(pre_heap.values()), worst
    return worst


def compare_with_installed():
    """Files of the normal build's mods that differ here from what the normal emulator has
    installed (expected: only what this build changes on purpose)."""
    out = []
    for mod in list(build.MODS) + list(build.EXPERIMENTS):
        ours, theirs = os.path.join(BUILD_NPC, mod), os.path.join(build.EDEN_LOAD, mod)
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


def build_mods():
    """Library entry for another build (the UI sandbox), and what main() runs: every mod of
    build.MODS into build.BUILD, `Character models` with our exe patch (model hook for the
    whole ring + page zoom) in place of the normal one, then the folder MOD with the NPC
    model files and the player actors' heaps. The caller sets build.BUILD / SIZES /
    EXE_PATCHES first, writes the size tables afterwards (build.write_rstb: SIZES has our
    models) and installs MOD next to the folders of build.MODS.
    Returns what check_build() takes: (patcher, zoom factors, reports, heap sizes)."""
    assert MOD not in build.MODS and MOD > max(build.MODS), "our folder must not shadow the normal mods' files"
    n_npc = len(characters())
    patcher = None
    for mod, fn in build.MODS.items():
        print(mod)
        shutil.rmtree(os.path.join(build.BUILD, mod), ignore_errors=True)
        if fn is build.character_models:
            patcher, factors = npc_character_models(mod, n_npc)
        else:
            fn(mod)
    assert patcher is not None
    print(MOD)
    shutil.rmtree(os.path.join(build.BUILD, MOD), ignore_errors=True)
    reports = npc_models(MOD)
    pre_heap = actor_heaps(MOD)
    print("\n".join("  " + n for n in retarget(MOD)))
    for mod in RETIRED:
        shutil.rmtree(os.path.join(build.BUILD, mod), ignore_errors=True)
    return patcher, factors, reports, pre_heap


def check_build(patcher, factors, reports, pre_heap):
    """The offline checks that do not depend on the page's buttons (after build_mods())."""
    n_npc = len(characters())
    n_values, n_paths = check_model_hook(patcher, n_npc)
    print("  model hook: %d stored numbers emulated, %d different model paths, all shipped" % (n_values, n_paths))
    runs, zoomed = check_zoom_hook(patcher, n_npc, factors)
    print("  page view: %d camera updates emulated, %d zoomed; factors %.2f .. %.2f"
          % (runs, zoomed, min(min(r) for r in factors), max(max(r) for r in factors)))
    worst = check_heaps(pre_heap)
    print("  pre-actor render heap: largest need %d (%s) of %d" % (worst[0], worst[1], min(pre_heap.values())))


def main():
    if "--list" in sys.argv:
        for i, name in enumerate(ring()):
            print("%4d  %s" % (i, name))
        return
    install = "--install" in sys.argv
    if install:
        for _ in range(10):     # a just-closed emulator takes a moment to go away
            if not npcenv.running():
                break
            time.sleep(1)
        else:
            print("The NPC emulator copy is running. Close it first: it keeps the mod files open.")
            sys.exit(1)
    build.BUILD = BUILD_NPC         # the normal build's output stays untouched
    build.SIZES = {}
    build.EXE_PATCHES = {}
    build.CHARACTER_BUTTONS = dict(BUTTONS)
    build._swap_step_list = _swap_step_list
    # as the main build (build.main, 2026-11-10): its page zoom in place of ours (same table;
    # it glides during a transition and keeps the shadow range) and the slide as the swap
    global zoom_hook, check_zoom_hook, GEAR_AWAY
    GEAR_AWAY = True        # portrait captures: no sword and shield (this test build only)
    me = sys.modules[__name__]
    zoom_hook = lambda p, n_npc: build.zoom_blend_hook(p, n_npc, me)                            # noqa: E731
    check_zoom_hook = lambda p, n_npc, factors: build.check_zoom_blend(p, n_npc, factors, me)   # noqa: E731
    build.SWAP_SLIDES = True
    build.add_page_screen = add_page_screen                 # + the backdrop's screen row
    build.page_layout = page_layout                         # + the animation that hides the page's 2D layer
    build.close_page_with_menu = close_page_with_menu       # + close the backdrop with the menu
    n_npc = len(characters())
    print("%d NPC characters, %d in the ring with %s" % (n_npc, n_npc + len(build.CHARACTERS),
                                                         " and ".join(c["name"] for c in build.CHARACTERS)))
    patcher, factors, reports, pre_heap = build_mods()
    hide_menu_backdrop(MOD)
    freeze_animations(MOD)
    if GEAR_AWAY:
        park_link_gear(MOD)
    for mod, fn in build.EXPERIMENTS.items():
        print(mod, "(test folder of the normal build)")
        shutil.rmtree(os.path.join(BUILD_NPC, mod), ignore_errors=True)
        fn(mod)
    for mod in build.RSTB_MODS + list(build.EXPERIMENTS):
        print(mod, "(combined size table)")
        build.write_rstb(mod, build.SIZES)

    print("checks")
    check_build(patcher, factors, reports, pre_heap)
    buttons = check_buttons()
    print("  buttons: " + ", ".join("%s %s%d" % (b, "+" if "Add" in buttons[b][0] and buttons[b][1] > 0 else
                                                  "" if "Add" in buttons[b][0] else "= ", buttons[b][1]) for b in buttons))
    for button, steps in sorted(check_backdrop().items()):
        print("  backdrop %-2s: %s" % (button, " -> ".join(steps)))
    runs, n_nodes, size = check_portrait_run()
    for b, (n, steps, frames) in sorted(runs.items()):
        print("  portrait run (%s): %d characters, %d steps, about %d s at 60 a second" % (b, n, steps, frames // 60))
    print("  page graph: %d nodes, %d bytes" % (n_nodes, size))
    lines = write_list(reports, pre_heap)
    print("  list: build-npc\\characters.txt (%d lines)" % len(lines))
    diff = compare_with_installed()
    print("  differs from the normal emulator's installed mods:")
    for mod, rel in diff:
        print("    %s: %s" % (mod, rel))
    if install:
        npcenv.setup()
        npcenv.check()
        for name in os.listdir(npcenv.LOAD):
            shutil.rmtree(os.path.join(npcenv.LOAD, name))
        for mod in list(build.MODS) + [MOD] + list(build.EXPERIMENTS):
            shutil.copytree(os.path.join(BUILD_NPC, mod), os.path.join(npcenv.LOAD, mod))
            print("  installed ->", os.path.join(npcenv.LOAD, mod))


if __name__ == "__main__":
    main()
