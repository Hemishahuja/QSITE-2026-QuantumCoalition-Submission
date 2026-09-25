"""Stretch goal A: rewrite a routed program into the native gate set {RZ, SX, CNOT}.

The deleted starter baseline (starter_kit/baseline_decompose.py at 57f9a53^) padded every
operation with RZ(0) identities: 7 gates per SWAP, 3 per 2Q, 3 per 1Q. This pass emits
the unpadded expansion instead.
"""

from __future__ import annotations


def decompose(routed_circuit: list[tuple]) -> list[tuple]:
    """Expand routed ops into native gates without identity padding.

    SWAP(a, b) -> CNOT(a, b), CNOT(b, a), CNOT(a, b)
    2Q(a, b)   -> CNOT(a, b)
    1Q(q)      -> a single SX(q) placeholder

    ("1Q", q) carries no angle, so this does not interpret the gate as identity
    and delete it. SX is one native gate (3 -> 1 versus the padded baseline).
    RZ(0) is not used as that placeholder: optimize_1q() removes RZ(0), and
    emitting it here would drop an unknown single-qubit operation.
    """
    decomposed: list[tuple] = []

    for op in routed_circuit:
        kind = op[0]
        if kind == "SWAP":
            _, left, right = op
            decomposed.extend(
                [
                    ("CNOT", left, right),
                    ("CNOT", right, left),
                    ("CNOT", left, right),
                ]
            )
        elif kind == "2Q":
            _, left, right = op
            decomposed.append(("CNOT", left, right))
        elif kind == "1Q":
            _, qubit = op
            # Placeholder only. The benchmark tuple has no parameter, so SX stands
            # in for "one native single-qubit gate" without claiming an angle.
            decomposed.append(("SX", qubit))
        else:
            raise ValueError(f"Unknown operation kind: {kind}")

    return decomposed
