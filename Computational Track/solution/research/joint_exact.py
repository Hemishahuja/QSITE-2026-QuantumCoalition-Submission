"""Exact JOINT search on the official objective f = 2*swaps + depth (score = f/2), research only.

Fully general move model: routed program = any list of SWAPs (any edge) and program gates in
program order; depth = official per-wire ASAP (tracked exactly via a 20-wire ready profile).
Free initial placement via lazy placement (see general_exact.py for why this is complete).

Unlike the SWAP-count search, eager execution is NOT assumed by default (SWAPs may be inserted
while the front gate is already adjacent, which can matter for depth). `eager=True` restores
the restriction (incomplete for depth; results are then labeled as restricted).

IDA* over thresholds T on f. Admissible bound:
    2*(swaps_so_far + swap_lb) + max(depth_so_far, depth_lb)
swap_lb: as in general_exact (front distance, disjoint-pair potential, optional suffix table).
depth_lb: logical ASAP chain over remaining gates from current per-token ready times, where the
front gate additionally needs d-1 SWAPs on the a- or b-chain.

    python solution/research/joint_exact.py <benchmark> <T_start> <T_max> <seconds> [eager] [prefix]
Returns: first feasible T (optimal f, with official-scorer-verified witness) or the largest
T proven infeasible before timeout.
"""

from __future__ import annotations

import json
import sys
import time

from common import BENCHMARKS, DIST, EDGES, HERE, N, gates_of, official
from general_exact import relabel


class Timeout(Exception):
    pass


def joint_search(gates_in, T_start, T_max, time_limit, eager=False, suffix_lb=None, memo_cap=1_500_000,
                 verbose=True):
    gates, idx = relabel(gates_in)
    G = len(gates)
    L = len(idx)
    dist = [[DIST[u][v] for v in range(N)] for u in range(N)]
    edges = EDGES
    suf = suffix_lb if suffix_lb is not None else [0] * (G + 1)
    deadline = time.perf_counter() + time_limit
    pos = [-1] * L
    occ = [-1] * N
    ready = [0] * N
    trail: list = []
    memo: dict = {}
    nodes = [0]

    def swap_lb(k):
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
            v = (phi + 1) // 2 + suf[j + 1]
            if v > best:
                best = v
        return best

    def depth_lb(k):
        r = {}
        for q in range(L):
            if pos[q] >= 0:
                r[q] = ready[pos[q]]
        dmax = max(ready)
        for j in range(k, G):
            a, b = gates[j]
            ra, rb = r.get(a, 0), r.get(b, 0)
            if j == k and pos[a] >= 0 and pos[b] >= 0:
                need = dist[pos[a]][pos[b]] - 1
                # split `need` SWAPs between the two chains to minimise the later one
                lo, hi = (ra, rb) if ra <= rb else (rb, ra)
                gap = hi - lo
                if need <= gap:
                    t = hi + 1
                else:
                    t = hi + (need - gap + 1) // 2 + 1
            else:
                t = max(ra, rb) + 1
            r[a] = r[b] = t
            if t > dmax:
                dmax = t
        return dmax

    def f_lb(k, s):
        if k >= G:
            return 2 * s + max(ready)
        return 2 * (s + swap_lb(k)) + depth_lb(k)

    def do_swap(u, v):
        lu, lv = occ[u], occ[v]
        occ[u], occ[v] = lv, lu
        if lu >= 0:
            pos[lu] = v
        if lv >= 0:
            pos[lv] = u
        old = (ready[u], ready[v])
        t = max(old) + 1
        ready[u] = ready[v] = t
        return old

    def undo_swap(u, v, old):
        lu, lv = occ[u], occ[v]
        occ[u], occ[v] = lv, lu
        if lu >= 0:
            pos[lu] = v
        if lv >= 0:
            pos[lv] = u
        ready[u], ready[v] = old

    def dfs(k, s, T, last):
        mark = len(trail)
        if _dfs(k, s, T, last):
            return True
        del trail[mark:]
        return False

    def _dfs(k, s, T, last):
        nodes[0] += 1
        if nodes[0] & 4095 == 0 and time.perf_counter() > deadline:
            raise Timeout
        if k == G:
            return 2 * s + max(ready) <= T
        a, b = gates[k]
        pa, pb = pos[a], pos[b]
        if pa < 0 or pb < 0:
            empties = [p for p in range(N) if occ[p] < 0]
            opts = []
            if pa < 0 and pb < 0:
                for u in empties:
                    for v in empties:
                        if u != v:
                            opts.append((dist[u][v], ready[u] + ready[v], u, v))
            elif pa < 0:
                for u in empties:
                    opts.append((dist[u][pb], ready[u], u, pb))
            else:
                for v in empties:
                    opts.append((dist[pa][v], ready[v], pa, v))
            opts.sort()
            for _, _, u, v in opts:
                newa, newb = pos[a] < 0, pos[b] < 0
                if newa:
                    pos[a] = u
                    occ[u] = a
                if newb:
                    pos[b] = v
                    occ[v] = b
                if f_lb(k, s) <= T:
                    trail.append(("place", a if newa else -1, u, b if newb else -1, v))
                    if dfs(k, s, T, None):
                        return True
                    trail.pop()
                if newa:
                    pos[a] = -1
                    occ[u] = -1
                if newb:
                    pos[b] = -1
                    occ[v] = -1
            return False
        key = (k, tuple(pos), tuple(ready))
        prev = memo.get(key)
        if prev is not None and s >= prev[0] and T <= prev[1]:
            return False
        adjacent = dist[pa][pb] == 1
        if adjacent:
            old = (ready[pa], ready[pb])
            t = max(old) + 1
            ready[pa] = ready[pb] = t
            if f_lb(k + 1, s) <= T:
                trail.append(("gate", k))
                if dfs(k + 1, s, T, None):
                    return True
                trail.pop()
            ready[pa], ready[pb] = old
        if not (adjacent and eager):
            cands = []
            for e in edges:
                if e == last:
                    continue
                u, v = e
                if occ[u] < 0 and occ[v] < 0:
                    continue
                old = do_swap(u, v)
                fl = f_lb(k, s + 1)
                undo_swap(u, v, old)
                if fl <= T:
                    cands.append((fl, e))
            cands.sort()
            for _, (u, v) in cands:
                old = do_swap(u, v)
                trail.append(("swap", u, v))
                if dfs(k, s + 1, T, (u, v)):
                    return True
                trail.pop()
                undo_swap(u, v, old)
        if len(memo) > memo_cap:
            memo.clear()
        memo[key] = (s, T)
        return False

    proven = T_start - 1
    for T in range(T_start, T_max + 1):
        t0 = time.perf_counter()
        trail.clear()
        try:
            ok = dfs(0, 0, T, None)
        except Timeout:
            return proven, None, nodes[0]
        if verbose:
            print(f"  T={T} (score {T/2}): {'FEASIBLE' if ok else 'infeasible'} "
                  f"({time.perf_counter()-t0:.1f}s, nodes={nodes[0]}, memo={len(memo)})", flush=True)
        if ok:
            return T, (list(trail), idx), nodes[0]
        proven = T
        for i in range(L):
            pos[i] = -1
        for p in range(N):
            occ[p] = -1
            ready[p] = 0
    return proven, None, nodes[0]


def witness_to_routed_joint(program, witness_idx):
    """Replay the exact op list (swaps interleaved with gates, in search order)."""
    witness, idx = witness_idx
    inv = {v: k for k, v in idx.items()}
    p2t = list(range(N))
    t2l = {}
    ops = []
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
            ops.append(("SWAP", u, v))
        else:
            ops.append(("GATE", step[1]))
    placement = {inv[l]: t for t, l in t2l.items()}
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    gates = [op for op in program if op[0] == "2Q"]
    routed = []
    for op in ops:
        if op[0] == "SWAP":
            _, u, v = op
            routed.append(op)
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        else:
            g = gates[op[1]]
            routed.append(("2Q", pos[g[1]], pos[g[2]]))
    return placement, routed


def main():
    name = sys.argv[1]
    T0, T1, tl = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    eager = len(sys.argv) > 5 and sys.argv[5] == "eager"
    prefix = int(sys.argv[6]) if len(sys.argv) > 6 else None
    prog = [op for op in BENCHMARKS[name] if op[0] == "2Q"]
    if prefix:
        prog = prog[:prefix]
    suffix = None
    lb_file = HERE / f"interval_lb_{name}.json"
    if lb_file.exists() and not prefix:
        data = json.loads(lb_file.read_text())
        G = data["G"]
        iv = {tuple(map(int, k.split("-"))): v[0] for k, v in data["intervals"].items()}
        # suffix[j] = best partition bound for gates[j:G]
        B = [0] * (G + 1)
        for j in range(G - 1, -1, -1):
            B[j] = max([iv.get((j, m), 0) + B[m] for m in range(j + 1, G + 1)])
        suffix = B
        print("using interval suffix bounds:", suffix)
    t0 = time.perf_counter()
    res, wit, nodes = joint_search(gates_of(prog), T0, T1, tl, eager=eager, suffix_lb=suffix)
    row = {"benchmark": name, "prefix": prefix, "eager": eager, "T_start": T0, "elapsed": round(time.perf_counter() - t0, 1),
           "nodes": nodes}
    if wit:
        pl, routed = witness_to_routed_joint(prog, wit)
        row.update(optimal_f=res, optimal_score=res / 2, witness_official=official(prog, pl, routed),
                   witness={"placement": pl, "routed": routed})
    else:
        row.update(proven_f_gt=res, proven_score_ge=(res + 1) / 2)
    print(json.dumps({k: v for k, v in row.items() if k != "witness"}))
    with (HERE / "joint_exact_results.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
