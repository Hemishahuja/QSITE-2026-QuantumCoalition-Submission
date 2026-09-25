"""Exact min-SWAP (general move model) for growing program prefixes.

min_swaps(prefix) is monotone in prefix length, so every prefix optimum (or proven prefix
lower bound) is a valid lower bound on the SWAP count of the whole program.

    python solution/research/prefix_bounds.py <benchmark> <start_len> <step> <per_prefix_seconds>
"""

from __future__ import annotations

import json
import sys
import time

from common import BENCHMARKS, HERE, gates_of, official
from general_exact import general_min_swaps, witness_to_routed

name = sys.argv[1]
start = int(sys.argv[2])
step = int(sys.argv[3])
tl = float(sys.argv[4])
prog = [op for op in BENCHMARKS[name] if op[0] == "2Q"]
G = len(prog)
out_path = HERE / f"prefix_bounds_{name}.jsonl"
lb = 0
n = start
while n <= G:
    t0 = time.perf_counter()
    res, wit, nodes = general_min_swaps(gates_of(prog[:n]), time_limit=tl, start_budget=lb)
    row = {"benchmark": name, "prefix": n, "elapsed": round(time.perf_counter() - t0, 1), "nodes": nodes}
    if wit:
        pl, routed = witness_to_routed(prog[:n], wit)
        off = official(prog[:n], pl, routed)
        row.update(exact_min_swaps=res, witness_official=off)
        lb = res
    else:
        row.update(proven_min_swaps_gt=res, timeout=True)
        lb = max(lb, res + 1)
    row["global_swap_lb_so_far"] = lb
    print(json.dumps(row), flush=True)
    with out_path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    if not wit:
        break
    n = min(G, n + step) if n < G else G + 1
