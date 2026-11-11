"""Give an NPC model the skeleton the player actor expects, so Link's animations, physics
and equipment run on it while the NPC keeps its own shape (README: NPC characters).

    new_bones, remap, inverse_binds, report = npcrig.player_rig(npc_model, link_model)
    npc_model.set_skeleton(new_bones, remap); npc_model.set_inverse_bind(...)

Why a plain model swap is not enough (all measured on the game's files):
- Link's animations are bound to bones by name and write absolute local rotations AND
  translations: every bone of the same name takes Link's bone offsets (his proportions,
  his face layout), not the NPC's.
- NPC rigs use Link's names and conventions for the body joints, but not everywhere:
  cloth / hair / face bones of the same name have other frames (90-180 degrees apart),
  and a few joints too (child Malon's waist, Dampe's bent neck).
- The player's code and data look bones up by name (Weapon_L, Pod_A, Head_2, Face_Root,
  the cloth and helper bones ...); NPC skeletons lack many of them.

What player_rig() builds, per NPC and per Link age:
1. JOINTS (spine, arms, hands, legs) stay bound to Link's animation. Each gets
   - Link's rest rotation as its own rest rotation (the bone's frame is turned, the
     inverse bind matrix follows, the mesh stays where it is), so "Link at rest" is
     exactly "NPC at rest" and a turn of Link's joint is the same turn of the NPC's.
     Exception ABSOLUTE (upper arms): models are built arms down 45 degrees (Link, some
     NPCs) or arms level (most NPCs); there Link's rotation is used as it is, so the
     arms hang as Link's do.
   - a static parent bone "Fx_<name>" that carries (NPC offset - Link offset): the
     animation writes Link's offset into the joint, the two add up to the NPC's own.
     Bone lengths, shoulder / hip width and height stay the NPC's.
2. Every other NPC bone that shares a name with a Link bone is renamed "Nx_<name>":
   nothing of Link's drives it, it keeps its rest pose (face, hair, cloth: static).
3. Every Link bone the result does not have by name is added without skin weights, under
   Link's parent with Link's rest transform (attach points, face, cloth and helper
   bones): everything the player actor looks for exists.
World rest positions of all NPC bones are unchanged; check_rig() proves it per file.
"""
import bfres
from bfres import Bone, Xf, IDENTITY, euler_matrix, matrix_euler, mat_mul, mat_t, mat_vec, rotation_angle

SIDES = ("L", "R")
_PER_SIDE = ["Clavicle", "Arm_1", "Arm_2", "Wrist", "Leg_1", "Leg_2", "Ankle", "Toe",
             "Finger_A_1", "Finger_A_2", "Finger_A_3"] + \
            ["Wrist_Finger_" + x for x in "BCDE"] + ["Finger_%s_%d" % (x, k) for x in "BCDE" for k in (1, 2, 3)]
JOINTS = ["Skl_Root", "Spine_1", "Spine_2", "Neck", "Head", "Waist"] + ["%s_%s" % (n, s) for s in SIDES for n in _PER_SIDE]
# what a model must have to count as a humanoid we can drive with Link's animations
CORE = ["Root", "Skl_Root", "Spine_1", "Spine_2", "Neck", "Head", "Waist"] + \
       ["%s_%s" % (n, s) for s in SIDES for n in ("Clavicle", "Arm_1", "Arm_2", "Wrist", "Leg_1", "Leg_2", "Ankle")]
ABSOLUTE = {"Arm_1_L", "Arm_1_R"}
OFFSET_PREFIX = "Fx_"
NPC_PREFIX = "Nx_"
OFFSET_FLAGS = 0x03801001   # visible, Euler rotation, segment scale compensate, scale is one
EPS_POS = 1e-6              # metres
EPS_ANGLE = 1e-3            # degrees


def rest_world(bones):
    """Rest world transform of each bone, ignoring scale (scaled bones must be leaves)."""
    scaled = {i for i, b in enumerate(bones) if b.scale != (1.0, 1.0, 1.0)}
    assert not any(b.parent in scaled for b in bones), "a scaled bone has children"
    out = []
    for i, b in enumerate(bones):
        local = Xf(euler_matrix(b.rotate), b.translate)
        out.append(local if b.parent < 0 else out[b.parent] * local)
    return out


def is_humanoid(model):
    names = {b.name for b in model.bones}
    return all(n in names for n in CORE)


def _same(a, b):
    return rotation_angle(a.r, b.r) < EPS_ANGLE and max(abs(x - y) for x, y in zip(a.t, b.t)) < EPS_POS


def player_rig(npc, link, absolute=ABSOLUTE, add_missing=True):
    """npc, link: bfres.Model. Returns (new bones, remap old->new index,
    {smooth matrix index: new inverse bind Xf}, report dict).
    add_missing=False leaves step 3 out (an experiment: fewer bones than Link has)."""
    link_by = {b.name: b for b in link.bones}
    link_parent = {b.name: (link.bones[b.parent].name if b.parent >= 0 else None) for b in link.bones}
    old = npc.bones
    assert old[0].name == "Root" and old[0].parent == -1 and link.bones[0].name == "Root"
    assert npc.skl_flags == link.skl_flags == 0x1200, "rotation mode"
    w_old = rest_world(old)
    rigid = npc.rigid_bones()
    npc_names = {b.name for b in old}
    joints = {n for n in JOINTS if n in npc_names and n in link_by}
    taken = npc_names | set(link_by)

    new, w_new, remap = [], [], [None] * len(old)
    anchor = [None] * len(old)      # old index -> new index of the nearest bound ancestor (itself if bound)
    # report: "turned" / "absolute" = degrees between the NPC's and Link's rest rotation for
    # joints that took Link's frame / kept their own ("fixed": ABSOLUTE ones, and joints a
    # rigid shape hangs on, whose frame cannot change without touching vertices)
    report = {"offsets": 0, "renamed": [], "turned": {}, "absolute": {}, "unbound": [], "fixed": []}

    def add(bone, world):
        new.append(bone)
        w_new.append(world)
        return len(new) - 1

    for i, b in enumerate(old):
        if i == 0:
            remap[0] = anchor[0] = add(b.copy(), w_old[0])
            continue
        p_new = remap[b.parent]
        wp_new, wp_old = w_new[p_new], w_old[b.parent]
        a_new = anchor[b.parent]
        if b.name in joints and new[a_new].name != link_parent[b.name]:
            # the NPC hangs this joint somewhere else than Link does: Link's values would be
            # read in another frame, so leave it alone (it is renamed below)
            report["unbound"].append(b.name)
            joints.discard(b.name)
        if b.name in joints:
            lb = link_by[b.name]
            assert b.scale == lb.scale == (1.0, 1.0, 1.0), ("scaled joint", b.name)
            wa = w_new[a_new]
            pos = w_old[i].t
            turn = b.name not in absolute and i not in rigid
            # offset bone: Link's parent frame, placed so that Link's offset lands on the NPC's joint
            f_world = Xf(wa.r, [p - q for p, q in zip(pos, mat_vec(wa.r, lb.translate))])
            parent, w_parent = p_new, wp_new
            if not _same(f_world, wp_new):
                f = Bone(OFFSET_PREFIX + b.name, parent=p_new, flags=OFFSET_FLAGS)
                assert f.name not in taken
                f.set_local(wp_new.inv() * f_world)
                parent, w_parent = add(f, f_world), f_world
                report["offsets"] += 1
            nb = b.copy(parent=parent)
            if not turn:
                report["fixed"].append(b.name)
            if turn:
                target = mat_mul(wa.r, euler_matrix(lb.rotate))
                angle = rotation_angle(target, w_old[i].r)
                if angle > 0.5:
                    report["turned"][b.name] = round(angle, 1)
                nb.set_local(rotate=lb.rotate[:3], translate=lb.translate)
            else:
                target = w_old[i].r
                local_r = mat_mul(mat_t(w_parent.r), target)
                angle = rotation_angle(local_r, euler_matrix(lb.rotate))
                if angle > 0.5:
                    report["absolute"][b.name] = round(angle, 1)
                nb.set_local(rotate=matrix_euler(local_r), translate=lb.translate)
            remap[i] = anchor[i] = add(nb, Xf(target, pos))
            continue
        nb = b.copy(parent=p_new)
        if b.name in link_by:
            nb.name = NPC_PREFIX + b.name
            assert nb.name not in taken, nb.name
            report["renamed"].append(b.name)
        if not _same(wp_new, wp_old):   # the parent's frame was turned: same place, new numbers
            nb.set_local(wp_new.inv() * w_old[i])
        remap[i] = add(nb, w_old[i])
        anchor[i] = a_new

    index = {b.name: k for k, b in enumerate(new)}
    added = []
    for lb in link.bones if add_missing else ():
        if lb.name in index:
            continue
        parent = index[link_parent[lb.name]]
        index[lb.name] = add(lb.copy(parent=parent, smooth=-1, rigid=-1), None)
        added.append(lb.name)
    report["added"] = added
    report["joints"] = sorted(joints)

    inverse = {}
    for i, b in enumerate(old):
        if b.smooth >= 0 and rotation_angle(w_new[remap[i]].r, w_old[i].r) >= EPS_ANGLE:
            assert b.scale == (1.0, 1.0, 1.0)
            inverse[b.smooth] = w_new[remap[i]].inv()
    report["bones"] = (len(old), len(new))
    return new, remap, inverse, report


def _skin_error(xf):
    return max(max(abs(xf.r[i][j] - IDENTITY[i][j]) for i in range(3) for j in range(3)), max(abs(x) for x in xf.t))


def check_rig(before, after, link, fixed, tol=2e-4, complete=True):
    """Independent check of a rigged model against the original (both bfres.Model, the
    rigged one parsed again from the written file; `fixed` = report["fixed"] of
    player_rig; complete=False for a rig made with add_missing=False). Returns the worst
    skinning error."""
    old, new = before.bones, after.bones
    index = {b.name: i for i, b in enumerate(new)}
    link_by = {b.name: b for b in link.bones}
    fixed = set(fixed)
    assert len(index) == len(new)
    # the same bones with the same matrices, under their own or their "Nx_" name
    back = {}
    for i, b in enumerate(old):
        n = NPC_PREFIX + b.name if NPC_PREFIX + b.name in index else b.name
        k = back[i] = index[n]
        assert (new[k].smooth, new[k].rigid, new[k].scale) == (b.smooth, b.rigid, b.scale), ("bone lost", b.name)
    assert len(set(back.values())) == len(old)
    assert after.m2b == [back[i] for i in before.m2b]
    for so, sn in zip(before.shapes, after.shapes):
        assert sn.bone == back[so.bone] and sn.skin_bones == [back[i] for i in so.skin_bones], so.name
    kept = set(back.values())
    for lb in link.bones:   # everything the player looks for exists; added bones sit where Link has them
        if not complete and lb.name not in index:
            continue
        assert lb.name in index, lb.name
        k = index[lb.name]
        if k not in kept:
            nb = new[k]
            assert (nb.smooth, nb.rigid) == (-1, -1) and new[nb.parent].name == link.bones[lb.parent].name, lb.name
            assert (nb.scale, nb.rotate, nb.translate) == (lb.scale, lb.rotate, lb.translate), lb.name
    w_old, w_new = rest_world(old), rest_world(new)
    worst = 0.0
    # 1. rest pose: every NPC bone where it was; skinning matrices are the identity; only
    #    joints that took Link's frame are turned, and never one that a rigid shape hangs on
    rigid = before.rigid_bones()
    for i, b in enumerate(old):
        k = back[i]
        assert max(abs(x - y) for x, y in zip(w_old[i].t, w_new[k].t)) < tol, ("moved", b.name)
        turned = rotation_angle(w_old[i].r, w_new[k].r) > 1e-2
        assert not turned or (new[k].name in JOINTS and new[k].name not in fixed and i not in rigid), ("turned", b.name)
        if b.smooth >= 0 and b.scale == (1.0, 1.0, 1.0):
            e = _skin_error(w_new[k] * after.inverse_bind(b.smooth))
            assert e < tol, ("rest skinning", b.name, e)
            worst = max(worst, e)
        elif b.smooth >= 0:     # scaled bone (never a joint): frame and matrix untouched
            assert not turned and after.inverse_bind_raw(b.smooth) == before.inverse_bind_raw(b.smooth), b.name
    # 2. what Link's animation does at Link's own rest pose: it writes Link's rest rotation
    #    and translation into every bone of his names. The NPC must then be in ITS rest
    #    pose; only the `fixed` joints may turn (arms: 45 degrees), each about its own joint
    #    position, taking everything below along as one rigid piece.
    pose = []
    for i, b in enumerate(new):
        src = link_by.get(b.name, b)
        local = Xf(euler_matrix(src.rotate), src.translate)
        pose.append(local if b.parent < 0 else pose[b.parent] * local)
    move = [pose[k] * w_new[k].inv() for k in range(len(new))]     # rest -> pose, in world space

    def fixed_above(k):
        while k >= 0 and new[k].name not in fixed:
            k = new[k].parent
        return k

    for i, b in enumerate(old):
        k = back[i]
        j = fixed_above(k)
        e = _skin_error(move[k] if j < 0 else move[k] * move[j].inv())
        assert e < tol, ("Link's rest pose deforms", b.name, e)
        worst = max(worst, e)
    for k, b in enumerate(new):
        if b.name in fixed:
            j = fixed_above(b.parent)
            pivot = w_new[k].t
            want = pivot if j < 0 else move[j].point(pivot)
            assert max(abs(x - y) for x, y in zip(move[k].point(pivot), want)) < tol, ("joint comes off", b.name)
    return worst
