"""Replace one meeting-chain in the dense incumbent and rescore.

The production route brings each gate together with one SWAP chain, then never
revisits that choice. A different chain that leaves every logical qubit on the
same physical qubit can keep the rest of the route and change only depth, or
the SWAP count, of that block.

Chains considered, from the mapping just after the previous gate:
- shortest paths to a meeting edge, plus one extra hop (slack 1)
- one non-shortening SWAP on a front or upcoming qubit, then a shortest chain

Same-mapping splices are scored with starter_kit.scorer.score_summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from solution.solve import Hardware, Program
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph
from starter_kit.scorer import score_summary

PATH_LIMIT = 8


class Snap:
    __slots__ = ("l2p", "p2l", "ready", "swaps", "depth")

    def __init__(self, n: int, L: int) -> None:
        self.l2p = [-1] * L
        self.p2l = [-1] * n
        self.ready = [0] * n
        self.swaps = 0
        self.depth = 0

    def copy(self) -> Snap:
        ch = Snap.__new__(Snap)
        ch.l2p = self.l2p[:]
        ch.p2l = self.p2l[:]
        ch.ready = self.ready[:]
        ch.swaps = self.swaps
        ch.depth = self.depth
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


def load_incumbent(name: str) -> tuple[dict[int, int], list[tuple]]:
    state = json.loads((ROOT / "solution" / "autopilot_state.json").read_text())
    row = state["rows"][name]
    placement = {int(k): int(v) for k, v in row["placement"].items()}
    routed = [tuple(op) for op in row["routed"]]
    return placement, routed


def apply_path(snap: Snap, path: tuple[int, ...]) -> list[tuple[int, int]] | None:
    moves: list[tuple[int, int]] = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        snap.swap(u, v)
        moves.append((u, v))
    return moves


def meeting_chains(hw: Hardware, snap: Snap, a: int, b: int, slack: int) -> list[list[tuple[int, int]]]:
    dist = hw.dist
    pa, pb = snap.l2p[a], snap.l2p[b]
    d = dist[pa][pb]
    if d <= 1:
        return [[]]
    limit = d - 1 + slack
    da = dist[pa]
    db = dist[pb]
    out: list[list[tuple[int, int]]] = []
    for u, v in hw.arcs:
        if da[u] > limit or da[u] + db[v] > limit:
            continue
        if v not in hw.adjset[u]:
            continue
        for path_a in hw.paths(pa, u, PATH_LIMIT):
            for path_b in hw.paths(pb, v, PATH_LIMIT):
                trial = snap.copy()
                moves = apply_path(trial, path_a)
                if trial.l2p[b] != pb:
                    continue
                if moves is None:
                    continue
                moves = moves + (apply_path(trial, path_b) or [])
                qa, qb = trial.l2p[a], trial.l2p[b]
                if qb not in hw.adjset[qa]:
                    continue
                out.append(moves)
                if len(out) >= 48:
                    return out
    return out


def side_then_meet(hw: Hardware, snap: Snap, gates: list[tuple[int, int]], k: int) -> list[list[tuple[int, int]]]:
    """One SWAP that does not shorten the front gate, then a shortest meeting chain."""
    a, b = gates[k]
    pa, pb = snap.l2p[a], snap.l2p[b]
    dist = hw.dist
    d = dist[pa][pb]
    extended = {pa, pb}
    for j in range(k, min(len(gates), k + 4)):
        for q in gates[j]:
            phys = snap.l2p[q]
            if phys >= 0:
                extended.add(phys)
    out: list[list[tuple[int, int]]] = []
    seen: set[tuple[int, int]] = set()
    for u, v in hw.arcs:
        if u > v:
            continue
        if u not in extended and v not in extended:
            continue
        na = v if pa == u else u if pa == v else pa
        nb = v if pb == u else u if pb == v else pb
        if dist[na][nb] < d:
            continue
        edge = (u, v) if u < v else (v, u)
        if edge in seen:
            continue
        seen.add(edge)
        trial = snap.copy()
        trial.swap(u, v)
        for chain in meeting_chains(hw, trial, a, b, slack=0):
            out.append([(u, v)] + chain)
            if len(out) >= 36:
                return out
    return out


def main() -> None:
    name = "dense_random"
    program = BENCHMARKS[name]
    graph = build_hardware_graph()
    prog = Program(program)
    hw = Hardware(graph)
    placement, routed = load_incumbent(name)
    base = score_summary(program, graph, placement, routed)
    print(
        f"incumbent valid={base['valid']} score={base['score']} swaps={base['swap_count']} depth={base['depth']}",
        flush=True,
    )

    labels = hw.labels
    node = Snap(hw.n, prog.L)
    for q, logical in enumerate(prog.logicals):
        node.l2p[q] = hw.index[placement[logical]]
        node.p2l[node.l2p[q]] = q
    blocks = []
    prefix_len: list[int] = []
    pending = []
    consumed = 0
    gate_i = 0
    for op in routed:
        if op[0] == "SWAP":
            pending.append(op)
        elif op[0] == "2Q":
            a, b = prog.gates[gate_i]
            snap = node.copy()
            for sop in pending:
                u, v = hw.index[sop[1]], hw.index[sop[2]]
                node.swap(u, v)
            pa, pb = node.l2p[a], node.l2p[b]
            if {labels[pa], labels[pb]} != {op[1], op[2]}:
                raise RuntimeError(f"diverged at gate {gate_i}: {op}")
            blocks.append((snap, pending, op, consumed))
            node.touch(pa, pb)
            consumed += len(pending) + 1
            pending = []
            gate_i += 1
        else:
            raise RuntimeError(op)
    if pending or gate_i != len(prog.gates):
        raise RuntimeError(f"tail pending={len(pending)} gates={gate_i}")

    out_path = Path(__file__).resolve().parent / "alt_chains_results.jsonl"
    best = float(base["score"])
    scored = 0
    same_map = 0
    for k, (snap, block, gate_op, start) in enumerate(blocks):
        a, b = prog.gates[k]
        chains = meeting_chains(hw, snap, a, b, slack=0)
        chains.extend(meeting_chains(hw, snap, a, b, slack=1))
        chains.extend(side_then_meet(hw, snap, prog.gates, k))
        orig_swaps = len(block)
        unique: set[tuple] = set()
        improved_here = 0
        for chain in chains:
            sig = tuple(chain)
            if sig in unique:
                continue
            unique.add(sig)
            trial = snap.copy()
            for u, v in chain:
                trial.swap(u, v)
            pa, pb = trial.l2p[a], trial.l2p[b]
            if pb not in hw.adjset[pa]:
                continue
            trial.touch(pa, pb)
            # Compare logical placement after the gate with the incumbent's.
            after = blocks[k + 1][0] if k + 1 < len(blocks) else node
            if trial.l2p != after.l2p:
                continue
            same_map += 1
            new_swaps = [("SWAP", labels[u], labels[v]) for u, v in chain]
            new_gate = ("2Q", labels[pa], labels[pb])
            full = routed[:start] + new_swaps + [new_gate] + routed[start + orig_swaps + 1 :]
            official = score_summary(program, graph, placement, full)
            scored += 1
            if not official["valid"]:
                continue
            if official["score"] < best:
                best = official["score"]
                improved_here += 1
                row = {
                    "benchmark": name,
                    "gate": k,
                    "orig_block": orig_swaps,
                    "new_block": len(chain),
                    "valid": True,
                    "swap_count": official["swap_count"],
                    "depth": official["depth"],
                    "score": official["score"],
                }
                print("IMPROVED", json.dumps(row), flush=True)
                with out_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
        print(
            f"gate {k}: orig_block={orig_swaps} candidates={len(unique)} same_map_total={same_map} best={best}",
            flush=True,
        )
    print(f"done scored_same_map={scored} best={best}", flush=True)


def pair_search() -> None:
    """Replace two consecutive meeting chains, keeping the mapping after the second gate."""
    name = "dense_random"
    program = BENCHMARKS[name]
    graph = build_hardware_graph()
    prog = Program(program)
    hw = Hardware(graph)
    placement, routed = load_incumbent(name)
    labels = hw.labels
    node = Snap(hw.n, prog.L)
    for q, logical in enumerate(prog.logicals):
        node.l2p[q] = hw.index[placement[logical]]
        node.p2l[node.l2p[q]] = q
    blocks = []
    pending: list[tuple] = []
    consumed = 0
    gate_i = 0
    for op in routed:
        if op[0] == "SWAP":
            pending.append(op)
        elif op[0] == "2Q":
            a, b = prog.gates[gate_i]
            snap = node.copy()
            for sop in pending:
                node.swap(hw.index[sop[1]], hw.index[sop[2]])
            pa, pb = node.l2p[a], node.l2p[b]
            if {labels[pa], labels[pb]} != {op[1], op[2]}:
                raise RuntimeError(f"diverged at gate {gate_i}")
            blocks.append((snap, pending, consumed))
            node.touch(pa, pb)
            consumed += len(pending) + 1
            pending = []
            gate_i += 1
        else:
            raise RuntimeError(op)
    out_path = Path(__file__).resolve().parent / "alt_chains_results.jsonl"
    best = 35.5
    scored = 0
    for k in range(len(blocks) - 1):
        snap, block0, start = blocks[k]
        _, block1, start1 = blocks[k + 1]
        end = start1 + len(block1) + 1
        after = blocks[k + 2][0] if k + 2 < len(blocks) else node
        first = meeting_chains(hw, snap, *prog.gates[k], slack=0)[:16]
        first.extend(meeting_chains(hw, snap, *prog.gates[k], slack=1)[:8])
        seen: set[tuple] = set()
        for chain0 in first:
            mid = snap.copy()
            a0, b0 = prog.gates[k]
            for u, v in chain0:
                mid.swap(u, v)
            pa, pb = mid.l2p[a0], mid.l2p[b0]
            if pb not in hw.adjset[pa]:
                continue
            gate0 = ("2Q", labels[pa], labels[pb])
            swaps0 = [("SWAP", labels[u], labels[v]) for u, v in chain0]
            mid.touch(pa, pb)
            second = meeting_chains(hw, mid, *prog.gates[k + 1], slack=0)[:12]
            for chain1 in second:
                sig = (tuple(chain0), tuple(chain1))
                if sig in seen:
                    continue
                seen.add(sig)
                trial = mid.copy()
                a1, b1 = prog.gates[k + 1]
                for u, v in chain1:
                    trial.swap(u, v)
                qa, qb = trial.l2p[a1], trial.l2p[b1]
                if qb not in hw.adjset[qa]:
                    continue
                trial.touch(qa, qb)
                if trial.l2p != after.l2p:
                    continue
                swaps1 = [("SWAP", labels[u], labels[v]) for u, v in chain1]
                gate1 = ("2Q", labels[qa], labels[qb])
                full = routed[:start] + swaps0 + [gate0] + swaps1 + [gate1] + routed[end:]
                official = score_summary(program, graph, placement, full)
                scored += 1
                if official["valid"] and official["score"] < best:
                    best = official["score"]
                    row = {
                        "benchmark": name,
                        "method": "pair_chain",
                        "gate": k,
                        "valid": True,
                        "swap_count": official["swap_count"],
                        "depth": official["depth"],
                        "score": official["score"],
                    }
                    print("IMPROVED", json.dumps(row), flush=True)
                    with out_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row) + "\n")
        print(f"pair {k}: scored={scored} best={best}", flush=True)
    print(f"pair done scored={scored} best={best}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pair":
        pair_search()
    else:
        main()
