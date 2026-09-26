"""Exact minimum SWAP count under the FULLY GENERAL move model (research only).

Model: gates must execute in program order; between consecutive gates ANY sequence of SWAPs
on ANY hardware edges may be applied (including SWAPs that move qubits unrelated to the
front gate, and SWAPs with empty physical qubits). Initial placement is free (encoded as
lazy placement: an unplaced logical qubit may be put on any currently empty physical qubit
the first time it is used -- equivalent to choosing its initial position, since empty slots
are indistinguishable).

WLOG rules used (all sound for the SWAP-count objective):
  * execute the front gate as soon as it is adjacent (a gate does not change the mapping);
  * never SWAP two empty physical qubits (identity on the relevant state);
  * never immediately undo the previous SWAP;
  * transposition table on (gate index, mapping) storing the largest remaining budget that
    was proven insufficient (valid across IDA* iterations).

Admissible heuristic: max(d(front)-1, ceil(Phi/2)) where Phi = sum of (d-1) over a greedy
set of logically vertex-disjoint upcoming gates whose qubits are both placed. One SWAP moves
two tokens by one hop each, so it lowers Phi by at most 2.

Returns proven results only: (min_swaps, witness) or (lower_bound_proven, None) on timeout.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import networkx as nx

from common import DIST, EDGES, N


class Timeout(Exception):
    pass


def relabel(gates):
    idx = {}
    out = []
    for a, b in gates:
        for q in (a, b):
            if q not in idx:
                idx[q] = len(idx)
        out.append((idx[a], idx[b]))
    return out, idx


def general_min_swaps(gates_in, max_budget=40, time_limit=60.0, start_budget=0, verbose=False,
                      memo_cap=6_000_000, graph=None, suffix_lb=None, order_seed: int | None = None):
    """suffix_lb[j] (optional): proven lower bound on SWAPs strictly after gate j-1 executes
    under a FREE mapping at that point (e.g. from interval_lb.py). Added to the local bound."""
    gates, idx = relabel(gates_in)
    G = len(gates)
    L = len(idx)
    if graph is None:
        n = N
        dist = [[DIST[u][v] for v in range(N)] for u in range(N)]
        edges = EDGES
    else:
        n = graph.number_of_nodes()
        d = dict(nx.all_pairs_shortest_path_length(graph))
        dist = [[d[u].get(v, 10**6) for v in range(n)] for u in range(n)]
        edges = sorted(tuple(sorted(e)) for e in graph.edges)
    return _search(gates, idx, G, L, n, dist, edges, max_budget, time_limit, start_budget, verbose,
                   memo_cap, suffix_lb, order_seed)


def _search(gates, idx, G, L, N, dist, edges, max_budget, time_limit, start_budget, verbose, memo_cap,
            suffix_lb, order_seed=None):
    deadline = time.perf_counter() + time_limit
    order_rng = random.Random(order_seed) if order_seed is not None else None
    pos = [-1] * L
    occ = [-1] * N
    memo: dict = {}
    trail: list = []
    nodes = [0]

    suf = suffix_lb if suffix_lb is not None else [0] * (G + 1)

    def h(k):
        a, b = gates[k]
        pa, pb = pos[a], pos[b]
        df = dist[pa][pb] - 1 if pa >= 0 and pb >= 0 else 0
        best = df + suf[k + 1]
        phi = 0
        taken = set()
        for j in range(k, G):
            x, y = gates[j]
            if x not in taken and y not in taken:
                px, py = pos[x], pos[y]
                if px >= 0 and py >= 0:
                    taken.add(x)
                    taken.add(y)
                    phi += dist[px][py] - 1
            # swaps before gate j executes are disjoint in time from swaps after it
            v = (phi + 1) // 2 + suf[j + 1]
            if v > best:
                best = v
        return best

    def do_swap(u, v):
        lu, lv = occ[u], occ[v]
        occ[u], occ[v] = lv, lu
        if lu >= 0:
            pos[lu] = v
        if lv >= 0:
            pos[lv] = u

    def dfs(k, rem, last):
        mark = len(trail)
        if _dfs(k, rem, last):
            return True
        del trail[mark:]
        return False

    def _dfs(k, rem, last):
        nodes[0] += 1
        if nodes[0] & 4095 == 0 and time.perf_counter() > deadline:
            raise Timeout
        # eager execution
        k0 = k
        while k < G:
            a, b = gates[k]
            pa, pb = pos[a], pos[b]
            if pa >= 0 and pb >= 0 and dist[pa][pb] == 1:
                trail.append(("gate", k))
                k += 1
                continue
            break
        if k == G:
            return True
        if k != k0:
            last = None
        a, b = gates[k]
        pa, pb = pos[a], pos[b]
        if pa < 0 or pb < 0:
            empties = [p for p in range(N) if occ[p] < 0]
            opts = []
            if pa < 0 and pb < 0:
                for u in empties:
                    for v in empties:
                        if u != v and dist[u][v] - 1 <= rem:
                            opts.append((dist[u][v], u, v))
            elif pa < 0:
                for u in empties:
                    if dist[u][pb] - 1 <= rem:
                        opts.append((dist[u][pb], u, pb))
            else:
                for v in empties:
                    if dist[pa][v] - 1 <= rem:
                        opts.append((dist[pa][v], pa, v))
            opts.sort()
            for _, u, v in opts:
                newa, newb = pos[a] < 0, pos[b] < 0
                if newa:
                    pos[a] = u
                    occ[u] = a
                if newb:
                    pos[b] = v
                    occ[v] = b
                if h(k) <= rem:
                    trail.append(("place", a if newa else -1, u, b if newb else -1, v))
                    if dfs(k, rem, None):
                        return True
                    trail.pop()
                if newa:
                    pos[a] = -1
                    occ[u] = -1
                if newb:
                    pos[b] = -1
                    occ[v] = -1
            return False
        key = (k, tuple(pos))
        prev = memo.get(key)
        if prev is not None and prev >= rem:
            return False
        if rem <= 0:
            return False
        cands = []
        for e in edges:
            if e == last:
                continue
            u, v = e
            if occ[u] < 0 and occ[v] < 0:
                continue
            do_swap(u, v)
            hv = h(k)
            do_swap(u, v)
            if hv <= rem - 1:
                cands.append((hv, e))
        if order_rng is None:
            cands.sort()
        else:
            cands.sort(key=lambda item: (item[0], order_rng.random()))
        for _, (u, v) in cands:
            do_swap(u, v)
            trail.append(("swap", u, v))
            if dfs(k, rem - 1, (u, v)):
                return True
            trail.pop()
            do_swap(u, v)
        if len(memo) > memo_cap:
            memo.clear()
        memo[key] = rem
        return False

    proven = start_budget - 1
    for B in range(start_budget, max_budget + 1):
        t0 = time.perf_counter()
        trail.clear()
        try:
            ok = dfs(0, B, None)
        except Timeout:
            # reset state
            for i in range(L):
                pos[i] = -1
            for p in range(N):
                occ[p] = -1
            return proven, None, nodes[0]
        if verbose:
            print(f"  B={B}: {'FEASIBLE' if ok else 'infeasible'} ({time.perf_counter()-t0:.1f}s, nodes={nodes[0]}, memo={len(memo)})",
                  flush=True)
        if ok:
            witness = list(trail)
            return B, (witness, idx), nodes[0]
        proven = B
        for i in range(L):
            pos[i] = -1
        for p in range(N):
            occ[p] = -1
    return proven, None, nodes[0]


def witness_to_routed(program, witness_idx):
    """Convert a witness into (placement, routed_program) on the original logical labels."""
    witness, idx = witness_idx
    inv = {v: k for k, v in idx.items()}
    init = {}
    # replay: need initial positions. Lazily placed qubits took an empty slot; trace slot back.
    # Track tokens: token id = initial physical position.
    p2t = list(range(N))
    t2l = {}
    swaps_before_gate = []
    cur = []
    for step in witness:
        if step[0] == "place":
            _, a, u, b, v = step
            if a >= 0:
                t2l[p2t[u]] = a
            if b >= 0:
                t2l[p2t[v]] = b
        elif step[0] == "swap":
            _, u, v = step
            p2t[u], p2t[v] = p2t[v], p2t[u]
            cur.append((u, v))
        else:
            swaps_before_gate.append(cur)
            cur = []
    for t, l in t2l.items():
        init[inv[l]] = t
    placement = dict(init)
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    routed = []
    k = 0
    for op in program:
        if op[0] != "2Q":
            routed.append(("1Q", pos[op[1]]))
            continue
        for u, v in swaps_before_gate[k]:
            routed.append(("SWAP", u, v))
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        routed.append(("2Q", pos[op[1]], pos[op[2]]))
        k += 1
    return placement, routed


if __name__ == "__main__":
    from common import BENCHMARKS, gates_of, official

    name = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else None
    tl = float(sys.argv[3]) if len(sys.argv) > 3 else 120
    start = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    prog = [op for op in BENCHMARKS[name] if op[0] == "2Q"]
    if n:
        prog = prog[:n]
    Gp = len(prog)
    suffix = None
    lb_file = Path(__file__).resolve().parent / f"interval_lb_{name}.json"
    if lb_file.exists():
        data = json.loads(lb_file.read_text())
        iv = {tuple(map(int, k.split("-"))): v[0] for k, v in data["intervals"].items()}
        B = [0] * (Gp + 1)
        for j in range(Gp - 1, -1, -1):
            B[j] = max(iv.get((j, m), 0) + B[m] for m in range(j + 1, Gp + 1))
        suffix = B
        start = max(start, B[0])
        print("interval suffix bounds:", suffix)
    res, wit, nodes = general_min_swaps(gates_of(prog), time_limit=tl, verbose=True, start_budget=start,
                                        suffix_lb=suffix, memo_cap=1_000_000)
    print(name, "prefix", len(prog), "result", res, "witness" if wit else "LB only (min > result)", "nodes", nodes)
    if wit:
        pl, routed = witness_to_routed(prog, wit)
        print("official on prefix:", official(prog, pl, routed))
