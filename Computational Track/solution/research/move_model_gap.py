"""Move-model gap test: fully general exact min-SWAP vs production `exact_min_swaps`
(which only allows SWAPs incident to the front gate's current carrier positions).

Random small programs on the real 20-qubit hardware. Any instance where the restricted
optimum is strictly larger refutes the "delay" completeness argument in solve.py and
cpsat_solver.py.

    python solution/research/move_model_gap.py <n_instances> <seed>
"""

from __future__ import annotations

import json
import random
import sys
import time

from common import GRAPH, HERE, official
from general_exact import general_min_swaps, witness_to_routed

from solution.solve import Hardware, Program, build_output, exact_min_swaps

n_inst = int(sys.argv[1])
seed = int(sys.argv[2])
rng = random.Random(seed)
hw = Hardware(GRAPH)
out = HERE / f"move_model_gap_seed{seed}.jsonl"
gaps = 0
done = 0
for i in range(n_inst):
    L = rng.randint(5, 9)
    G = rng.randint(6, 14)
    prog = []
    while len(prog) < G:
        a, b = rng.sample(range(L), 2)
        prog.append(("2Q", a, b))
    gates = [(op[1], op[2]) for op in prog]
    t0 = time.perf_counter()
    gen, wit, _ = general_min_swaps(gates, max_budget=12, time_limit=30)
    t1 = time.perf_counter()
    if wit is None:
        continue
    rs, l2t, moves = exact_min_swaps(Program(prog), hw, time.perf_counter() + 60, max_s=12)
    t2 = time.perf_counter()
    if l2t is None:
        continue
    done += 1
    row = {"i": i, "L": L, "G": G, "general": gen, "restricted": rs, "tg": round(t1 - t0, 2), "tr": round(t2 - t1, 2),
           "program": prog}
    # verify both witnesses with the official scorer
    pl, routed = witness_to_routed(prog, wit)
    row["general_official"] = official(prog, pl, routed)
    pl2, routed2 = build_output(Program(prog), hw, l2t, moves)
    row["restricted_official"] = official(prog, pl2, routed2)
    if rs != gen:
        gaps += 1
        row["GAP"] = True
        row["general_witness"] = {"placement": pl, "routed": routed}
        print("GAP", json.dumps(row), flush=True)
    with out.open("a") as f:
        f.write(json.dumps(row) + "\n")
print(f"done={done} gaps={gaps}")
