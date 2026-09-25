"""Q-Synth (Shaik & van de Pol, SAT'24) optimal SWAP count under DAG (gate-dependency) semantics.

The DAG model allows executing independent gates out of program order, so its feasible set is a
SUPERSET of ours (strict program order). Hence Q-Synth's proven-optimal SWAP count is a valid
LOWER BOUND on our minimum SWAP count. Research only; not a submission dependency.

    python solution/research/qsynth_lb.py <benchmark> <timeout_s>
"""

from __future__ import annotations

import json
import sys
import time

from qiskit import QuantumCircuit
from qsynth import layout_synthesis

from common import BENCHMARKS, EDGES, HERE

name = sys.argv[1]
timeout = float(sys.argv[2])
program = BENCHMARKS[name]
logicals = sorted({q for op in program for q in op[1:]})
idx = {q: i for i, q in enumerate(logicals)}
qc = QuantumCircuit(len(logicals))
for op in program:
    if op[0] == "2Q":
        qc.cx(idx[op[1]], idx[op[2]])
coupling = [list(e) for e in EDGES] + [[v, u] for u, v in EDGES]
t0 = time.perf_counter()
res = layout_synthesis(qc, coupling, metric="cx-count", timeout=timeout, verbose=int(sys.argv[3]) if len(sys.argv) > 3 else -1,
                       intermediate_files_path=str(HERE / "_qsynth_tmp"))
el = time.perf_counter() - t0
fields = {k: getattr(res, k) for k in dir(res) if not k.startswith("_") and not callable(getattr(res, k))
          and k not in ("circuit", "mapped_circuit")}
out = {"benchmark": name, "elapsed": round(el, 1), "timeout": timeout, "attrs": [k for k in dir(res) if not k.startswith("_")]}
circ = getattr(res, "circuit", None) or getattr(res, "mapped_circuit", None)
if circ is not None:
    ops = dict(circ.count_ops())
    out["mapped_count_ops"] = {k: int(v) for k, v in ops.items()}
    out["input_cx"] = len([op for op in program if op[0] == "2Q"])
    out["dag_optimal_swaps"] = int(ops.get("swap", 0)) if "swap" in ops else (int(ops.get("cx", 0)) - out["input_cx"]) // 3
for k, v in fields.items():
    try:
        json.dumps(v)
        out[k] = v
    except TypeError:
        out[k] = str(v)
print(json.dumps(out, indent=1))
with (HERE / "qsynth_results.jsonl").open("a") as f:
    f.write(json.dumps(out) + "\n")
