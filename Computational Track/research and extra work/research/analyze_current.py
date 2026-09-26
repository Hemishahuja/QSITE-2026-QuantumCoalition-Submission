"""Per-gate anatomy of the captured production solutions (current_solutions.json).

    python "research and extra work/research/analyze_current.py"
"""

from __future__ import annotations

from common import BENCHMARKS, critical_path, gates_of, load_json

from starter_kit.scorer import schedule_layers_ordered

sols = load_json("current_solutions.json")
for name, program in BENCHMARKS.items():
    s = sols[name]
    routed = [tuple(op) for op in s["routed"]]
    per_gate = []
    cur = 0
    for op in routed:
        if op[0] == "SWAP":
            cur += 1
        elif op[0] == "2Q":
            per_gate.append(cur)
            cur = 0
    cum = []
    c = 0
    for x in per_gate:
        c += x
        cum.append(c)
    layers = schedule_layers_ordered(routed)
    swap_layers = sum(1 for layer in layers if all(op[0] == "SWAP" for op in layer))
    # layer of each program gate
    last = {}
    gate_layer = []
    for op in routed:
        if op[0] == "1Q":
            continue
        t = 1 + max(last.get(op[1], 0), last.get(op[2], 0))
        last[op[1]] = last[op[2]] = t
        if op[0] == "2Q":
            gate_layer.append(t)
    print(f"\n{name}: swaps={sum(per_gate)} depth={len(layers)} critical_path={critical_path(gates_of(program))} "
          f"swap-only layers={swap_layers}")
    print("  swaps before gate k:", per_gate)
    print("  cumulative swaps after gate k (1-indexed prefix):", cum)
    print("  ASAP layer of each program gate:", gate_layer)
    print("  layer sizes:", [len(layer) for layer in layers])
