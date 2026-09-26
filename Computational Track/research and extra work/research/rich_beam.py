"""Single-SWAP beam for strict program order.

The production router only inserts a SWAP chain that shortens the front gate, and
it never swaps once that gate is already adjacent. This search also allows a
single SWAP that touches the front qubits or the next few gates without
shortening the front gate, including while that gate is already adjacent.

Every survivor is scored with starter_kit.scorer.score_summary.
This file does not change solve.py.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from solution.solve import Hardware, Program
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph
from starter_kit.scorer import score_summary

EDGES: list[tuple[int, int]] = []
LOOKAHEAD = 8


class Node:
    __slots__ = (
        "l2p",
        "p2l",
        "ready",
        "swaps",
        "depth",
        "wander",
        "k",
        "look",
        "kind",
        "parent",
        "op",
    )

    def __init__(self, n: int, L: int) -> None:
        self.l2p = [-1] * L
        self.p2l = [-1] * n
        self.ready = [0] * n
        self.swaps = 0
        self.depth = 0
        self.wander = 0
        self.k = 0
        self.look = 0
        self.kind = "progress"
        self.parent: Node | None = None
        self.op: tuple | None = None

    def copy(self) -> Node:
        ch = Node.__new__(Node)
        ch.l2p = self.l2p[:]
        ch.p2l = self.p2l[:]
        ch.ready = self.ready[:]
        ch.swaps = self.swaps
        ch.depth = self.depth
        ch.wander = self.wander
        ch.k = self.k
        ch.look = self.look
        ch.kind = self.kind
        ch.parent = self
        ch.op = None
        return ch

    def touch(self, u: int, v: int) -> None:
        layer = max(self.ready[u], self.ready[v]) + 1
        self.ready[u] = self.ready[v] = layer
        if layer > self.depth:
            self.depth = layer

    def swap(self, u: int, v: int) -> None:
        lu, lv = self.p2l[u], self.p2l[v]
        self.p2l[u], self.p2l[v] = lv, lu
        if lu >= 0:
            self.l2p[lu] = v
        if lv >= 0:
            self.l2p[lv] = u
        self.touch(u, v)
        self.swaps += 1

    def place(self, logical: int, phys: int) -> None:
        self.l2p[logical] = phys
        self.p2l[phys] = logical

    def cost(self) -> float:
        return self.swaps + 0.5 * self.depth


def load_incumbent(name: str) -> tuple[dict[int, int], list[tuple]]:
    state = json.loads((ROOT / "solution" / "autopilot_state.json").read_text())
    row = state["rows"][name]
    placement = {int(k): int(v) for k, v in row["placement"].items()}
    routed = [tuple(op) for op in row["routed"]]
    return placement, routed


def lookahead(hw: Hardware, gates: list[tuple[int, int]], node: Node, k: int) -> int:
    dist = hw.dist
    total = 0
    end = min(len(gates), k + LOOKAHEAD)
    for j in range(k, end):
        a, b = gates[j]
        pa, pb = node.l2p[a], node.l2p[b]
        if pa >= 0 and pb >= 0:
            total += max(0, dist[pa][pb] - 1)
    return total


def seed_node(prog: Program, hw: Hardware, placement: dict[int, int]) -> Node:
    node = Node(hw.n, prog.L)
    for q, logical in enumerate(prog.logicals):
        node.place(q, hw.index[placement[logical]])
    node.look = lookahead(hw, prog.gates, node, 0)
    return node


def replay(
    prog: Program,
    hw: Hardware,
    placement: dict[int, int],
    routed: list[tuple],
    gate_limit: int,
) -> tuple[Node, list[tuple]]:
    node = seed_node(prog, hw, placement)
    prefix: list[tuple] = []
    gate_i = 0
    for op in routed:
        if gate_i >= gate_limit:
            break
        if op[0] == "SWAP":
            u, v = hw.index[op[1]], hw.index[op[2]]
            node.swap(u, v)
            prefix.append(op)
        elif op[0] == "2Q":
            a, b = prog.gates[gate_i]
            pa, pb = node.l2p[a], node.l2p[b]
            if {hw.labels[pa], hw.labels[pb]} != {op[1], op[2]}:
                raise RuntimeError(f"replay diverged at gate {gate_i}: {op}")
            node.touch(pa, pb)
            prefix.append(op)
            gate_i += 1
        else:
            raise RuntimeError(f"unexpected op {op}")
    if gate_i != gate_limit:
        raise RuntimeError(f"prefix ended at gate {gate_i}, wanted {gate_limit}")
    node.wander = 0
    node.k = gate_limit
    node.look = lookahead(hw, prog.gates, node, gate_limit)
    node.parent = None
    node.op = None
    node.kind = "progress"
    return node, prefix


def route_of(node: Node) -> list[tuple]:
    ops: list[tuple] = []
    cur: Node | None = node
    while cur is not None and cur.op is not None:
        ops.append(cur.op)
        cur = cur.parent
    ops.reverse()
    return ops


def _moved(pa: int, pb: int, u: int, v: int) -> tuple[int, int]:
    na = v if pa == u else u if pa == v else pa
    nb = v if pb == u else u if pb == v else pb
    return na, nb


def expand(hw: Hardware, gates: list[tuple[int, int]], node: Node, labels: list[int]) -> list[Node]:
    k = node.k
    a, b = gates[k]
    pa, pb = node.l2p[a], node.l2p[b]
    dist = hw.dist
    d = dist[pa][pb]
    out: list[Node] = []
    if d == 1:
        ch = node.copy()
        ch.touch(pa, pb)
        ch.wander = 0
        ch.k = k + 1
        ch.look = lookahead(hw, gates, ch, ch.k)
        ch.kind = "progress"
        ch.op = ("2Q", labels[pa], labels[pb])
        out.append(ch)
        if node.wander >= 2:
            return out

    front = {pa, pb}
    extended = set(front)
    for j in range(k, min(len(gates), k + 4)):
        for q in gates[j]:
            phys = node.l2p[q]
            if phys >= 0:
                extended.add(phys)

    progress: list[tuple[int, int, int, int]] = []
    side: list[tuple[int, int, int, int]] = []
    allow_side = node.wander < 2
    for u, v in EDGES:
        hits = u in front or v in front
        if not hits and (u not in extended and v not in extended):
            continue
        na, nb = _moved(pa, pb, u, v)
        nd = dist[na][nb]
        idle = node.ready[u] + node.ready[v]
        if nd < d:
            progress.append((nd, idle, u, v))
        elif allow_side and (hits or nd <= d):
            side.append((nd, idle, u, v))
    progress.sort()
    side.sort()
    chosen = progress + side[:6]
    seen: set[tuple[int, int]] = set()
    for nd, _idle, u, v in chosen:
        if (u, v) in seen:
            continue
        seen.add((u, v))
        ch = node.copy()
        ch.swap(u, v)
        shortened = nd < d
        ch.wander = 0 if shortened else node.wander + 1
        ch.k = k
        ch.look = lookahead(hw, gates, ch, k)
        ch.kind = "progress" if shortened else "side"
        ch.op = ("SWAP", labels[u], labels[v])
        out.append(ch)
    return out


def _select(live: list[Node], width: int) -> list[Node]:
    if len(live) <= width:
        return live
    live.sort(key=lambda n: (n.cost(), n.look, sum(n.ready)))
    side_budget = max(8, width // 4)
    progress_budget = width - side_budget
    progress = [n for n in live if n.kind == "progress"]
    side = [n for n in live if n.kind != "progress"]
    kept = progress[:progress_budget] + side[:side_budget]
    if len(kept) < width:
        chosen = set(map(id, kept))
        for n in live:
            if id(n) not in chosen:
                kept.append(n)
                if len(kept) >= width:
                    break
    return kept


def beam(
    hw: Hardware,
    gates: list[tuple[int, int]],
    start: Node,
    width: int,
    deadline: float,
    score_cap: float,
) -> Node | None:
    labels = hw.labels
    G = len(gates)
    live = [start]
    finished: list[Node] = []
    guard = 0
    limit = (G - start.k) * 8 + 4
    while live and guard < limit:
        guard += 1
        if time.perf_counter() > deadline:
            break
        pool: dict[tuple, list[Node]] = {}
        best_k = G
        for node in live:
            if node.cost() >= score_cap:
                continue
            if node.k >= G:
                finished.append(node)
                continue
            if node.k < best_k:
                best_k = node.k
            for ch in expand(hw, gates, node, labels):
                if ch.cost() >= score_cap:
                    continue
                if ch.k >= G:
                    finished.append(ch)
                    continue
                key = (ch.k, tuple(ch.l2p))
                pool.setdefault(key, []).append(ch)
        if guard == 1 or guard % 10 == 0:
            print(
                f"    layer {guard} best_k={best_k} pool={len(pool)} finished={len(finished)}",
                flush=True,
            )
        live = []
        for group in pool.values():
            group.sort(key=lambda n: (n.cost(), n.look, sum(n.ready)))
            kept = group[:2]
            idlest = min(group, key=lambda n: sum(n.ready))
            if idlest not in kept:
                kept.append(idlest)
            live.extend(kept)
        live = _select(live, width)
    if not finished:
        return None
    return min(finished, key=lambda n: (n.cost(), n.swaps))


def greedy_route(prog: Program, hw: Hardware, placement: dict[int, int]) -> list[tuple]:
    """Distance-reducing front SWAPs only. A valid route here means the simulator matches the scorer."""
    node = seed_node(prog, hw, placement)
    labels = hw.labels
    ops: list[tuple] = []
    for a, b in prog.gates:
        for _ in range(40):
            pa, pb = node.l2p[a], node.l2p[b]
            d = hw.dist[pa][pb]
            if d == 1:
                node.touch(pa, pb)
                ops.append(("2Q", labels[pa], labels[pb]))
                break
            best: tuple[int, int, int, int] | None = None
            for u, v in EDGES:
                if u not in (pa, pb) and v not in (pa, pb):
                    continue
                na, nb = _moved(pa, pb, u, v)
                nd = hw.dist[na][nb]
                if nd >= d:
                    continue
                cand = (nd, node.ready[u] + node.ready[v], u, v)
                if best is None or cand < best:
                    best = cand
            if best is None:
                raise RuntimeError(f"greedy stuck at gate {node.k} distance {d}")
            _nd, _idle, u, v = best
            node.swap(u, v)
            ops.append(("SWAP", labels[u], labels[v]))
        else:
            raise RuntimeError("greedy exceeded 40 SWAPs on one gate")
    return ops


def _log(name: str, row: dict) -> None:
    out_path = Path(__file__).resolve().parent / "rich_beam_results.jsonl"
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    print(
        f"  {name}: valid={row['valid']} score={row['score']} "
        f"swaps={row['swap_count']} depth={row['depth']} msg={row['message']}",
        flush=True,
    )


def run_one(name: str, cuts: list[int], width: int, seconds: float, score_cap: float) -> float | None:
    program = BENCHMARKS[name]
    graph = build_hardware_graph()
    prog = Program(program)
    hw = Hardware(graph)
    placement, routed = load_incumbent(name)
    base = score_summary(program, graph, placement, routed)
    print(
        f"{name} incumbent valid={base['valid']} score={base['score']} "
        f"swaps={base['swap_count']} depth={base['depth']}",
        flush=True,
    )
    greedy = greedy_route(prog, hw, placement)
    gscore = score_summary(program, graph, placement, greedy)
    _log(
        "greedy",
        {
            "benchmark": name,
            "method": "greedy",
            "valid": gscore["valid"],
            "message": gscore["message"],
            "swap_count": gscore["swap_count"],
            "depth": gscore["depth"],
            "score": gscore["score"],
        },
    )
    deadline = time.perf_counter() + seconds
    best: float | None = None
    for cut in cuts:
        if time.perf_counter() > deadline:
            print(f"  cut {cut}: deadline", flush=True)
            break
        start, prefix = replay(prog, hw, placement, routed, cut)
        print(
            f"  cut {cut}: prefix swaps={start.swaps} depth={start.depth} cost={start.cost()} look={start.look}",
            flush=True,
        )
        found = beam(hw, prog.gates, start, width, deadline, score_cap)
        if found is None:
            print(f"  cut {cut}: no complete route under cap {score_cap}", flush=True)
            continue
        full = prefix + route_of(found)
        official = score_summary(program, graph, placement, full)
        row = {
            "benchmark": name,
            "method": "rich_beam",
            "cut": cut,
            "width": width,
            "valid": official["valid"],
            "message": official["message"],
            "swap_count": official["swap_count"],
            "depth": official["depth"],
            "score": official["score"],
        }
        _log(f"cut {cut}", row)
        if official["valid"] and (best is None or official["score"] < best):
            best = official["score"]
    return best


def main() -> None:
    global EDGES
    hw = Hardware(build_hardware_graph())
    EDGES = [(u, v) for u in range(hw.n) for v in hw.adj[u] if u < v]
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("qaoa", "all"):
        run_one("qaoa_random", [0, 8], width=128, seconds=60, score_cap=12.0)
    if which in ("dense", "all"):
        # Cap 36 confirms the beam can finish the known tail. Cap 35.5 keeps only a strict improvement.
        run_one("dense_random", [32], width=160, seconds=90, score_cap=36.0)
        run_one("dense_random", [32, 24, 16, 8, 0], width=160, seconds=600, score_cap=35.5)


if __name__ == "__main__":
    main()
