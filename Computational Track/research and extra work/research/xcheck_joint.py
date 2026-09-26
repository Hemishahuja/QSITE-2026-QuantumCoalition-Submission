"""Brute-force cross-check of joint_exact.joint_search and je_por.por_search on tiny instances.

Brute force: every injective placement x every sequence of <= S SWAPs (any edge, including
empty-empty and immediate undos) inserted before each gate; f = 2*swaps + depth with the
official ASAP rule; the argmin is re-scored with starter_kit.scorer.score_summary on the small graph.

Checks (exact searches are uncapped in SWAPs):
  * soundness: brute_min(S) >= exact optimum f* (else the exact search wrongly proved infeasibility);
  * tightness: if the exact witness uses <= S SWAPs then brute_min(S) == f*;
  * every exact witness is valid under the official scorer with score f*/2.

    python "research and extra work/research/xcheck_joint.py" <instances> <seed>
"""

from __future__ import annotations

import itertools
import json
import random
import sys
import time

import networkx as nx

import joint_exact
import je_por
from common import HERE
from starter_kit.scorer import score_summary


def brute(graph, gates, S):
    n = graph.number_of_nodes()
    edges = sorted(tuple(sorted(e)) for e in graph.edges)
    adj = {u: set(graph.neighbors(u)) for u in graph.nodes}
    qubits = sorted({q for g in gates for q in g})
    G = len(gates)
    best = [10**9, None]

    def rec(k, used, pos, p2l, ready, ops):
        if k == G:
            f = 2 * used + max(ready)
            if f < best[0]:
                best[0], best[1] = f, list(ops)
            return
        a, b = gates[k]
        pa, pb = pos[a], pos[b]
        if pb in adj[pa]:
            t = max(ready[pa], ready[pb]) + 1
            r2 = ready[:]
            r2[pa] = r2[pb] = t
            ops.append(("2Q", pa, pb))
            rec(k + 1, used, pos, p2l, r2, ops)
            ops.pop()
        if used < S:
            for u, v in edges:
                lu, lv = p2l[u], p2l[v]
                pos2 = dict(pos)
                p2l2 = p2l[:]
                p2l2[u], p2l2[v] = lv, lu
                if lu is not None:
                    pos2[lu] = v
                if lv is not None:
                    pos2[lv] = u
                r2 = ready[:]
                r2[u] = r2[v] = max(ready[u], ready[v]) + 1
                ops.append(("SWAP", u, v))
                rec(k, used + 1, pos2, p2l2, r2, ops)
                ops.pop()

    best_pl = None
    for phys in itertools.permutations(range(n), len(qubits)):
        pos = dict(zip(qubits, phys))
        p2l = [None] * n
        for q, p in pos.items():
            p2l[p] = q
        before = best[0]
        rec(0, 0, pos, p2l, [0] * n, [])
        if best[0] < before:
            best_pl = dict(pos)
    return best[0], best_pl, best[1]


def random_instance(rng, hard=False):
    n = rng.choice([6, 7]) if hard else rng.choice([5, 6, 6, 7])
    while True:
        m = rng.randint(n - 1, n) if hard else rng.randint(n - 1, n + 1)
        g = nx.gnm_random_graph(n, m, seed=rng.randrange(10**9))
        if nx.is_connected(g):
            break
    L = rng.choice([4, 5]) if hard else rng.choice([3, 3, 4])
    G = rng.randint(6, 7) if hard else rng.randint(4, 6)
    gates = []
    while len(gates) < G:
        a, b = rng.sample(range(L), 2)
        gates.append((a, b))
    return g, gates


def run_joint_exact(g, gates, tl):
    n = g.number_of_nodes()
    d = dict(nx.all_pairs_shortest_path_length(g))
    saved = (joint_exact.N, joint_exact.DIST, joint_exact.EDGES)
    joint_exact.N, joint_exact.DIST = n, d
    joint_exact.EDGES = sorted(tuple(sorted(e)) for e in g.edges)
    try:
        res, wit, _ = joint_exact.joint_search(gates, 0, 60, tl, verbose=False)
    finally:
        joint_exact.N, joint_exact.DIST, joint_exact.EDGES = saved
    if wit is None:
        return None, None
    prog = [("2Q", a, b) for a, b in gates]
    trail, idx = wit
    pl, routed = je_por.witness_to_routed(prog, trail, idx, n=n)
    return res, score_summary(prog, g, pl, routed)


def run_por(g, gates, tl, restrict_root):
    n = g.number_of_nodes()
    d = dict(nx.all_pairs_shortest_path_length(g))
    dist = [[d[u].get(v, 10**6) for v in range(n)] for u in range(n)]
    edges = sorted(tuple(sorted(e)) for e in g.edges)
    prog = [("2Q", a, b) for a, b in gates]
    roots = None
    if restrict_root:
        roots = [(u, v) for u, v in edges] + [(v, u) for u, v in edges]
    for T in range(0, 60):
        feas_any = False
        if roots is None:
            status, trail, idx, _ = je_por.por_search(gates, T, tl, n=n, dist=dist, edges=edges)
            if status == "timeout":
                return None, None
            feas_any = status == "feasible"
        else:
            for r in roots:
                status, trail, idx, _ = je_por.por_search(gates, T, tl, n=n, dist=dist, edges=edges, root_opts=[r])
                if status == "timeout":
                    return None, None
                if status == "feasible":
                    feas_any = True
                    break
        if feas_any:
            pl, routed = je_por.witness_to_routed(prog, trail, idx, n=n)
            return T, score_summary(prog, g, pl, routed)
    return None, None


def main():
    count = int(sys.argv[1])
    rng = random.Random(int(sys.argv[2]))
    hard = len(sys.argv) > 3 and sys.argv[3] == "hard"
    S = 4 if hard else 3
    rows = []
    bad = 0
    for i in range(count):
        g, gates = random_instance(rng, hard)
        t0 = time.perf_counter()
        bmin, bpl, bops = brute(g, gates, S)
        prog = [("2Q", a, b) for a, b in gates]
        boff = score_summary(prog, g, bpl, bops)
        tb = time.perf_counter() - t0
        je_f, je_off = run_joint_exact(g, gates, 60)
        por_f, por_off = run_por(g, gates, 60, False)
        porr_f, porr_off = run_por(g, gates, 60, True)
        row = {"i": i, "n": g.number_of_nodes(), "edges": sorted(map(list, g.edges)), "gates": gates,
               "brute_f": bmin, "brute_official": boff["score"] * 2 if boff["valid"] else None,
               "joint_exact_f": je_f, "je_off": je_off and (je_off["valid"], je_off["score"] * 2, je_off["swap_count"]),
               "por_f": por_f, "por_off": por_off and (por_off["valid"], por_off["score"] * 2, por_off["swap_count"]),
               "por_root_f": porr_f,
               "porr_off": porr_off and (porr_off["valid"], porr_off["score"] * 2, porr_off["swap_count"]),
               "brute_s": round(tb, 1)}
        problems = []
        for label, f, off in (("joint_exact", je_f, je_off), ("por", por_f, por_off), ("por_root", porr_f, porr_off)):
            if f is None:
                problems.append(f"{label}: timeout")
                continue
            if not off["valid"] or off["score"] * 2 != f:
                problems.append(f"{label}: witness mismatch {off['valid']} {off['score']}")
            if bmin < f:
                problems.append(f"{label}: UNSOUND brute {bmin} < exact {f}")
            if off["swap_count"] <= S and bmin != f:
                problems.append(f"{label}: brute {bmin} != exact {f} although witness has <= {S} swaps")
        row["problems"] = problems
        bad += bool(problems)
        rows.append(row)
        print(json.dumps(row), flush=True)
    print(json.dumps({"instances": count, "with_problems": bad}), flush=True)
    with (HERE / "xcheck_joint_results.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
