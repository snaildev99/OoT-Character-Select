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
4. FOLLOW: helper bones the game drives on Link from his physics parameters
   (Phive/HelperBoneParam in the player's actor pack) and that the NPC has under the same
   name and the same joint. Link's foot yaw is not in the shin: the game reads it off the
   ankle and writes it as a roll into Ph_Ankle_Assist, and every NPC's lower shin is
   skinned to its own bone of that name (which the NPC's own parameters drive the same
   way). Left static (step 2) the whole twist lands on the ankle seam. So Link's bone is
   added first and the NPC's bone ("Nx_") hangs under it instead of under the joint: it
   keeps its rest place and turns with Link's helper. The game places a driven bone
   relative to its base bone, not its skeleton parent (stock files: Ph_Hip has parent
   Leg_1, base Waist), so an offset bone above Link's helper would be ignored; the NPC's
   shin therefore turns about Link's axis, which misses the NPC's own by a few
   millimetres (report["follow"], in metres). A bone further off that axis than
   FOLLOW_MAX_SLOPE of its distance from the joint stays static (report["not following"]:
   the sitting Npc_Hylia_MM_003, and Nabooru, whose legs are 15 degrees off Link's).
5. LEGS THE FOOT IK CAN WALK (legs="retarget" or "scale"; experiments). The foot IK
   (Phive/FootIKParam: a two-bone solver that names only the ankle, `IKBone`) takes the
   ankle's parent and grandparent for knee and hip. With step 1 those are Fx_Ankle and
   Leg_2: the solver bends a stub below the knee, the foot lands on its target and the
   shin stays behind (snaildev, 2026-11-11: fine in the menu, off while walking, very off on
   slopes and ledges). So here Leg_1 -> Leg_2 -> Ankle are direct parents, and the leg's
   size has to come from somewhere else:
   - "scale": Link's own offsets, and one static bone above the hip, "Sx_Leg_1_<side>",
     with a uniform scale (Leg_1's "segment scale compensate" flag cleared, so the scale
     reaches everything below; bones below are written in the scaled space). One number
     cannot fit thigh and shin at once: the scale is the least squares fit. REJECTED IN
     GAME (2026-11-11): with two characters built this way the whole world was lit bright
     cyan for the rest of the session, whoever was picked afterwards.
   - "retarget": Leg_2 and Ankle carry Link's offsets times a factor each (thigh, shin),
     which only works when the game does not overwrite them with Link's: the engine's
     retargeting (AnimationParam `IsRetarget`) takes bone lengths from the model, as it
     does for Biggoron (bones 15 times the Goron skeleton's) on the Goron animations.
     Needs the player's AnimationParam changed (npc_characters, the leg retarget test).
   Either way the ankle stays exactly where it is; knee and hip pivot move to the nearest
   place on Link's leg directions (report["legs"]; as child Link millimetres for Saria,
   1-2 cm for adults). The mesh does not move (inverse bind matrices follow).
6. FACE (face=True; an experiment until seen in game). Link's face is animated by clips
   too (Face_Blink, Face_Talk, Face_Damage ..., and half of the body clips): rotation and
   translation of the bones under Face_Root, by name. The NPCs have the same bones (lids,
   brows, lips, cheeks, eyeballs; adult Link's conventions, child Link's lids are 70-80
   degrees off) within a centimetre of Link's, so here they are bound exactly as the
   joints of step 1: Link's rest rotation as the frame, an offset bone for the place.
   Link's turn or shift of a face bone, seen from the head, is then the NPC's. Where Link
   has a bone between that the NPC lacks (Head_2 above Face_Root, Mouth_Root above the
   lips), Link's bone is added first and the NPC's bones hang under it. A bone a rigid
   shape hangs on (the eyeballs) keeps its frame; it is bound only when that frame is
   Link's within FACE_RIGID_MAX degrees, else it stays static.
7. SKIRT (skirt=True; an experiment until seen in game). Link's tunic skirt is a cloth
   simulation (Phive/Cloth/Player_<age>.phcl, SimulationMesh_Skirt) that moves twelve
   columns of four bones, Ph_Skirt_1..4_<column>, by name; it hangs by gravity and is
   pushed out by capsules on his legs. A dress has the same twelve columns under the same
   names (child Zelda: seven bones each, down to the floor), moved by the NPC's own cloth
   file, which the player does not load: as a player the dress was a stiff shell on the
   waist. Here each dress bone of a column hangs under Link's bone of the same place in
   the column (skirt_map), Link's bones added first as in step 4; the dress bones past
   Link's last stay a chain under the dress bone above them. So the upper dress moves as
   Link's tunic skirt does and the lower dress goes on from its hem.
World rest positions of all NPC bones are unchanged (but for those two pivots);
check_rig() proves it per file.
"""
import math

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
FOLLOW = ("Ph_Ankle_Assist_L", "Ph_Ankle_Assist_R")
FOLLOW_AXIS = 0             # the helpers' aim axis: x (the roll turns about it)
FOLLOW_MAX_SLOPE = 0.12     # the NPC's bone may be this far off Link's roll axis, as a fraction of its
                            # distance from the joint; beyond that it is left static
OFFSET_PREFIX = "Fx_"
NPC_PREFIX = "Nx_"
OFFSET_FLAGS = 0x03801001   # visible, Euler rotation, segment scale compensate, scale is one
SCALE_PREFIX = "Sx_"
SCALE_FLAGS = 0x01801001    # as OFFSET_FLAGS, scale uniform but not one
FLAG_SSC = 1 << 23          # segment scale compensate: the parent's own scale does not reach this bone
LEG_CHAIN = ("Leg_1", "Leg_2", "Ankle")
FACE_ROOT = "Face_Root"     # step 6: this bone and every Link bone below it,
FACE_EXTRA = ("Ear_L", "Ear_R")     # and these
FACE_RIGID_MAX = 3.0        # degrees a rigid face bone's frame may be off Link's
SKIRT_TOP = "Ph_Skirt_1_"   # step 7: a skirt column starts at a bone of this name (+ the column's name)
EPS_POS = 1e-6              # metres
EPS_ANGLE = 1e-3            # degrees


def _scaled(r, k):
    return [[x * k for x in row] for row in r]


def _scale_of(xf):
    return sum(xf.r[k][0] ** 2 for k in range(3)) ** 0.5


def _inv(xf):
    """Inverse of a transform whose matrix is a rotation times a uniform scale."""
    k = _scale_of(xf) ** 2
    rt = _scaled(mat_t(xf.r), 1.0 / k)
    return Xf(rt, [-x for x in mat_vec(rt, xf.t)])


def _unit(xf):
    return _scaled(xf.r, 1.0 / _scale_of(xf))


def rest_world(bones, pose=None):
    """Rest world transform of each bone without its own scale: a rotation, times the
    uniform scale it inherits (a scaled bone with children must be scaled uniformly; the
    game's own scaled bones are leaves). A child takes its parent's own scale unless its
    "segment scale compensate" flag is set. pose: {bone index: (rotate, translate)} instead
    of the rest values."""
    out, full = [], []
    for i, b in enumerate(bones):
        rotate, translate = (pose or {}).get(i, (b.rotate, b.translate))
        local = Xf(euler_matrix(rotate), translate)
        if b.parent >= 0:
            up = out[b.parent] if b.flags & FLAG_SSC else full[b.parent]
            local = up * local
        out.append(local)
        if b.scale == (1.0, 1.0, 1.0):
            full.append(local)
        else:
            full.append(None)
            if any(c.parent == i for c in bones):
                assert b.scale[0] == b.scale[1] == b.scale[2], ("a bone with children is scaled unevenly", b.name)
                full[i] = Xf(_scaled(local.r, b.scale[0]), local.t)
    return out


def is_humanoid(model):
    names = {b.name for b in model.bones}
    return all(n in names for n in CORE)


def _same(a, b):
    return rotation_angle(a.r, b.r) < EPS_ANGLE and max(abs(x - y) for x, y in zip(a.t, b.t)) < EPS_POS


def _off_axis(helper, place):
    """Distance of a point from the helper bone's roll axis (rest world transforms)."""
    local = mat_vec(mat_t(helper.r), [p - q for p, q in zip(place, helper.t)])
    return sum(x * x for k, x in enumerate(local) if k != FOLLOW_AXIS) ** 0.5


def _leg_plan(old, w_old, link_by, waist_r, side, rigid, absolute, mode):
    """Step 5 for one leg: (factors, [hip, knee, ankle] pivots, hip and knee error), or None
    when this model's leg is not the plain chain. factors: "scale" = the one scale;
    "retarget" = (thigh factor, shin factor)."""
    names = ["%s_%s" % (n, side) for n in LEG_CHAIN]
    index = {b.name: i for i, b in enumerate(old)}
    if any(n not in index or n not in link_by or n in absolute for n in names):
        return None
    # a joint whose own frame positions vertices can neither move nor be scaled; the ankle
    # stays in place in "retarget" (four Hylian women have a rigid shape on it)
    if any(index[n] in rigid for n in (names if mode == "scale" else names[:2])):
        return None
    i1, i2, i3 = (index[n] for n in names)
    if old[i2].parent != i1 or old[i3].parent != i2:
        return None
    h, k, a = w_old[i1].t, w_old[i2].t, w_old[i3].t
    r1 = mat_mul(waist_r, euler_matrix(link_by[names[0]].rotate))
    r2 = mat_mul(r1, euler_matrix(link_by[names[1]].rotate))
    thigh, shin = mat_vec(r1, link_by[names[1]].translate), mat_vec(r2, link_by[names[2]].translate)
    leg = [x + y for x, y in zip(thigh, shin)]
    n2, n12 = [x - y for x, y in zip(a, k)], [x - y for x, y in zip(a, h)]
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))     # noqa: E731
    if mode == "retarget":
        f_shin = dot(n2, shin) / dot(shin, shin)
        knee = [x - f_shin * y for x, y in zip(a, shin)]
        f_thigh = dot([x - y for x, y in zip(knee, h)], thigh) / dot(thigh, thigh)
        if min(f_shin, f_thigh) <= 0.05:
            return None
        hip = [x - f_thigh * y for x, y in zip(knee, thigh)]
        return (f_thigh, f_shin), [hip, knee, list(a)], (math.dist(hip, h), math.dist(knee, k))
    scale = (dot(n2, shin) + dot(n12, leg)) / (dot(shin, shin) + dot(leg, leg))
    if scale <= 0.05:
        return None     # a leg that points the other way (the sitting Npc_Hylia_MM_003)
    knee = [x - scale * y for x, y in zip(a, shin)]
    hip = [x - scale * y for x, y in zip(a, leg)]
    return scale, [hip, knee, list(a)], (math.dist(hip, h), math.dist(knee, k))


def _column(bones, top):
    """The chain of bone indices from `top` down, as long as each has exactly one child."""
    chain = [top]
    while True:
        kids = [i for i, b in enumerate(bones) if b.parent == chain[-1]]
        if len(kids) != 1:
            return chain
        chain.append(kids[0])


def skirt_map(npc, link):
    """{NPC bone name: Link bone name} for step 7: the columns both have, bone by bone from
    the top, as far as Link's column goes."""
    out = {}
    npc_index = {b.name: i for i, b in enumerate(npc.bones)}
    for i, lb in enumerate(link.bones):
        if lb.name.startswith(SKIRT_TOP) and lb.name in npc_index:
            ours = _column(npc.bones, npc_index[lb.name])
            for j, k in zip(ours, _column(link.bones, i)):
                out[npc.bones[j].name] = link.bones[k].name
    return out


def player_rig(npc, link, absolute=ABSOLUTE, add_missing=True, follow=FOLLOW, legs_mode=None, face=False,
               skirt=False):
    """npc, link: bfres.Model. Returns (new bones, remap old->new index,
    {smooth matrix index: new inverse bind Xf}, report dict).
    add_missing=False leaves steps 3 and 4 out (an experiment: fewer bones than Link has);
    follow=() leaves step 4 out (the rig as it was before the ankle fix);
    legs_mode="retarget" / "scale" is step 5; face=True is step 6; skirt=True is step 7."""
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
    face_joints = set()
    if face:
        assert add_missing
        under = {FACE_ROOT}
        for lb in link.bones:       # parents come first
            if link_parent[lb.name] in under:
                under.add(lb.name)
        face_joints = {n for n in under | set(FACE_EXTRA) if n in npc_names and n in link_by}
        joints |= face_joints
    early = {}                      # Link bones the NPC lacks, added before the face bones under them
    hang = skirt_map(npc, link) if skirt else {}
    assert add_missing or not hang

    new, w_new, remap = [], [], [None] * len(old)
    w_scale = []                    # per new bone: the uniform scale its children live in (1 but under Sx_ bones)
    legs = {}                       # joint name -> (side, place in LEG_CHAIN) for the legs of step 5
    plans = {}                      # side -> _leg_plan
    anchor = [None] * len(old)      # old index -> new index of the nearest bound ancestor (itself if bound)
    # report: "turned" / "absolute" = degrees between the NPC's and Link's rest rotation for
    # joints that took Link's frame / kept their own ("fixed": ABSOLUTE ones, and joints a
    # rigid shape hangs on, whose frame cannot change without touching vertices)
    # "follow": {helper name: metres between the NPC's bone and Link's roll axis}
    report = {"offsets": 0, "renamed": [], "turned": {}, "absolute": {}, "unbound": [], "fixed": [], "follow": {},
              "not following": [], "legs": {}, "face": [], "face static": [], "skirt": {}}

    def add(bone, world, link_units=False):
        """world: rotation and place without scale. Under a scaled bone the translation is
        divided by the scale, unless it is Link's own number (which is meant to be scaled)."""
        k = w_scale[bone.parent] if bone.parent >= 0 else 1.0
        if k != 1.0 and not link_units:
            bone.translate = tuple(x / k for x in bone.translate)
        new.append(bone)
        w_new.append(world)
        w_scale.append(k)
        return len(new) - 1

    def below(parent, bone):
        """World transform of a bone with Link's own values under a new bone."""
        return w_new[parent] * Xf(euler_matrix(bone.rotate), [x * w_scale[parent] for x in bone.translate])

    for i, b in enumerate(old):
        if i == 0:
            remap[0] = anchor[0] = add(b.copy(), w_old[0])
            continue
        p_new = remap[b.parent]
        wp_new, wp_old = w_new[p_new], w_old[b.parent]
        a_new = anchor[b.parent]
        if b.name in face_joints:
            lb = link_by[b.name]
            chain, q = [], link_parent[b.name]      # Link's bones between, which the NPC lacks
            while q is not None and q != new[a_new].name and q not in npc_names:
                chain.append(q)
                q = link_parent[q]
            ok = q == new[a_new].name and b.scale == lb.scale == (1.0, 1.0, 1.0) and w_scale[a_new] == 1.0
            if ok and i in rigid:
                wa = w_new[a_new]
                for n in reversed(chain):
                    wa = wa * Xf(euler_matrix(link_by[n].rotate), link_by[n].translate)
                ok = rotation_angle(mat_mul(wa.r, euler_matrix(lb.rotate)), w_old[i].r) <= FACE_RIGID_MAX
            if ok:
                for n in reversed(chain):
                    if n not in early:
                        early[n] = add(link_by[n].copy(parent=a_new, smooth=-1, rigid=-1), below(a_new, link_by[n]),
                                       link_units=True)
                    assert new[early[n]].parent == a_new, n
                    if p_new == a_new:
                        p_new, wp_new = early[n], w_new[early[n]]
                    a_new = early[n]
                report["face"].append(b.name)
            else:
                report["face static"].append(b.name)
                joints.discard(b.name)
                face_joints.discard(b.name)
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
            side = b.name[-1]
            if legs_mode and b.name == LEG_CHAIN[0] + "_" + side and w_scale[p_new] == 1.0:
                assert legs_mode in ("scale", "retarget"), legs_mode
                plans[side] = _leg_plan(old, w_old, link_by, wa.r, side, rigid, absolute, legs_mode)
                if plans[side]:
                    legs.update({"%s_%s" % (n, side): (side, k) for k, n in enumerate(LEG_CHAIN)})
                    factors = plans[side][0]
                    report["legs"][side] = dict(
                        {"scale": round(factors, 5)} if legs_mode == "scale" else
                        {"thigh": round(factors[0], 5), "shin": round(factors[1], 5)},
                        hip=round(plans[side][2][0], 5), knee=round(plans[side][2][1], 5))
            if b.name in legs and legs_mode == "retarget":
                side, place = legs[b.name]
                pos = plans[side][1][place]     # the hip: the usual offset bone, at the moved pivot
                if place > 0:
                    # knee and ankle: straight under the joint above, Link's offset times the factor
                    reach = [x * plans[side][0][place - 1] for x in lb.translate]
                    assert p_new == a_new and max(abs(x - y - z) for x, y, z in
                                                  zip(pos, wa.t, mat_vec(wa.r, reach))) < 1e-5, b.name
                    nb = b.copy(parent=a_new)
                    if turn:
                        target = mat_mul(wa.r, euler_matrix(lb.rotate))
                        angle = rotation_angle(target, w_old[i].r)
                        if angle > 0.5:
                            report["turned"][b.name] = round(angle, 1)
                        nb.set_local(rotate=lb.rotate[:3], translate=reach)
                    else:       # as below: the joint keeps its own frame
                        report["fixed"].append(b.name)
                        target = w_old[i].r
                        local_r = mat_mul(mat_t(wa.r), target)
                        angle = rotation_angle(local_r, euler_matrix(lb.rotate))
                        if angle > 0.5:
                            report["absolute"][b.name] = round(angle, 1)
                        nb.set_local(rotate=matrix_euler(local_r), translate=reach)
                    remap[i] = anchor[i] = add(nb, Xf(target, pos), link_units=True)
                    continue
            elif b.name in legs:
                side, place = legs[b.name]
                scale, pivots = plans[side][0], plans[side][1]
                pos = pivots[place]
                target = mat_mul(wa.r, euler_matrix(lb.rotate))
                angle = rotation_angle(target, w_old[i].r)
                if angle > 0.5:
                    report["turned"][b.name] = round(angle, 1)
                parent = a_new
                if place == 0:
                    # the scale bone: Link's parent frame, placed so that Link's scaled offset lands on the hip
                    f_world = Xf(wa.r, [p - scale * q for p, q in zip(pos, mat_vec(wa.r, lb.translate))])
                    f = Bone(SCALE_PREFIX + b.name, parent=p_new, flags=SCALE_FLAGS, scale=(scale,) * 3)
                    assert f.name not in taken
                    f.set_local(wp_new.inv() * f_world)
                    parent = add(f, f_world)
                    w_scale[parent] = scale
                else:
                    assert p_new == a_new and max(abs(x - y) for x, y in zip(pos, below(a_new, lb).t)) < 1e-5, b.name
                nb = b.copy(parent=parent)
                nb.set_local(rotate=lb.rotate[:3], translate=lb.translate)
                if place == 0:
                    nb.flags &= ~FLAG_SSC
                remap[i] = anchor[i] = add(nb, Xf(target, pos), link_units=True)
                continue
            # offset bone: Link's parent frame, placed so that Link's offset lands on the NPC's joint
            reach = [x * w_scale[a_new] for x in lb.translate]
            f_world = Xf(wa.r, [p - q for p, q in zip(pos, mat_vec(wa.r, reach))])
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
            remap[i] = anchor[i] = add(nb, Xf(target, pos), link_units=True)
            continue
        if b.name in hang:
            # Link's simulated bone first (and his bones above it that are not there yet), with
            # his values and no weights; the dress bone under it, where it was
            chain, q = [], hang[b.name]
            while q not in early and not any(x.name == q for x in new):
                chain.append(q)
                q = link_parent[q]
                assert q is not None and q not in npc_names or any(x.name == q for x in new), (b.name, q)
            up = early[q] if q in early else next(k for k, x in enumerate(new) if x.name == q)
            for n in reversed(chain):
                assert n not in npc_names or n in hang, n
                up = early[n] = add(link_by[n].copy(parent=up, smooth=-1, rigid=-1), below(up, link_by[n]),
                                    link_units=True)
            assert w_scale[up] == 1.0 and b.scale == (1.0, 1.0, 1.0), b.name
            nb = b.copy(parent=up)
            if b.name in link_by:
                nb.name = NPC_PREFIX + b.name
                assert nb.name not in taken, nb.name
                report["renamed"].append(b.name)
            nb.set_local(w_new[up].inv() * w_old[i])
            report["skirt"][b.name] = (hang[b.name], round(math.dist(w_new[up].t, w_old[i].t), 5))
            remap[i] = add(nb, w_old[i])
            anchor[i] = a_new
            continue
        w_helper = None
        if add_missing and b.name in follow and b.name in link_by and new[a_new].name == link_parent[b.name]:
            w_helper = below(a_new, link_by[b.name])
            if _off_axis(w_helper, w_old[i].t) > FOLLOW_MAX_SLOPE * math.dist(w_old[i].t, w_new[a_new].t):
                # a model built in another pose (Npc_Hylia_MM_003 sits, Nabooru's legs are
                # 15 degrees off): Link's axis is not along this shin, a roll about it would
                # swing the shin sideways by more than the twist it takes off the ankle
                report["not following"].append(b.name)
                w_helper = None
        if w_helper is not None:
            # Link's driven helper first (his values, no weights), the NPC's bone under it
            lb = link_by[b.name]
            k = add(lb.copy(parent=a_new, smooth=-1, rigid=-1), w_helper, link_units=True)
            nb = b.copy(parent=k, name=NPC_PREFIX + b.name)
            assert nb.name not in taken, nb.name
            nb.set_local(w_helper.inv() * w_old[i])
            report["renamed"].append(b.name)
            report["follow"][b.name] = round(_off_axis(w_helper, w_old[i].t), 5)
            remap[i] = add(nb, w_old[i])
            anchor[i] = a_new
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
    added = sorted(early, key=early.get)
    for lb in link.bones if add_missing else ():
        if lb.name in index:
            continue
        parent = index[link_parent[lb.name]]
        index[lb.name] = add(lb.copy(parent=parent, smooth=-1, rigid=-1), None, link_units=True)
        added.append(lb.name)
    report["added"] = added
    report["joints"] = sorted(joints)

    inverse = {}
    for i, b in enumerate(old):
        k = remap[i]
        assert w_scale[k] == 1.0 or (i not in rigid and b.scale == (1.0, 1.0, 1.0)), ("cannot be scaled", b.name)
        if b.smooth >= 0 and (w_scale[k] != 1.0 or rotation_angle(w_new[k].r, w_old[i].r) >= EPS_ANGLE
                              or max(abs(x - y) for x, y in zip(w_new[k].t, w_old[i].t)) >= EPS_POS):
            assert b.scale == (1.0, 1.0, 1.0)
            inverse[b.smooth] = (w_new[k].inv() if w_scale[k] == 1.0
                                 else _inv(Xf(_scaled(w_new[k].r, w_scale[k]), w_new[k].t)))
    report["bones"] = (len(old), len(new))
    return new, remap, inverse, report


def _skin_error(xf):
    return max(max(abs(xf.r[i][j] - IDENTITY[i][j]) for i in range(3) for j in range(3)), max(abs(x) for x in xf.t))


def check_rig(before, after, link, fixed, tol=2e-4, complete=True, follow=(), legs=(), face=(), skirt=()):
    """Independent check of a rigged model against the original (both bfres.Model, the
    rigged one parsed again from the written file; `fixed` = report["fixed"] of
    player_rig; complete=False for a rig made with add_missing=False; `follow` =
    report["follow"], `legs` = report["legs"]). Returns the worst skinning error."""
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
    legs = dict(legs)
    face = set(face)    # report["face"]: the face bones bound in step 6
    moved = {}      # joint name -> metres its pivot may have moved (step 5)
    own = set()     # bones of Link's names whose offset is the model's own (step 5, "retarget")
    for side, leg in legs.items():
        k1, k2, k3 = (index["%s_%s" % (n, side)] for n in LEG_CHAIN)
        top = new[k1].parent
        # what the foot IK walks: ankle -> knee -> hip, each with Link's own offset and no scale of its own
        assert new[k3].parent == k2 and new[k2].parent == k1, side
        if "scale" in leg:
            assert new[top].name == SCALE_PREFIX + new[k1].name, side
            assert new[top].scale == (new[top].scale[0],) * 3 and abs(new[top].scale[0] - leg["scale"]) < 1e-4, side
            assert new[top].flags & FLAG_SSC and not new[k1].flags & FLAG_SSC, side
        for k, factor in ((k1, 1.0), (k2, leg.get("thigh", 1.0)), (k3, leg.get("shin", 1.0))):
            lb = link_by[new[k].name]
            assert new[k].scale == lb.scale and (new[k].rotate == lb.rotate or new[k].name in fixed), new[k].name
            assert max(abs(x - factor * y) for x, y in zip(new[k].translate, lb.translate)) < 1e-4, new[k].name
            if factor != 1.0:
                own.add(k)      # the game must leave this offset alone (retargeting)
        moved[new[k1].name], moved[new[k2].name] = leg["hip"], leg["knee"]
    assert all(b.parent < k for k, b in enumerate(new)), "a bone comes before its parent"
    w_old, w_new = rest_world(old), rest_world(new)
    worst = 0.0
    # 1. rest pose: every NPC bone where it was; skinning matrices are the identity; only
    #    joints that took Link's frame are turned, and never one that a rigid shape hangs on
    rigid = before.rigid_bones()
    for i, b in enumerate(old):
        k = back[i]
        if b.name in moved:
            assert abs(math.dist(w_old[i].t, w_new[k].t) - moved[b.name]) < tol, ("moved", b.name)
        else:
            assert max(abs(x - y) for x, y in zip(w_old[i].t, w_new[k].t)) < tol, ("moved", b.name)
        turned = rotation_angle(w_old[i].r, _unit(w_new[k])) > 1e-2
        assert not turned or ((new[k].name in JOINTS or new[k].name in face) and new[k].name not in fixed
                              and i not in rigid), ("turned", b.name)
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
    pose = rest_world(new, {i: (link_by[b.name].rotate, b.translate if i in own else link_by[b.name].translate)
                            for i, b in enumerate(new) if b.name in link_by})
    move = [pose[k] * _inv(w_new[k]) for k in range(len(new))]     # rest -> pose, in world space

    def fixed_above(k):
        while k >= 0 and new[k].name not in fixed:
            k = new[k].parent
        return k

    for i, b in enumerate(old):
        k = back[i]
        j = fixed_above(k)
        e = _skin_error(move[k] if j < 0 else move[k] * _inv(move[j]))
        assert e < tol, ("Link's rest pose deforms", b.name, e)
        worst = max(worst, e)
    for k, b in enumerate(new):
        if b.name in fixed:
            j = fixed_above(b.parent)
            pivot = w_new[k].t
            want = pivot if j < 0 else move[j].point(pivot)
            assert max(abs(x - y) for x, y in zip(move[k].point(pivot), want)) < tol, ("joint comes off", b.name)
    # 3. followed helpers: the game writes a roll into Link's helper bone. The NPC's bone of
    #    that name must then make the same turn about the same axis (it hangs under Link's,
    #    which is one of the added bones checked above) and take its own children along;
    #    everything else must stay. The axis misses the NPC's bone by what player_rig said.
    old_index = {b.name: i for i, b in enumerate(old)}
    for name, reported in sorted(dict(follow).items()):
        k, i = index[name], old_index[name]
        assert k not in kept and new[back[i]].name == NPC_PREFIX + name and new[back[i]].parent == k, ("not following", name)
        off = _off_axis(Xf(_unit(w_new[k]), w_new[k].t), w_new[back[i]].t)
        assert abs(off - reported) < tol, ("off axis", name, off, reported)
        below, stack = set(), [i]
        while stack:
            j = stack.pop()
            below.add(j)
            stack.extend(c for c, b in enumerate(old) if b.parent == j)
        for angle in (0.5, -1.2):
            roll = [0.0, 0.0, 0.0]
            roll[FOLLOW_AXIS] = angle
            turn = Xf(euler_matrix(roll), (0.0, 0.0, 0.0))
            rolled = matrix_euler(mat_mul(euler_matrix(new[k].rotate), turn.r))
            pose = rest_world(new, {k: ((rolled[0], rolled[1], rolled[2], 1.0), new[k].translate)})
            want = w_new[k] * turn * _inv(w_new[k])     # the turn, in world space
            for j, b in enumerate(old):
                move = pose[back[j]] * _inv(w_new[back[j]])
                e = _skin_error(_inv(want) * move if j in below else move)
                assert e < tol, ("roll of", name, "moves", b.name, e)
                worst = max(worst, e)
    # 4. legs of step 5: a bend written into the knee (what the foot IK does to the ankle's
    #    parent) turns the shin, the foot and everything on them about the knee pivot as one
    #    rigid piece, and nothing above the knee moves
    for side in legs:
        k2 = index["%s_%s" % (LEG_CHAIN[1], side)]
        under = set()
        for j, b in enumerate(new):
            if j == k2 or b.parent in under:
                under.add(j)
        bend = Xf(euler_matrix((0.0, 0.0, -0.9)), (0.0, 0.0, 0.0))
        bent = matrix_euler(mat_mul(euler_matrix(new[k2].rotate), bend.r))
        pose = rest_world(new, {k2: ((bent[0], bent[1], bent[2], 1.0), new[k2].translate)})
        want = Xf(_unit(w_new[k2]), w_new[k2].t) * bend * _inv(Xf(_unit(w_new[k2]), w_new[k2].t))
        for j, b in enumerate(old):
            move = pose[back[j]] * _inv(w_new[back[j]])
            e = _skin_error(_inv(want) * move if back[j] in under else move)
            assert e < tol, ("knee bend", side, "moves", b.name, e)
            worst = max(worst, e)
    # 5. the face of step 6: each bound face bone has Link's offset and (unless a rigid shape
    #    hangs on it) Link's rotation, under bones that have the frame of Link's parent; so
    #    what a clip writes is read as on Link. A turn and shift written into it moves the
    #    NPC's bone and everything below it as one piece about the NPC's own place, and
    #    nothing else moves
    for name in sorted(face):
        k = index[name]
        lb = link_by[name]
        assert k in kept and new[k].translate == lb.translate, name
        up = new[k].parent
        while new[up].name.startswith(OFFSET_PREFIX):
            up = new[up].parent
        assert new[up].name == link.bones[lb.parent].name, (name, new[up].name)
        if name in fixed:
            continue
        assert new[k].rotate == lb.rotate, name
        assert rotation_angle(_unit(w_new[new[k].parent]), _unit(w_new[up])) < 1e-2, ("offset bone turned", name)
        under = set()
        for j, b in enumerate(new):
            if j == k or b.parent in under:
                under.add(j)
        turn = Xf(euler_matrix((0.3, -0.2, 0.4)), (0.002, -0.003, 0.001))
        local = Xf(euler_matrix(new[k].rotate), new[k].translate) * turn
        r = matrix_euler(local.r)
        pose = rest_world(new, {k: ((r[0], r[1], r[2], 1.0), tuple(local.t))})
        frame = Xf(_unit(w_new[k]), w_new[k].t)
        want = frame * turn * _inv(frame)
        for i, b in enumerate(old):
            move = pose[back[i]] * _inv(w_new[back[i]])
            e = _skin_error(_inv(want) * move if back[i] in under else move)
            assert e < tol, ("face turn of", name, "moves", b.name, e)
            worst = max(worst, e)
    # 6. the skirt of step 7: each dress bone hangs under Link's simulated bone (one of the
    #    added bones checked above), so a turn and shift the simulation gives Link's bone
    #    moves the dress bone, and what is below Link's bone, as one piece; nothing else moves
    for name, (target, reported) in sorted(dict(skirt).items()):
        i, k = old_index[name], index[target]
        assert k not in kept and new[back[i]].parent == k, ("not on the skirt", name)
        assert abs(math.dist(w_new[k].t, w_new[back[i]].t) - reported) < tol, name
        under = set()
        for j, b in enumerate(new):
            if j == k or b.parent in under:
                under.add(j)
        turn = Xf(euler_matrix((0.4, -0.3, 0.2)), (0.01, -0.02, 0.03))
        local = Xf(euler_matrix(new[k].rotate), new[k].translate) * turn
        r = matrix_euler(local.r)
        pose = rest_world(new, {k: ((r[0], r[1], r[2], 1.0), tuple(local.t))})
        frame = Xf(_unit(w_new[k]), w_new[k].t)
        want = frame * turn * _inv(frame)
        for j, b in enumerate(old):
            move = pose[back[j]] * _inv(w_new[back[j]])
            e = _skin_error(_inv(want) * move if back[j] in under else move)
            assert e < tol, ("skirt move of", target, "moves", b.name, e)
            worst = max(worst, e)
    return worst
