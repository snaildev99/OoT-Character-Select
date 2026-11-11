"""Character portraits, step 1 of 2: a model's geometry and textures as plain arrays for Blender.

    venv\\Scripts\\python.exe -I tools\\npcportrait.py Npc_Kokiri_WC_Saria [more model names]

Writes build-npc\\portraits\\<model>\\: scene.json (shapes, materials, head position), one
.npz per shape (positions, normals, uvs, triangles; rest pose, model space, metres, y up) and
one .npy per texture (h, w, 4 uint8 RGBA as the game samples it). Step 2 renders them:
tools\\npcportrait_blender.py (run with venv-blender, which has Blender as a Python module).

Vertex data (FVTX, one per shape here): attributes {name, u16 format, u16 offset, u16 buffer}
at +0x08, buffer sizes +0x30 (0x10 each), strides +0x38, u32 offset of the first buffer from
the start of the file's buffer section at +0x48, counts at +0x4C (attributes u8, buffers u8,
index u16, vertices u32). A mesh (shape +0x18, 0x38 each; the first is the full detail one):
index buffer size record +0x18, u32 offset from the buffer section +0x20, primitive type
+0x24 (3 = triangles), index format +0x28 (1 = u16, 2 = u32), index count +0x2C, first
vertex +0x30. Smooth shapes (2+ weights) store vertices in model space; a shape with 0
weights is in its bone's frame, with 1 weight in the frame of the bone its index names.

Textures (Tex/<name>.bntx, platform "Ounce"): block-linear as on the first Switch (rows of
64-byte x 8-row groups, block height from the header, halved for small mips), but the order
INSIDE a group differs. Byte offset of (x bytes 0..63, y 0..7) in its 512-byte group:
    (x >> 5) << 8 | (y >> 2) << 7 | ((x >> 4) & 1) << 6 | (y & 3) << 4 | (x & 15)
(the first Switch has y bits 1-2 at 6, x bit 4 at 5, y bit 0 at 4). Found from the data
(which slots are neighbours, averaged over a texture's groups) and checked against each
texture's own next mip: mean error 0.2-1.3 of 255 with this order, 5-25 with the old one
(BC1 / BC4 / BC7 / ASTC 5x5-12x12, 256-1152 pixels). toolsntx.py still has the old order;
ounce_deswizzle() below replaces it for the textures read here.
"""
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bfres  # noqa: E402
import bntx  # noqa: E402
import nxfmt  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "build-npc", "portraits")
MODEL_DIR = os.path.join(nxfmt.ROMFS, "Model")
TEX_DIR = os.path.join(nxfmt.ROMFS, "Tex")
MATERIAL_SIZE = 0xB0
PARAMS = {  # shader parameters the renderer may want: name -> (64-bit key, struct format)
    "color0": (0x68E131E72BCD91AD, "<4f"), "color1": (0xE647277D46E8C1D8, "<4f"),
    "color2": (0x0187464EC6541F6E, "<4f"), "color3": (0x961383B79667F911, "<4f"),
    "vector0": (0xD3E83F1EE98ED582, "<4f"), "emission": (0xF686DFDEF5207CB8, "<f"),
    "alpha_ref": (0xDF211804D7DE974E, "<f"),
    "scalar0": (0x0, "<f"), "scalar1": (0xC8FE5105F75AD71F, "<f"), "scalar2": (0xB61B3B19F9EFD69F, "<f"),
    "scalar3": (0x3851331D68FBD697, "<f"), "scalar4": (0xA0866BE5FC9EE4F8, "<f"),
}


def ounce_deswizzle(width, height, blk_w, blk_h, bpp, block_height_log2, data):
    """Block-linear -> rows for this platform (see the head); same interface as bntx.deswizzle."""
    w, h = -(-width // blk_w), -(-height // blk_h)
    block_height = 1 << block_height_log2
    pow2 = 1
    while pow2 < h:
        pow2 *= 2
    while block_height > 1 and pow2 < block_height * 8:
        block_height //= 2
    gobs_x = ((w * bpp + 63) & ~63) // 64
    ys, xs = np.mgrid[0:h, 0:w]
    xb = xs * bpp
    group = (ys // (8 * block_height)) * 512 * block_height * gobs_x + (xb // 64) * 512 * block_height +         ((ys % (8 * block_height)) // 8) * 512
    x2, y2 = xb & 63, ys & 7
    off = ((x2 >> 5) << 8) + ((y2 >> 2) << 7) + (((x2 >> 4) & 1) << 6) + ((y2 & 3) << 4) + (x2 & 0xF)
    addr = (group + off).reshape(-1)
    src = np.frombuffer(data, dtype=np.uint8)
    idx = addr[:, None] + np.arange(bpp)[None, :]
    assert idx.max() < len(src), "texture data too short"
    return src[idx].reshape(h, w * bpp).tobytes(), block_height.bit_length() - 1


bntx.deswizzle = ounce_deswizzle     # for this process only; the tool's file is not ours to change


def _attr(buf, stride, count, offset, fmt):
    """One vertex attribute as a float / int array (count, n)."""
    def raw(dtype, n):
        size = np.dtype(dtype).itemsize * n
        idx = (np.arange(count) * stride + offset)[:, None] + np.arange(size)[None, :]
        return np.frombuffer(buf[idx].tobytes(), dtype).reshape(count, n)
    if fmt == 0x1805:
        return raw("<f4", 3).astype(np.float32)
    if fmt == 0x1705:
        return raw("<f4", 2).astype(np.float32)
    if fmt == 0x1905:
        return raw("<f4", 4).astype(np.float32)
    if fmt == 0x1205:
        return raw("<f2", 2).astype(np.float32)
    if fmt == 0x1505:
        return raw("<f2", 4).astype(np.float32)
    if fmt == 0x1201:
        return raw("<u2", 2).astype(np.float32) / 65535
    if fmt == 0x1202:
        return np.maximum(raw("<i2", 2).astype(np.float32) / 32767, -1)
    if fmt == 0x0901:
        return raw("u1", 2).astype(np.float32) / 255
    if fmt == 0x0902:
        return np.maximum(raw("i1", 2).astype(np.float32) / 127, -1)
    if fmt == 0x0B01:
        return raw("u1", 4).astype(np.float32) / 255
    if fmt == 0x0B02:
        return np.maximum(raw("i1", 4).astype(np.float32) / 127, -1)
    if fmt == 0x0B03:
        return raw("u1", 4).astype(np.int32)
    if fmt == 0x0903:
        return raw("u1", 2).astype(np.int32)
    if fmt == 0x0203:
        return raw("u1", 1).astype(np.int32)
    if fmt == 0x0201:
        return raw("u1", 1).astype(np.float32) / 255
    if fmt == 0x0E02:       # 10:10:10:2 signed normalized
        v = raw("<u4", 1)[:, 0]
        out = np.zeros((count, 3), np.float32)
        for i in range(3):
            c = ((v >> (10 * i)) & 0x3FF).astype(np.int32)
            c = np.where(c >= 512, c - 1024, c)
            out[:, i] = np.maximum(c / 511.0, -1)
        return out
    raise AssertionError("vertex format %#x" % fmt)


def shape_geometry(f, m, s, world):
    """positions, normals (model space), uvs, triangles of a shape's full-detail mesh."""
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
        assert sizes[bi] == strides[bi] * count, ("vertex buffer size", s.name, name)
        buf = np.frombuffer(bytes(d[starts[bi]:starts[bi] + sizes[bi]]), np.uint8)
        attrs[name] = _attr(buf, strides[bi], count, aoff, fmt)
    pos, nrm = attrs["_p0"].astype(np.float64), attrs["_n0"][:, :3].astype(np.float64)
    uv = attrs.get("_u0", np.zeros((count, 2), np.float32))
    if s.weights == 0:
        mats = np.repeat(np.array(world[s.bone])[None], count, axis=0)
    elif s.weights == 1:
        idx = attrs["_i0"][:, 0]
        table = np.array([world[m.m2b[i]] for i in range(len(m.m2b))])
        mats = table[idx]
    else:
        mats = None
    if mats is not None:
        pos = np.einsum("nij,nj->ni", mats[:, :3, :3], pos) + mats[:, :3, 3]
        nrm = np.einsum("nij,nj->ni", mats[:, :3, :3], nrm)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
    mesh = f.u64(s.off + 0x18)
    ioff, prim, ifmt, icount, first = struct.unpack_from("<5I", d, mesh + 0x20)
    assert prim == 3 and ifmt in (1, 2), ("mesh", s.name, prim, ifmt)
    dt = "<u2" if ifmt == 1 else "<u4"
    tris = np.frombuffer(bytes(d[base + ioff:base + ioff + icount * np.dtype(dt).itemsize]), dt).astype(np.int64) + first
    assert icount % 3 == 0 and tris.max() < count, s.name
    return pos.astype(np.float32), nrm.astype(np.float32), uv.astype(np.float32), tris.reshape(-1, 3).astype(np.int32)


def material_info(f, m, index):
    """{sampler name: texture name} and the material's raw parameter block."""
    o = f.u64(m.off + 0x38) + index * MATERIAL_SIZE
    samplers = f.read_dict(f.u64(o + 0x38))[0] if f.u64(o + 0x38) else []
    names = [f.string(f.u64(f.u64(o + 0x20) + 8 * k)) for k in range(len(samplers))]
    return dict(zip(samplers, names))


def common_name(name, model):
    """Some materials name a texture of their own that the game does not ship (Saria's
    eyebrows: Mt_Eyebrow_<model>_Alb); the shared one is <part>_CmnTex_Chara_<kind>."""
    if "_%s_" % model not in name:
        return None
    part, kind = name.split("_%s_" % model)
    return "%s_CmnTex_Chara_%s" % (part[3:] if part.startswith("Mt_") else part, kind)


def texture(name, folder, model=""):
    """Decode Tex/<name>.bntx to <folder>/<name>.npy; returns numbers about it (or None)."""
    src = os.path.join(TEX_DIR, name + ".bntx.zs")
    stand_in = None
    if not os.path.exists(src):
        stand_in = common_name(name, model)
        src = os.path.join(TEX_DIR, "%s.bntx.zs" % stand_in)
        if not stand_in or not os.path.exists(src):
            return None
    arc = bntx.Bntx(nxfmt.read(src))
    t = arc[name] if name in arc else list(arc.tex.values())[0]
    rgba = t.rgba()
    np.save(os.path.join(folder, name + ".npy"), rgba)
    a = rgba[:, :, 3]
    return {"stand_in": stand_in, "size": [t.width, t.height], "format": t.format, "srgb": bool(t.srgb), "comp": list(t.comp),
            "alpha_min": int(a.min()), "alpha_mean": round(float(a.mean()) / 255, 3),
            "rgb_mean": [round(float(rgba[:, :, i].mean()) / 255, 3) for i in range(3)],
            "grey": bool(np.abs(rgba[:, :, 0].astype(int) - rgba[:, :, 2]).max() < 6)}


def export(model):
    stem = "%s.%s" % (model, model)
    f = bfres.Fres(nxfmt.read(os.path.join(MODEL_DIR, stem + ".bfres.zs")))
    m = f.model()
    folder = os.path.join(OUT, model)
    os.makedirs(folder, exist_ok=True)
    world = []
    for xf in bfres.world_matrices(m.bones):    # rigid transforms (scaled bones are leaves without vertices)
        w = np.eye(4)
        w[:3, :3], w[:3, 3] = np.array(xf.r), np.array(xf.t)
        world.append(w)
    names = [b.name for b in m.bones]
    scene = {"model": model, "shapes": [], "materials": {}, "textures": {}, "bones": {}}
    for bone in ("Head", "Neck", "Spine_2", "Face_Root", "Eyeball_L", "Eyeball_R"):
        if bone in names:
            scene["bones"][bone] = [float(v) for v in world[names.index(bone)][:3, 3]]
    lo, hi = np.full(3, 1e9), np.full(3, -1e9)
    for s in m.shapes:
        pos, nrm, uv, tris = shape_geometry(f, m, s, world)
        np.savez(os.path.join(folder, "shape_%02d.npz" % s.index), pos=pos, nrm=nrm, uv=uv, tris=tris)
        lo, hi = np.minimum(lo, pos.min(axis=0)), np.maximum(hi, pos.max(axis=0))
        head = names.index("Head")
        scene["shapes"].append({"file": "shape_%02d.npz" % s.index, "name": s.name,
                                "material": m.material_names[s.material], "vertices": len(pos),
                                "triangles": len(tris), "weights": s.weights,
                                "lo": [float(v) for v in pos.min(axis=0)], "hi": [float(v) for v in pos.max(axis=0)],
                                "uv_lo": [float(v) for v in uv.min(axis=0)], "uv_hi": [float(v) for v in uv.max(axis=0)],
                                "head_bone": bool(s.weights == 0 and s.bone == head)})
    scene["lo"], scene["hi"] = [float(v) for v in lo], [float(v) for v in hi]
    for i, name in enumerate(m.material_names):
        tex = material_info(f, m, i)
        params = {k: m.shader_param(name, key, fmt) for k, (key, fmt) in PARAMS.items() if key}
        scene["materials"][name] = {"textures": tex, "params": {k: list(v) for k, v in params.items() if v}}
        for t in tex.values():
            if t not in scene["textures"]:
                scene["textures"][t] = texture(t, folder, model)
    with open(os.path.join(folder, "scene.json"), "w") as fh:
        json.dump(scene, fh, indent=1)
    return scene


def main():
    for model in sys.argv[1:]:
        sc = export(model)
        print(model, "->", os.path.join(OUT, model))
        print("  bounds", [round(v, 3) for v in sc["lo"]], [round(v, 3) for v in sc["hi"]], "| bones", {k: [round(x, 3) for x in v] for k, v in sc["bones"].items()})
        for s in sc["shapes"]:
            print("  %-44s %-20s %6d v %6d t  w%d  y %.2f..%.2f  uv %s..%s" % (
                s["name"], s["material"], s["vertices"], s["triangles"], s["weights"], s["lo"][1], s["hi"][1],
                [round(v, 2) for v in s["uv_lo"]], [round(v, 2) for v in s["uv_hi"]]))
        for name, t in sc["textures"].items():
            print("  tex %-52s %s" % (name, "MISSING" if t is None else "%s%s %s a_min %d a_mean %.2f rgb %s%s" % (
                "(= %s) " % t["stand_in"] if t["stand_in"] else "", t["size"], t["format"], t["alpha_min"], t["alpha_mean"], t["rgb_mean"], " grey" if t["grey"] else "")))


if __name__ == "__main__":
    main()
