"""Our own check for stretch goals A and B. There is no official scorer hook.

Compares decompose() + optimize_1q() against the deleted bad baseline
(starter_kit/baseline_decompose.py and baseline_oneq.py at git 57f9a53^).

    python solution/verify_stretch.py

Run from the `Computational Track` directory.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.decompose import decompose
from solution.optimize_1q import optimize_1q
from solution.solve import solve
from starter_kit.benchmarks import BENCHMARKS
from starter_kit.hardware import build_hardware_graph

DISCLAIMER = (
    "NOT verified by the official starter_kit.scorer (no live scoring hook exists "
    "for this stretch goal); this is our own reference count only."
)


def bad_baseline_decompose(routed_circuit: list[tuple]) -> list[tuple]:
    """Verbatim behavior of the deleted starter_kit/baseline_decompose.py (57f9a53^)."""
    decomposed: list[tuple] = []
    for op in routed_circuit:
        kind = op[0]
        if kind == "SWAP":
            _, left, right = op
            decomposed.extend(
                [
                    ("RZ", left, 0.0),
                    ("CNOT", left, right),
                    ("RZ", right, 0.0),
                    ("CNOT", right, left),
                    ("RZ", left, 0.0),
                    ("CNOT", left, right),
                    ("RZ", right, 0.0),
                ]
            )
        elif kind == "2Q":
            _, left, right = op
            decomposed.extend([("RZ", left, 0.0), ("CNOT", left, right), ("RZ", right, 0.0)])
        elif kind == "1Q":
            _, qubit = op
            decomposed.extend([("RZ", qubit, 0.0), ("SX", qubit), ("RZ", qubit, 0.0)])
        else:
            raise ValueError(f"Unknown operation kind: {kind}")
    return decomposed


def bad_baseline_optimize_1q(circuit: list[tuple]) -> list[tuple]:
    """Verbatim behavior of the deleted starter_kit/baseline_oneq.py: do nothing."""
    return list(circuit)


def expected_cnots(routed_circuit: list[tuple]) -> list[tuple]:
    """SWAP -> three alternating CNOTs; 2Q -> one CNOT; 1Q contributes none."""
    cnots: list[tuple] = []
    for op in routed_circuit:
        kind = op[0]
        if kind == "SWAP":
            _, left, right = op
            cnots.extend(
                [
                    ("CNOT", left, right),
                    ("CNOT", right, left),
                    ("CNOT", left, right),
                ]
            )
        elif kind == "2Q":
            cnots.append(("CNOT", op[1], op[2]))
        elif kind == "1Q":
            continue
        else:
            raise ValueError(f"Unknown operation kind: {kind}")
    return cnots


def cnots_of(circuit: list[tuple]) -> list[tuple]:
    return [op for op in circuit if op[0] == "CNOT"]


def assert_native(circuit: list[tuple]) -> None:
    for op in circuit:
        if op[0] == "RZ":
            if len(op) != 3 or not isinstance(op[1], int) or not isinstance(op[2], float):
                raise AssertionError(f"malformed RZ: {op!r}")
        elif op[0] == "SX":
            if len(op) != 2 or not isinstance(op[1], int):
                raise AssertionError(f"malformed SX: {op!r}")
        elif op[0] == "CNOT":
            if len(op) != 3 or not isinstance(op[1], int) or not isinstance(op[2], int):
                raise AssertionError(f"malformed CNOT: {op!r}")
        else:
            raise AssertionError(f"non-native gate: {op!r}")


def check_routed(name: str, routed: list[tuple], decomposed: list[tuple], optimized: list[tuple]) -> None:
    assert_native(decomposed)
    assert_native(optimized)
    expected = expected_cnots(routed)
    got = cnots_of(decomposed)
    if got != expected:
        raise AssertionError(
            f"{name}: decompose() CNOT sequence does not match SWAP/2Q expansion "
            f"(expected {len(expected)}, got {len(got)})"
        )
    if cnots_of(optimized) != got:
        raise AssertionError(f"{name}: optimize_1q() changed the CNOT sequence")


def _expect(label: str, got: list[tuple], expected: list[tuple]) -> None:
    if got != expected:
        raise AssertionError(f"{label}\n  got:      {got}\n  expected: {expected}")
    print(f"  pass  {label}")


def synthetic_checks() -> None:
    """Exercise rules the six public benchmarks never hit (they contain no 1Q ops)."""
    print("Synthetic checks (not included in the benchmark gate totals)")
    _expect(
        "SWAP expands to 3 CNOTs",
        decompose([("SWAP", 0, 1)]),
        [("CNOT", 0, 1), ("CNOT", 1, 0), ("CNOT", 0, 1)],
    )
    _expect("2Q expands to 1 CNOT", decompose([("2Q", 4, 5)]), [("CNOT", 4, 5)])
    _expect("1Q expands to one SX placeholder", decompose([("1Q", 3)]), [("SX", 3)])
    padded = bad_baseline_decompose([("1Q", 3), ("2Q", 0, 1), ("SWAP", 0, 1)])
    plain = decompose([("1Q", 3), ("2Q", 0, 1), ("SWAP", 0, 1)])
    if not (len(padded) == 3 + 3 + 7 and len(plain) == 1 + 1 + 3):
        raise AssertionError(f"padding lengths wrong: bad={len(padded)} ours={len(plain)}")
    print("  pass  unpadded lengths are 1/1/3 vs baseline 3/3/7")

    _expect(
        "merge adjacent RZ",
        optimize_1q([("RZ", 0, 0.3), ("RZ", 0, 0.5)]),
        [("RZ", 0, 0.8)],
    )
    _expect(
        "drop RZ(0) and RZ(2*pi)",
        optimize_1q([("RZ", 0, 0.0), ("CNOT", 0, 1), ("RZ", 1, 2 * math.pi)]),
        [("CNOT", 0, 1)],
    )
    _expect(
        "drop RZ(4*pi) and RZ(a)+RZ(-a)",
        optimize_1q([("RZ", 2, 4 * math.pi), ("RZ", 2, 0.25), ("RZ", 2, -0.25)]),
        [],
    )
    _expect(
        "do not merge RZ across a CNOT on that qubit",
        optimize_1q([("RZ", 0, 0.2), ("CNOT", 0, 1), ("RZ", 0, 0.3)]),
        [("RZ", 0, 0.2), ("CNOT", 0, 1), ("RZ", 0, 0.3)],
    )
    _expect(
        "merge RZ on a qubit that a foreign CNOT does not touch",
        optimize_1q([("RZ", 0, 0.2), ("CNOT", 1, 2), ("RZ", 0, 0.3)]),
        [("RZ", 0, 0.5), ("CNOT", 1, 2)],
    )
    _expect(
        "keep other qubits' gates in place while merging",
        optimize_1q([("RZ", 0, 0.1), ("RZ", 1, 0.2), ("RZ", 0, 0.3)]),
        [("RZ", 0, 0.4), ("RZ", 1, 0.2)],
    )
    two_sx = [("SX", 0), ("SX", 0)]
    _expect("keep SX.SX (SX^2 = X, not identity)", optimize_1q(two_sx), two_sx)
    _expect(
        "drop SX^4 and keep CNOT order",
        optimize_1q(
            [
                ("RZ", 0, 0.1),
                ("RZ", 0, 0.2),
                ("CNOT", 0, 1),
                ("SX", 1),
                ("SX", 1),
                ("SX", 1),
                ("SX", 1),
                ("CNOT", 1, 0),
                ("RZ", 0, 0.0),
            ]
        ),
        [("RZ", 0, 0.3), ("CNOT", 0, 1), ("CNOT", 1, 0)],
    )
    print()


def main() -> int:
    synthetic_checks()

    graph = build_hardware_graph()
    header = (
        f"{'benchmark':<16} {'swaps':>5} {'2Q':>5} {'1Q':>4} "
        f"{'baseline':>9} {'decompose':>10} {'optimized':>10} {'saved':>6} {'N*0.1':>7}"
    )
    print(header)
    print("-" * len(header))

    total_baseline = 0
    total_decompose = 0
    total_optimized = 0
    for name, program in BENCHMARKS.items():
        t0 = time.perf_counter()
        _placement, routed = solve(program, graph)
        elapsed = time.perf_counter() - t0
        routed = [tuple(op) for op in routed]

        decomposed = decompose(routed)
        optimized = optimize_1q(decomposed)
        baseline = bad_baseline_optimize_1q(bad_baseline_decompose(routed))
        check_routed(name, routed, decomposed, optimized)

        swaps = sum(1 for op in routed if op[0] == "SWAP")
        two_q = sum(1 for op in routed if op[0] == "2Q")
        one_q = sum(1 for op in routed if op[0] == "1Q")
        saved = len(baseline) - len(optimized)
        if saved <= 0:
            raise AssertionError(f"{name}: gate count did not drop ({len(baseline)} -> {len(optimized)})")
        # Unpadded expansion alone saves 4 per SWAP, 2 per 2Q, 2 per 1Q versus the bad baseline.
        expected_saved = 4 * swaps + 2 * two_q + 2 * one_q
        if len(baseline) - len(decomposed) != expected_saved:
            raise AssertionError(
                f"{name}: decompose savings {len(baseline) - len(decomposed)} != {expected_saved}"
            )

        total_baseline += len(baseline)
        total_decompose += len(decomposed)
        total_optimized += len(optimized)
        print(
            f"{name:<16} {swaps:>5} {two_q:>5} {one_q:>4} "
            f"{len(baseline):>9} {len(decomposed):>10} {len(optimized):>10} "
            f"{saved:>6} {saved * 0.1:>7.1f}   ({elapsed:.1f}s)"
        )

    total_saved = total_baseline - total_optimized
    print("-" * len(header))
    print(
        f"{'TOTAL':<16} {'':>5} {'':>5} {'':>4} "
        f"{total_baseline:>9} {total_decompose:>10} {total_optimized:>10} "
        f"{total_saved:>6} {total_saved * 0.1:>7.1f}"
    )
    print()
    print(f"Gates saved N = {total_saved}")
    print(f"Notional bonus N*0.1 = {total_saved * 0.1:.1f}")
    print(DISCLAIMER)
    print(
        "optimize_1q changed "
        f"{total_decompose - total_optimized} gates on these 6 routed programs. "
        "Public benchmarks have no ('1Q', ...) ops, and decompose() emits no RZ(0) padding, "
        "so the savings here come from not emitting that padding."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
