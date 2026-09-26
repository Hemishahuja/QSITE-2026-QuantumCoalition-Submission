"""External calibration: Qiskit SABRE (SabreLayout + SabreSwap) under the strict order rule.

Strict program order is enforced by a full-width barrier after every 2Q gate, so SABRE's
front layer always holds exactly one gate (the same decision space as our router: which
SWAPs go between consecutive gates). Output is converted to the competition format and
scored ONLY with the official starter_kit scorer. Research only; qiskit is not a
dependency of the submission.

    python "research and extra work/research/sabre_calibration.py" <n_seeds>
"""

from __future__ import annotations

import json
import sys

from qiskit import QuantumCircuit
from qiskit.transpiler import CouplingMap
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager

from common import BENCHMARKS, EDGES, GRAPH, HERE, N, official


def build_circuit(program, logicals):
    idx = {q: i for i, q in enumerate(logicals)}
    qc = QuantumCircuit(len(logicals))
    for op in program:
        if op[0] == "2Q":
            qc.cz(idx[op[1]], idx[op[2]])
            qc.barrier()
    return qc, idx


def convert(program, logicals, tqc):
    layout = tqc.layout.initial_layout
    v2p = {}
    for vq, p in layout.get_virtual_bits().items():
        v2p[vq] = p
    qubits_in = tqc.layout.input_qubit_mapping  # virtual Qubit -> index in the input circuit
    placement = {}
    for vq, i in qubits_in.items():
        if i < len(logicals):
            placement[logicals[i]] = v2p[vq]
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    routed = []
    gates = [op for op in program if op[0] == "2Q"]
    k = 0
    for inst in tqc.data:
        name = inst.operation.name
        if name == "barrier":
            continue
        ps = [tqc.find_bit(q).index for q in inst.qubits]
        if name == "swap":
            u, v = ps
            routed.append(("SWAP", u, v))
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        elif name == "cz":
            a, b = gates[k][1], gates[k][2]
            assert {pos[a], pos[b]} == set(ps), "gate order/mapping mismatch"
            routed.append(("2Q", pos[a], pos[b]))
            k += 1
        else:
            raise ValueError(name)
    assert k == len(gates)
    return placement, routed


def linearize(program, logicals, tqc):
    """Unconstrained SABRE output -> strict-order routed program, if a topological order of the
    routed DAG exists that keeps program gates in program order (depth is DAG-invariant)."""
    layout = tqc.layout.initial_layout
    v2p = layout.get_virtual_bits()
    placement = {logicals[i]: v2p[vq] for vq, i in tqc.layout.input_qubit_mapping.items() if i < len(logicals)}
    ops = []
    for inst in tqc.data:
        if inst.operation.name == "barrier":
            continue
        ops.append((inst.operation.name, tuple(tqc.find_bit(q).index for q in inst.qubits)))
    # per-wire queues of op indices
    queues = {}
    for i, (_, ps) in enumerate(ops):
        for p in ps:
            queues.setdefault(p, []).append(i)
    head = {p: 0 for p in queues}
    done = [False] * len(ops)
    gates = [op for op in program if op[0] == "2Q"]
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    routed = []
    k = 0

    def is_source(i):
        return all(queues[p][head[p]] == i for p in ops[i][1])

    remaining = len(ops)
    while remaining:
        progressed = False
        for p in list(queues):
            if head[p] >= len(queues[p]):
                continue
            i = queues[p][head[p]]
            if done[i] or ops[i][0] != "swap" or not is_source(i):
                continue
            u, v = ops[i][1]
            routed.append(("SWAP", u, v))
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
            done[i] = True
            remaining -= 1
            for q in ops[i][1]:
                head[q] += 1
            progressed = True
        if k < len(gates):
            a, b = gates[k][1], gates[k][2]
            pa, pb = pos[a], pos[b]
            i = queues[pa][head[pa]] if head[pa] < len(queues[pa]) else None
            if i is not None and ops[i][0] == "cz" and set(ops[i][1]) == {pa, pb} and is_source(i):
                routed.append(("2Q", pa, pb))
                done[i] = True
                remaining -= 1
                head[pa] += 1
                head[pb] += 1
                k += 1
                progressed = True
        if not progressed:
            return None
    return placement, routed


def run_variant(program, logicals, cm, n_seeds, variant):
    qc_bar, _ = build_circuit(program, logicals)
    idx = {q: i for i, q in enumerate(logicals)}
    qc_free = QuantumCircuit(len(logicals))
    for op in program:
        if op[0] == "2Q":
            qc_free.cz(idx[op[1]], idx[op[2]])
    best = None
    swaps_seen = []
    linearizable = 0
    for seed in range(n_seeds):
        if variant == "barrier":
            pm = generate_preset_pass_manager(optimization_level=0, coupling_map=cm, layout_method="sabre",
                                              routing_method="sabre", seed_transpiler=seed)
            out = convert(program, logicals, pm.run(qc_bar))
        elif variant == "layout_free_route_barrier":
            pm = generate_preset_pass_manager(optimization_level=0, coupling_map=cm, layout_method="sabre",
                                              routing_method="sabre", seed_transpiler=seed)
            t_free = pm.run(qc_free)
            init = t_free.layout.initial_layout
            v2p = init.get_virtual_bits()
            layout_list = [None] * len(logicals)
            for vq, i in t_free.layout.input_qubit_mapping.items():
                if i < len(logicals):
                    layout_list[i] = v2p[vq]
            pm2 = generate_preset_pass_manager(optimization_level=0, coupling_map=cm, initial_layout=layout_list,
                                               routing_method="sabre", seed_transpiler=seed)
            out = convert(program, logicals, pm2.run(qc_bar))
        else:  # unconstrained, keep only if re-linearizable into strict order
            pm = generate_preset_pass_manager(optimization_level=0, coupling_map=cm, layout_method="sabre",
                                              routing_method="sabre", seed_transpiler=seed)
            out = linearize(program, logicals, pm.run(qc_free))
            if out is None:
                continue
            linearizable += 1
        res = official(program, *out)
        assert res["valid"], res
        swaps_seen.append(res["swap_count"])
        if best is None or res["score"] < best["score"]:
            best = dict(res, seed=seed)
    return {"best": best, "min_swaps_seen": min(swaps_seen) if swaps_seen else None,
            "median_swaps": sorted(swaps_seen)[len(swaps_seen) // 2] if swaps_seen else None,
            "valid_outputs": len(swaps_seen), "linearizable": linearizable if variant == "free_linearized" else None}


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    cm = CouplingMap([list(e) for e in EDGES] + [[v, u] for u, v in EDGES])
    all_results = {}
    for variant in ("barrier", "layout_free_route_barrier", "free_linearized"):
        results = {}
        for name, program in BENCHMARKS.items():
            logicals = sorted({q for op in program for q in op[1:]})
            results[name] = run_variant(program, logicals, cm, n_seeds, variant)
            print(variant, name, json.dumps(results[name]), flush=True)
        total = sum(r["best"]["score"] for r in results.values() if r["best"]) if all(
            r["best"] for r in results.values()) else None
        print(variant, "TOTAL best-of-seeds official score:", total, flush=True)
        all_results[variant] = {"results": results, "total": total}
    (HERE / "sabre_calibration.json").write_text(json.dumps({"n_seeds": n_seeds, **all_results}, indent=1))


if __name__ == "__main__":
    assert N == GRAPH.number_of_nodes()
    main()
