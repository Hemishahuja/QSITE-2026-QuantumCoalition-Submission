"""Robustness suite. Every output has to pass the official validator and never lose to baseline.

Run from the `Computational Track` directory:
    python solution/test_robustness.py            # full suite
    python solution/test_robustness.py --quick    # fewer random cases
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.solve import lower_bound, score_routed, solve  # noqa: E402
from starter_kit.baseline_routing import solve as baseline_solve  # noqa: E402
from starter_kit.hardware import build_hardware_graph  # noqa: E402
from starter_kit.scorer import score_summary  # noqa: E402


def random_program(rng: random.Random, num_qubits: int, num_gates: int, p_1q: float, labels: list[int]) -> list[tuple]:
    program: list[tuple] = []
    for _ in range(num_gates):
        if rng.random() < p_1q:
            program.append(("1Q", rng.choice(labels[:num_qubits])))
        else:
            a, b = rng.sample(labels[:num_qubits], 2)
            program.append(("2Q", a, b))
    return program


def hardware_graphs() -> dict[str, nx.Graph]:
    graphs = {"heavy_hex_20": build_hardware_graph()}
    graphs["line_12"] = nx.path_graph(12)
    graphs["grid_4x4"] = nx.convert_node_labels_to_integers(nx.grid_2d_graph(4, 4))
    ring = nx.cycle_graph(10)
    ring.add_edges_from([(0, 2), (4, 6)])
    graphs["ring_with_triangles_10"] = ring
    relabeled = nx.relabel_nodes(build_hardware_graph(), {i: 100 + 3 * i for i in range(20)})
    graphs["heavy_hex_relabeled"] = relabeled
    return graphs


def edge_cases() -> list[tuple[str, list[tuple]]]:
    return [
        ("empty", []),
        ("single_1q", [("1Q", 0)]),
        ("single_2q", [("2Q", 0, 1)]),
        ("reversed_orientation", [("2Q", 1, 0), ("2Q", 0, 1), ("2Q", 1, 0)]),
        ("1q_only_qubit", [("2Q", 0, 1), ("1Q", 5), ("2Q", 1, 2), ("1Q", 5)]),
        ("1q_before_first_use", [("1Q", 3), ("1Q", 4), ("2Q", 3, 4), ("1Q", 3)]),
        ("sparse_labels", [("2Q", 7, 42), ("1Q", 42), ("2Q", 42, 3), ("2Q", 3, 7), ("2Q", 7, 99)]),
        ("repeated_pair", [("2Q", 0, 5)] * 6),
        ("star_10", [("2Q", 0, i) for i in range(1, 11)]),
        ("all_pairs_6", [("2Q", i, j) for i in range(6) for j in range(i + 1, 6)]),
        ("twenty_qubits", [("2Q", i, (i * 7 + 3) % 20) for i in range(20) if i != (i * 7 + 3) % 20]),
        (
            "disconnected_components",
            [("2Q", 0, 1), ("2Q", 1, 2), ("2Q", 2, 0), ("2Q", 10, 11), ("2Q", 11, 12), ("2Q", 12, 10)],
        ),
        ("disconnected_plus_singletons", [("2Q", 0, 1), ("1Q", 7), ("2Q", 3, 4), ("1Q", 8), ("2Q", 4, 3)]),
    ]


def check(name: str, program: list[tuple], graph: nx.Graph, budget: float, failures: list[str]) -> tuple[float, float]:
    start = time.perf_counter()
    placement, routed = solve(program, graph, time_budget=budget)
    elapsed = time.perf_counter() - start
    result = score_summary(program, graph, placement, routed)
    if not result["valid"]:
        failures.append(f"{name}: INVALID ({result['message']})")
        return float("inf"), elapsed
    if abs(result["score"] - score_routed(routed)) > 1e-9:
        failures.append(f"{name}: internal score {score_routed(routed)} != official {result['score']}")
    try:
        bl_placement, bl_routed = baseline_solve(program, graph)
        baseline = score_summary(program, graph, bl_placement, bl_routed)["score"]
    except Exception:
        baseline = float("inf")
    if result["score"] > baseline + 1e-9:
        failures.append(f"{name}: worse than baseline ({result['score']} > {baseline})")
    lb = lower_bound(program, graph, time_limit=1.0)
    if result["score"] < lb - 1e-9:
        failures.append(f"{name}: score {result['score']} below the claimed lower bound {lb}")
    if elapsed > budget * 1.5 + 1.0:
        failures.append(f"{name}: took {elapsed:.1f}s for a {budget}s budget")
    return result["score"], elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--budget", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    graphs = hardware_graphs()
    failures: list[str] = []
    count = 0

    for case_name, program in edge_cases():
        for graph_name, graph in graphs.items():
            logicals = {q for op in program for q in op[1:]}
            if len(logicals) > graph.number_of_nodes():
                continue
            check(f"{case_name}@{graph_name}", program, graph, args.budget, failures)
            count += 1

    trials = 15 if args.quick else 60
    for i in range(trials):
        graph_name = rng.choice(list(graphs))
        graph = graphs[graph_name]
        n_phys = graph.number_of_nodes()
        num_qubits = rng.randint(2, n_phys)
        labels = rng.sample(range(1000), n_phys) if rng.random() < 0.3 else list(range(n_phys))
        program = random_program(rng, num_qubits, rng.randint(1, 50), rng.choice((0.0, 0.2, 0.5)), labels)
        check(f"random{i}(q={num_qubits},g={len(program)})@{graph_name}", program, graph, args.budget, failures)
        count += 1

    # Scale test: bigger hardware graph and a much larger random program, closer to what a
    # hidden judge benchmark might look like. Uses a slightly larger budget since it's just one case.
    big_graph = nx.convert_node_labels_to_integers(nx.grid_2d_graph(8, 8))
    big_program = random_program(rng, 50, 150, 0.1, list(range(64)))
    check("scale_test(q=50,g=150)@grid_8x8", big_program, big_graph, max(args.budget, 5.0), failures)
    count += 1

    print(f"Ran {count} cases.")
    if failures:
        print(f"{len(failures)} FAILURES:")
        for f in failures:
            print("  " + f)
        return 1
    print("All outputs valid, consistent with the official scorer, and no worse than the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
