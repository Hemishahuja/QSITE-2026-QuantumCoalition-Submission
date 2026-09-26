"""Resume Q-Synth's forward SWAP-count search at a given step (research only).

Same encoding as qsynth_lb.py (cx-count metric, DAG dependencies, full 20-qubit coupling graph),
but the forward search starts at `start_step` instead of 0, so already-proven UNSAT steps are not
redone. Q-Synth's own verbose printer crashes on a None result (timeout / not found); that path is
patched to log cleanly instead.

A step s reported "Result: False" means: no DAG-semantics mapping with exactly s SWAPs exists.
Forward search from 0 had already shown steps 0..10 UNSAT for dense_random (qs_dense.log).

    python "research and extra work/research/qsynth_resume.py" <benchmark> <start_step> <timeout_s> [verbose]
"""

from __future__ import annotations

import json
import sys
import time

import qsynth.api as qapi
import qsynth.LayoutSynthesis.layout_synthesis as qls
from qiskit import QuantumCircuit

from common import BENCHMARKS, EDGES, HERE

name = sys.argv[1]
start_step = int(sys.argv[2])
timeout = float(sys.argv[3])
verbose = int(sys.argv[4]) if len(sys.argv) > 4 else 1

_orig_ls = qls.layout_synthesis


def _ls_from_start(*args, **kwargs):
    if kwargs.get("search_strategy", "forward") == "forward":
        kwargs["start"] = start_step
    return _orig_ls(*args, **kwargs)


qls.layout_synthesis = _ls_from_start
_orig_print = qapi.print_result_and_stats


def _safe_print(circuit, result, verbose_level):
    if result is None:
        print("Q-Synth returned no result (internal timeout or no plan found)", flush=True)
        return
    _orig_print(circuit, result, verbose_level)


qapi.print_result_and_stats = _safe_print

program = BENCHMARKS[name]
logicals = sorted({q for op in program for q in op[1:]})
idx = {q: i for i, q in enumerate(logicals)}
qc = QuantumCircuit(len(logicals))
for op in program:
    if op[0] == "2Q":
        qc.cx(idx[op[1]], idx[op[2]])
coupling = [list(e) for e in EDGES] + [[v, u] for u, v in EDGES]
t0 = time.perf_counter()
res = qapi.layout_synthesis(qc, coupling, metric="cx-count", timeout=timeout, verbose=verbose,
                            intermediate_files_path=str(HERE / f"_qsynth_tmp_{name}_resume"))
el = time.perf_counter() - t0
out = {"benchmark": name, "start_step": start_step, "elapsed": round(el, 1), "timeout": timeout,
       "result": None if res is None else "found"}
circ = getattr(res, "circuit", None) if res is not None else None
if circ is not None:
    ops = dict(circ.count_ops())
    out["mapped_count_ops"] = {k: int(v) for k, v in ops.items()}
    out["dag_optimal_swaps"] = int(ops.get("swap", 0))
    out["initial_mapping"] = getattr(res, "initial_mapping", None)
print(json.dumps(out, indent=1), flush=True)
with (HERE / "qsynth_results.jsonl").open("a") as f:
    f.write(json.dumps(out) + "\n")
