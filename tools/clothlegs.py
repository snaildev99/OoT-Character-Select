"""Skirts and dresses on the player: the dress bones hang on the legs, and the cloth cannot stay on the wrong side
of a leg. (Model fixes session, 2026-11-13; README, NPC section, "Dresses and the legs", has how it was found.)

A dress's simulated tube is pulled towards "places" that come from the dress bones' pose BEFORE the cloth. On the
NPC the game's helper bone parameters swing the dress's top bones with the thighs; on the player they do not
reach the dress, so the places stayed a narrow tube under the waist, the legs left it, and the cloth settled on
the wrong side of a leg, where it stays (the leash that pulls a point to its place is soft, the collision has the
last word in a step and pushes a point out on the side it is on).

Two things, both in place (no file changes size):

1. model(): in the character's player model, dress bones are hung on the leg joints. A column is an unbranched
   chain of dress bones; by a bone's rest place: at hip height and above it stays as it is; below that, down to
   a quarter of a thigh under the knee, it hangs on the thigh of its side; below that on the shin of its side.
   Columns in the middle and columns behind the legs stay as they are. Every bone keeps its place and frame at
   rest: only bone records change (parent, rest offset).
2. cloth(): in the character's cloth file, the points whose places follow a leg get a firm leash (stiffness 1)
   that is no longer than the place is from that leg's axis, less a margin (and never less than half that way):
   after the leash's turn in a step such a point is on its place's side of the leg, and the collision pushes
   it out there.

Passed in game for young Zelda as child Link (snaildev, 2026-11-13: the charged stance covered, running knees gone,
the Characters page covered, the dress looks right). mods\\npc_characters.py (cloth_all) uses both for every
character with a skirt or dress mesh.
"""
import hashlib
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(os.path.dirname(HERE), "mods") not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mods"))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bfres  # noqa: E402
import npcrig  # noqa: E402
import nxfmt  # noqa: E402
import nxpack  # noqa: E402
from bfres import Xf, euler_matrix, mat_mul, matrix_euler, rotation_angle  # noqa: E402

MESHES = r"Skirt|Dress"   # the simulated meshes this is for, by name
CENTRE = 0.015          # a column this close to the body's middle is nobody's side
BACK = 110.0            # degrees from straight ahead: a column further round than this is behind the legs
THIGH_FROM = -0.1       # of a thigh's length below the hip joint: bones above this stay as they are
SHIN_FROM = 0.25        # of a thigh's length below the knee joint: bones below this hang on the shin
FOLLOWS = 0.5           # a point whose place follows the legs by at least this share gets the firm leash
MARGIN = 0.04           # metres: the firm leash ends this far short of the leg's axis
FIRM = 1.0              # the firm leash's stiffness
SET_STIFFNESS = 0x6C    # in an hclLocalRangeConstraintSet: the stiffness of entries that have none of their own
SIDES = {"L": 1.0, "R": -1.0}
VERSION = b"clothlegs 1"


def _npc():
    import npc_characters
    return npc_characters


# ---------------------------------------------------------------- what a character has

def meshes(stem):
    """The character's skirt and dress meshes (names in its own cloth file)."""
    npc = _npc()
    return [m for m in npc.ClothTag(npc.cloth_file(stem)[0]).meshes() if re.search(MESHES, m)]


def mesh_bones(t, data, mesh):
    """The bones of a simulated mesh's own skeleton that are not the model's joints (None: no such skeleton)."""
    npc = _npc()
    for i in range(len(t.items)):
        if t.type(i) != "hkSkeleton":
            continue
        if [t.text(x) for x in t.refs(i, False) if t.type(x) == "char"][:1] != [npc.CLOTH_SKELETON + mesh]:
            continue
        hb = next(x for x in t.refs(i, False) if t.type(x) == "hkBone")
        a, _size = t.span(hb)
        names = {t.text(struct.unpack_from("<Q", data, a + 16 * k)[0]) for k in range(t.items[hb][2])}
        return names - set(npcrig.JOINTS) - {"Root", "Skl_Root"}
    return None


def dress_bones(stem):
    """Every bone of the character's skirt and dress meshes."""
    npc = _npc()
    data = npc.cloth_file(stem)[0]
    t = npc.ClothTag(data)
    out = set()
    for m in meshes(stem):
        out |= mesh_bones(t, data, m) or set()
    return out


# ---------------------------------------------------------------- 1. the model

def plan(model, names):
    """From the NPC's own model and its dress bones `names`: (moves, bodies).
    moves: {dress bone: joint} for the bones that get a new parent (the first bone of a stretch; the bones below
    it in its column go along). bodies: {dress bone: joint} for every dress bone that then moves with a leg."""
    bones = model.bones
    rest = npcrig.rest_world(bones)
    index = {b.name: i for i, b in enumerate(bones)}
    if not all(("Leg_1_" + s) in index and ("Leg_2_" + s) in index and ("Ankle_" + s) in index for s in SIDES):
        return {}, {}
    hip = {s: rest[index["Leg_1_" + s]].t for s in SIDES}
    knee = {s: rest[index["Leg_2_" + s]].t for s in SIDES}
    assert all(hip[s][0] * k > 0 for s, k in SIDES.items()), "which side is which"
    for s in SIDES:         # the model faces +z: its toes are in front of its ankles
        if "Toe_" + s in index:
            assert rest[index["Toe_" + s]].t[2] > rest[index["Ankle_" + s]].t[2], "the model does not face +z"
    hip_y, knee_y = (hip["L"][1] + hip["R"][1]) / 2, (knee["L"][1] + knee["R"][1]) / 2
    thigh = hip_y - knee_y
    if thigh < 0.05 or abs(hip["L"][1] - hip["R"][1]) > 0.02 or abs(knee["L"][1] - knee["R"][1]) > 0.03:
        return {}, {}       # not standing (a model built sitting)
    axis_z = (hip["L"][2] + hip["R"][2]) / 2
    dress = [i for i, b in enumerate(bones) if b.name in names]
    inside = set(dress)
    kids = {i: 0 for i in dress}
    for i in dress:
        if bones[i].parent in inside:
            kids[bones[i].parent] += 1
    # columns: unbranched chains. A bone with several dress bones under it belongs to none (it stays as it is).
    column = {}
    for i in dress:
        if kids[i] > 1:
            continue
        p = bones[i].parent
        column[i] = column[p] if p in column else i
    want = {}
    for top in sorted(set(column.values())):
        members = [i for i, c in column.items() if c == top]
        x = sum(rest[i].t[0] for i in members) / len(members)
        z = sum(rest[i].t[2] for i in members) / len(members) - axis_z
        if abs(x) < CENTRE or abs(math.degrees(math.atan2(x, z))) > BACK:
            continue
        side = "L" if x > 0 else "R"
        for i in members:
            y = rest[i].t[1]
            if y > hip_y - THIGH_FROM * thigh:
                continue
            want[i] = ("Leg_1_" if y > knee_y - SHIN_FROM * thigh else "Leg_2_") + side
    # what each dress bone moves with now (through its parents), and after the moves
    legs = {index[n + s]: n + s for n in ("Leg_1_", "Leg_2_") for s in SIDES}

    def through(i):         # the leg joint a bone hangs under through bones that are not of the dress, or None
        while i >= 0:
            if i in legs:
                return legs[i]
            i = bones[i].parent
        return None
    cur, moves = {}, {}
    for i in dress:
        p = bones[i].parent
        up = cur[p] if p in cur else through(p)
        if i in want and want[i] != up:
            moves[bones[i].name] = want[i]
            cur[i] = want[i]
        else:
            cur[i] = up
    return moves, {bones[i].name: j for i, j in cur.items() if j}


def hang(data, moves):
    """The model file `data` (decompressed, as the build makes it) with the bones `moves` ({name in this file:
    joint}) hung on their joints, in place: -> bytes of the same length.
    Where a joint comes after a bone that is to hang on it (parents must come first in the file), the bones are
    put in another order: such bones and what is under them go to the end. Then the bone records, the bone
    dictionary, the table from matrices to bones and the shapes' bone numbers are all written again, in place."""
    f = bfres.Fres(data)
    m = f.model()
    bones = m.bones
    n = len(bones)
    index = {b.name: i for i, b in enumerate(bones)}
    world = npcrig.rest_world(bones)
    parent = [b.parent for b in bones]
    local = {}
    for name, joint in moves.items():
        k, j = index[name], index[joint]
        assert bones[k].scale == (1.0, 1.0, 1.0) and bones[k].parent != j, name
        up = j
        while up >= 0:
            assert up != k, ("the joint hangs under the bone", name)
            up = bones[up].parent
        parent[k] = j
        local[k] = world[j].inv() * world[k]
    order, placed, pending = [], set(), list(range(n))
    while pending:
        later = []
        for i in pending:
            if parent[i] < 0 or parent[i] in placed:
                order.append(i)
                placed.add(i)
            else:
                later.append(i)
        assert len(later) < len(pending), "bones that hang on each other"
        pending = later
    new_of = {old: new for new, old in enumerate(order)}
    out = bytearray(data)
    for new, old in enumerate(order):
        b = bones[old].copy(parent=new_of[parent[old]] if parent[old] >= 0 else -1)
        if old in local:
            b.set_local(local[old])
        out[m.bones_off + new * bfres.BONE_SIZE:m.bones_off + (new + 1) * bfres.BONE_SIZE] = b.pack(new, m.name_ptr[b.name])
    if order != list(range(n)):
        names = [bones[old].name for old in order]
        root_key = f.u64(m.dict_off + 8 + 8)
        nodes = bfres.resdic_build([x.encode() for x in names])
        blob = struct.pack("<Ii", 0, n)
        for (ref, left, right), key in zip(nodes, [root_key] + [m.name_ptr[x] for x in names]):
            blob += struct.pack("<iHHQ", ref, left, right, key)
        assert bytes(out[m.dict_off:m.dict_off + 8]) == blob[:8], "the bone dictionary's head"
        out[m.dict_off:m.dict_off + len(blob)] = blob
        struct.pack_into("<%dH" % len(m.m2b), out, m.m2b_off, *(new_of[i] for i in m.m2b))
        for sh in m.shapes:
            struct.pack_into("<H", out, sh.off + 0x54, new_of[sh.bone])
            if sh.skin_bones:
                struct.pack_into("<%dH" % len(sh.skin_bones), out, sh.skin_ptr, *(new_of[i] for i in sh.skin_bones))
    return bytes(out)


def _under(bones):
    """[the set of a bone and everything below it]"""
    out = [{i} for i in range(len(bones))]
    for i in range(len(bones) - 1, 0, -1):
        out[bones[i].parent] |= out[i]
    return out


def check(before, after, moves):
    """`after` against `before` (both decompressed model files, each parsed again): the same bones under the same
    names with the same matrices and the same numbers but for the moved bones' parent and offset; every bone
    where and how it was at rest; the shapes on the same bones; nothing else in the file changed; the moved bones
    on their joints; and a turn written into a thigh or a shin takes exactly what hung under it before and the
    dress bones planned for it (with what hangs under them) along as one piece. -> the number of bones moved."""
    assert len(after) == len(before)
    mb, ma = bfres.Fres(before).model(), bfres.Fres(after).model()
    old, new = mb.bones, ma.bones
    index = {b.name: i for i, b in enumerate(new)}
    assert len(index) == len(new) == len(old) and all(b.name in index for b in old)
    back = [index[b.name] for b in old]         # old number -> new number
    same_order = back == list(range(len(old)))
    assert all(b.parent < i for i, b in enumerate(new)), "a bone comes before its parent"
    w_old, w_new = npcrig.rest_world(old), npcrig.rest_world(new)
    moved = set()
    for i, b in enumerate(old):
        nb = new[back[i]]
        assert (nb.smooth, nb.rigid, nb.scale, nb.billboard) == (b.smooth, b.rigid, b.scale, b.billboard), b.name
        assert math.dist(w_old[i].t, w_new[back[i]].t) < 2e-5 and rotation_angle(w_old[i].r, w_new[back[i]].r) < 2e-2, ("moved at rest", b.name)
        if b.name in moves:
            assert nb.parent == index[moves[b.name]] and new[nb.parent].name != old[b.parent].name, b.name
            moved.add(i)
        else:
            assert (nb.flags, nb.rotate, nb.translate) == (b.flags, b.rotate, b.translate), ("record changed", b.name)
            assert nb.parent == (back[b.parent] if b.parent >= 0 else -1), ("parent changed", b.name)
    assert len(moved) == len(moves)
    assert ma.m2b == [back[i] for i in mb.m2b]
    for so, sn in zip(mb.shapes, ma.shapes):
        assert sn.name == so.name and sn.bone == back[so.bone] and sn.skin_bones == [back[i] for i in so.skin_bones], so.name
    # the bytes: only the bone array, and with another order the dictionary, the matrix table and the shapes' numbers
    may = [(mb.bones_off, mb.bones_off + len(old) * bfres.BONE_SIZE)]
    if not same_order:
        may.append((mb.dict_off, mb.dict_off + 8 + 16 * (len(old) + 1)))
        may.append((mb.m2b_off, mb.m2b_off + 2 * len(mb.m2b)))
        for sh in mb.shapes:
            may.append((sh.off + 0x54, sh.off + 0x56))
            if sh.skin_bones:
                may.append((sh.skin_ptr, sh.skin_ptr + 2 * len(sh.skin_bones)))
    else:
        may = [(mb.bones_off + i * bfres.BONE_SIZE, mb.bones_off + (i + 1) * bfres.BONE_SIZE) for i in sorted(moved)]
    may.sort()
    at = 0
    for a, b in may:
        assert before[at:a] == after[at:a], ("bytes changed outside the bones", at, a)
        at = max(at, b)
    assert before[at:] == after[at:], "bytes changed after the bones"
    assert ma.material_names == mb.material_names and ma.name == mb.name
    under_old, under_new = _under(old), _under(new)
    for joint in sorted(set(moves.values())):
        k = index[joint]
        turn = Xf(euler_matrix((0.3, -0.5, 0.9)), (0.0, 0.0, 0.0))
        r = matrix_euler(mat_mul(euler_matrix(new[k].rotate), turn.r))
        pose = npcrig.rest_world(new, {k: ((r[0], r[1], r[2], 1.0), new[k].translate)})
        frame = Xf(w_new[k].r, w_new[k].t)
        want = frame * turn * frame.inv()
        # what must go along, in new numbers: what hung under the joint before, less the bones that now hang on a
        # joint elsewhere, plus the bones that now hang on this joint or on one under it
        k_old = back.index(k)
        along = {back[i] for i in under_old[k_old]}
        for name, target in moves.items():
            sub = {back[i] for i in under_old[back.index(index[name])]}
            if back.index(index[target]) in under_old[k_old]:
                along |= sub
            else:
                along -= sub
        assert along == under_new[k], ("what goes along with", joint)
        for i, b in enumerate(new):
            move = pose[i] * w_new[i].inv()
            e = npcrig._skin_error(want.inv() * move if i in along else move)
            assert e < 2e-4, ("a turn of", joint, "moves" if i not in along else "leaves", b.name, e)
    return len(moved)


def model(stem, age, zs, report):
    """The character's player model `zs` (as npc_characters.rigged made it, with its `report`) with the dress
    bones hung on the legs: -> (compressed bytes, number of bones hung; 0: nothing to do, `zs` itself).
    The result is checked (check) and kept in the model cache."""
    npc = _npc()
    if stem in npc.LEGS_AS_THEY_ARE:
        return zs, 0
    names = dress_bones(stem)
    if not names:
        return zs, 0
    raw, stock = npc.npc_data(stem)
    moves, _bodies = plan(bfres.Fres(stock).model(), names)
    alias = dict(report["cloth"])
    moved = {alias.get(n, n): j for n, j in moves.items()}
    if not moved:
        return zs, 0
    key = hashlib.sha1(b"|".join([VERSION, hashlib.sha1(zs).digest(), repr(sorted(moved.items())).encode(),
                                  b"%d" % npc.ZS_LEVEL])).hexdigest()
    path = os.path.join(npc.CACHE, "legs_" + key + ".zs")
    if os.path.exists(path):
        return open(path, "rb").read(), len(moved)
    data = nxfmt.unzs(zs, stem)
    out = hang(data, moved)
    assert check(data, out, moved) == len(moved)
    new = nxpack.zs_compress_like(out, raw, npc.ZS_LEVEL)
    assert nxfmt.unzs(new, stem) == out
    os.makedirs(npc.CACHE, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(new)
    return new, len(moved)


# ---------------------------------------------------------------- 2. the cloth file

def cloth_points(data, mesh):
    """Of a simulated mesh of a cloth file: per particle its rest place, the bones its place is made from
    ([(bone name, weight)], from the mesh's "Skin" operator and the particle's reference vertex; None where that
    is not known), where its leash entry is in the file (None for a pinned one), the entries' kind, and where
    the leash set's own stiffness is. -> (places, weights, entries, kind, set offset), or None for a mesh that
    is built another way (no leash, a skin operator in bone space)."""
    npc = _npc()
    t = npc.ClothTag(data)
    cloth = next(i for i in range(len(t.items)) if t.type(i) == "hclClothData" and
                 next(t.text(x) for x in t.refs(i, False) if t.type(x) == "char") == mesh)
    skel = next((i for i in range(len(t.items)) if t.type(i) == "hkSkeleton" and
                 [t.text(x) for x in t.refs(i, False) if t.type(x) == "char"][:1] == [npc.CLOTH_SKELETON + mesh]), None)
    op = next((r for r in t.refs(cloth) if t.type(r) == "hclObjectSpaceSkinPOperator"), None)
    info = t.meshes()[mesh]
    if skel is None or op is None or "local" not in info:
        return None
    hb = next(x for x in t.refs(skel, False) if t.type(x) == "hkBone")
    a, _ = t.span(hb)
    names = [t.text(struct.unpack_from("<Q", data, a + 16 * k)[0]) for k in range(t.items[hb][2])]
    first = {}
    for x in t.refs(op, False):
        first.setdefault(t.type(x), x)
    a, _ = t.span(first["hkUint16"])
    sub = struct.unpack_from("<%dH" % t.items[first["hkUint16"]][2], data, a)
    a, _ = t.span(first["hkUint8"])
    control = list(data[a:a + t.items[first["hkUint8"]][2]])
    kinds = {0: ("Four", 4, 224), 1: ("Three", 3, 176), 2: ("Two", 2, 128), 3: ("One", 1, 64)}
    seen, weights = {}, {}
    for c in control:
        kind, nw, size = kinds[c]
        a, _ = t.span(first["hclObjectSpaceDeformer::%sBlendEntryBlock" % kind])
        o = a + size * seen.get(c, 0)
        seen[c] = seen.get(c, 0) + 1
        vi = struct.unpack_from("<16H", data, o)
        bi = struct.unpack_from("<%dH" % (16 * nw), data, o + 32)
        wt = data[o + 32 + 32 * nw:o + 32 + 48 * nw] if nw > 1 else bytes([255]) * 16
        for k in range(16):
            weights[vi[k]] = [(names[sub[bi[k * nw + m]]], wt[k * nw + m] / 255.0) for m in range(nw) if wt[k * nw + m]]
    n = t.items[info["particles"]][2]
    sim = next(r for r in t.refs(cloth) if t.type(r) == "hclSimClothData")
    pose = next(q for q in t.refs(sim) if t.type(q) == "hclSimClothPose")
    vec = next(z for z in t.refs(pose, False) if t.items[z][2] == n)
    a, _ = t.span(vec)
    places = [struct.unpack_from("<3f", data, a + 16 * k) for k in range(n)]
    kind = t.type(info["local"]).split("::")[-1]
    step = {"LocalStiffnessConstraint": 20, "LocalConstraint": 16}[kind]
    a, size = t.span(info["local"])
    count = t.items[info["local"]][2]
    assert count * step <= size < count * step + 16, (mesh, kind)
    vertex, entry = {}, [None] * n
    for k in range(count):
        p, v = struct.unpack_from("<HH", data, a + step * k)
        assert p < n, (mesh, p)
        vertex[p], entry[p] = v, a + step * k
    mv = next((r for r in t.refs(cloth) if t.type(r) == "hclMoveParticlesOperator"), None)
    if mv is not None:
        pairs = next(x for x in t.refs(mv, False) if t.type(x) == "hclMoveParticlesOperator::VertexParticlePair")
        a, _ = t.span(pairs)
        for k in range(t.items[pairs][2]):
            v, p = struct.unpack_from("<HH", data, a + 4 * k)
            vertex.setdefault(p, v)
    lset = next(q for q in t.refs(sim) if t.type(q) == "hclLocalRangeConstraintSet" and info["local"] in t.refs(q, False))
    a, size = t.span(lset)
    assert size >= SET_STIFFNESS + 4 and 0 < struct.unpack_from("<f", data, a + SET_STIFFNESS)[0] <= 1, (mesh, "the leash set's stiffness")
    return places, [weights.get(vertex.get(p)) for p in range(n)], entry, kind, a + SET_STIFFNESS


def _to_segment(p, a, b):
    ab = [y - x for x, y in zip(a, b)]
    u = max(0.0, min(1.0, sum((x - y) * z for x, y, z in zip(p, a, ab)) / sum(z * z for z in ab)))
    return math.dist(p, [x + u * z for x, z in zip(a, ab)])


def firm_leash(stock, shipped, npc_model, which, bodies):
    """The cloth file as shipped (`shipped`: the stock file `stock` with names changed, the same layout) with a
    firm leash for the points of the meshes `which` whose places follow a leg (`bodies`: {the NPC's dress bone:
    joint}): stiffness FIRM, and no longer than the place is from that leg's axis less MARGIN. Entries without
    a stiffness of their own take the set's: that is raised to FIRM for the whole mesh if it is less.
    -> (bytes, [lines], {mesh: {particle: new length}})"""
    assert len(stock) == len(shipped)
    rest = npcrig.rest_world(npc_model.bones)
    at = {b.name: rest[i].t for i, b in enumerate(npc_model.bones)}
    out = bytearray(shipped)
    notes, touched, points = [], [], {}
    for mesh in sorted(which):
        got = cloth_points(stock, mesh)
        if got is None:
            notes.append("%s: built another way, left as it is" % mesh)
            continue
        places, weights, entry, kind, set_at = got
        done = []
        for p, w in enumerate(weights):
            if w is None or entry[p] is None:
                continue
            share = {}
            for name, x in w:
                side = bodies[name][-1] if name in bodies else None
                share[side] = share.get(side, 0.0) + x
            tot = sum(share.values())
            legs = {k: v / tot for k, v in share.items() if k}
            if sum(legs.values()) < FOLLOWS:
                continue
            side = max(legs, key=legs.get)
            axis = [at["Leg_1_" + side], at["Leg_2_" + side], at["Ankle_" + side]]
            far = min(_to_segment(places[p], axis[0], axis[1]), _to_segment(places[p], axis[1], axis[2]))
            o = entry[p]
            step = 20 if kind == "LocalStiffnessConstraint" else 16
            assert stock[o:o + step] == shipped[o:o + step], (mesh, p)
            q, _v, length = struct.unpack_from("<HHf", stock, o)
            assert q == p and 0 <= length < 3, (mesh, p, length)
            # (a short skirt's points are a few centimetres from the thigh: there the leash keeps half the way)
            new = min(length, max(far - MARGIN, far / 2))
            struct.pack_into("<f", out, o + 4, new)
            touched.append((o + 4, o + 8))
            stiff = None
            if kind == "LocalStiffnessConstraint":
                stiff = struct.unpack_from("<f", stock, o + 16)[0]
                assert 0 < stiff <= 1, (mesh, p, stiff)
                struct.pack_into("<f", out, o + 16, FIRM)
                touched.append((o + 16, o + 20))
            done.append((p, far, length, new, stiff))
        if not done:
            notes.append("%s: no point follows a leg" % mesh)
            continue
        line = "%s: %d of %d points follow a leg (places %.1f to %.1f cm from its axis): leash %.1f to %.1f cm -> %.1f to %.1f cm (%d shortened)" % (
            mesh, len(done), len(weights), min(d[1] for d in done) * 100, max(d[1] for d in done) * 100,
            min(d[2] for d in done) * 100, max(d[2] for d in done) * 100, min(d[3] for d in done) * 100, max(d[3] for d in done) * 100,
            sum(1 for d in done if d[3] < d[2]))
        if kind == "LocalStiffnessConstraint":
            line += "; their stiffness %.2f to %.2f -> %.1f" % (min(d[4] for d in done), max(d[4] for d in done), FIRM)
        else:
            was = struct.unpack_from("<f", stock, set_at)[0]
            assert stock[set_at:set_at + 4] == shipped[set_at:set_at + 4], mesh
            if was < FIRM:
                struct.pack_into("<f", out, set_at, FIRM)
                touched.append((set_at, set_at + 4))
            line += "; the mesh's own stiffness %.2f%s" % (was, " -> %.1f for all its points" % FIRM if was < FIRM else " (firm as it is)")
        notes.append(line)
        points[mesh] = {d[0]: d[3] for d in done}
    out = bytes(out)
    assert len(out) == len(shipped)
    assert all(any(a <= i < b for a, b in touched) for i in range(len(out)) if out[i] != shipped[i]), "a byte outside the leash changed"
    return out, notes, points


def cloth(stem, shipped):
    """The character's cloth file as the build ships it (names changed) with the firm leash:
    -> (bytes, [lines], number of points)."""
    npc = _npc()
    if stem in npc.LEGS_AS_THEY_ARE:
        return shipped, [], 0
    which = meshes(stem)
    names = dress_bones(stem)
    if not which or not names:
        return shipped, [], 0
    stock = npc.cloth_file(stem)[0]
    npc_model = bfres.Fres(npc.npc_data(stem)[1]).model()
    _moves, bodies = plan(npc_model, names)
    if not bodies:
        return shipped, [], 0
    out, notes, points = firm_leash(stock, shipped, npc_model, which, bodies)
    return out, notes, sum(len(v) for v in points.values())
