"""Joint exact search (f = 2*swaps + depth) with partial-order reduction, research only.

Same fully general move model, ASAP depth semantics, lazy placement and admissible bound as
joint_exact.py, plus reductions that keep at least one optimal routing reachable:

  * Partial-order reduction (trace normal form, gate letters ordered before all SWAP letters):
      - two consecutive SWAPs on vertex-disjoint edges must appear in increasing edge index;
      - a program gate may not execute immediately after a SWAP that touches neither of its wires
        (that SWAP commutes with the gate, so the gate-first order represents the same trace).
    Every trace (class of op sequences equal up to swapping adjacent ops on disjoint wires)
    has a lexicographically minimal representative satisfying both local rules, and all ops in a
    trace give the same final state (pos + ready profile), hence the same f.
  * No immediate undo of the previous SWAP, no SWAP of two unoccupied qubits (dominated).
  * Gate 0 is placed on an adjacent pair with nothing else placed (swaps before gate 0 are
    dominated), optionally one representative per hardware-automorphism orbit.
  * The memo key includes the last action, so a cached failure is only reused for an identical
    sub-search (no graph-history-interaction issue).

    python "research and extra work/research/je_por.py" <benchmark> <T> <seconds> [workers] [tag]
Runs one threshold T (feasible iff some routing has 2*swaps + depth <= T), splitting the root
placements of gate 0 across worker processes. Every witness is re-scored by the official scorer.
"""

from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool

from networkx.algorithms.isomorphism import GraphMatcher

from common import BENCHMARKS, DIST, EDGES, GRAPH, HERE, N, gates_of, official
from general_exact import relabel


class Timeout(Exception):
    pass


def por_search(gates_in, T, time_limit, n=N, dist=None, edges=None, suffix_lb=None, root_opts=None,
               memo_cap=600_000):
    """Returns (status, trail, idx, nodes); status in {"feasible", "infeasible", "timeout"}."""
    gates, idx = relabel(gates_in)
    G = len(gates)
    L = len(idx)
    if dist is None:
        dist = [[DIST[u][v] for v in range(n)] for u in range(n)]
    if edges is None:
        edges = EDGES
    E = len(edges)
    disjoint = [[not (set(edges[i]) & set(edges[j])) for j in range(E)] for i in range(E)]
    suf = suffix_lb if suffix_lb is not None else [0] * (G + 1)
    deadline = time.perf_counter() + time_limit
    pos = [-1] * L
    occ = [-1] * n
    ready = [0] * n
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
                lo, hi = (ra, rb) if ra <= rb else (rb, ra)
                gap = hi - lo
                t = hi + 1 if need <= gap else hi + (need - gap + 1) // 2 + 1
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

    def dfs(k, s, last):
        mark = len(trail)
        if _dfs(k, s, last):
            return True
        del trail[mark:]
        return False

    def _dfs(k, s, last):
        nodes[0] += 1
        if nodes[0] & 4095 == 0 and time.perf_counter() > deadline:
            raise Timeout
        if k == G:
            return 2 * s + max(ready) <= T
        a, b = gates[k]
        pa, pb = pos[a], pos[b]
        if pa < 0 or pb < 0:
            empties = [p for p in range(n) if occ[p] < 0]
            opts = []
            if k == 0 and root_opts is not None:
                opts = [(0, 0, u, v) for u, v in root_opts]
            elif pa < 0 and pb < 0:
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
                    if dfs(k, s, -1):
                        return True
                    trail.pop()
                if newa:
                    pos[a] = -1
                    occ[u] = -1
                if newb:
                    pos[b] = -1
                    occ[v] = -1
            return False
        key = (k, tuple(pos), tuple(ready), last)
        prev = memo.get(key)
        if prev is not None and s >= prev[0] and T <= prev[1]:
            return False
        if dist[pa][pb] == 1 and (last < 0 or pa in edges[last] or pb in edges[last]):
            old = (ready[pa], ready[pb])
            t = max(old) + 1
            ready[pa] = ready[pb] = t
            if f_lb(k + 1, s) <= T:
                trail.append(("gate", k))
                if dfs(k + 1, s, -1):
                    return True
                trail.pop()
            ready[pa], ready[pb] = old
        cands = []
        for i in range(E):
            if last >= 0 and (i == last or (disjoint[last][i] and i < last)):
                continue
            u, v = edges[i]
            if occ[u] < 0 and occ[v] < 0:
                continue
            old = do_swap(u, v)
            fl = f_lb(k, s + 1)
            undo_swap(u, v, old)
            if fl <= T:
                cands.append((fl, i))
        cands.sort()
        for _, i in cands:
            u, v = edges[i]
            old = do_swap(u, v)
            trail.append(("swap", u, v))
            if dfs(k, s + 1, i):
                return True
            trail.pop()
            undo_swap(u, v, old)
        if len(memo) > memo_cap:
            memo.clear()
        memo[key] = (s, T)
        return False

    try:
        ok = dfs(0, 0, -1)
    except Timeout:
        return "timeout", None, idx, nodes[0]
    return ("feasible" if ok else "infeasible"), (list(trail) if ok else None), idx, nodes[0]


def witness_to_routed(program, trail, idx, n=N):
    """Replay the trail; initial placement of a lazily placed qubit = origin of its token."""
    inv = {v: k for k, v in idx.items()}
    p2t = list(range(n))
    t2l = {}
    ops = []
    for step in trail:
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


def root_orbit_reps(graph=GRAPH):
    autos = list(GraphMatcher(graph, graph).isomorphisms_iter())
    arcs = sorted((u, v) for u, v in graph.edges) + sorted((v, u) for u, v in graph.edges)
    reps, seen = [], set()
    for u, v in sorted(arcs):
        if (u, v) in seen:
            continue
        reps.append((u, v))
        for s in autos:
            seen.add((s[u], s[v]))
    return reps, len(autos)


def suffix_table(name, G):
    lb_file = HERE / f"interval_lb_{name}.json"
    if not lb_file.exists():
        return None
    data = json.loads(lb_file.read_text())
    iv = {tuple(map(int, k.split("-"))): v[0] for k, v in data["intervals"].items()}
    B = [0] * (G + 1)
    for j in range(G - 1, -1, -1):
        B[j] = max(iv.get((j, m), 0) + B[m] for m in range(j + 1, G + 1))
    return B


def _work(args):
    name, T, tl, root, memo_cap = args
    prog = [op for op in BENCHMARKS[name] if op[0] == "2Q"]
    gates = gates_of(prog)
    suf = suffix_table(name, len(gates))
    t0 = time.perf_counter()
    status, trail, idx, nodes = por_search(gates, T, tl, suffix_lb=suf, root_opts=[root], memo_cap=memo_cap)
    row = {"root": list(root), "status": status, "nodes": nodes, "elapsed": round(time.perf_counter() - t0, 1)}
    if trail is not None:
        pl, routed = witness_to_routed(prog, trail, idx)
        row["witness_official"] = official(prog, pl, routed)
        row["witness"] = {"placement": pl, "routed": routed}
    return row


def main():
    name = sys.argv[1]
    T = int(sys.argv[2])
    tl = float(sys.argv[3])
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    tag = sys.argv[5] if len(sys.argv) > 5 else ""
    memo_cap = int(sys.argv[6]) if len(sys.argv) > 6 else 400_000
    reps, n_auto = root_orbit_reps()
    print(f"{name} T={T} (score {T/2}): {len(reps)} root placements ({n_auto} automorphisms), "
          f"{workers} workers, {tl}s each", flush=True)
    t0 = time.perf_counter()
    out = HERE / f"je_por_{name}_T{T}{tag}.jsonl"
    statuses = []
    todo = reps
    if out.exists():
        prev = [json.loads(line) for line in out.read_text().splitlines()]
        decided = {tuple(r["root"]): r["status"] for r in prev if r["status"] != "timeout"}
        statuses = list(decided.values())
        todo = [r for r in reps if r not in decided]
        print(f"resuming: {len(decided)} roots already decided in {out.name}", flush=True)
    with Pool(workers) as pool:
        for row in pool.imap_unordered(_work, [(name, T, tl, r, memo_cap) for r in todo]):
            statuses.append(row["status"])
            print(json.dumps({k: v for k, v in row.items() if k != "witness"}), flush=True)
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            if row["status"] == "feasible" and row["witness_official"]["valid"]:
                print("FEASIBLE witness found; stopping.", flush=True)
                pool.terminate()
                break
    summary = {"benchmark": name, "T": T, "roots": len(reps), "done": len(statuses),
               "infeasible": statuses.count("infeasible"), "timeout": statuses.count("timeout"),
               "feasible": statuses.count("feasible"), "elapsed": round(time.perf_counter() - t0, 1)}
    if summary["feasible"]:
        summary["verdict"] = f"FEASIBLE: score <= {T/2} achievable (see witness_official)"
    elif summary["infeasible"] == len(reps):
        summary["verdict"] = f"PROVEN INFEASIBLE: every routing has 2*swaps+depth > {T} (score >= {(T+1)/2})"
    else:
        summary["verdict"] = "UNDECIDED (some roots timed out)"
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
