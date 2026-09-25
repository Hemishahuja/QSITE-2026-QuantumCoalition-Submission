"""One-shot: how long a dense_random prefix must be before the allocation bound lifts off zero."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wagner_tap as w


def main() -> None:
    program = w.program_of("dense_random")
    gates = w.gates_of(program)
    qubits = w.qubits_of(program)
    loaded = w.load_current("dense_random")
    assert loaded is not None
    maps = w.mappings_from_routed(loaded[0], loaded[1], len(gates))
    lengths = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [8, 10, 12]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    for n_gates in lengths:
        sub = gates[:n_gates]
        hint = maps[:n_gates]
        _dist, lb = w.allocation_lb(hint, qubits)
        print(f"PREFIX {n_gates} incumbent_lb={lb}", flush=True)
        tap = w.solve_tap(sub, qubits, hint, seconds, workers=1)
        print(
            f"  status={tap['status']} bound={tap['bound']} obj={tap.get('objective')} elapsed={tap['elapsed']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
