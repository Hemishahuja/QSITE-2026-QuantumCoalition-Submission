"""Gate-structured beam that keeps the dense incumbent in the beam.

Each step finishes the front gate. Candidates are shortest meeting SWAPs plus
SWAPs that do not shorten that gate (a sideways or pre-pay move) followed by a
short meeting. The incumbent state after every gate is reinserted, so a later
deviation is still compared against the known route. A finished route is kept
only when its partial score stays under 35.5. Survivors are checked with
starter_kit.scorer.score_summary.
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


class Node:
    __slots__ = ("l2p", "p2l", "ready", "swaps", "depth", "k", "look", "kind", "parent", "ops")

    def __init__(self, n: int, L: int) -> None:
        self.l2p = [-1] * L
        self.p2l = [-1] * n
        self.ready = [0] * n
        self.swaps = 0
        self.depth = 0
        self.k = 0
        self.look = 0
        self.kind = "progress"
        self.parent: Node | None = None
        self.ops: tuple | None = None

    def copy(self) -> Node:
        ch = Node.__new__(Node)
        ch.l2p = self.l2p[:]
        ch.p2l = self.p2l[:]
        ch.ready = self.ready[:]
        ch.swaps = self.swaps
        ch.depth = self.depth
        ch.k = self.k
        ch.look = self.look
        ch.kind = self.kind
        ch.parent = self
        ch.ops = None
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

    def cost(self) -> float:
        return self.swaps + 0.5 * self.depth


def look_of(hw: Hardware, gates: list[tuple[int, int]], node: Node) -> int:
    total = 0
    dist = hw.dist
    for j in range(node.k, min(len(gates), node.k + 8)):
        a, b = gates[j]
        pa, pb = node.l2p[a], node.l2p[b]
        if pa >= 0 and pb >= 0:
            total += max(0, dist[pa][pb] - 1)
    return total


def replay_pins(prog: Program, hw: Hardware, placement: dict[int, int], routed: list[tuple]) -> list[Node]:
    node = Node(hw.n, prog.L)
    for q, logical in enumerate(prog.logicals):
        phys = hw.index[placement[logical]]
        node.l2p[q] = phys
        node.p2l[phys] = q
    pins: list[Node] = []
    gate_i = 0
    for op in routed:
        if op[0] == "SWAP":
            node.swap(hw.index[op[1]], hw.index[op[2]])
        elif op[0] == "2Q":
            a, b = prog.gates[gate_i]
            pa, pb = node.l2p[a], node.l2p[b]
            if {hw.labels[pa], hw.labels[pb]} != {op[1], op[2]}:
                raise RuntimeError(f"pin diverged at {gate_i}")
            node.touch(pa, pb)
            gate_i += 1
            node.k = gate_i
            snap = node.copy()
            snap.parent = None
            snap.ops = None
            snap.kind = "pin"
            snap.look = look_of(hw, prog.gates, snap)
            pins.append(snap)
        else:
            raise RuntimeError(op)
    if gate_i != len(prog.gates):
        raise RuntimeError("pin replay missed gates")
    return pins


def _moved(pa: int, pb: int, u: int, v: int) -> tuple[int, int]:
    na = v if pa == u else u if pa == v else pa
    nb = v if pb == u else u if pb == v else pb
    return na, nb


def finish_gate(
    hw: Hardware,
    gates: list[tuple[int, int]],
    node: Node,
    labels: list[int],
    edges: list[tuple[int, int]],
) -> list[Node]:
    """Finish gates[node.k]. Include shortest SWAPs and a few non-shortening ones."""
    k = node.k
    a, b = gates[k]
    dist = hw.dist
    done: list[Node] = []
    seen: set[tuple] = set()

    def consider(st: Node, side: bool, swaps: list[tuple[int, int]]) -> None:
        pa, pb = st.l2p[a], st.l2p[b]
        if dist[pa][pb] != 1:
            return
        ch = st.copy()
        ch.parent = node
        ch.touch(pa, pb)
        ch.k = k + 1
        ch.kind = "side" if side or node.kind == "side" else "progress"
        ch.look = look_of(hw, gates, ch)
        ch.ops = tuple(("SWAP", labels[u], labels[v]) for u, v in swaps) + (("2Q", labels[pa], labels[pb]),)
        done.append(ch)

    # Internal beam of partial SWAP sequences for this one gate.
    live = [(node, False, [])]
    for _step in range(10):
        nxt = []
        for st, side, swaps in live:
            pa, pb = st.l2p[a], st.l2p[b]
            d = dist[pa][pb]
            if d == 1:
                consider(st, side, swaps)
                if len(swaps) >= 2:
                    continue
            extended = {pa, pb}
            for j in range(k, min(len(gates), k + 4)):
                for q in gates[j]:
                    phys = st.l2p[q]
                    if phys >= 0:
                        extended.add(phys)
            ranked = []
            for u, v in edges:
                hits = u in (pa, pb) or v in (pa, pb)
                if not hits and u not in extended and v not in extended:
                    continue
                na, nb = _moved(pa, pb, u, v)
                nd = dist[na][nb]
                if not hits and nd > d:
                    continue
                ranked.append((0 if nd < d else 1, nd, st.ready[u] + st.ready[v], u, v))
            ranked.sort()
            kept = [item for item in ranked if item[0] == 0][:6]
            if side is False:
                kept.extend(item for item in ranked if item[0] == 1)
                kept = kept[:8]
            for bias, _nd, _idle, u, v in kept:
                ch = st.copy()
                ch.swap(u, v)
                key = tuple(ch.l2p)
                if key in seen:
                    continue
                seen.add(key)
                nxt.append((ch, side or bias == 1, swaps + [(u, v)]))
                if len(nxt) >= 24:
                    break
            if len(nxt) >= 24:
                break
        if not nxt:
            break
        nxt.sort(key=lambda item: (item[0].swaps + 0.5 * item[0].depth, 0 if not item[1] else 1))
        live = nxt[:10]
    # Dedup finished mappings.
    best: dict[tuple, Node] = {}
    for ch in done:
        key = (ch.kind, tuple(ch.l2p))
        old = best.get(key)
        if old is None or ch.cost() < old.cost() or (ch.cost() == old.cost() and sum(ch.ready) < sum(old.ready)):
            best[key] = ch
    return list(best.values())


def route_of(node: Node) -> list[tuple]:
    chunks: list[tuple] = []
    cur: Node | None = node
    while cur is not None and cur.ops:
        chunks.append(cur.ops)
        cur = cur.parent
    chunks.reverse()
    return [op for chunk in chunks for op in chunk]


def main() -> None:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 420
    score_cap = 35.5
    program = BENCHMARKS["dense_random"]
    graph = build_hardware_graph()
    prog = Program(program)
    hw = Hardware(graph)
    state = json.loads((ROOT / "solution" / "autopilot_state.json").read_text())
    row = state["rows"]["dense_random"]
    placement = {int(k): int(v) for k, v in row["placement"].items()}
    routed = [tuple(op) for op in row["routed"]]
    base = score_summary(program, graph, placement, routed)
    print(f"incumbent score={base['score']} valid={base['valid']}", flush=True)
    pins = replay_pins(prog, hw, placement, routed)
    edges = [(u, v) for u in range(hw.n) for v in hw.adj[u] if u < v]
    labels = hw.labels
    start = Node(hw.n, prog.L)
    for q, logical in enumerate(prog.logicals):
        phys = hw.index[placement[logical]]
        start.l2p[q] = phys
        start.p2l[phys] = q
    start.look = look_of(hw, prog.gates, start)
    live = [start]
    deadline = time.perf_counter() + seconds
    best_node: Node | None = None
    champion: Node | None = None
    champion_gap = 0.0
    held: list[Node] = []
    for k in range(len(prog.gates)):
        if time.perf_counter() > deadline:
            print(f"deadline at gate {k}", flush=True)
            break
        pool: dict[tuple, Node] = {}
        for node in live:
            if node.k != k or node.cost() >= score_cap:
                continue
            for ch in finish_gate(hw, prog.gates, node, labels, edges):
                if ch.cost() >= score_cap:
                    continue
                key = tuple(ch.l2p)
                old = pool.get(key)
                if old is None or (ch.cost(), ch.look, sum(ch.ready)) < (old.cost(), old.look, sum(old.ready)):
                    pool[key] = ch
                elif ch.kind == "side" and old.kind != "side" and ch.cost() <= old.cost() + 1:
                    pool[(key, "side")] = ch
        pin = pins[k]
        if pin.cost() < score_cap:
            pool[("pin", tuple(pin.l2p))] = pin
        ranked = sorted(pool.values(), key=lambda n: (n.cost(), n.look, sum(n.ready)))
        side = [n for n in ranked if n.kind == "side"]
        rest = [n for n in ranked if n.kind != "side"]
        side_budget = max(12, width // 5)
        live = rest[: width - side_budget] + side[:side_budget]
        if len(live) < width:
            chosen = set(map(id, live))
            for n in ranked:
                if id(n) not in chosen:
                    live.append(n)
                    if len(live) >= width:
                        break
        if not live:
            print(f"gate {k + 1} live empty; cap stopped the search", flush=True)
            break
        held = [n for n in live if n.ops]
        best_cost = min(n.cost() for n in live)
        gap = pin.cost() - best_cost
        if held and gap > champion_gap:
            champion_gap = gap
            champion = min(held, key=lambda n: (n.cost(), n.swaps))
        print(
            f"gate {k + 1} live={len(live)} pool={len(pool)} best_cost={best_cost} pin={pin.cost()}",
            flush=True,
        )
        if k + 1 == len(prog.gates):
            under = [n for n in live if n.ops]
            if under:
                best_node = min(under, key=lambda n: (n.cost(), n.swaps))
    out_path = Path(__file__).resolve().parent / "pinned_beam_results.jsonl"

    def emit(method: str, node: Node, prog_ops: list[tuple], note: str) -> None:
        used = []
        seen_g = 0
        for op in prog_ops:
            if op[0] == "2Q":
                if seen_g >= node.k:
                    break
                seen_g += 1
            used.append(op)
        logicals = {q for op in used for q in op[1:]}
        pl = {q: p for q, p in placement.items() if q in logicals}
        full = route_of(node)
        official = score_summary(used, graph, pl, full)
        out = {
            "benchmark": "dense_random",
            "method": method,
            "note": note,
            "width": width,
            "valid": official["valid"],
            "message": official["message"],
            "swap_count": official["swap_count"],
            "depth": official["depth"],
            "score": official["score"],
            "model_cost": node.cost(),
            "gates": node.k,
        }
        print(json.dumps(out), flush=True)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(out) + "\n")
        if method == "pinned_beam_prefix" and official["valid"]:
            saved = {
                "placement": {str(q): p for q, p in placement.items()},
                "routed": full,
                "gates": node.k,
                "official": {k: official[k] for k in ("valid", "message", "swap_count", "depth", "score")},
            }
            (Path(__file__).resolve().parent / "pinned_prefix.json").write_text(json.dumps(saved))

    if champion is not None and champion_gap > 0:
        emit("pinned_beam_prefix", champion, program, f"largest model gap {champion_gap}")
    if best_node is None and held:
        # Finish the last open gate even if that reaches the cap, so the official score is known.
        forced: list[Node] = []
        for node in sorted(held, key=lambda n: n.cost())[:8]:
            forced.extend(finish_gate(hw, prog.gates, node, labels, edges))
        if forced:
            best_node = min(forced, key=lambda n: (n.cost(), n.swaps))
            emit("pinned_beam_forced", best_node, program, "completed past the cap")
            return
    if best_node is None:
        print("no complete route under 35.5", flush=True)
        return
    full = route_of(best_node)
    official = score_summary(program, graph, placement, full)
    out = {
        "benchmark": "dense_random",
        "method": "pinned_beam",
        "width": width,
        "valid": official["valid"],
        "message": official["message"],
        "swap_count": official["swap_count"],
        "depth": official["depth"],
        "score": official["score"],
        "model_cost": best_node.cost(),
    }
    print(json.dumps(out), flush=True)
    with (Path(__file__).resolve().parent / "pinned_beam_results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
