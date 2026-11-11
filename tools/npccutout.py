"""Cut-out portraits from the frames tools\\npcshots.py saved during a portrait run.

    venv\\Scripts\\python.exe -I tools\\npccutout.py [character numbers]

For each character of the last run in build-npc\\portraits\\shots.jsonl: the last frame on the
black backdrop and the first on the white one (the game switches the colour behind a
character that does not move), transparency from their difference, the colour from the black
frame, then a square around head and shoulders -> build-npc\\portraits\\cut\\<nn>_<name>.png
(RGBA, the frame's own resolution). Prints numbers about every step; nobody here looks at
the pictures (Pillow reads them as arrays).

How the square is found without looking: the Characters page shows every character with his
feet on the same line and his Head bone at the same height on screen (the page's zoom does
that: tools in mods\\npc_characters.py), so pixels per metre = Link's pixels per metre x
Link's Head bone height / this character's, and the feet are the lowest row the character
covers. Head bone height and top of the head come from the model file.
"""
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "mods"))

import bfres  # noqa: E402
import npcportrait  # noqa: E402
import nxfmt  # noqa: E402

OUT = os.path.join(ROOT, "build-npc", "portraits")
INDEX = os.path.join(OUT, "shots.jsonl")
CUT = os.path.join(OUT, "cut")
REGION_X = (0.50, 0.86)     # the character's part of the frame (he stands on the right third line;
                            # the page's 2D items are left of 0.09 and right of 0.91 of the width)
REGION_Y = (0.01, 0.936)    # between the header (to row 262 of 2160 at its lowest) and the footer (from 2034);
                            # both are 2D, drawn over the character and the same on black and white (= "solid")
STAND_X = 2.0 / 3.0         # characters stand on the right-third line of the page
SQUARE_2160 = (2274, 304, 572)   # left, top, side of the portrait square in a 3840 x 2160 frame
TOP_MARGIN = (352 - 304) / 572.0  # Link's: the share of the square above the top of his cap
FRAME_SIDE = 1.8            # square side in head heights (Head bone to the top of the head)
FRAME_CENTRE = 0.25         # centre above the Head bone, in head heights


def srgb_to_linear(a):
    a = a / 255.0
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(a):
    a = np.clip(a, 0, 1)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - 0.055)


def load(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float64)


def backdrop(a):
    h, w = a.shape[:2]
    patch = a[int(h * 0.35):int(h * 0.65), int(w * 0.05):int(w * 0.30)].reshape(-1, 3)
    return patch.mean(axis=0), float(patch.std())


def last_run():
    """{character number: [(time, path)]} of the last session in the index that has a run in
    it (several characters with several frames each; the tool also lists stray frames of the
    normal page, and every start of the tool begins a session)."""
    rows = [json.loads(line) for line in open(INDEX, encoding="utf-8")]
    starts = [i for i, r in enumerate(rows) if "session" in r] + [len(rows)]
    for a, b in reversed(list(zip(starts, starts[1:]))):
        shots = {}
        for r in rows[a + 1:b]:
            if r.get("after") and r["before"][0] == r["after"][0]:
                shots.setdefault(r["before"][0], []).append((r["t0"], r["path"]))
        if sum(len(v) >= 4 for v in shots.values()) >= 2:
            return shots
    return {}


_metrics = {}


def metrics(stem):
    """(Head bone height, top of the model, lowest point) in metres from the model file."""
    if stem not in _metrics:
        f = bfres.Fres(nxfmt.read(os.path.join(nxfmt.ROMFS, "Model", stem + ".bfres.zs")))
        m = f.model()
        world = []
        for xf in bfres.world_matrices(m.bones):
            w = np.eye(4)
            w[:3, :3], w[:3, 3] = np.array(xf.r), np.array(xf.t)
            world.append(w)
        head = world[[b.name for b in m.bones].index("Head")][1, 3]
        top, low = -1e9, 1e9
        for s in m.shapes:
            try:
                pos = npcportrait.shape_geometry(f, m, s, world)[0]
            except (AssertionError, ValueError, KeyError):
                continue
            top, low = max(top, float(pos[:, 1].max())), min(low, float(pos[:, 1].min()))
        _metrics[stem] = (float(head), top, low)
    return _metrics[stem]


def character(number):
    """(name, model stem) of a stored number."""
    import npc_characters as n
    ring = n.ring()
    link = "Player_Child.Player_Child"
    if number < len(n.build.CHARACTERS):
        return ring[number], link
    return ring[number], n.characters()[number - len(n.build.CHARACTERS)]["stem"]


LINK_PPM = 1456.0           # child Link on the page: pixels per metre in a 2160-row frame (soles at row 2004)
FEET_ROW = 2004.0
_eyes = {}


def eyes(stem):
    """(Head bone height, eyes above the Head bone, distance between the eyes) in metres, from
    the model's rest pose; None where a model has no eye bones."""
    if stem not in _eyes:
        m = bfres.Fres(nxfmt.read(os.path.join(nxfmt.ROMFS, "Model", stem + ".bfres.zs"))).model()
        names = [b.name for b in m.bones]
        pos = {}
        for want in ("Head", "Eyeball_L", "Eyeball_R"):
            if want not in names:
                continue
            chain, i = [], names.index(want)
            while i >= 0:
                chain.append(i)
                i = m.bones[i].parent
            xf = None
            for i in reversed(chain):       # (not bfres.world_matrices: a few models have a scaled leaf bone)
                b = m.bones[i]
                loc = bfres.Xf(bfres.euler_matrix(b.rotate[:3]), list(b.translate))
                xf = loc if xf is None else xf * loc
            pos[want] = xf.t
        if len(pos) < 3:
            _eyes[stem] = None
        else:
            _eyes[stem] = (pos["Head"][1], (pos["Eyeball_L"][1] + pos["Eyeball_R"][1]) / 2 - pos["Head"][1],
                           abs(pos["Eyeball_L"][0] - pos["Eyeball_R"][0]))
    return _eyes[stem]


def fitted_square(stem):
    """The portrait square for a character whose FACE is as large in it as Link's is in his:
    (left, top, side) in a 2160-row frame, or None without eye bones. The page zoom makes
    bodies equally tall, so heads differ (adults' are about 0.6 of child Link's on screen, a
    Goron's twice): the square is scaled by the distance between the eyes on screen and
    placed so that the eyes sit where Link's sit in SQUARE_2160."""
    link, me = eyes("Player_Child.Player_Child"), eyes(stem)
    if me is None:
        return None
    left0, top0, side0 = SQUARE_2160
    head_row = FEET_ROW - link[0] * LINK_PPM               # the same row for everybody
    link_eye_row = head_row - link[1] * LINK_PPM
    ppm = LINK_PPM * link[0] / me[0]
    scale = (me[2] * ppm) / (link[2] * LINK_PPM)
    side = side0 * scale
    eye_row = head_row - me[1] * ppm
    top = eye_row - (link_eye_row - top0) / side0 * side
    centre = left0 + side0 / 2.0
    return centre - side / 2.0, top, side, scale


def cut(number, shots, link_scale=None, save=True, fit=False):
    """One character. Returns a dict of numbers (and writes the picture)."""
    name, stem = character(number)
    kinds = []
    for t, path in sorted(shots):
        a = load(path)
        bg, spread = backdrop(a)
        kind = "black" if bg.max() < 2 and spread < 0.5 else "white" if bg.min() > 200 and spread < 0.5 else "other"
        kinds.append((t, path, kind, bg))
    whites = [k for k in kinds if k[2] == "white"]
    out = {"number": number, "name": name, "frames": [k[2] for k in kinds]}
    if not whites:
        out["error"] = "no frame on white"
        return out
    blacks = [k for k in kinds if k[2] == "black" and k[0] < whites[0][0]]
    if not blacks:
        out["error"] = "no frame on black before the white ones"
        return out
    b, w = load(blacks[-1][1]), load(whites[0][1])
    wbg = whites[0][3]
    # the two candidates for where the game blended: display values or linear light
    alphas = {}
    for space in ("display", "linear"):
        bb, ww, wb = (b, w, wbg) if space == "display" else (srgb_to_linear(b), srgb_to_linear(w), srgb_to_linear(wbg))
        per = 1 - (ww - bb) / wb
        alphas[space] = per
    h, wd = b.shape[:2]
    x0, x1 = int(REGION_X[0] * wd), int(REGION_X[1] * wd)
    y0, y1 = int(REGION_Y[0] * h), int(REGION_Y[1] * h)
    best = None
    for space, per in alphas.items():
        a = per[y0:y1, x0:x1]
        mean = a.mean(axis=2)
        edge = (mean > 0.1) & (mean < 0.9)
        disagree = float(a[edge].std(axis=1).mean()) if edge.any() else 0.0
        out["channel_disagreement_" + space] = round(disagree, 4)
        if best is None or disagree < best[1]:
            best = (space, disagree)
    space = best[0]
    out["space"] = space
    alpha = np.clip(alphas[space].mean(axis=2), 0, 1)
    region = alpha[y0:y1, x0:x1]
    solid = region > 0.5
    rows = np.nonzero(solid.any(axis=1))[0]
    cols = np.nonzero(solid.any(axis=0))[0]
    if len(rows) == 0:
        out["error"] = "nothing in front of the backdrop"
        return out
    top_row, feet_row = y0 + int(rows[0]), y0 + int(rows[-1])
    out.update({"covered": round(float(solid.mean()), 3), "partly": round(float(((region > 0.02) & (region < 0.98)).mean()), 4),
                "inside_below_1": round(float((region[solid] < 0.98).mean()), 4),
                "box": [x0 + int(cols[0]), top_row, x0 + int(cols[-1]), feet_row],
                "touches_region_edge": bool(rows[0] == 0 or rows[-1] == len(region) - 1 or cols[0] == 0 or cols[-1] == region.shape[1] - 1)})
    # snaildev, 2026-11-10: ONE square for everybody. The page zoom puts every character's feet
    # and Head bone on the same rows, so nothing is measured per character: the square is
    # child Link's head and shoulders (1.8 x the distance from his Head bone to the top of his
    # cap, centred a quarter of that above the bone, on the right-third line where characters
    # stand), found once from his own frames (2026-11-10: soles at row 2004, cap at 352, of 2160).
    square = SQUARE_2160
    if fit:
        fs = fitted_square(stem)
        if fs is None:
            out["fit"] = "no eye bones: Link's square"
        else:
            square, out["face_scale"] = fs[:3], round(fs[3], 2)
    left, upper, side = (v * h / 2160.0 for v in square)
    if fit:
        # snaildev, 2026-11-10 (after the first fitted set): (1) centre the head, not the standing
        # line: the square's centre is the centre of what the character covers between the top
        # of his head and the Head bone row; (2) no more empty space above a head than Link has
        # above his cap (adult Zelda's head sat low): the square starts at most TOP_MARGIN of
        # its side above the top of the head. Taller hair than that still crosses the top edge.
        head_row = (FEET_ROW - eyes("Player_Child.Player_Child")[0] * LINK_PPM) * h / 2160.0
        mid = left + side / 2.0
        c0, c1 = max(int(mid - side), 0), min(int(mid + side), wd)
        cover = alpha[y0:int(head_row), c0:c1] > 0.5
        rows_ = np.nonzero(cover.any(axis=1))[0]
        if len(rows_):
            head_top = y0 + int(rows_[0])
            weight = alpha[head_top:int(head_row), c0:c1].sum(axis=0)
            centre = c0 + float((weight * (np.arange(len(weight)) + 0.5)).sum() / max(weight.sum(), 1e-6))
            out["head_top_row"], out["head_centre_x"] = head_top, round(centre, 1)
            left = centre - side / 2.0
            upper = max(upper, head_top - TOP_MARGIN * side)
    left, upper, side = int(round(left)), int(round(upper)), int(round(side))
    hh = side / FRAME_SIDE
    out.update({"head_px": round(hh, 1), "side": side, "square": [left, upper, left + side, upper + side]})
    if upper < 0 or left < 0 or upper + side > h or left + side > wd:
        out["error"] = "the square leaves the frame"
        return out
    a = alpha[upper:upper + side, left:left + side]
    col = b[upper:upper + side, left:left + side]
    if space == "linear":
        col = linear_to_srgb(srgb_to_linear(col) / np.maximum(a[:, :, None], 1e-3)) * 255
    else:
        col = col / np.maximum(a[:, :, None], 1e-3)
    col[a < 0.004] = 0
    rgba = np.dstack([np.clip(col, 0, 255), a * 255]).round().astype(np.uint8)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    out.update({"square_covered": round(float((a > 0.5).mean()), 3),
                "square_border_covered": {"top": round(float((a[0] > 0.5).mean()), 3), "bottom": round(float((a[-1] > 0.5).mean()), 3),
                                          "left": round(float((a[:, 0] > 0.5).mean()), 3), "right": round(float((a[:, -1] > 0.5).mean()), 3)},
                "frames_used": [os.path.basename(blacks[-1][1]), os.path.basename(whites[0][1])]})
    if save:
        CUT = os.path.join(OUT, "cut-fit" if fit else "cut")
        os.makedirs(CUT, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in name)
        path = os.path.join(CUT, "%02d_%s.png" % (number, safe))
        Image.fromarray(rgba, "RGBA").save(path)
        out["file"] = path
    out["link_scale"] = link_scale
    return out


def main():
    shots = last_run()
    numbers = [int(x) for x in sys.argv[1:] if not x.startswith("--")] or sorted(shots)
    link_scale = None
    if 0 in shots:
        r = cut(0, shots[0], save=False)
        link_scale = r.get("link_scale")
    for number in numbers:
        if number not in shots:
            print(number, "no frames")
            continue
        r = cut(number, shots[number], link_scale, fit="--fit" in sys.argv)
        r.pop("link_scale", None)
        print(json.dumps(r))


if __name__ == "__main__":
    main()
