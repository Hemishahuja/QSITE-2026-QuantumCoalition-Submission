"""Architecture gap on the TRUE objective: production solve() vs joint exact optimum
(general move model, exact ASAP depth) on small random programs on the real hardware.

    python solution/research/arch_gap.py <n_instances> <seed> <solve_budget_s> <exact_s>
"""

from __future__ import annotations

import json
import random
import sys
import time

from common import GRAPH, HERE, critical_path, official
from joint_exact import joint_search, witness_to_routed_joint

from solution.solve import solve

n_inst, seed, budget, exact_s = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
rng = random.Random(seed)
out = HERE / f"arch_gap_seed{seed}.jsonl"
stats = {"done": 0, "prod_optimal": 0, "prod_worse": 0, "exact_timeout": 0}
for i in range(n_inst):
    L = rng.randint(6, 10)
    G = rng.randint(8, 14)
    prog = []
    while len(prog) < G:
        a, b = rng.sample(range(L), 2)
        prog.append(("2Q", a, b))
    gates = [(op[1], op[2]) for op in prog]
    pl, routed = solve(prog, GRAPH, time_budget=budget)
    prod = official(prog, pl, routed)
    T_prod = int(round(2 * prod["score"]))
    t0 = time.perf_counter()
    # search for anything strictly better than production
    res, wit, _ = joint_search(gates, critical_path(gates), T_prod - 1, exact_s, verbose=False)
    row = {"i": i, "L": L, "G": G, "prod": prod, "program": prog, "t_exact": round(time.perf_counter() - t0, 1)}
    if wit is not None:
        epl, erouted = witness_to_routed_joint(prog, wit)
        ex = official(prog, epl, erouted)
        row["better_exact"] = ex
        row["witness"] = {"placement": epl, "routed": erouted}
        stats["prod_worse"] += 1
        print("PROD SUBOPTIMAL", i, prod["score"], "->", ex["score"], ex, flush=True)
    elif res >= T_prod - 1:
        row["prod_proven_optimal"] = True
        stats["prod_optimal"] += 1
    else:
        row["exact_timeout_proven_f_gt"] = res
        stats["exact_timeout"] += 1
    stats["done"] += 1
    with out.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(i, stats, flush=True)
print("FINAL", stats)
