"""Exact window re-routing (LNS) of a known solution with FIXED boundary mappings.

For a window of gates [i, j) of the captured production solution: start from the exact mapping
before gate i's SWAPs, execute gates i..j-1 in order, and end in exactly the mapping the original
solution has when gate j-1 executes (so the untouched suffix stays valid). Search (general move
model, any SWAP on any edge) for strictly fewer SWAPs than the original window uses. Every
spliced candidate is re-scored with the official scorer; accepted only if the official score
improves.

    python "research and extra work/research/lns_window.py" <benchmark> <min_w> <max_w> <per_window_s>
"""

from __future__ import annotations

import json
import sys
import time

from common import BENCHMARKS, DIST, EDGES, HERE, N, load_json, official


class Timeout(Exception):
    pass


def anatomy(program, placement, routed):
    pos = {int(k): v for k, v in placement.items()}
    p2l = {p: l for l, p in pos.items()}
    before, swaps, cur = [], [], []
    for op in routed:
        if op[0] == "SWAP":
            cur.append((op[1], op[2]))
            _, u, v = op
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        elif op[0] == "2Q":
            swaps.append(cur)
            cur = []
    # mapping before the swaps of each gate
    pos = {int(k): v for k, v in placement.items()}
    p2l = {p: l for l, p in pos.items()}
    for k, sw in enumerate(swaps):
        before.append(dict(pos))
        for u, v in sw:
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
    before.append(dict(pos))  # mapping after last gate (no trailing swaps)
    return before, swaps


def window_search(gates, start, target, budget_max, time_limit):
    """Min SWAPs to run `gates` in order from mapping `start`, ending exactly at `target`."""
    dist = [[DIST[u][v] for v in range(N)] for u in range(N)]
    logicals = sorted(start)
    li = {q: i for i, q in enumerate(logicals)}
    g = [(li[a], li[b]) for a, b in gates]
    G = len(g)
    L = len(logicals)
    pos = [start[q] for q in logicals]
    tgt = [target[q] for q in logicals] if target is not None else None
    occ = [-1] * N
    for i, p in enumerate(pos):
        occ[p] = i
    deadline = time.perf_counter() + time_limit
    memo = {}
    trail = []
    nodes = [0]

    def h(k):
        tok = sum(dist[pos[i]][tgt[i]] for i in range(L)) if tgt is not None else 0
        best = (tok + 1) // 2
        if k < G:
            a, b = g[k]
            best = max(best, dist[pos[a]][pos[b]] - 1)
            phi = 0
            taken = set()
            for j in range(k, G):
                x, y = g[j]
                if x in taken or y in taken:
                    continue
                taken.add(x)
                taken.add(y)
                phi += dist[pos[x]][pos[y]] - 1
            best = max(best, (phi + 1) // 2)
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
        k0 = k
        while k < G and dist[pos[g[k][0]]][pos[g[k][1]]] == 1:
            trail.append(("gate", k))
            k += 1
        if k != k0:
            last = None
        if k == G and (tgt is None or pos == tgt):
            return True
        key = (k, tuple(pos))
        prev = memo.get(key)
        if prev is not None and prev >= rem:
            return False
        if rem <= 0:
            return False
        cands = []
        for e in EDGES:
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
        cands.sort()
        for _, (u, v) in cands:
            do_swap(u, v)
            trail.append(("swap", u, v))
            if dfs(k, rem - 1, (u, v)):
                return True
            trail.pop()
            do_swap(u, v)
        if len(memo) > 400_000:
            memo.clear()
        memo[key] = rem
        return False

    lb = h(0)
    for B in range(lb, budget_max + 1):
        trail.clear()
        try:
            if dfs(0, B, None):
                # per-gate swap lists (+ trailing swaps after last gate)
                per = [[] for _ in range(G + 1)]
                k = 0
                for st in trail:
                    if st[0] == "swap":
                        per[k].append((st[1], st[2]))
                    else:
                        k += 1
                return B, per, nodes[0]
        except Timeout:
            return None, B - 1, nodes[0]
        memo.clear()
    return None, budget_max, nodes[0]


def splice(program, placement, swaps, i, j, new_per):
    gates2 = [op for op in program if op[0] == "2Q"]
    routed = []
    for k, op in enumerate(gates2):
        if i <= k < j:
            sw = new_per[k - i]
        else:
            sw = swaps[k]
        if k == j:
            sw = new_per[j - i] + sw  # trailing window swaps go before gate j's own swaps
        routed.extend(("SWAP", u, v) for u, v in sw)
        routed.append(("PENDING", k))
    if j == len(gates2):
        routed.extend(("SWAP", u, v) for u, v in new_per[j - i])
    # resolve physical operands by replay
    pos = {int(k): v for k, v in placement.items()}
    p2l = {p: l for l, p in pos.items()}
    out = []
    for op in routed:
        if op[0] == "SWAP":
            _, u, v = op
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
            out.append(op)
        else:
            a, b = gates2[op[1]][1], gates2[op[1]][2]
            out.append(("2Q", pos[a], pos[b]))
    return out


def main():
    name = sys.argv[1]
    wmin, wmax, tl = int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    program = BENCHMARKS[name]
    sol = load_json("current_solutions.json")[name]
    placement = {int(k): v for k, v in sol["placement"].items()}
    routed = [tuple(op) for op in sol["routed"]]
    base = official(program, placement, routed)
    print("baseline official:", base, flush=True)
    before, swaps = anatomy(program, placement, routed)
    gates = [(op[1], op[2]) for op in program if op[0] == "2Q"]
    G = len(gates)
    rows = []
    best = base
    suffix_only = len(sys.argv) > 5 and sys.argv[5] == "suffix"
    for w in range(wmin, wmax + 1):
        for i in range(0, G - w + 1):
            j = i + w
            if suffix_only and j != G:
                continue
            orig = sum(len(swaps[k]) for k in range(i, j))
            if orig == 0:
                continue
            t0 = time.perf_counter()
            res, per, nodes = window_search(gates[i:j], before[i], before[j] if j < G else None, orig - 1, tl)
            row = {"w": w, "i": i, "j": j, "orig_swaps": orig, "t": round(time.perf_counter() - t0, 1)}
            if res is not None:
                new_routed = splice(program, placement, swaps, i, j, per)
                off = official(program, placement, new_routed)
                row.update(new_swaps=res, spliced_official=off)
                if off["valid"] and off["score"] < best["score"]:
                    best = off
                    row["IMPROVES_BEST"] = True
            elif per >= orig - 1:
                row["window_locally_optimal_for_swaps"] = True
            else:
                row["timeout_proven_min_gt"] = per
            rows.append(row)
            print(json.dumps(row), flush=True)
    (HERE / f"lns_window_{name}.json").write_text(json.dumps({"baseline": base, "best": best, "rows": rows}, indent=0))
    n_opt = sum(1 for r in rows if r.get("window_locally_optimal_for_swaps"))
    n_imp = sum(1 for r in rows if "new_swaps" in r)
    n_to = sum(1 for r in rows if "timeout_proven_min_gt" in r)
    print(f"windows={len(rows)} swap-reducible={n_imp} proven-locally-optimal={n_opt} timeouts={n_to} best={best}")


if __name__ == "__main__":
    main()
