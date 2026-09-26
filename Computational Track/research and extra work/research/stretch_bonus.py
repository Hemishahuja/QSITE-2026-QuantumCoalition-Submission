"""Quantify the (ambiguous) Stretch-A bonus on our captured routed outputs.

`baseline_decompose` below is a verbatim copy of the organizer's deleted
starter_kit/baseline_decompose.py (git show 57f9a53^:"Computational Track/starter_kit/baseline_decompose.py").
The README (current) still says: stretch_A_bonus = N x 0.1 where N = gates saved vs the bad
decomposer. Whether it counts is unresolved (the formula was removed from the headline and the
baseline file deleted in 57f9a53).

    python "research and extra work/research/stretch_bonus.py"
"""

from __future__ import annotations

from common import BENCHMARKS, load_json


def baseline_decompose(routed_circuit):
    decomposed = []
    for op in routed_circuit:
        kind = op[0]
        if kind == "SWAP":
            _, left, right = op
            decomposed.extend([("RZ", left, 0.0), ("CNOT", left, right), ("RZ", right, 0.0), ("CNOT", right, left),
                               ("RZ", left, 0.0), ("CNOT", left, right), ("RZ", right, 0.0)])
        elif kind == "2Q":
            _, left, right = op
            decomposed.extend([("RZ", left, 0.0), ("CNOT", left, right), ("RZ", right, 0.0)])
        elif kind == "1Q":
            _, qubit = op
            decomposed.extend([("RZ", qubit, 0.0), ("SX", qubit), ("RZ", qubit, 0.0)])
        else:
            raise ValueError(kind)
    return decomposed


def plain_decompose(routed):
    """SWAP -> 3 CNOT, 2Q -> 1 CNOT placeholder (same convention as the baseline), 1Q -> 1 gate."""
    out = []
    for op in routed:
        if op[0] == "SWAP":
            _, a, b = op
            out += [("CNOT", a, b), ("CNOT", b, a), ("CNOT", a, b)]
        elif op[0] == "2Q":
            out.append(("CNOT", op[1], op[2]))
        else:
            out.append(("SX", op[1]))
    return out


def swap_gate_merges(routed):
    """Count SWAP/2Q pairs on the same wire pair with nothing else touching those wires in between.
    If 2Q is literally a CNOT, CNOT.SWAP = 2 CNOTs, saving 2 more gates each (only an estimate:
    it depends on the unknown semantics of the abstract 2Q gate)."""
    count = 0
    ops = [op for op in routed if op[0] != "1Q"]
    used = [False] * len(ops)
    for i, op in enumerate(ops):
        if used[i]:
            continue
        pair = {op[1], op[2]}
        for j in range(i + 1, len(ops)):
            o2 = ops[j]
            if pair & {o2[1], o2[2]}:
                if {o2[1], o2[2]} == pair and {op[0], o2[0]} == {"SWAP", "2Q"} and not used[j]:
                    used[i] = used[j] = True
                    count += 1
                break
    return count


sols = load_json("current_solutions.json")
tot_base = tot_plain = tot_merge = 0
print(f"{'benchmark':<15} {'swaps':>5} {'2Q':>4} {'baseline':>8} {'plain':>6} {'N':>5} {'bonus':>6} {'merges':>6}")
for name in BENCHMARKS:
    routed = [tuple(op) for op in sols[name]["routed"]]
    nb = len(baseline_decompose(routed))
    npl = len(plain_decompose(routed))
    m = swap_gate_merges(routed)
    sw = sum(1 for op in routed if op[0] == "SWAP")
    g2 = sum(1 for op in routed if op[0] == "2Q")
    tot_base += nb
    tot_plain += npl
    tot_merge += m
    print(f"{name:<15} {sw:>5} {g2:>4} {nb:>8} {npl:>6} {nb-npl:>5} {0.1*(nb-npl):>6.1f} {m:>6}")
N = tot_base - tot_plain
print(f"TOTAL baseline gates={tot_base} plain={tot_plain} N={N} bonus=0.1*N={0.1*N:.1f}; "
      f"extra if SWAP/CNOT merges allowed: {tot_merge} merges -> +{0.2*tot_merge:.1f}")
print("Per-SWAP bonus 0.4 (7->3 gates); per program 2Q gate 0.2 (3->1); program-gate part is identical for every team.")
