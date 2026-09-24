"""One-off diagnostic (not part of the submission): is dense_random's gap dominated by
placement quality, routing-search quality, or scheduling/depth? Runs many trials that vary
initial placement and routing search *independently*, then reports:

- the overall (swaps, depth) distribution
- the best swap count seen at all (ignoring depth)
- the best depth seen for each observed swap count (a Pareto frontier)
- whether particular placement strategies consistently win, or it's mostly routing-search luck

Run from the `Computational Track` directory:
    python solution/_diagnose_dense_random.py
"""

from __future__ import annotations

import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.solve import (
    GateSeq,
    Hardware,
    Program,
    Router,
    anneal_placement,
    build_output,
    cancel_redundant_swaps,
    initial_state,
    is_valid,
    prefix_embeddings,
    score_routed,
)
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph
from starter_kit.scorer import score_summary

NAME = "dense_random"
TIME_BUDGET_S = 240  # overall wall-clock cap for this whole diagnostic


def degree_match_placement(hw: Hardware, gates: list[tuple[int, int]], L: int) -> list[int]:
    count = [0] * L
    for a, b in gates:
        count[a] += 1
        count[b] += 1
    order = sorted(range(L), key=lambda q: -count[q])
    phys_order = sorted(range(hw.n), key=lambda p: -hw.degree[p])
    placement = [-1] * L
    for logical, phys in zip(order, phys_order):
        placement[logical] = phys
    return placement


def random_placement(hw: Hardware, L: int, rng: random.Random) -> list[int]:
    phys = rng.sample(range(hw.n), L)
    return list(phys)


def build_placements(hw: Hardware, prog: Program, rng: random.Random) -> list[tuple[str, list[int] | None]]:
    placements: list[tuple[str, list[int] | None]] = [("lazy", None)]
    placements.append(("degree_match", degree_match_placement(hw, prog.gates, prog.L)))
    for decay in (1.0, 0.9, 0.75, 0.5):
        pl = anneal_placement(hw, prog.gates, prog.L, rng, decay=decay, iters=4000)
        if pl is not None:
            placements.append((f"anneal(decay={decay})", pl))
    for i, pl in enumerate(prefix_embeddings(hw, prog.gates, prog.L, time.perf_counter() + 5.0, rng, count=3)):
        placements.append((f"prefix_embed{i}", pl))
    for i in range(6):
        placements.append((f"random{i}", random_placement(hw, prog.L, rng)))
    return placements


ROUTING_CONFIGS: list[tuple[str, dict]] = [
    ("narrow", {"width": 16, "slack": 0}),
    ("medium", {"width": 128, "slack": 1}),
    ("wide", {"width": 512, "slack": 1}),
    ("wide_slack2", {"width": 512, "slack": 2}),
    ("long_window", {"width": 256, "window": 24, "decay": 0.5}),
    ("short_window", {"width": 256, "window": 6, "decay": 0.9}),
    ("high_alpha", {"width": 256, "alpha": 2.0}),
    ("low_alpha", {"width": 256, "alpha": 0.3}),
]


def main() -> None:
    graph = build_hardware_graph()
    hw = Hardware(graph)
    prog = Program(BENCHMARKS[NAME])
    seq = GateSeq(prog.gates, prog.L)
    rng = random.Random(2026)

    placements = build_placements(hw, prog, rng)
    print(f"Testing {len(placements)} placement strategies x {len(ROUTING_CONFIGS)} routing configs x 2 seeds each")
    print(f"Placement tags: {[p[0] for p in placements]}\n")

    trials: list[dict] = []
    deadline = time.perf_counter() + TIME_BUDGET_S
    for p_tag, placement in placements:
        for r_tag, params in ROUTING_CONFIGS:
            for seed in (1, 2):
                if time.perf_counter() > deadline:
                    print("Hit overall time budget, stopping early.")
                    break
                router = Router(hw, seq, prog.L, params, random.Random(seed))
                t0 = time.perf_counter()
                state = router.run(initial_state(hw.n, prog.L, placement), time.perf_counter() + 20)
                elapsed = time.perf_counter() - t0
                if state is None:
                    continue
                placement_out, routed = build_output(prog, hw, state.l2t, _unwind(state))
                cleaned = cancel_redundant_swaps(routed)
                if is_valid(prog.ops, graph, placement_out, cleaned):
                    routed = cleaned
                if not is_valid(prog.ops, graph, placement_out, routed):
                    continue
                score = score_routed(routed)
                swaps = sum(1 for op in routed if op[0] == "SWAP")
                depth_layers = _depth_of(routed)
                trials.append(
                    {
                        "placement": p_tag,
                        "routing": r_tag,
                        "seed": seed,
                        "swaps": swaps,
                        "depth": depth_layers,
                        "score": score,
                        "time": elapsed,
                        "placement_out": placement_out,
                        "routed": routed,
                    }
                )

    print(f"Collected {len(trials)} valid trials\n")

    swaps_list = [t["swaps"] for t in trials]
    depth_list = [t["depth"] for t in trials]
    score_list = [t["score"] for t in trials]

    print("=== Overall distribution ===")
    print(f"swaps: min={min(swaps_list)} max={max(swaps_list)} mean={statistics.mean(swaps_list):.1f} median={statistics.median(swaps_list)} stdev={statistics.pstdev(swaps_list):.1f}")
    print(f"depth: min={min(depth_list)} max={max(depth_list)} mean={statistics.mean(depth_list):.1f} median={statistics.median(depth_list)} stdev={statistics.pstdev(depth_list):.1f}")
    print(f"score: min={min(score_list):.1f} max={max(score_list):.1f} mean={statistics.mean(score_list):.1f} median={statistics.median(score_list):.1f}")

    best_swaps = min(swaps_list)
    print(f"\nBest SWAP count observed (any depth): {best_swaps}")

    print("\n=== Pareto frontier: best depth observed for each swap count ===")
    by_swaps: dict[int, list[dict]] = defaultdict(list)
    for t in trials:
        by_swaps[t["swaps"]].append(t)
    for s in sorted(by_swaps):
        group = by_swaps[s]
        best = min(group, key=lambda t: t["depth"])
        print(f"  swaps={s:>3}: best depth={best['depth']:>3} (score={s + 0.5*best['depth']:.1f}), n={len(group)} trials, "
              f"from placement={best['placement']} routing={best['routing']}")

    print("\n=== Best score per placement strategy (routing varied) ===")
    by_placement: dict[str, list[dict]] = defaultdict(list)
    for t in trials:
        by_placement[t["placement"]].append(t)
    ranked_placements = sorted(by_placement.items(), key=lambda kv: min(t["score"] for t in kv[1]))
    for tag, group in ranked_placements:
        best = min(group, key=lambda t: t["score"])
        scores = [t["score"] for t in group]
        print(f"  {tag:<20} best={best['score']:>6.1f} (swaps={best['swaps']}, depth={best['depth']}, via {best['routing']})  "
              f"range=[{min(scores):.1f}, {max(scores):.1f}] n={len(group)}")

    print("\n=== Best score per routing config (placement varied) ===")
    by_routing: dict[str, list[dict]] = defaultdict(list)
    for t in trials:
        by_routing[t["routing"]].append(t)
    ranked_routing = sorted(by_routing.items(), key=lambda kv: min(t["score"] for t in kv[1]))
    for tag, group in ranked_routing:
        best = min(group, key=lambda t: t["score"])
        scores = [t["score"] for t in group]
        print(f"  {tag:<20} best={best['score']:>6.1f} (swaps={best['swaps']}, depth={best['depth']}, via {best['placement']})  "
              f"range=[{min(scores):.1f}, {max(scores):.1f}] n={len(group)}")

    overall_best = min(trials, key=lambda t: t["score"])
    print(f"\n=== Overall best trial ===")
    print(f"placement={overall_best['placement']} routing={overall_best['routing']} seed={overall_best['seed']} "
          f"-> swaps={overall_best['swaps']} depth={overall_best['depth']} score={overall_best['score']:.1f}")

    official = score_summary(BENCHMARKS[NAME], graph, overall_best["placement_out"], overall_best["routed"])
    print(f"Official scorer confirms: valid={official['valid']} score={official['score']:.1f} "
          f"swaps={official['swap_count']} depth={official['depth']}")

    print("\n=== Interpretation hints ===")
    placement_spread = max(min(t["score"] for t in g) for g in by_placement.values()) - min(min(t["score"] for t in g) for g in by_placement.values())
    routing_spread = max(min(t["score"] for t in g) for g in by_routing.values()) - min(min(t["score"] for t in g) for g in by_routing.values())
    print(f"Spread in best-score across placement strategies (routing varied): {placement_spread:.1f}")
    print(f"Spread in best-score across routing configs (placement varied): {routing_spread:.1f}")
    depth_range_at_best_swaps = by_swaps[best_swaps]
    depths_at_best = [t["depth"] for t in depth_range_at_best_swaps]
    print(f"At the best swap count ({best_swaps}), depth ranges over [{min(depths_at_best)}, {max(depths_at_best)}] across {len(depths_at_best)} trials "
          f"-- {'wide spread => scheduling-sensitive' if max(depths_at_best)-min(depths_at_best) >= 3 else 'narrow spread'}")


def _unwind(state):
    moves = []
    node = state.hist
    while node is not None:
        node, step = node
        moves.append(step)
    moves.reverse()
    return moves


def _depth_of(routed: list[tuple]) -> int:
    last: dict = {}
    depth = 0
    for op in routed:
        if op[0] == "1Q":
            continue
        t = 1 + max(last.get(op[1], 0), last.get(op[2], 0))
        last[op[1]] = last[op[2]] = t
        depth = max(depth, t)
    return depth


if __name__ == "__main__":
    main()
