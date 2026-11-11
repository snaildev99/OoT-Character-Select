"""Character portraits, step 2 of 2: render the arrays of tools\\npcportrait.py with Blender.

    venv-blender\\Scripts\\python.exe tools\\npcportrait_blender.py Npc_Kokiri_WC_Saria [name]

venv-blender = Python 3.11 with `pip install bpy==4.5.16 numpy` (Blender as a module; nothing
installed system-wide). Writes build-npc\\portraits\\<name>_natural.png and _cinematic.png
(head and shoulders, transparent background, 1024 x 1024) and prints numbers about each
(coverage, brightness): nobody here looks at the pictures but snaildev.

Materials are approximations of the game's character shader from its textures:
- _a0 colour (x _ao0), _rgh0 roughness, _n0 normal (two channels, z rebuilt).
- hair: _a0 alpha as coverage. Eyebrows / eyelashes: _a0 is a grey mask, colour from
  ns_param_color0 (eyebrows) or dark (lashes).
- eyes: sclera (_a1) everywhere, the iris texture (_a0) scaled into the disc of the mask
  (_gn3, radius about 0.27 of the eye's uv square), a pupil added; the outer shell as a thin
  glossy layer (catch lights); the eye shadow mesh as a soft dark overlay by its mask.
- life: skin scatters light (subsurface) and has a soft sheen at
  grazing angles; lights are round (round catch lights); the surroundings are a soft sky /
  ground gradient instead of an even white, so forms shade off.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build-npc", "portraits")
SIZE = 1024
IRIS_DISC = 0.27        # radius of the iris in the eye's uv square (from the mask texture)
IRIS_TEX = 0.45         # radius of the iris inside its own texture
PUPIL = 0.075           # pupil radius in the eye's uv square


def game_to_blender(p):
    """Game: x left, y up, z forward. Blender: z up, the character faces -y."""
    p = np.asarray(p, np.float32)
    return np.stack([p[..., 0], -p[..., 2], p[..., 1]], axis=-1)


class Scene:
    def __init__(self, model):
        self.folder = os.path.join(OUT, model)
        self.info = json.load(open(os.path.join(self.folder, "scene.json")))
        self.images = {}

    def image(self, name, kind):
        """kind: 'color' (sRGB), 'data', 'normal' (z rebuilt, green flipped for the flipped v)."""
        key = (name, kind)
        if key in self.images:
            return self.images[key]
        if self.info["textures"].get(name) is None:
            return None
        a = np.load(os.path.join(self.folder, name + ".npy")).astype(np.float32) / 255
        if kind == "normal":
            x, y = a[:, :, 0] * 2 - 1, a[:, :, 1] * 2 - 1
            z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
            a = np.stack([a[:, :, 0], 1 - a[:, :, 1], z * 0.5 + 0.5, np.ones_like(z)], axis=2)
        a = a[::-1]     # Blender's rows go bottom-up
        img = bpy.data.images.new("%s.%s" % (name, kind), a.shape[1], a.shape[0], alpha=True)
        img.colorspace_settings.name = "sRGB" if kind == "color" else "Non-Color"
        img.alpha_mode = "CHANNEL_PACKED"
        img.pixels.foreach_set(np.ascontiguousarray(a).ravel())
        img.pack()
        self.images[key] = img
        return img


def tex_node(nt, img, x, y, uv=None):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = img
    n.location = (x, y)
    if uv is not None:
        nt.links.new(uv, n.inputs["Vector"])
    return n


def base_material(sc, name, style):
    info = sc.info["materials"][name]
    tex, par = info["textures"], info.get("params", {})
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    L = nt.links.new
    skin = name in ("Mt_Face", "Mt_Face_Eyelid", "Mt_Skin", "Mt_Skin_Body", "Mt_Head")
    hairy = "Hair" in name
    mask_only = name.startswith(("Mt_Eyebrow", "Mt_Eyelash"))

    if name.startswith("Mt_Eye_Inner"):
        uvn = nt.nodes.new("ShaderNodeTexCoord")
        scl = tex_node(nt, sc.image(tex["_a1"], "color"), -700, 300, uvn.outputs["UV"])
        mp = nt.nodes.new("ShaderNodeMapping")       # iris texture scaled into the mask's disc, about the centre
        k = IRIS_TEX / IRIS_DISC
        mp.inputs["Scale"].default_value = (k, k, 1)
        mp.inputs["Location"].default_value = (0.5 - 0.5 * k, 0.5 - 0.5 * k, 0)
        L(uvn.outputs["UV"], mp.inputs["Vector"])
        iris = tex_node(nt, sc.image(tex["_a0"], "color"), -700, 0, mp.outputs["Vector"])
        iris.extension = "EXTEND"
        mask = tex_node(nt, sc.image(tex["_gn3"], "data"), -700, -300, uvn.outputs["UV"])
        mix = nt.nodes.new("ShaderNodeMixRGB")
        L(mask.outputs["Color"], mix.inputs["Fac"])
        L(scl.outputs["Color"], mix.inputs["Color1"])
        L(iris.outputs["Color"], mix.inputs["Color2"])
        # pupil: distance from the centre of the uv square
        sub = nt.nodes.new("ShaderNodeVectorMath")
        sub.operation = "DISTANCE"
        L(uvn.outputs["UV"], sub.inputs[0])
        sub.inputs[1].default_value = (0.5, 0.5, 0)
        ramp = nt.nodes.new("ShaderNodeMapRange")
        ramp.inputs["From Min"].default_value = PUPIL * 0.8
        ramp.inputs["From Max"].default_value = PUPIL * 1.15
        L(sub.outputs["Value"], ramp.inputs["Value"])
        pup = nt.nodes.new("ShaderNodeMixRGB")
        pup.blend_type = "MULTIPLY"
        pup.inputs["Fac"].default_value = 1.0
        L(mix.outputs["Color"], pup.inputs["Color1"])
        L(ramp.outputs["Result"], pup.inputs["Color2"])
        L(pup.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.35
        bsdf.inputs["Specular IOR Level"].default_value = 0.1     # the shell carries the highlights
        return mat

    if name.startswith("Mt_Eye_Outer"):
        nt.nodes.remove(bsdf)
        out = nt.nodes["Material Output"]
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        gl = nt.nodes.new("ShaderNodeBsdfGlossy")
        gl.inputs["Roughness"].default_value = 0.04
        mx = nt.nodes.new("ShaderNodeMixShader")
        mx.inputs["Fac"].default_value = 0.06
        L(tr.outputs[0], mx.inputs[1])
        L(gl.outputs[0], mx.inputs[2])
        L(mx.outputs[0], out.inputs["Surface"])
        return mat

    if name.startswith("Mt_EyeShadow") or name.startswith("Mt_Eye_Shadow"):
        nt.nodes.remove(bsdf)
        out = nt.nodes["Material Output"]
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        dk = nt.nodes.new("ShaderNodeBsdfDiffuse")
        dk.inputs["Color"].default_value = (0.02, 0.012, 0.01, 1)
        mx = nt.nodes.new("ShaderNodeMixShader")
        img = sc.image(tex.get("_gn3", ""), "data")
        if img is not None:
            m = tex_node(nt, img, -500, 0)
            sc_ = nt.nodes.new("ShaderNodeMath")
            sc_.operation = "MULTIPLY"
            sc_.inputs[1].default_value = 0.55
            L(m.outputs["Color"], sc_.inputs[0])
            L(sc_.outputs[0], mx.inputs["Fac"])
        else:
            mx.inputs["Fac"].default_value = 0.0
        L(tr.outputs[0], mx.inputs[1])
        L(dk.outputs[0], mx.inputs[2])
        L(mx.outputs[0], out.inputs["Surface"])
        return mat

    alb = sc.image(tex.get("_a0", ""), "color" if not mask_only else "data")
    if mask_only:
        c = par.get("color0", [0.03, 0.02, 0.015, 1])
        if not name.startswith("Mt_Eyebrow") or c[:3] == [1.0, 0.03, 0.0]:
            c = [0.02, 0.014, 0.01, 1]
        bsdf.inputs["Base Color"].default_value = (c[0], c[1], c[2], 1)
        bsdf.inputs["Roughness"].default_value = 0.6
        if alb is not None:
            a = tex_node(nt, alb, -500, 0)
            gain = nt.nodes.new("ShaderNodeMapRange")       # the mask is faint: stretch it
            gain.inputs["From Min"].default_value = 0.02
            gain.inputs["From Max"].default_value = 0.35
            L(a.outputs["Color"], gain.inputs["Value"])
            L(gain.outputs["Result"], bsdf.inputs["Alpha"])
        return mat

    if alb is not None:
        a = tex_node(nt, alb, -700, 300)
        col = a.outputs["Color"]
        ao = sc.image(tex.get("_ao0", ""), "data")
        if ao is not None:
            an = tex_node(nt, ao, -700, 600)
            soft = nt.nodes.new("ShaderNodeMapRange")       # the game's baked occlusion, a little softened
            soft.inputs["To Min"].default_value = 0.2
            L(an.outputs["Color"], soft.inputs["Value"])
            mul = nt.nodes.new("ShaderNodeMixRGB")
            mul.blend_type = "MULTIPLY"
            mul.inputs["Fac"].default_value = 1.0
            L(col, mul.inputs["Color1"])
            L(soft.outputs["Result"], mul.inputs["Color2"])
            col = mul.outputs["Color"]
        L(col, bsdf.inputs["Base Color"])
        info_a = sc.info["textures"][tex["_a0"]]
        if info_a["alpha_min"] < 250 and info_a["format"] != "BC4":
            L(a.outputs["Alpha"], bsdf.inputs["Alpha"])
        if skin:
            bsdf.inputs["Subsurface Weight"].default_value = 0.45
            bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
            bsdf.inputs["Subsurface Scale"].default_value = 0.012    # metres: light bleeds about a centimetre
            bsdf.inputs["Sheen Weight"].default_value = 0.12         # soft glow at grazing angles
            bsdf.inputs["Sheen Roughness"].default_value = 0.45
    rgh = sc.image(tex.get("_rgh0", ""), "data")
    if rgh is not None:
        r = tex_node(nt, rgh, -700, 0)
        L(r.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.6
    nrm = sc.image(tex.get("_n0", ""), "normal")
    if nrm is not None:
        n = tex_node(nt, nrm, -700, -300)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.inputs["Strength"].default_value = 1.0
        L(n.outputs["Color"], nm.inputs["Color"])
        L(nm.outputs["Normal"], bsdf.inputs["Normal"])
    if hairy:
        bsdf.inputs["Anisotropic"].default_value = 0.5
        bsdf.inputs["Specular IOR Level"].default_value = 0.35
        bsdf.inputs["Sheen Weight"].default_value = 0.25
        bsdf.inputs["Sheen Roughness"].default_value = 0.4
    return mat


EYE_TURN = 0.0          # how far the eyes turn towards the camera (0 = as modelled; snaildev: fine as they are)


def rotation_between(a, b, fraction):
    """Rotation matrix turning unit vector a towards unit vector b by a fraction of the angle."""
    axis = np.cross(a, b)
    n = np.linalg.norm(axis)
    if n < 1e-6:
        return np.eye(3)
    axis /= n
    ang = math.atan2(n, float(np.dot(a, b))) * fraction
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * k + (1 - math.cos(ang)) * (k @ k)


def look_at_camera(sc, s, pos, nrm, uv, camera_game):
    """Turn each eyeball of an eye shape about its bone so that the iris (centre of the eye's
    uv square) faces the camera. Game coordinates in and out."""
    pos, nrm = pos.copy(), nrm.copy()
    angles = {}
    for side, sel in (("L", pos[:, 0] > 0), ("R", pos[:, 0] < 0)):
        e = sc.info["bones"].get("Eyeball_" + side)
        if e is None or not sel.any():
            continue
        e = np.array(e)
        gaze = sc.gaze.get(side)
        if gaze is None:
            continue
        to_cam = np.array(camera_game) - e
        to_cam /= np.linalg.norm(to_cam)
        r = rotation_between(gaze, to_cam, EYE_TURN)
        pos[sel] = (pos[sel] - e) @ r.T + e
        nrm[sel] = nrm[sel] @ r.T
        angles[side] = round(math.degrees(math.acos(np.clip(np.dot(gaze, to_cam), -1, 1))) * EYE_TURN, 1)
    return pos, nrm, angles


def find_gaze(sc):
    """Per eye: the direction from the eyeball bone to the vertex at the centre of the iris."""
    sc.gaze = {}
    for s in sc.info["shapes"]:
        if "Eye_Inner" not in s["name"]:
            continue
        d = np.load(os.path.join(sc.folder, s["file"]))
        for side, sel in (("L", d["pos"][:, 0] > 0), ("R", d["pos"][:, 0] < 0)):
            e = sc.info["bones"].get("Eyeball_" + side)
            if e is None or not sel.any():
                continue
            p, u = d["pos"][sel], d["uv"][sel]
            c = np.argmin(np.hypot(u[:, 0] - 0.5, u[:, 1] - 0.5))
            g = p[c] - np.array(e)
            sc.gaze[side] = g / np.linalg.norm(g)


def build_meshes(sc, style, camera_game=None):
    mats = {}
    objs = []
    find_gaze(sc)
    sc.eye_angles = {}
    for s in sc.info["shapes"]:
        d = np.load(os.path.join(sc.folder, s["file"]))
        gp, gn = d["pos"], d["nrm"]
        if camera_game is not None and ("Eye_Inner" in s["name"] or "Eye_Outer" in s["name"]):
            gp, gn, sc.eye_angles = look_at_camera(sc, s, gp, gn, d["uv"], camera_game)
        pos, nrm, uv, tris = game_to_blender(gp), game_to_blender(gn), d["uv"], d["tris"]
        me = bpy.data.meshes.new(s["name"])
        me.vertices.add(len(pos))
        me.vertices.foreach_set("co", pos.ravel())
        me.loops.add(tris.size)
        me.loops.foreach_set("vertex_index", tris.ravel())
        me.polygons.add(len(tris))
        me.polygons.foreach_set("loop_start", np.arange(0, tris.size, 3))
        me.polygons.foreach_set("loop_total", np.full(len(tris), 3))
        me.polygons.foreach_set("use_smooth", np.ones(len(tris), bool))
        me.update(calc_edges=True)
        layer = me.uv_layers.new(name="uv")
        luv = uv[tris.ravel()].copy()
        luv[:, 1] = 1 - luv[:, 1]
        layer.data.foreach_set("uv", luv.ravel())
        me.validate(clean_customdata=False)
        if len(me.vertices) == len(pos):
            me.normals_split_custom_set_from_vertices(nrm.tolist())
        name = s["material"]
        if name not in mats:
            mats[name] = base_material(sc, name, style)
        me.materials.append(mats[name])
        ob = bpy.data.objects.new(s["name"], me)
        bpy.context.scene.collection.objects.link(ob)
        objs.append(ob)
    return objs


def framing(sc):
    """Centre and side (metres, game coordinates) of the head-and-shoulders square."""
    head = sc.info["bones"]["Head"]
    top = max(s["hi"][1] for s in sc.info["shapes"] if s["hi"][1] > head[1])
    h = top - head[1]
    eyes = [sc.info["bones"][b] for b in ("Eyeball_L", "Eyeball_R") if b in sc.info["bones"]]
    z = (np.mean([e[2] for e in eyes]) * 0.5 if eyes else head[2])
    centre = [0.0, head[1] + 0.25 * h, float(z)]
    return centre, 1.8 * h


def camera_position(centre, side, yaw_deg=16.0, pitch_deg=4.0):
    dist = (side / 2) / math.tan(math.atan(18 / 85))
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return np.array(centre) + dist * np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                                               math.cos(yaw) * math.cos(pitch)])


def add_camera(centre, side, yaw_deg=16.0, pitch_deg=4.0):
    cam = bpy.data.cameras.new("cam")
    cam.lens = 85
    cam.sensor_width = 36
    cam.clip_start, cam.clip_end = 0.05, 50
    ob = bpy.data.objects.new("cam", cam)
    bpy.context.scene.collection.objects.link(ob)
    dist = (side / 2) / math.tan(math.atan(18 / 85))
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    # game coordinates: the character looks along +z; the camera stands in front, a bit to its left and above
    g = np.array(centre) + dist * np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
    ob.location = Vector(game_to_blender(g).tolist())
    target = Vector(game_to_blender(centre).tolist())
    ob.rotation_euler = (target - ob.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = ob
    return ob


def area_light(name, centre, direction_game, distance, power, size, color):
    """A light `distance` from the head centre in a direction given in game axes, aimed at it."""
    light = bpy.data.lights.new(name, "AREA")
    light.shape = "DISK"        # round catch lights in the eyes
    light.energy, light.size, light.color = power, size, color
    ob = bpy.data.objects.new(name, light)
    bpy.context.scene.collection.objects.link(ob)
    d = np.array(direction_game, float)
    d /= np.linalg.norm(d)
    ob.location = Vector(game_to_blender(np.array(centre) + distance * d).tolist())
    target = Vector(game_to_blender(centre).tolist())
    ob.rotation_euler = (target - ob.location).to_track_quat("-Z", "Y").to_euler()
    return ob


def sky_world(top, horizon, ground, strength):
    """Surroundings that shade forms: light from above, a darker warm ground, no even white."""
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Incoming"], sep.inputs[0])      # world: the view direction, z up
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = 1.0
    rng.inputs["From Max"].default_value = -1.0               # 0 = straight down, 1 = straight up
    nt.links.new(sep.outputs["Z"], rng.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*ground, 1)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (*top, 1)
    mid = ramp.color_ramp.elements.new(0.52)
    mid.color = (*horizon, 1)
    nt.links.new(rng.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.world = world


# Lighting styles. Directions in game axes: +x the character's left (the camera's side),
# +y up, +z in front. Lights: (name, direction, distance m, watts, size m, colour).
STYLES = {
    # soft daylight portrait: big key in front on the far side, open shadows
    "natural": {"sky": ((0.80, 0.88, 1.0), (0.55, 0.56, 0.58), (0.22, 0.18, 0.14), 0.45),
                "look": "AgX - Medium High Contrast", "target": 0.56, "lights": [
                    ("key", (-0.45, 0.60, 1.0), 1.7, 95, 1.3, (1.0, 0.96, 0.90)),
                    ("fill", (0.95, 0.10, 0.75), 2.0, 22, 1.8, (0.90, 0.94, 1.0)),
                    ("rim", (0.70, 0.65, -1.0), 1.6, 70, 0.7, (1.0, 0.97, 0.92))]},
    # warm key, cool rim, the whole face readable
    "cinematic": {"sky": ((0.45, 0.60, 1.0), (0.20, 0.24, 0.32), (0.10, 0.07, 0.05), 0.30),
                  "look": "AgX - High Contrast", "target": 0.46, "lights": [
                      ("key", (-0.70, 0.55, 0.85), 1.5, 120, 0.8, (1.0, 0.84, 0.66)),
                      ("fill", (0.90, 0.05, 0.80), 2.0, 42, 1.6, (0.72, 0.82, 1.0)),
                      ("rim", (0.80, 0.60, -1.0), 1.4, 150, 0.5, (0.65, 0.80, 1.0)),
                      ("kicker", (-0.95, 0.20, -0.75), 1.5, 55, 0.45, (1.0, 0.80, 0.60))]},
    # half way between the two: slightly warm key, a clear but soft shadow side, light rim
    "balanced": {"sky": ((0.62, 0.74, 1.0), (0.38, 0.40, 0.45), (0.16, 0.12, 0.09), 0.38),
                 "look": "AgX - Medium High Contrast", "target": 0.52, "lights": [
                     ("key", (-0.58, 0.58, 0.92), 1.6, 105, 1.05, (1.0, 0.90, 0.78)),
                     ("fill", (0.92, 0.08, 0.78), 2.0, 30, 1.7, (0.82, 0.88, 1.0)),
                     ("rim", (0.75, 0.62, -1.0), 1.5, 105, 0.6, (0.82, 0.89, 1.0)),
                     ("kicker", (-0.95, 0.20, -0.75), 1.5, 25, 0.45, (1.0, 0.86, 0.70))]},
    # one warm soft spotlight from above in front, dim warm room around it
    "spotlight": {"sky": ((0.30, 0.22, 0.16), (0.20, 0.14, 0.10), (0.12, 0.08, 0.05), 0.35),
                  "look": "AgX - Medium High Contrast", "target": 0.52, "lights": [
                      ("spot", (-0.15, 1.0, 0.85), 1.6, 150, 0.9, (1.0, 0.86, 0.68)),
                      ("bounce", (0.2, -0.6, 1.0), 1.5, 16, 1.6, (1.0, 0.82, 0.62)),
                      ("rim", (0.5, 0.7, -1.0), 1.5, 45, 0.6, (1.0, 0.90, 0.78))]},
    # by the hearth: a low orange fire glow from in front and one side, a dim warm room, a faint cool window behind
    "hearth": {"sky": ((0.20, 0.15, 0.12), (0.16, 0.11, 0.08), (0.14, 0.08, 0.04), 0.30),
               "look": "AgX - Medium High Contrast", "target": 0.50, "lights": [
                   ("fire", (-0.75, -0.10, 0.95), 1.4, 110, 0.9, (1.0, 0.62, 0.32)),
                   ("room", (0.6, 0.55, 0.9), 2.0, 34, 1.8, (1.0, 0.86, 0.70)),
                   ("ember", (-0.3, -0.7, 0.6), 1.2, 14, 0.8, (1.0, 0.45, 0.20)),
                   ("window", (0.85, 0.5, -1.0), 1.6, 40, 0.6, (0.62, 0.74, 1.0))]},
}


def sky_world(top, horizon, ground, strength):
    """Surroundings that shade forms: light from above, a darker ground, no even white."""
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Incoming"], sep.inputs[0])      # world: the view direction, z up
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = 1.0
    rng.inputs["From Max"].default_value = -1.0               # 0 = straight down, 1 = straight up
    nt.links.new(sep.outputs["Z"], rng.inputs["Value"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*ground, 1)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (*top, 1)
    mid = ramp.color_ramp.elements.new(0.52)
    mid.color = (*horizon, 1)
    nt.links.new(rng.outputs["Result"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.world = world


def lighting(style, centre):
    st = STYLES[style]
    vs = bpy.context.scene.view_settings
    vs.view_transform = "AgX"
    vs.look = st["look"]
    sky_world(*st["sky"])
    for name, direction, distance, power, size, color in st["lights"]:
        area_light(name, centre, direction, distance, power, size, color)


def setup_render(samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.transparent_max_bounces = 24
    sc.render.film_transparent = True
    sc.render.resolution_x = sc.render.resolution_y = SIZE
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.render.image_settings.color_depth = "8"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("OPTIX", "CUDA"):
            try:
                prefs.compute_device_type = kind
                prefs.get_devices()
                gpus = [d for d in prefs.devices if d.type == kind]
                if gpus:
                    for d in prefs.devices:
                        d.use = d.type == kind
                    sc.cycles.device = "GPU"
                    return kind
            except Exception:
                continue
    except Exception:
        pass
    sc.cycles.device = "CPU"
    return "CPU"


def pixel(point_game):
    """Where a point (game coordinates) lands in the picture: (x, y) in 0..1 from the top left."""
    from bpy_extras.object_utils import world_to_camera_view
    sc = bpy.context.scene
    v = world_to_camera_view(sc, sc.camera, Vector(game_to_blender(point_game).tolist()))
    return float(v.x), 1 - float(v.y)


def probes(info):
    """Windows to describe: each eye (around the eyeball), a cheek below each eye, the chin."""
    out = {}
    b = info["bones"]
    for side in ("L", "R"):
        e = b.get("Eyeball_" + side)
        if e:
            front = [e[0], e[1], e[2] + 0.02]
            out["eye_" + side] = (pixel(front), 0.035)
            out["cheek_" + side] = (pixel([e[0] * 1.2, e[1] - 0.05, e[2] + 0.02]), 0.02)
    return out


def measure(path, windows=None):
    """Numbers about a rendered picture (display values 0..1)."""
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    a = a.reshape(h, w, 4)[::-1]
    alpha = a[:, :, 3]
    solid = alpha > 0.9
    lum = a[:, :, :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    ys, xs = np.nonzero(alpha > 0.05)
    box = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None
    c = slice(int(h * 0.35), int(h * 0.6)), slice(int(w * 0.38), int(w * 0.62))
    extra = {}
    for key, ((px, py), half) in (windows or {}).items():
        x0, x1 = int((px - half) * w), int((px + half) * w)
        y0, y1 = int((py - half) * h), int((py + half) * h)
        win = a[max(y0, 0):y1, max(x0, 0):x1]
        if win.size == 0:
            continue
        r, g, b_ = win[:, :, 0], win[:, :, 1], win[:, :, 2]
        l = lum[max(y0, 0):y1, max(x0, 0):x1]
        extra[key] = {"at": [round(px, 3), round(py, 3)], "rgb": [round(float(c.mean()), 3) for c in (r, g, b_)],
                      "dark": round(float((l < 0.08).mean()), 3),
                      "blue": round(float(((b_ > r * 1.3) & (b_ > g)).mean()), 3),
                      "white": round(float((win[:, :, :3].min(axis=2) > 0.5).mean()), 3),
                      "skin": round(float(((r > g) & (g > b_) & (l > 0.15)).mean()), 3)}
    return {"probes": extra, "coverage": round(float((alpha > 0.5).mean()), 3), "soft_edge": round(float(((alpha > 0.05) & (alpha < 0.95)).mean()), 3),
            "corners_alpha": round(float(np.mean([alpha[:40, :40].mean(), alpha[:40, -40:].mean()])), 3),
            "box": box, "lum_mean": round(float(lum[solid].mean()), 3) if solid.any() else None,
            "lum_p05_p95": [round(float(v), 3) for v in np.percentile(lum[solid], [5, 95])] if solid.any() else None,
            "face_lum": round(float(lum[c][solid[c]].mean()), 3) if solid[c].any() else None,
            "face_rgb": [round(float(a[c][:, :, i][solid[c]].mean()), 3) for i in range(3)] if solid[c].any() else None,
            "clipped": round(float((a[:, :, :3].max(axis=2)[solid] > 0.98).mean()), 4) if solid.any() else None}


def render(model, name, style):
    target_face = STYLES[style]["target"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = Scene(model)
    centre, side = framing(sc)
    build_meshes(sc, style, camera_position(centre, side))
    add_camera(centre, side)
    lighting(style, centre)
    device = setup_render(24)
    scene = bpy.context.scene
    path = os.path.join(OUT, "%s_%s.png" % (name, style))
    # exposure: two quick small renders, then the real one
    scene.render.resolution_percentage = 25
    for _ in range(2):
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        m = measure(path, probes(sc.info))
        cheeks = [v["rgb"] for k, v in m["probes"].items() if k.startswith("cheek")]
        now = float(np.mean([0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] for c in cheeks])) if cheeks else m["face_lum"]
        if not now:
            break
        scene.view_settings.exposure += math.log2(max(target_face, 1e-3) / max(now, 1e-3)) * 0.9
    scene.render.resolution_percentage = 100
    scene.cycles.samples = 160
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    m = measure(path, probes(sc.info))
    m.update({"eyes_turned_deg": sc.eye_angles, "device": device, "exposure": round(scene.view_settings.exposure, 2), "side_m": round(side, 3),
              "centre": [round(v, 3) for v in centre]})
    return path, m


def main():
    """<model> [name] [style ...]; without styles: all of STYLES."""
    model = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else model
    for style in sys.argv[3:] or list(STYLES):
        path, m = render(model, name, style)
        print("RESULT", path)
        print("RESULT", json.dumps(m))


if __name__ == "__main__":
    main()
