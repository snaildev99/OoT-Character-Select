"""Cut-out portraits of the "more characters" from the frames tools\\moreshots.py saved.

    venv\\Scripts\\python.exe -I tools\\morecutout.py --fit [character numbers]

tools\\npccutout.py as it is (the face-fitted square, snaildev's framing rules), with
  - our character list (mods\\more_characters.py: stored numbers 100 and up),
  - our folders: frames listed in build-more\\portraits\\shots.jsonl, pictures written to
    build-more\\portraits\\cut-fit\\<nnn>_<name>.png (three digits; the first 100 portraits in
    build-npc\\portraits are not touched),
  - the adapted model files where the stock file cannot be read (more_characters.ADAPT).
Its ADJUST table (snaildev's notes on the first 100) has no entry for our numbers.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "mods"))

import bfres  # noqa: E402
import more_characters as more  # noqa: E402
import npccutout  # noqa: E402
import nxfmt  # noqa: E402

OUT = os.path.join(ROOT, "build-more", "portraits")
LINK_HEAD = 0.25            # child Link: Head bone to the top of his cap, metres
MODEL_DIR = os.path.normcase(os.path.join(nxfmt.ROMFS, "Model"))


class _Files:
    """nxfmt for npccutout: `read` gives the adapted model where there is one."""

    def __getattr__(self, name):
        return getattr(nxfmt, name)

    @staticmethod
    def read(path):
        folder, name = os.path.split(os.path.normcase(path))
        if folder == MODEL_DIR:
            for stem in more.ADAPT:
                if name == os.path.normcase(stem + ".bfres.zs"):
                    return more.npc_data(stem)[1]
        return nxfmt.read(path)


def setup():
    more._capture.append(True)      # the capture build's list: adult Link is its last character
    more.apply()
    assert not any(n >= 100 for n in npccutout.ADJUST)
    npccutout.OUT = OUT
    npccutout.INDEX = os.path.join(OUT, "shots.jsonl")
    npccutout.CUT = os.path.join(OUT, "cut")
    npccutout.nxfmt = _Files()
    npccutout.eyes = eyes
    npccutout.backdrop = backdrop
    assert not any(n in npccutout.ADJUST for n in list(ADJUST) + list(SQUARE))
    npccutout.ADJUST.update(ADJUST)
    npccutout.ADJUST.update({n: (1.0, 0.0, 0.0) for n in SQUARE})       # so that `adjusted` is asked
    npccutout.adjusted = adjusted


# snaildev's notes on the first set (2026-11-13: "most fine"; these were off), as npccutout.ADJUST
# entries {number: (zoom, right, down)}: the square's side times zoom, then the CHARACTER
# moves right / down in the picture by that share of the side.
#   Dark Link: head a bit too far up and to the left (his cap was 0.17 of the side above the edge).
#   Twinrova: head too far up (the headgear may be cut) and slightly too far left.
#   Gibdo, ReDead: the top of the head was cut off (0.21 / 0.23 of the side above the edge):
#     now Link's margin above the top of the head.
#   Link (opening): the top of his head was slightly cut (0.08): Link's margin.
ADJUST = {
    100: (1.0, 0.07, 0.20),
    104: (1.0, 0.06, 0.15),
    108: (1.0, 0.0, 0.294),
    109: (1.0, 0.0, 0.319),
    114: (1.0, 0.0, 0.166),
}
# Squares set outright, (left, top, side) in a 2160-row frame.
#   Wolfos (snaildev: his head is way off to the left; the crop was too far right and probably
#     too close): by the silhouette of his frames he stands hunched, with a part that reaches
#     left to column 1557 in rows 740-920 (the muzzle); the fitted square was 2118..2681 x
#     424..987, inside his body. This one is centred left of the standing line on that part.
#   Link (adult): "his framing should match Dark Link exactly": Dark Link's square, taken
#     from his own cut (DARK_LINK), since both are the same model in the same pose.
SQUARE = {
    112: (1560.0, 330.0, 800.0),
    # second run (2026-11-13, after the page zoom for these three was widened):
    #   Koume & Kotake (boss): from the neck down the boss form's silhouette is the small
    #     form's moved 25 columns right; what it has above (rows 90-1000) pulled the fitted
    #     square 300 columns right of the face. It gets the small form's square, moved 25.
    #   Cow: she stands on four legs, 1,070 columns wide and 700 rows high; the fitted square
    #     lay inside her body. By the silhouette her head is the right end (the parts that
    #     stick up at columns 2670-3045 in rows 1210-1280): a square on that end, a guess.
    # snaildev's look, 2026-11-13: the witches "still a bit too zoomed in, her head too far in
    #   the top left"; the cow's head "way off to the left (cropped out), and slightly lower".
    #   Witches: so the head is the wide part at the top of the silhouette (rows 580-1000,
    #     columns 2050-2670), not where the Head bone put the face (row 1129): the square is
    #     centred on that part with Link's margin above it, and a quarter larger (was
    #     2096 / 2121, 825, 631). The boss form's is the same, 25 columns right.
    #   Cow: so her head is the LEFT end of the silhouette (columns 1971-2250, rows 1350-1700),
    #     and what sticks up at the right end is her rump and tail (was 2590, 1150, 460).
    102: (1970.0, 520.0, 800.0),
    103: (1995.0, 520.0, 800.0),
    113: (1900.0, 1240.0, 560.0),
}
DARK_LINK, ADULT_LINK = 100, more.ADULT_LINK_PORTRAIT
_squares = {}       # number -> the square its cut used


def adjusted(number, left, upper, side):
    if number == ADULT_LINK:
        assert DARK_LINK in _squares, "cut Dark Link first"
        out = _squares[DARK_LINK]
    elif number in SQUARE:
        out = SQUARE[number]
    else:
        zoom, right, down = npccutout.ADJUST.get(number, (1.0, 0.0, 0.0))
        new = side * zoom
        out = (left - (new - side) / 2.0 - right * new, upper - (new - side) / 2.0 - down * new, new)
    _squares[number] = out
    return out


# npccutout.backdrop reads the backdrop's colour from one patch left of the character. The
# cow (a four-legged body on Link's two-legged pose) reaches into it, and no frame counted
# as black or white (first run, 2026-11-13). Here the calmest of several patches is taken:
# npccutout's own first, so every frame it could read is read as before.
PATCHES = [(0.35, 0.65, 0.05, 0.30), (0.02, 0.12, 0.02, 0.14), (0.02, 0.12, 0.86, 0.98),
           (0.86, 0.98, 0.02, 0.14), (0.86, 0.98, 0.86, 0.98)]


def backdrop(a):
    h, w = a.shape[:2]
    best = None
    for y0, y1, x0, x1 in PATCHES:
        patch = a[int(h * y0):int(h * y1), int(w * x0):int(w * x1)].reshape(-1, 3)
        got = (patch.mean(axis=0), float(patch.std()))
        if got[1] < 0.5:
            return got
        if best is None or got[1] < best[1]:
            best = got
    return best


# The fitted square goes by the eyes (npccutout.eyes: the bones Eyeball_L / Eyeball_R). Ours
# call them otherwise (EYE_BONES), or have none (helmets, bandages, the cow): for those the
# eyes are put where Link's would be on a head of this model's size (Head bone to the top of
# the model against Link's 0.25 m), a first guess to be corrected from snaildev's notes
# (npccutout.ADJUST takes entries for our numbers).
EYE_BONES = {
    "Enemy_SkullKid.Enemy_SkullKid": ("Eyeball_Root_L", "Eyeball_Root_R"),
    "Enemy_Moblin.Enemy_Moblin": ("Eye_L", "Eye_R"),
    "Enemy_Moblin_Club.Enemy_Moblin_Club": ("Eye_L", "Eye_R"),
    "Enemy_Lizalfos.Enemy_Lizalfos": ("EyeBall_L", "EyeBall_R"),
}
_real_eyes = npccutout.eyes
_eyes = {}


def _world(m, name):
    names = [b.name for b in m.bones]
    chain, i = [], names.index(name)
    while i >= 0:
        chain.append(i)
        i = m.bones[i].parent
    xf = None
    for i in reversed(chain):
        b = m.bones[i]
        loc = bfres.Xf(bfres.euler_matrix(b.rotate[:3]), list(b.translate))
        xf = loc if xf is None else xf * loc
    return xf.t


def eyes(stem):
    if stem in _eyes:
        return _eyes[stem]
    got = _real_eyes(stem)
    if got is None and stem in dict(more.EXTRA):
        m = bfres.Fres(more.npc_data(stem)[1]).model()
        head = _world(m, "Head")
        if stem in EYE_BONES:
            a, b = (_world(m, n) for n in EYE_BONES[stem])
            got = (head[1], (a[1] + b[1]) / 2 - head[1], abs(a[0] - b[0]))
        else:
            link = _real_eyes("Player_Child.Player_Child")
            size = (npccutout.metrics(stem)[1] - head[1]) / LINK_HEAD
            got = (head[1], link[1] * size, link[2] * size)
    k = more.ZOOM.get(stem, 1.0)
    if got is not None and k != 1.0:
        # the page shows this one smaller (more.ZOOM): npccutout takes pixels per metre from
        # the first number and puts everybody's Head bone on one row; give it the height the
        # zoom was told, and the eyes as far below that as they really are
        got = (got[0] * k, got[1] - got[0] * (k - 1.0), got[2])
    _eyes[stem] = got
    return got


def main():
    setup()
    npccutout.ADJUST[ADULT_LINK] = (1.0, 0.0, 0.0)
    asked = [int(x) for x in sys.argv[1:] if not x.startswith("--")]
    if not asked or ADULT_LINK in asked:
        # his square is Dark Link's: have that one cut first (numbers are cut in the order given)
        rest = [n for n in (asked or sorted(npccutout.last_run())) if n not in (DARK_LINK, ADULT_LINK)]
        sys.argv[1:] = [x for x in sys.argv[1:] if x.startswith("--")] + [str(n) for n in [DARK_LINK] + rest + [ADULT_LINK]]
    npccutout.main()


if __name__ == "__main__":
    main()
