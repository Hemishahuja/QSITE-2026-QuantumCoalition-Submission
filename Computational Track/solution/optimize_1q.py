"""Stretch goal B: simplify single-qubit runs in a native {RZ, SX, CNOT} circuit.

Rewrites stay inside one qubit's own gates. CNOT order is unchanged: a CNOT is a
barrier for the two qubits it touches, and every surviving gate keeps its original
relative order.
"""

from __future__ import annotations

import math
from collections import defaultdict

# RZ(2*pi*k) is identity up to a global phase. Angles closer than this to a
# multiple of 2*pi are treated as that identity.
_ANGLE_EPS = 1e-8
_TWO_PI = math.tau


def _canonical_angle(theta: float) -> float:
    """Wrap theta into [0, 2*pi), mapping exact multiples of 2*pi to 0.0."""
    wrapped = math.fmod(float(theta), _TWO_PI)
    if wrapped < 0.0:
        wrapped += _TWO_PI
    if min(wrapped, _TWO_PI - wrapped) <= _ANGLE_EPS:
        return 0.0
    return float(round(wrapped, 12))


def _simplify_run(qubit: int, gates: list[tuple]) -> list[tuple]:
    """Merge RZ, drop RZ identities, and drop SX runs whose length is a multiple of 4.

    SX is sqrt(X), not an involution: SX^2 = X (not I) and SX^4 = I up to global
    phase. Adjacent SX pairs are therefore kept. RZ(a) followed by RZ(-a) cancels
    through the merge rule plus the zero-angle drop, which covers the self-inverse
    case RZ(pi) · RZ(pi) = I up to global phase.
    """
    folded: list[tuple] = []
    for gate in gates:
        kind = gate[0]
        if kind == "RZ":
            if len(gate) != 3:
                raise ValueError(f"RZ gate must be (RZ, qubit, theta), got {gate!r}")
            incoming = float(gate[2])
            if folded and folded[-1][0] == "RZ":
                theta = _canonical_angle(folded[-1][2] + incoming)
                folded.pop()
            else:
                theta = _canonical_angle(incoming)
            if theta != 0.0:
                folded.append(("RZ", qubit, theta))
        elif kind == "SX":
            folded.append(("SX", qubit))
        else:
            raise ValueError(f"Unsupported single-qubit gate in a 1Q run: {gate!r}")

    simplified: list[tuple] = []
    index = 0
    while index < len(folded):
        if folded[index][0] != "SX":
            simplified.append(folded[index])
            index += 1
            continue
        end = index + 1
        while end < len(folded) and folded[end][0] == "SX":
            end += 1
        kept = (end - index) % 4
        simplified.extend(("SX", qubit) for _ in range(kept))
        index = end
    return simplified


def _qubits_touched(op: tuple) -> list[int]:
    return [arg for arg in op[1:] if isinstance(arg, int)]


def optimize_1q(circuit: list[tuple]) -> list[tuple]:
    """Simplify single-qubit gates without reordering two-qubit structure.

    Adjacent RZ gates on the same qubit merge (RZ(a) · RZ(b) -> RZ(a+b)).
    RZ(0) and RZ(2*pi*k) are removed. Four consecutive SX gates on one qubit
    are removed (SX^4 = I up to global phase). Gates on different qubits are
    not reordered, and no CNOT is moved, dropped, or inserted.
    """
    # Per qubit, the indices of its 1Q gates split into runs by any op that
    # touches that qubit and is not itself an RZ/SX (CNOT, or anything else).
    open_run: dict[int, list[int]] = defaultdict(list)
    runs: dict[int, list[list[int]]] = defaultdict(list)

    for index, op in enumerate(circuit):
        kind = op[0]
        if kind in ("RZ", "SX"):
            if len(op) < 2 or not isinstance(op[1], int):
                raise ValueError(f"Single-qubit gate missing a qubit index: {op!r}")
            open_run[op[1]].append(index)
            continue
        for qubit in _qubits_touched(op):
            runs[qubit].append(open_run[qubit])
            open_run[qubit] = []

    for qubit, run in open_run.items():
        runs[qubit].append(run)

    rewritten: dict[int, tuple | None] = {}
    for qubit, qubit_runs in runs.items():
        for indices in qubit_runs:
            if not indices:
                continue
            simplified = _simplify_run(qubit, [circuit[index] for index in indices])
            if len(simplified) > len(indices):
                raise RuntimeError("single-qubit simplification expanded a run")
            for offset, index in enumerate(indices):
                rewritten[index] = simplified[offset] if offset < len(simplified) else None

    optimized: list[tuple] = []
    for index, op in enumerate(circuit):
        if index not in rewritten:
            optimized.append(op)
        elif rewritten[index] is not None:
            optimized.append(rewritten[index])
    return optimized
