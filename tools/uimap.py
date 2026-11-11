"""Map of every UI layout: research\\ui-map\\<archive>\\<layout>.txt plus index.txt and parts.json.

    venv\\Scripts\\python.exe tools\\uimap.py

Per layout: pane tree (index, name, type, position, size, scale, visible, alpha; part ->
layout and its initial animation frames; text font, size and message source; picture
textures), animations (frames, targets as pane + property) and where parts are used.
"""
import json
import os
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import layout  # noqa: E402
import nxfmt  # noqa: E402
import uiedit  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "ui-map")
ARCHIVES = ["Common", "Title", "ZeldaNotes", "Boot"]
PROPS = {0: "scale", 1: "translate", 2: "visible", 3: "prop3", 4: "alpha", 0x43: "part-anim frame",
         0x44: "part-anim enable"}


def prop_name(k):
    """The property a target key names: 9 bits, (b2 >> 6 & 1) | b3 << 1 | (b4 & 1) << 8, so
    the same b3 with b2 = 0x6x and with b2 = 0x2x are different properties."""
    g = k[3]
    if k[4] == 1:
        return PROPS.get(g, "part %#x" % g)
    if not k[2] & 0x40 and g < 27:
        return {2: "size"}.get(g, "prop %d (b2 0x20)" % g)
    if g in PROPS:
        return PROPS[g]
    return ("material %#x" if g < 57 else "text %#x") % g


def textures(ed, v):
    if v.material == 0xFFFF:
        return []
    f, c = ed._pass_runs()[v.material]
    out = []
    for p in ed.passes()[f:f + c]:
        for t in p.ptr[0].items:
            nm = t.ptr.get(8)
            if isinstance(nm, layout.Pool) and nm.text:
                out.append(nm.text)
    return out


def describe(name, data, used_by):
    lay = layout.Layout(data)
    ed = uiedit.Editor(lay)
    r = lay.root
    parts = {it[0].text: it[1] for it in r.ptr[0xC0].items}
    texts = {it[0].text: it[1] for it in r.ptr[0x80].items}
    alpha = [struct.unpack_from("<5f", x.buf)[4] for x in r.ptr[0x1E8].items]
    lines = ["%s  (%d panes, %d animations; used by: %s)" % (name, len(ed.panes), len(r.ptr[0x140].items),
                                                              ", ".join(sorted(used_by)) or "a screen")]
    depth = {-1: -1}
    summary = {"panes": len(ed.panes), "texts": [], "parts": [], "anims": r.ptr[0x140].names(), "textures": []}
    for i in range(len(ed.panes)):
        v = ed.pane(i)
        depth[i] = depth[v.parent] + 1
        bits = ["%-7s" % uiedit.TYPES[v.type], "pos=(%g,%g)" % v.pos, "size=(%g,%g)" % v.size]
        if v.scale != (1.0, 1.0):
            bits.append("scale=(%g,%g)" % v.scale)
        if v.rotation:
            bits.append("rot=%.3g" % v.rotation)
        a = alpha[v.alpha_index] if v.alpha_index < len(alpha) else None
        if a is not None and a != 1.0:
            bits.append("alpha=%.3g" % a)
        if not v.visible:
            bits.append("HIDDEN")
        if v.type == 6:
            rec = parts[v.name]
            anims = " ".join("%s=%g" % (e.ptr[0].text, struct.unpack_from("<f", e.buf, 8)[0])
                             for e in rec.ptr[0x28].items)
            bits.append("-> %s %s" % (rec.ptr[0].text, anims))
            summary["parts"].append(rec.ptr[0].text)
        if v.type == 2:
            t = texts[v.name]
            ref = t.ptr[0x18].text
            src = {"": "msbt", "*": "msbt"}.get(ref, "code" if ref.startswith("@") else "copy " + ref[1:])
            bits.append("font=%s %g text from %s" % (t.ptr[0].text, t.get("<f", 8)[0], src))
            summary["texts"].append([v.name, src])
        tex = textures(ed, v)
        if tex:
            bits.append("tex=" + ",".join(tex))
            summary["textures"] += tex
        lines.append("%3d %s%-28s %s" % (i, "  " * depth[i], v.name, " ".join(bits)))
    names = ed.names()
    plist = [x.text for x in r.ptr[0x1F8].ptr[0].items]
    lines.append("")
    lines.append("animations:")
    for it in r.ptr[0x140].items:
        an, anim = it[0].text, it[1]
        tg = []
        for t in anim.ptr[0].ptr[8].items:
            k = t.ptr[0]
            pn = names[uiedit.key_pane(k)]
            what = prop_name(k)
            if k[4] == 1 and k[0] < len(plist):
                what += " " + plist[k[0]]
            tg.append("%s %s" % (pn, what))
        lines.append("  %-24s %3d frames: %s" % (an, anim.get("<I", 0x28)[0], "; ".join(tg)))
    summary["textures"] = sorted(set(summary["textures"]))
    return "\n".join(lines) + "\n", summary


def main():
    os.makedirs(OUT, exist_ok=True)
    all_layouts = {}
    for arc in ARCHIVES:
        raw = nxfmt.read(os.path.join(nxfmt.ROMFS, "UI/Layout2Archive/%s.Product.100.Nin_Ounce_NVN2.blarc.zs" % arc))
        for n, d in nxfmt.sarc_files(raw).items():
            all_layouts[n[:-5]] = (arc, d)
    used_by = defaultdict(set)
    for n, (arc, d) in all_layouts.items():
        for it in layout.Layout(d).root.ptr[0xC0].items:
            used_by[it[1].ptr[0].text].add(n)
    index, js = [], {}
    for n in sorted(all_layouts):
        arc, d = all_layouts[n]
        text, summ = describe(n, d, used_by[n])
        os.makedirs(os.path.join(OUT, arc), exist_ok=True)
        with open(os.path.join(OUT, arc, n + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)
        summ.update(archive=arc, used_by=sorted(used_by[n]))
        js[n] = summ
        index.append("%-32s %-10s %4d panes  states: %s" % (n, arc, summ["panes"], " ".join(summ["anims"])[:110]))
    with open(os.path.join(OUT, "index.txt"), "w", encoding="utf-8") as f:
        f.write("UI layouts (tools\\uimap.py). Pa* = parts (reusable pieces), others = screens.\n\n" + "\n".join(index) + "\n")
    with open(os.path.join(OUT, "parts.json"), "w", encoding="utf-8") as f:
        json.dump(js, f, indent=1)
    print("wrote", len(all_layouts), "layout maps to", OUT)


if __name__ == "__main__":
    main()
