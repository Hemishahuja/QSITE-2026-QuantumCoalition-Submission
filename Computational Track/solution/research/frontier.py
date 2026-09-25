"""Swap/depth Pareto frontier reachable by the PRODUCTION beam router's move model when its
objective weight on depth is changed (research-only monkeypatch of State.cost; solve.py itself
is not modified). Every point is validated with the official scorer.

    python solution/research/frontier.py <benchmark>
"""

from __future__ import annotations

import importlib
import json
import random
import sys
import time

from common import BENCHMARKS, GRAPH, HERE, official

S = importlib.import_module("solution.solve")

name = sys.argv[1]
program = BENCHMARKS[name]
prog = S.Program(program)
hw = S.Hardware(GRAPH)
seq = S.GateSeq(prog.gates, prog.L)
points = {}
t0 = time.perf_counter()
for w in (0.5, 0.25, 0.1, 0.02):
    S.State.cost = property(lambda s, w=w: s.swaps + w * s.depth)
    for width in (64, 256, 1024):
        for slack in (0, 1, 2):
            router = S.Router(hw, seq, prog.L, {"width": width, "slack": slack, "paths": 3}, random.Random(0))
            # evaluate() hard-codes 0.5*depth; scale its depth term via alpha-free subclass
            st = router.run(S.initial_state(hw.n, prog.L, None), time.perf_counter() + 120)
            if st is None:
                continue
            pl, routed = S.build_output(prog, hw, st.l2t, S.unwind(st))
            res = official(program, pl, routed)
            assert res["valid"]
            key = (res["swap_count"], res["depth"])
            points.setdefault(key, []).append({"w": w, "width": width, "slack": slack})
            print(w, width, slack, res["swap_count"], res["depth"], res["score"],
                  f"{time.perf_counter()-t0:.0f}s", flush=True)
front = []
for (s, d) in sorted(points):
    if not front or d < front[-1][1]:
        front.append((s, d))
print("Pareto frontier (swaps, depth, score):", [(s, d, s + d / 2) for s, d in front])
(HERE / f"frontier_{name}.json").write_text(json.dumps({"points": {f"{s},{d}": v for (s, d), v in points.items()},
                                                        "frontier": front}, indent=1))
