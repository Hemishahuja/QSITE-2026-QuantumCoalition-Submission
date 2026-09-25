"""Shared helpers for research experiments (not part of the submission)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import networkx as nx  # noqa: E402

from starter_kit.benchmarks import BENCHMARKS  # noqa: E402
from starter_kit.hardware import build_hardware_graph  # noqa: E402
from starter_kit.scorer import score_summary  # noqa: E402

HERE = Path(__file__).resolve().parent
GRAPH = build_hardware_graph()
N = GRAPH.number_of_nodes()
EDGES = sorted(tuple(sorted(e)) for e in GRAPH.edges)
DIST = dict(nx.all_pairs_shortest_path_length(GRAPH))
ADJ = {u: sorted(GRAPH.neighbors(u)) for u in GRAPH.nodes}


def gates_of(program):
    return [(op[1], op[2]) for op in program if op[0] == "2Q"]


def critical_path(gates):
    ready = {}
    depth = 0
    for a, b in gates:
        t = max(ready.get(a, 0), ready.get(b, 0)) + 1
        ready[a] = ready[b] = t
        depth = max(depth, t)
    return depth


def official(program, placement, routed):
    r = score_summary(program, GRAPH, placement, routed)
    return {k: r[k] for k in ("valid", "message", "swap_count", "depth", "score")}


def save_json(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=1))


def load_json(name):
    return json.loads((HERE / name).read_text())
