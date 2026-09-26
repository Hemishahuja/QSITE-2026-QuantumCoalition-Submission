"""Run production solve() (20 s, seed 0) on every benchmark, save outputs + official scores.

    python "research and extra work/research/capture_current.py"
"""

from __future__ import annotations

import time

from common import BENCHMARKS, GRAPH, official, save_json

from solution.solve import solve

out = {}
for name, program in BENCHMARKS.items():
    t0 = time.perf_counter()
    placement, routed = solve(program, GRAPH, time_budget=20.0)
    res = official(program, placement, routed)
    res["elapsed"] = time.perf_counter() - t0
    print(name, res, flush=True)
    out[name] = {"placement": {str(k): v for k, v in placement.items()}, "routed": routed, "official": res}
save_json("current_solutions.json", out)
print("total", sum(v["official"]["score"] for v in out.values()))
