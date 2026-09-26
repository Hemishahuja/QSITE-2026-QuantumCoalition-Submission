"""Free-end-mapping window LNS on a captured solution (audit experiment E2), research only.

For a window of K consecutive program gates [i, i+K) of the current best route:
  1. Start state = the route's exact mapping and per-wire ready profile just after gate i-1.
  2. Enumerate EVERY end mapping reachable while executing gates i..i+K-1 in order with at most
     B SWAPs (B = SWAPs the current route spends in the window + `extra`). Move model: any SWAP on
     any hardware edge (at least one endpoint occupied), gates executed as soon as adjacent (this
     does not change the set of reachable end mappings for a given SWAP sequence). A memo on
     (gate index, mapping, ready profile) -> fewest SWAPs seen dedups commuting orders. For each end
     mapping keep the best (swaps + 0.5*max ready, sum ready) window route. The end mapping is FREE.
  3. Re-route the tail (gates i+K..G-1) from each end state with the production Router
     (read-only import from solution/solve.py), starting from the real mapping + ready profile so
     depth interactions are accounted: a narrow pass for every end state, then a wide pass for the
     best `top` of them.
  4. Splice prefix + window + tail, score with the REAL starter_kit.scorer.score_summary.

    python "research and extra work/research/lns_free.py" <benchmark> <K list> <start step> <enum_s> <workers> [extra] [top] [wide]
"""

from __future__ import annotations

import json
import random
import sys
import time
from multiprocessing import Pool

from common import BENCHMARKS, DIST, EDGES, GRAPH, HERE, N, load_json, official
from solution.solve import DEFAULT_PARAMS, GateSeq, Hardware, Program, Router, State, unwind

HW = Hardware(GRAPH)
assert HW.labels == list(range(N))


def load_current(name):
    cur = load_json("current_solutions.json")[name]
    placement = {int(k): v for k, v in cur["placement"].items()}
    routed = [tuple(op) for op in cur["routed"]]
    return placement, routed


def segment(program, placement, routed):
    """Per program gate k: index in `routed` of its op, mapping and ready profile just after gate k-1."""
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    ready = [0] * N
    states = [(dict(pos), ready[:], 0, 0)]  # (mapping, ready, routed index, swaps so far) after gate k-1
    swaps = 0
    k = 0
    for idx, op in enumerate(routed):
        if op[0] == "SWAP":
            _, u, v = op
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
            ready[u] = ready[v] = max(ready[u], ready[v]) + 1
            swaps += 1
        elif op[0] == "2Q":
            _, u, v = op
            ready[u] = ready[v] = max(ready[u], ready[v]) + 1
            k += 1
            states.append((dict(pos), ready[:], idx + 1, swaps))
    return states


class Timeout(Exception):
    pass


def enumerate_window(gates, start_pos, start_ready, B, time_limit, memo_cap=100_000, max_ends=4000):
    """All end mappings reachable with <= B SWAPs. gates: list of (a, b) logical labels."""
    qubits = sorted(start_pos)
    qi = {q: i for i, q in enumerate(qubits)}
    wg = [(qi[a], qi[b]) for a, b in gates]
    Kw = len(wg)
    pos = [start_pos[q] for q in qubits]
    occ = [-1] * N
    for i, p in enumerate(pos):
        occ[p] = i
    ready = start_ready[:]
    dist = [[DIST[u][v] for v in range(N)] for u in range(N)]
    deadline = time.perf_counter() + time_limit
    memo: dict = {}
    ends: dict = {}
    trail: list = []
    nodes = [0]
    complete = [True]

    def h(k):
        a, b = wg[k]
        best = dist[pos[a]][pos[b]] - 1
        phi = 0
        taken = set()
        for j in range(k, Kw):
            x, y = wg[j]
            if x not in taken and y not in taken:
                taken.add(x)
                taken.add(y)
                phi += dist[pos[x]][pos[y]] - 1
                v = (phi + 1) // 2
                if v > best:
                    best = v
        return best

    def apply(u, v):
        lu, lv = occ[u], occ[v]
        occ[u], occ[v] = lv, lu
        if lu >= 0:
            pos[lu] = v
        if lv >= 0:
            pos[lv] = u

    def rec(k, s, last, Bl):
        nodes[0] += 1
        if nodes[0] & 2047 == 0 and time.perf_counter() > deadline:
            raise Timeout
        executed = []
        while k < Kw:
            a, b = wg[k]
            pa, pb = pos[a], pos[b]
            if dist[pa][pb] != 1:
                break
            old = (ready[pa], ready[pb])
            ready[pa] = ready[pb] = max(old) + 1
            executed.append((pa, pb, old))
            trail.append(("gate", k))
            k += 1
            last = None
        try:
            if k == Kw:
                key = tuple(pos)
                val = (s + 0.5 * max(ready), sum(ready))
                old = ends.get(key)
                if (old is None and len(ends) < max_ends) or (old is not None and val < old[0]):
                    ends[key] = (val, s, ready[:], list(trail))
                return
            key = (k, tuple(pos), tuple(ready))
            prev = memo.get(key)
            if prev is not None and prev <= s:
                return
            if len(memo) > memo_cap:
                memo.clear()
            memo[key] = s
            cands = []
            for e in EDGES:
                if e == last:
                    continue
                u, v = e
                if occ[u] < 0 and occ[v] < 0:
                    continue
                apply(u, v)
                hv = h(k)
                apply(u, v)
                if s + 1 + hv <= Bl:
                    cands.append((hv, e))
            cands.sort()
            for _, (u, v) in cands:
                apply(u, v)
                old = (ready[u], ready[v])
                ready[u] = ready[v] = max(old) + 1
                trail.append(("swap", u, v))
                rec(k, s + 1, (u, v), Bl)
                trail.pop()
                ready[u], ready[v] = old
                apply(u, v)
        finally:
            for pa, pb, old in reversed(executed):
                ready[pa], ready[pb] = old
                trail.pop()

    levels_done = []
    try:
        for Bl in range(0, B + 1):
            memo.clear()
            rec(0, 0, None, Bl)
            levels_done.append(Bl)
    except Timeout:
        complete[0] = False
    out = []
    for key, (val, s, rdy, tr) in ends.items():
        out.append({"end_pos": {q: key[qi[q]] for q in qubits}, "swaps": s, "ready": rdy, "trail": tr, "val": val})
    out.sort(key=lambda r: r["val"])
    return out, (levels_done[-1] if levels_done else -1), nodes[0]


def window_ops(gates, start_pos, trail):
    """Physical op list for a window trail (gates keep program operand order)."""
    pos = dict(start_pos)
    p2l = {p: l for l, p in pos.items()}
    ops = []
    for step in trail:
        if step[0] == "swap":
            _, u, v = step
            ops.append(("SWAP", u, v))
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        else:
            a, b = gates[step[1]]
            ops.append(("2Q", pos[a], pos[b]))
    return ops


def route_tail(prog, tail_gates, end_pos, ready, swaps_before, width, budget_s, seed=0):
    """Production beam on the tail from a full mapping + ready profile. Returns physical op list."""
    if not tail_gates:
        return []
    L = prog.L
    seq = GateSeq([(prog.lindex[a], prog.lindex[b]) for a, b in tail_gates], L)
    placement = [-1] * L
    for q, p in end_pos.items():
        placement[prog.lindex[q]] = p
    st = State.__new__(State)
    st.t2p = list(range(N))
    st.p2t = list(range(N))
    st.l2t = placement[:]
    st.t2l = [-1] * N
    for l, p in enumerate(placement):
        if p >= 0:
            st.t2l[p] = l
    st.ready = ready[:]
    st.swaps = swaps_before
    st.depth = max(ready)
    st.hist = None
    params = dict(DEFAULT_PARAMS)
    params["width"] = width
    router = Router(HW, seq, L, params, random.Random(seed))
    final = router.run(st, time.perf_counter() + budget_s)
    if final is None:
        return None
    moves = unwind(final)
    pos = dict(end_pos)
    p2l = {p: l for l, p in pos.items()}
    ops = []
    for (a, b), mv in zip(tail_gates, moves):
        for u, v in mv:
            ops.append(("SWAP", u, v))
            lu, lv = p2l.get(u), p2l.get(v)
            p2l[u], p2l[v] = lv, lu
            if lu is not None:
                pos[lu] = v
            if lv is not None:
                pos[lv] = u
        ops.append(("2Q", pos[a], pos[b]))
    return ops


def run_window(args):
    name, i, K, enum_s, extra, top, wide = args
    t0 = time.perf_counter()
    program = BENCHMARKS[name]
    prog = Program(program)
    gates = [(op[1], op[2]) for op in program if op[0] == "2Q"]
    placement, routed = load_current(name)
    base = official(program, placement, routed)
    states = segment(program, placement, routed)
    G = len(gates)
    j = min(G, i + K)
    pos_i, ready_i, ridx_i, sw_i = states[i]
    _, _, ridx_j, sw_j = states[j]
    w_orig = sw_j - sw_i
    B = w_orig + extra
    ends, complete, nodes = enumerate_window(gates[i:j], pos_i, ready_i, B, enum_s)
    t_enum = time.perf_counter() - t0
    prefix = routed[:ridx_i]
    orig_end = states[j][0]
    tail = gates[j:]
    # narrow pass for every end state (capped by time), then wide pass on the best
    scored = []
    t_narrow_end = time.perf_counter() + max(20.0, enum_s)
    for e in ends:
        if time.perf_counter() > t_narrow_end:
            break
        tail_ops = route_tail(prog, tail, e["end_pos"], e["ready"], sw_i + e["swaps"], 8, 10.0)
        if tail_ops is None:
            continue
        cand = prefix + window_ops(gates[i:j], pos_i, e["trail"]) + tail_ops
        r = official(program, placement, cand)
        scored.append((r["score"], e, r))
    n_narrow = len(scored)
    scored.sort(key=lambda t: t[0])
    best = None
    for sc, e, r in scored[:top]:
        tail_ops = route_tail(prog, tail, e["end_pos"], e["ready"], sw_i + e["swaps"], wide, 60.0)
        if tail_ops is None:
            continue
        cand = prefix + window_ops(gates[i:j], pos_i, e["trail"]) + tail_ops
        r2 = official(program, placement, cand)
        for rr, rt in ((r, "narrow"), (r2, "wide")):
            if rr["valid"] and (best is None or rr["score"] < best[0]["score"]):
                best = (rr, rt, e, cand if rt == "wide" else None)
    orig_in_ends = any(e["end_pos"] == orig_end for e in ends)
    min_win = min((e["swaps"] for e in ends), default=None)
    row = {"benchmark": name, "i": i, "K": j - i, "extra": extra, "w_orig": w_orig, "B": B,
           "budget_levels_exhausted": complete,
           "enum_nodes": nodes, "end_states": len(ends), "orig_end_found": orig_in_ends,
           "min_window_swaps": min_win, "narrow_evaluated": n_narrow, "base_score": base["score"],
           "best_score": best[0]["score"] if best else None, "best_pass": best[1] if best else None,
           "best_swaps_depth": [best[0]["swap_count"], best[0]["depth"]] if best else None,
           "t_enum": round(t_enum, 1), "elapsed": round(time.perf_counter() - t0, 1)}
    if best and best[0]["score"] < base["score"]:
        cand = best[3]
        if cand is None:
            e = best[2]
            cand = prefix + window_ops(gates[i:j], pos_i, e["trail"]) + route_tail(
                prog, tail, e["end_pos"], e["ready"], sw_i + e["swaps"], 8, 10.0)
        row["improved_routed"] = cand
        row["improved_placement"] = placement
        row["improved_official"] = official(program, placement, cand)
    return row


def main():
    name = sys.argv[1]
    Ks = [int(x) for x in sys.argv[2].split(",")]
    step = int(sys.argv[3])
    enum_s = float(sys.argv[4])
    workers = int(sys.argv[5])
    extra = int(sys.argv[6]) if len(sys.argv) > 6 else 0
    top = int(sys.argv[7]) if len(sys.argv) > 7 else 6
    wide = int(sys.argv[8]) if len(sys.argv) > 8 else 512
    G = sum(1 for op in BENCHMARKS[name] if op[0] == "2Q")
    tasks = [(name, i, K, enum_s, extra, top, wide) for K in Ks for i in range(0, G - K + 1, step)]
    if (G - Ks[0]) % step:
        tasks += [(name, G - K, K, enum_s, extra, top, wide) for K in Ks]
    print(f"{name}: {len(tasks)} windows, K={Ks}, step={step}, extra={extra}, enum {enum_s}s, top={top}, wide={wide}",
          flush=True)
    out = HERE / f"lns_free_{name}.jsonl"
    if out.exists():
        done = {(r["i"], r["K"]) for r in map(json.loads, out.read_text().splitlines()) if r.get("extra", 0) == extra}
        tasks = [t for t in tasks if (t[1], min(G, t[1] + t[2]) - t[1]) not in done]
        print(f"resuming: {len(done)} windows already in {out.name}, {len(tasks)} to go", flush=True)
    t0 = time.perf_counter()
    best = None
    with Pool(workers) as pool:
        for row in pool.imap_unordered(run_window, tasks):
            slim = {k: v for k, v in row.items() if not k.startswith("improved_") or k == "improved_official"}
            print(json.dumps(slim), flush=True)
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            if row["best_score"] is not None and (best is None or row["best_score"] < best):
                best = row["best_score"]
    print(json.dumps({"windows": len(tasks), "best_splice_score": best, "elapsed": round(time.perf_counter() - t0, 1)}),
          flush=True)


if __name__ == "__main__":
    main()
