"""The recoloured textures' recipes, run on the player's copy of the game's textures.

A copy of what mods\\customize.py does to a texture (hair_fn, cloth_fn, cloth_weight and
the colours), so that the player's side needs no part of our build. gamefiles\\make.py
checks, file by file, that these make the bytes the build made; a change in customize.py
that this copy does not follow stops that check.

A recipe is (source texture, new name, kind, parameters):
    ("hair", k)                       -> hair_fn(mean colour of the source, HAIR[k - 1])
    ("cloth", mode, bin, s, v, k)     -> cloth_fn(mode, centre of the bin, s, v, CLOTH[k - 1])
"""
import math

import numpy as np

import texrecolor

HAIR = [(0.125, 0.60, 0.74), (0.07, 0.62, 0.30), (0.07, 0.30, 0.09), (0.015, 0.85, 0.48), (0.066, 0.82, 0.70),
        (0.29, 0.75, 0.46), (0.60, 0.72, 0.50), (0.93, 0.45, 0.80), (0.10, 0.06, 0.88)]
CLOTH = [(0.30, 0.62, 0.36, 0.7), (0.995, 0.80, 0.48, 0.7), (0.61, 0.70, 0.46, 0.7), (0.77, 0.55, 0.42, 0.7),
         (0.14, 0.78, 0.74, 0.8), (0.60, 0.10, 0.09, 1.0), (0.11, 0.05, 0.86, 1.0)]
CLOTH_HUE_IN, CLOTH_HUE_OUT = 0.065, 0.115
CLOTH_BINS = 72
CLOTH_TAGS = "cdefgijklmnopqrstuvwxyzab"


def _smooth(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def cloth_weight(hsv, mode, centre):
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    if mode == "hue":
        dh = np.abs(((h - centre + 0.5) % 1.0) - 0.5)
        return np.clip((CLOTH_HUE_OUT - dh) / (CLOTH_HUE_OUT - CLOTH_HUE_IN), 0.0, 1.0) * _smooth(s, 0.10, 0.22) * _smooth(v, 0.04, 0.10)
    return np.maximum(1.0 - _smooth(s, 0.20, 0.34), 1.0 - _smooth(v, 0.06, 0.12))


def hair_fn(mean, target):
    hm, sm, vm = mean
    hue, sat, val = target
    gamma = math.log(val) / math.log(min(max(vm, 0.03), 0.97))

    def fn(rgb):
        hsv = texrecolor.rgb_to_hsv(rgb)
        dh = ((hsv[:, 0] - hm + 0.5) % 1.0) - 0.5
        out = np.stack([hue + 0.5 * dh, np.clip(sat * np.sqrt(hsv[:, 1] / max(sm, 0.05)), 0.0, 1.0),
                        np.power(np.clip(hsv[:, 2], 0.0, 1.0), gamma)], axis=1)
        return texrecolor.hsv_to_rgb(out)
    return fn


def cloth_fn(mode, centre, s_mean, v_mean, target):
    hue, sat, val, own = target
    vm = min(max(v_mean, 0.03), 0.97)
    goal = val ** own * vm ** (1.0 - own)
    gamma = max(math.log(goal) / math.log(vm), 0.3)

    def fn(rgb):
        hsv = texrecolor.rgb_to_hsv(rgb)
        w = cloth_weight(hsv, mode, centre)
        if mode == "hue":
            dh = ((hsv[:, 0] - centre + 0.5) % 1.0) - 0.5
            h2 = hue + 0.5 * dh
            s2 = np.clip(sat * np.sqrt(hsv[:, 1] / max(s_mean, 0.05)), 0.0, 1.0)
        else:
            h2 = np.full(len(hsv), hue)
            s2 = np.full(len(hsv), sat)
        v = np.clip(hsv[:, 2], 0.0, 1.0)
        v2 = v * (goal / vm) if goal < vm else np.power(v, gamma)
        new = texrecolor.hsv_to_rgb(np.stack([h2, s2, v2], axis=1))
        return rgb + (new - rgb) * w[:, None]
    return fn


def centre_of(b):
    return (b + 0.5) / CLOTH_BINS


def cloth_means(data, mode, centre):
    """(share, s_mean, v_mean) of a texture's part of the main colour, as customize reads them."""
    t = texrecolor.data_range(data)[0]
    px = texrecolor.decoded(data)
    step = max(1, int((px.shape[0] * px.shape[1] / 250000.0) ** 0.5))
    px = px[::step, ::step].reshape(-1, 4).astype(np.float64) / 255.0
    if t.comp[3] == 5 and (px[:, 3] > 0.5).any():
        px = px[px[:, 3] > 0.5]
    hsv = texrecolor.rgb_to_hsv(px[:, :3])
    w = cloth_weight(hsv, mode, centre)
    return float(w.mean()), float((w * hsv[:, 1]).sum() / w.sum()), float((w * hsv[:, 2]).sum() / w.sum())


def hue_order(data):
    """The hue bins of a texture's coloured texels, the most common first (where to look first)."""
    px = texrecolor.decoded(data)
    step = max(1, int((px.shape[0] * px.shape[1] / 250000.0) ** 0.5))
    px = px[::step, ::step].reshape(-1, 4).astype(np.float64) / 255.0
    hsv = texrecolor.rgb_to_hsv(px[:, :3])
    coloured = (hsv[:, 1] > 0.22) & (hsv[:, 2] > 0.10)
    hist = np.bincount(np.floor(hsv[coloured, 0] * CLOTH_BINS).astype(int) % CLOTH_BINS, minlength=CLOTH_BINS)
    sm = sum(np.roll(hist, k) for k in range(-2, 3))
    return [int(b) for b in np.argsort(-sm, kind="stable")]


def make(data, new, recipe):
    """The recoloured texture file (decompressed) from the source's decompressed bytes."""
    kind = recipe[0]
    if kind == "hair":
        fn = hair_fn(texrecolor.mean_colour(data), HAIR[recipe[1] - 1])
    elif kind == "cloth":
        _, mode, b, s, v, k = recipe
        fn = cloth_fn(mode, None if mode != "hue" else centre_of(b), s, v, CLOTH[k - 1])
    else:
        raise ValueError("unknown recipe " + repr(recipe))
    return texrecolor.recolour(data, new, fn)
