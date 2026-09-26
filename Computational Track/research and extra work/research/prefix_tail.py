"""Route the dense tail after the proven 3-SWAP prefix of the first 16 gates.

prefix_bounds showed those 16 gates need 3 SWAPs; the incumbent has already used 4
by then. The witness is dense_prefix16_witness.json. Logicals 7 and 9 are still
unplaced there, so the tail router places them lazily. The tail is the production
Router started from the witness mapping and ready profile. Every full splice is
scored with starter_kit.scorer.score_summary.
"""

from __future__ import annotations

import heapq
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from general_exact import general_min_swaps, witness_to_routed
from solution.solve import GateSeq, Hardware, Program, Router, build_output, initial_state, unwind
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph
from starter_kit.scorer import score_summary

PREFIX_GATES = 16


def prefix_moves(hw: Hardware, routed: list[tuple]) -> list[tuple[tuple[int, int], ...]]:
    groups: list[tuple[tuple[int, int], ...]] = []
    pending: list[tuple[int, int]] = []
    for op in routed:
        if op[0] == "SWAP":
            pending.append((hw.index[op[1]], hw.index[op[2]]))
        elif op[0] == "2Q":
            groups.append(tuple(pending))
            pending = []
        else:
            raise RuntimeError(op)
    if pending:
        raise RuntimeError("witness ended on a SWAP")
    return groups


def replay_prefix(hw: Hardware, prog: Program, placement: dict[int, int], routed: list[tuple]):
    slot = [-1] * prog.L
    for logical, phys in placement.items():
        slot[prog.lindex[logical]] = hw.index[phys]
    state = initial_state(hw.n, prog.L, slot)
    gate_i = 0
    for op in routed:
        if op[0] == "SWAP":
            state.swap(hw.index[op[1]], hw.index[op[2]])
        elif op[0] == "2Q":
            a, b = prog.gates[gate_i]
            pa = state.t2p[state.l2t[a]]
            pb = state.t2p[state.l2t[b]]
            if {hw.labels[pa], hw.labels[pb]} != {op[1], op[2]}:
                raise RuntimeError(f"prefix diverged at gate {gate_i}: {op}")
            state.execute(pa, pb)
            gate_i += 1
        else:
            raise RuntimeError(op)
    state.hist = None
    return state


def beam_from(router: Router, init, start_k: int, deadline: float):
    beam = [init]
    for k in range(start_k, router.seq.G):
        best: dict[tuple, tuple[float, int, object]] = {}
        for st in beam:
            for ch in router.expand(st, k):
                key = tuple(ch.t2p[t] if t >= 0 else -1 for t in ch.l2t)
                cost = ch.cost
                tie = sum(ch.ready)
                old = best.get(key)
                if old is None or cost < old[0] or (cost == old[0] and tie < old[1]):
                    best[key] = (cost, tie, ch)
        if not best:
            return None
        scored = [(router.evaluate(ch, k + 1), i, ch) for i, (_, _, ch) in enumerate(best.values())]
        if len(scored) > router.width:
            scored = heapq.nsmallest(router.width, scored)
        beam = [item[2] for item in scored]
        if time.perf_counter() > deadline:
            return None
    return min(beam, key=lambda s: (s.cost, s.swaps))


def route_witness(witness: dict, width: int, seconds: float, slack: int, tag: str) -> dict | None:
    program = BENCHMARKS["dense_random"]
    graph = build_hardware_graph()
    prog = Program(program)
    hw = Hardware(graph)
    placement = {int(k): int(v) for k, v in witness["placement"].items()}
    routed = [tuple(op) for op in witness["routed"]]
    prefix = prefix_moves(hw, routed)
    start_k = int(witness.get("gates", PREFIX_GATES))
    if len(prefix) != start_k:
        raise RuntimeError(f"expected {start_k} prefix gates, got {len(prefix)}")
    state = replay_prefix(hw, prog, placement, routed)
    print(
        f"prefix replay swaps={state.swaps} depth={state.depth} placed={sum(t >= 0 for t in state.l2t)}",
        flush=True,
    )
    router = Router(
        hw,
        GateSeq(prog.gates, prog.L),
        prog.L,
        {"width": width, "slack": slack, "paths": 4, "route_cap": 48},
        random.Random(0),
    )
    deadline = time.perf_counter() + seconds
    found = beam_from(router, state, start_k, deadline)
    if found is None:
        print("tail beam returned nothing", flush=True)
        return
    suffix = unwind(found)
    print(f"suffix gates={len(suffix)} total_swaps={found.swaps} depth={found.depth} cost={found.cost}", flush=True)
    moves = prefix + suffix
    if len(moves) != len(prog.gates):
        raise RuntimeError(f"move groups {len(moves)} != gates {len(prog.gates)}")
    full_placement, full_routed = build_output(prog, hw, found.l2t, moves)
    official = score_summary(program, graph, full_placement, full_routed)
    row = {
        "benchmark": "dense_random",
        "method": "prefix16_tail",
        "tag": tag,
        "width": width,
        "slack": slack,
        "valid": official["valid"],
        "message": official["message"],
        "swap_count": official["swap_count"],
        "depth": official["depth"],
        "score": official["score"],
    }
    print(json.dumps(row), flush=True)
    with (HERE / "prefix_tail_results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "saved":
        width = int(sys.argv[2]) if len(sys.argv) > 2 else 256
        seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 180
        slack = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        witness = json.loads((HERE / "pinned_prefix.json").read_text())
        route_witness(witness, width, seconds, slack, tag=f"pinned23-w{width}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "seeds":
        # Different DFS orders yield different 3-SWAP prefixes. Each tail is short.
        prog = [op for op in BENCHMARKS["dense_random"] if op[0] == "2Q"]
        gates = [(op[1], op[2]) for op in prog[:PREFIX_GATES]]
        for seed in (1, 2, 3, 4, 5, 6):
            print(f"=== order_seed {seed} ===", flush=True)
            res, wit, nodes = general_min_swaps(
                gates,
                time_limit=90,
                start_budget=3,
                max_budget=3,
                verbose=True,
                order_seed=seed,
            )
            if wit is None:
                print(f"seed {seed}: no witness nodes={nodes} res={res}", flush=True)
                continue
            pl, routed = witness_to_routed(prog[:PREFIX_GATES], wit)
            witness = {"placement": {str(k): v for k, v in pl.items()}, "routed": routed}
            route_witness(witness, width=64, seconds=90, slack=1, tag=f"seed{seed}")
        return
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 64
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    slack = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    witness = json.loads((HERE / "dense_prefix16_witness.json").read_text())
    route_witness(witness, width, seconds, slack, tag="saved")


if __name__ == "__main__":
    main()
