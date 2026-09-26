"""E5: SabreLayout-style many random-layout lineages (forward -> backward -> forward) using the
production beam router (via Solver internals; solve.py unchanged). Official scorer on every result.

    python "research and extra work/research/many_lineages.py" <benchmark> <seconds> <width>
"""

from __future__ import annotations

import importlib
import json
import random
import sys
import time

from common import BENCHMARKS, GRAPH, HERE, official

S = importlib.import_module("solution.solve")

name, secs, width = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
program = BENCHMARKS[name]
solver = S.Solver(program, GRAPH, seed=1)
rng = random.Random(12345)
deadline = time.perf_counter() + secs
best = None
scores = []
n = 0
while time.perf_counter() < deadline:
    n += 1
    phys = list(range(solver.hw.n))
    rng.shuffle(phys)
    pl = phys[: solver.prog.L]
    params = {"width": width, "slack": rng.choice((0, 1, 2)), "paths": rng.choice((1, 2, 3))}
    st = solver._beam(pl, params, deadline)
    for _ in range(2):
        if st is None:
            break
        back = solver._beam(solver._final_placement(st), params, deadline, reverse=True)
        if back is None:
            break
        st = solver._beam(solver._final_placement(back), params, deadline)
    if st is None:
        continue
    placement, routed = S.build_output(solver.prog, solver.hw, st.l2t, S.unwind(st))
    res = official(program, placement, routed)
    if not res["valid"]:
        continue
    scores.append(res["score"])
    if best is None or res["score"] < best["score"]:
        best = res
        print(n, "new best", res, flush=True)
print(f"lineages={len(scores)} best={best} median={sorted(scores)[len(scores)//2] if scores else None}")
(HERE / f"many_lineages_{name}.json").write_text(json.dumps({"lineages": len(scores), "best": best,
                                                              "scores": scores, "width": width}))
