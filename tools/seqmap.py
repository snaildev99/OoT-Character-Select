"""Compact text outline of the game's sequence graphs (Sequence/*.ainb), for reading.

    venv\\Scripts\\python.exe tools\\seqmap.py <out dir>            every graph -> <out dir>\\<name>.txt
    venv\\Scripts\\python.exe tools\\seqmap.py <out dir> <file>     one graph (a .ainb path)
    venv\\Scripts\\python.exe tools\\seqmap.py --find <text>        nodes whose name or values contain <text>

One line per node: index, type or node name, [root], inputs (name=value, or name<-node.output),
properties that are not the element defaults, then its children / transitions. Read only.
(Field swap session, 2026-11-10; uses tools\\ainbx.py.)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ainbx  # noqa: E402
import nxfmt  # noqa: E402

SEQ = os.path.join(nxfmt.ROMFS, "Sequence")
QUIET_PROPS = {"BusyPolicy": 0, "NumLoop": 0, "PlayPolicy": 0, "ResultPolicy": 0, "IsDelayJudge": False,
               "IsLoop": False, "IsUpdateNextInFrame": True, "ExecuteEveryFrame": False}


def _val(p):
    v = p.get("Default Value")
    if p.get("Node Index", -1) is not None and p.get("Node Index", -1) >= 0:
        return "<-%d.%s" % (p["Node Index"], p.get("Output Index", 0))
    if p.get("Sources"):
        return "<-[%s]" % ",".join("%d.%s" % (s.get("Node Index", -1), s.get("Output Index", 0)) for s in p["Sources"])
    flags = p.get("Flags") or []
    mark = "" if "Is Output" not in flags else "~"      # ~ = "left at the node type's default"
    return mark + repr(v)


def node_line(n):
    out = ["%4d" % n["Node Index"], n.get("Name") or n["Node Type"]]
    if n.get("Name") and n["Node Type"] != "UserDefined":
        out[-1] = "%s(%s)" % (n["Node Type"], n["Name"])
    if "Is Root Node" in n.get("Flags", []):
        out.append("[root]")
    other = [f for f in n.get("Flags", []) if f != "Is Root Node"]
    if other:
        out.append("[%s]" % ",".join(other))
    for kind, ps in (n.get("Parameters", {}).get("Inputs") or {}).items():
        for p in ps:
            out.append("%s=%s" % (p["Name"], _val(p)))
    for kind, ps in (n.get("Properties") or {}).items():
        for p in ps:
            if QUIET_PROPS.get(p["Name"], object()) != p.get("Default Value"):
                out.append("{%s=%r}" % (p["Name"], p.get("Default Value")))
    outs = [p["Name"] for ps in (n.get("Parameters", {}).get("Outputs") or {}).values() for p in ps]
    if outs:
        out.append("out(%s)" % ",".join(outs))
    for kind, plugs in (n.get("Plugs") or {}).items():
        if kind == "Child":
            out.append("-> " + " ".join(("%s:%d" % (c["Name"], c["Node Index"])) if c.get("Name") else str(c["Node Index"])
                                        for c in plugs))
        elif kind == "Transition":
            out.append("=> " + " ".join(str(c["Node Index"]) for c in plugs))
        elif kind == "Generic":
            pass        # the query links, already shown at the inputs
        else:
            out.append("%s: %s" % (kind, " ".join("%s:%d" % (c.get("Name", ""), c["Node Index"]) for c in plugs)))
    if n.get("Queries"):
        out.append("q%s" % n["Queries"])
    return "  ".join(out)


def outline(path):
    d = ainbx.load(open(path, "rb").read()).as_dict()
    lines = ["%s  (%d nodes)" % (d["Filename"], len(d["Nodes"]))]
    for c in d.get("Commands", []):
        lines.append("command %s -> %d" % (c["Name"], c["Root Node Index"]))
    bb = d.get("Blackboard") or {}
    for kind, ps in bb.items():
        for p in ps:
            lines.append("blackboard %s %s = %r" % (kind, p.get("Name"), p.get("Default Value", p.get("Init Value"))))
    for n in d["Nodes"]:
        lines.append(node_line(n))
    for m in d.get("Modules") or []:
        lines.append("module %s" % (m,))
    return lines


def main():
    if sys.argv[1] == "--find":
        text = sys.argv[2].lower()
        for f in sorted(os.listdir(SEQ)):
            if f.endswith(".ainb"):
                for line in outline(os.path.join(SEQ, f))[1:]:
                    if text in line.lower():
                        print("%-40s %s" % (f[:-5], line))
        return
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    files = [sys.argv[2]] if len(sys.argv) > 2 else [os.path.join(SEQ, f) for f in sorted(os.listdir(SEQ))
                                                     if f.endswith(".ainb")]
    for path in files:
        name = os.path.basename(path)[:-5]
        with open(os.path.join(out, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(outline(path)) + "\n")
    print(len(files), "graphs ->", out)


if __name__ == "__main__":
    main()
