"""Faithful AINB load/save on top of the `ainb` package (0.2.17).

Two writer quirks of the package are fixed here so an unedited file is
rewritten byte for byte:
- Transition plugs pointed at the first *equal* transition entry; the game
  gives every plug its own entry. We index by identity instead.
- Node query lists were laid out in node order; the game uses its own order.
  We remember each node's original base index and keep that order (nodes
  added later go after the original ones).

Usage:
    a = ainbx.load(path_or_bytes)
    ... edit a.nodes etc. ...
    data = ainbx.dump(a)
"""
import struct

import ainb
import ainb.ainb as _ainb_mod
import ainb.node as _node_mod

_QBASE = {}  # id(node) -> (node, original base query index)


def _transition_write(self, writer, ctx):
    writer.write_s32(self.node_index)
    for i, t in enumerate(ctx.transitions):
        if t is self.transition:
            writer.write_u32(i)
            return
    writer.write_u32(ctx.transitions.index(self.transition))


_node_mod.TransitionPlug._write = _transition_write

_orig_build_context = _ainb_mod.AINB._build_context


def _build_context(self, ctx):
    _orig_build_context(self, ctx)
    nodes = self.nodes
    far = 1 << 30
    order = sorted(range(len(nodes)), key=lambda i: (_QBASE.get(id(nodes[i]), (None, far))[1], i))
    queries, bases = [], [0] * len(nodes)
    for i in order:
        n = nodes[i]
        if n.queries:
            bases[i] = len(queries)
            queries.extend(ctx.query_map[q] for q in n.queries)
    assert len(queries) == len(ctx.queries)
    ctx.queries[:] = queries
    ctx.query_base_indices[:] = bases


_ainb_mod.AINB._build_context = _build_context


def load(src):
    data = open(src, "rb").read() if isinstance(src, str) else bytes(src)
    a = ainb.AINB.from_binary(data)
    ver = struct.unpack_from("<I", data, 4)[0]
    if ver >= 0x405:
        ncmd = struct.unpack_from("<I", data, 0x0C)[0]
        base = 0x74 + 0x18 * ncmd
        for i, n in enumerate(a.nodes):
            if n.queries:
                _QBASE[id(n)] = (n, struct.unpack_from("<H", data, base + 0x3C * i + 0x24)[0])
    return a


def dump(a):
    return a.to_binary()


def from_dict(d, like=None, keep_query_order=True):
    """Build an AINB from an as_dict() dict. Pass the loaded original as `like`
    to keep its empty blackboard section and (unless keep_query_order is False,
    e.g. for a new file cloned from it) its query order for nodes that keep
    their index."""
    a = ainb.AINB.from_dict(d)
    if like is not None:
        # as_dict() drops an empty blackboard, but the file still has its header
        if a.blackboard is None and like.blackboard is not None and not d.get("Blackboard"):
            a.blackboard = like.blackboard
    if like is not None and keep_query_order:
        for i, (old, new) in enumerate(zip(like.nodes, a.nodes)):
            if id(old) in _QBASE and new.queries:
                _QBASE[id(new)] = (new, _QBASE[id(old)][1])
    return a
