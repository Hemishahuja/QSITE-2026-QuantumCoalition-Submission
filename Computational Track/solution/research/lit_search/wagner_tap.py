"""Fixed-order routing prototypes the earlier audit listed but did not run.

1. Wagner, Bärmann, Liers (JOTA 2023, arXiv:2206.01294): one allocation per
   program gate (order fixed), CP-SAT minimizes the Miltzow swap lower bound
   sum_t ceil(L_t / 2), then each consecutive pair is realized by token swapping.
   That optimum is a lower bound on SWAP count for ANY fixed-order routing.
2. The second half of the same method applied to the incumbent allocation
   (exact token swapping between the mappings the current route already uses).
3. BMT-style multi-segment chaining (Siraichi et al., OOPSLA 2019): contiguous
   zero-SWAP embeddable segments, a shortest-path choice of embeddings, token
   swapping only on the bridges. The audit stopped after the first segment.

Every reported score comes from starter_kit.scorer.score_summary via common.official.

    python -u wagner_tap.py selftest
    python -u wagner_tap.py <benchmark> <tap_seconds>
"""

from __future__ import annotations

import heapq
import json
import math
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
sys.path.insert(0, str(RESEARCH))

from common import ADJ, BENCHMARKS, DIST, EDGES, GRAPH, N, official  # noqa: E402
from ortools.sat.python import cp_model  # noqa: E402
from solution.solve import Hardware, find_embeddings  # noqa: E402

HW = Hardware(GRAPH)
assert HW.labels == list(range(N))

D = [[DIST[i][j] for j in range(N)] for i in range(N)]
EDGE_PAIRS = [(u, v) for u, v in EDGES] + [(v, u) for u, v in EDGES]
LOG_FH = None


def log(msg: str) -> None:
    print(msg, flush=True)
    if LOG_FH is not None:
        LOG_FH.write(msg + "\n")
        LOG_FH.flush()


def program_of(name: str) -> list[tuple]:
    program = [tuple(op) for op in BENCHMARKS[name]]
    if any(op[0] != "2Q" for op in program):
        raise SystemExit(f"{name} contains a non-2Q op; this script does not emit those")
    return program


def gates_of(program: list[tuple]) -> list[tuple[int, int]]:
    return [(op[1], op[2]) for op in program]


def qubits_of(program: list[tuple]) -> list[int]:
    return sorted({q for op in program for q in op[1:]})


def load_current(name: str):
    path = RESEARCH / "current_solutions.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if name not in data:
        return None
    cur = data[name]
    placement = {int(k): int(v) for k, v in cur["placement"].items()}
    routed = [tuple(op) for op in cur["routed"]]
    return placement, routed


def mappings_from_routed(placement: dict[int, int], routed: list[tuple], n_gates: int) -> list[dict[int, int]]:
    """Mapping of logical -> physical at the moment each program gate executes."""
    pos = dict(placement)
    maps: list[dict[int, int]] = []
    for op in routed:
        if op[0] == "SWAP":
            _, u, v = op
            inv = {p: q for q, p in pos.items()}
            qu, qv = inv.get(u), inv.get(v)
            if qu is not None:
                pos[qu] = v
            if qv is not None:
                pos[qv] = u
        elif op[0] == "2Q":
            maps.append(dict(pos))
    if len(maps) != n_gates:
        raise RuntimeError(f"expected {n_gates} gate mappings, found {len(maps)}")
    return maps


def apply_swap(pos: dict[int, int], u: int, v: int) -> None:
    inv = {p: q for q, p in pos.items()}
    qu, qv = inv.get(u), inv.get(v)
    if qu is not None:
        pos[qu] = v
    if qv is not None:
        pos[qv] = u


def gap_lengths(a: dict[int, int], b: dict[int, int], qubits: list[int]) -> tuple[int, int]:
    total = sum(D[a[q]][b[q]] for q in qubits)
    return total, (total + 1) // 2


def allocation_lb(maps: list[dict[int, int]], qubits: list[int]) -> tuple[int, int]:
    dist_sum = 0
    lb = 0
    for i in range(len(maps) - 1):
        length, piece = gap_lengths(maps[i], maps[i + 1], qubits)
        dist_sum += length
        lb += piece
    return dist_sum, lb


def _state(pos: dict[int, int], qubits: list[int]) -> tuple[int, ...]:
    return tuple(pos[q] for q in qubits)


def _from_state(state: tuple[int, ...], qubits: list[int]) -> dict[int, int]:
    return {q: state[i] for i, q in enumerate(qubits)}


def astar_swaps(start: dict[int, int], goal: dict[int, int], qubits: list[int],
                node_cap: int = 60_000, time_cap: float = 1.5) -> list[tuple[int, int]] | None:
    """Exact token swap with vacancies. None if the cap is hit."""
    s = _state(start, qubits)
    g = _state(goal, qubits)
    if s == g:
        return []
    qn = len(qubits)
    goal_of = g

    def heur(state: tuple[int, ...]) -> int:
        return (sum(D[state[i]][goal_of[i]] for i in range(qn)) + 1) // 2

    gs = {s: 0}
    parent: dict[tuple[int, ...], tuple[tuple[int, ...], tuple[int, int]]] = {}
    counter = 0
    pq: list[tuple[int, int, int, tuple[int, ...]]] = [(heur(s), 0, counter, s)]
    expanded = 0
    t0 = time.perf_counter()
    while pq:
        if expanded > node_cap or time.perf_counter() - t0 > time_cap:
            return None
        _, cost, _, state = heapq.heappop(pq)
        if cost != gs.get(state):
            continue
        if state == g:
            swaps: list[tuple[int, int]] = []
            cur = state
            while cur != s:
                prev, edge = parent[cur]
                swaps.append(edge)
                cur = prev
            swaps.reverse()
            return swaps
        expanded += 1
        loc = {state[i]: i for i in range(qn)}
        for u, v in EDGES:
            iu, iv = loc.get(u), loc.get(v)
            if iu is None and iv is None:
                continue
            if iu is not None and iv is not None and state[iu] == goal_of[iu] and state[iv] == goal_of[iv]:
                continue
            ns_l = list(state)
            if iu is not None:
                ns_l[iu] = v
            if iv is not None:
                ns_l[iv] = u
            ns = tuple(ns_l)
            nc = cost + 1
            if nc < gs.get(ns, 10**9):
                gs[ns] = nc
                parent[ns] = (state, (u, v))
                counter += 1
                heapq.heappush(pq, (nc + heur(ns), nc, counter, ns))
    return None


def _build_f(pos: dict[int, int], goal: dict[int, int], adj: dict[int, list[int]]) -> list[list[int]]:
    f: list[list[int]] = [[] for _ in range(N)]
    for q, v in pos.items():
        tgt = goal[q]
        if v == tgt:
            continue
        base = D[v][tgt]
        for w in adj[v]:
            if D[w][tgt] < base:
                f[v].append(w)
    return f


def _rotate_cycle(pos: dict[int, int], cyc: list[int], swaps: list[tuple[int, int]]) -> None:
    """Net effect: the token on cyc[i] moves to cyc[(i+1) % k]. Uses k-1 swaps."""
    for i in range(len(cyc) - 2, -1, -1):
        u, v = cyc[i], cyc[i + 1]
        apply_swap(pos, u, v)
        swaps.append((u, v))


def _happy_once(start: dict[int, int], goal: dict[int, int], qubits: list[int],
                adj: dict[int, list[int]]) -> list[tuple[int, int]]:
    """One Miltzow walk: follow distance-decreasing edges until a cycle or a dead end.

    A cycle rotates every token one step closer. A dead end is one swap onto an empty
    vertex or onto a token that is already home (an unhappy swap). State repetition
    aborts this walk so the caller can try another start vertex.
    """
    pos = dict(start)
    swaps: list[tuple[int, int]] = []
    seen: set[tuple[int, ...]] = set()
    initial = sum(D[pos[q]][goal[q]] for q in qubits)
    cap = 4 * initial + N + 5
    order = list(qubits)
    for _ in range(cap):
        if all(pos[q] == goal[q] for q in qubits):
            return swaps
        key = _state(pos, qubits)
        if key in seen:
            raise RuntimeError("happy-swap repeated a configuration")
        seen.add(key)
        f = _build_f(pos, goal, adj)
        start_v = next(pos[q] for q in order if pos[q] != goal[q])
        path = [start_v]
        index = {start_v: 0}
        v = start_v
        action = None
        while action is None:
            nbrs = f[v]
            closer = next((w for w in nbrs if w in index), None)
            if closer is None and nbrs:
                closer = nbrs[0]
            if closer is None:
                if len(path) < 2:
                    raise RuntimeError("misplaced token has no closer neighbor")
                action = ("edge", path[-2], path[-1])
                break
            if closer in index:
                action = ("cycle", path[index[closer]:])
                break
            path.append(closer)
            index[closer] = len(path) - 1
            v = closer
            if len(path) > N:
                raise RuntimeError("distance-decreasing walk exceeded the graph")
        if action[0] == "cycle":
            _rotate_cycle(pos, action[1], swaps)
        else:
            apply_swap(pos, action[1], action[2])
            swaps.append((action[1], action[2]))
    raise RuntimeError("happy-swap exceeded its swap cap")


def happy_swaps(start: dict[int, int], goal: dict[int, int], qubits: list[int],
                reverse_adj: bool = False) -> list[tuple[int, int]]:
    """Shortest successful Miltzow walk over start vertices and adjacency order."""
    adj = {u: (list(reversed(ADJ[u])) if reverse_adj else list(ADJ[u])) for u in range(N)}
    best: list[tuple[int, int]] | None = None
    errors = 0
    for offset in range(len(qubits)):
        order = qubits[offset:] + qubits[:offset]
        try:
            seq = _happy_once(start, goal, order, adj)
        except RuntimeError:
            errors += 1
            continue
        if best is None or len(seq) < len(best):
            best = seq
    if best is None:
        raise RuntimeError(f"happy-swap failed from every start ({errors} aborts)")
    return best


def connect(start: dict[int, int], goal: dict[int, int], qubits: list[int]) -> tuple[list[tuple[int, int]], str]:
    exact = astar_swaps(start, goal, qubits)
    if exact is not None:
        return exact, "exact"
    best = None
    for rev in (False, True):
        try:
            seq = happy_swaps(start, goal, qubits, reverse_adj=rev)
        except RuntimeError:
            continue
        if best is None or len(seq) < len(best):
            best = seq
    if best is None:
        raise RuntimeError("token swapping failed")
    end = dict(start)
    for u, v in best:
        apply_swap(end, u, v)
    if any(end[q] != goal[q] for q in qubits):
        raise RuntimeError("approximate token swapping missed the target")
    return best, "approx"


def realize(maps: list[dict[int, int]], gates: list[tuple[int, int]], qubits: list[int]) -> tuple[dict[int, int], list[tuple], list[str]]:
    pos = dict(maps[0])
    routed: list[tuple] = []
    how: list[str] = []
    for t, (a, b) in enumerate(gates):
        if t:
            swaps, kind = connect(pos, maps[t], qubits)
            how.append(kind)
            for u, v in swaps:
                routed.append(("SWAP", u, v))
                apply_swap(pos, u, v)
            if any(pos[q] != maps[t][q] for q in qubits):
                raise RuntimeError(f"gap {t} did not reach its allocation")
        if D[pos[a]][pos[b]] != 1:
            raise RuntimeError(f"gate {t} endpoints are not adjacent under the allocation")
        routed.append(("2Q", pos[a], pos[b]))
    return dict(maps[0]), routed, how


def solve_tap(gates: list[tuple[int, int]], qubits: list[int], hint: list[dict[int, int]] | None,
              seconds: float, workers: int = 1) -> dict:
    """Minimize sum of per-gap ceil(distance-sum/2). Optimum lower-bounds SWAP count."""
    model = cp_model.CpModel()
    qindex = {q: i for i, q in enumerate(qubits)}
    gcount = len(gates)
    qcount = len(qubits)
    pos = [[model.NewIntVar(0, N - 1, f"p_{t}_{i}") for i in range(qcount)] for t in range(gcount)]
    for t in range(gcount):
        model.AddAllDifferent(pos[t])
        a, b = gates[t]
        model.AddAllowedAssignments([pos[t][qindex[a]], pos[t][qindex[b]]], EDGE_PAIRS)
    flat = [D[i][j] for i in range(N) for j in range(N)]
    lbs = []
    for t in range(gcount - 1):
        costs = []
        for i in range(qcount):
            idx = model.NewIntVar(0, N * N - 1, f"i_{t}_{i}")
            model.Add(idx == pos[t][i] * N + pos[t + 1][i])
            cost = model.NewIntVar(0, N, f"c_{t}_{i}")
            model.AddElement(idx, flat, cost)
            costs.append(cost)
        length = model.NewIntVar(0, qcount * N, f"L_{t}")
        half = model.NewIntVar(0, qcount * N, f"half_{t}")
        furthest = model.NewIntVar(0, N, f"far_{t}")
        piece = model.NewIntVar(0, qcount * N, f"lb_{t}")
        model.Add(length == sum(costs))
        model.Add(2 * half >= length)
        model.Add(2 * half <= length + 1)
        model.AddMaxEquality(furthest, costs)
        model.AddMaxEquality(piece, [half, furthest])
        lbs.append(piece)
    model.Minimize(sum(lbs) if lbs else 0)
    if hint is not None:
        for t in range(gcount):
            for i, q in enumerate(qubits):
                model.AddHint(pos[t][i], hint[t][q])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = workers
    solver.parameters.symmetry_level = 0
    if hasattr(solver.parameters, "max_memory_in_mb"):
        solver.parameters.max_memory_in_mb = 4096
    solver.parameters.log_search_progress = True
    if hasattr(solver.parameters, "log_to_stdout"):
        solver.parameters.log_to_stdout = True
    t0 = time.perf_counter()
    status = solver.Solve(model)
    elapsed = time.perf_counter() - t0
    name = solver.StatusName(status)
    bound = solver.BestObjectiveBound()
    out: dict = {"status": name, "elapsed": round(elapsed, 3), "bound": bound}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        maps = []
        for t in range(gcount):
            maps.append({q: int(solver.Value(pos[t][qindex[q]])) for q in qubits})
        out["objective"] = solver.ObjectiveValue()
        out["maps"] = maps
    log(f"TAP status={name} bound={bound} obj={out.get('objective')} elapsed={elapsed:.1f}s")
    return out


def solve_slices(gates: list[tuple[int, int]], qubits: list[int], hint: list[dict[int, int]] | None,
                 seconds_each: float, gaps_per_slice: int) -> list[dict]:
    """Partition the gaps and solve each token-allocation slice on its own.

    A global allocation restricts to a feasible solution of every slice, so the sum of
    the slice bounds is a lower bound on the full distance lower bound, hence on swaps.
    """
    ngaps = len(gates) - 1
    rows = []
    start = 0
    while start < ngaps:
        stop = min(ngaps, start + gaps_per_slice)
        sub_gates = gates[start:stop + 1]
        sub_hint = hint[start:stop + 1] if hint is not None else None
        hint_lb = None
        if sub_hint is not None:
            _dist, hint_lb = allocation_lb(sub_hint, qubits)
        log(f"slice gaps[{start},{stop}) gates={len(sub_gates)} incumbent_lb={hint_lb} budget={seconds_each:.0f}s")
        tap = solve_tap(sub_gates, qubits, sub_hint, seconds_each, workers=1)
        proven = math.ceil(tap["bound"] - 1e-9)
        row = {"gaps": [start, stop], "bound": tap["bound"], "swaps_at_least": proven,
               "status": tap["status"], "objective": tap.get("objective"), "elapsed": tap["elapsed"],
               "incumbent_lb": hint_lb}
        if "maps" in tap:
            dist_sum, lb = allocation_lb(tap["maps"], qubits)
            row["objective_recomputed"] = lb
            row["distance_sum"] = dist_sum
        log(f"  slice swaps>={proven} status={tap['status']} obj={tap.get('objective')}")
        rows.append(row)
        start = stop
    total = sum(r["swaps_at_least"] for r in rows)
    log(f"sliced TAP swaps >= {total} over {len(rows)} slices")
    return rows


def _complete(partial: dict[int, int], qubits: list[int], high: bool) -> dict[int, int]:
    used = set(partial.values())
    free = [i for i in range(N) if i not in used]
    if high:
        free.reverse()
    full = dict(partial)
    for q, p in zip([q for q in qubits if q not in full], free):
        full[q] = p
    if len(full) != len(qubits) or len(set(full.values())) != len(qubits):
        raise RuntimeError("completion is not an injective placement of every logical qubit")
    return full


def _segment_maps(gates: list[tuple[int, int]], i: int, j: int, qubits: list[int],
                  incumbent: list[dict[int, int]] | None) -> list[dict[int, int]]:
    found_maps: list[dict[int, int]] = []
    seen: set[tuple] = set()

    def add(full: dict[int, int]) -> None:
        if any(D[full[a]][full[b]] != 1 for a, b in gates[i:j]):
            return
        key = tuple(full[q] for q in qubits)
        if key in seen:
            return
        seen.add(key)
        found_maps.append(full)

    deadline = time.perf_counter() + 2.0
    for seed in range(4):
        found, _exhausted = find_embeddings(
            HW, gates[i:j], deadline, limit=6, rng=random.Random(1000 * i + seed), node_budget=40_000,
        )
        for partial in found:
            add(_complete(partial, qubits, high=False))
            add(_complete(partial, qubits, high=True))
        if time.perf_counter() > deadline:
            break
    if incumbent is not None:
        for t in range(i, j):
            add(incumbent[t])
    return found_maps


def _maximal_segments(gates: list[tuple[int, int]], cap: int | None) -> list[tuple[int, int]]:
    gcount = len(gates)
    segs: list[tuple[int, int]] = []
    i = 0
    while i < gcount:
        hi_lim = gcount if cap is None else min(gcount, i + cap)
        ans = None
        lo, hi = i + 1, hi_lim
        while lo <= hi:
            mid = (lo + hi) // 2
            deadline = time.perf_counter() + 1.5
            found, _ex = find_embeddings(HW, gates[i:mid], deadline, limit=1, node_budget=30_000)
            if found:
                ans = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if ans is None:
            raise RuntimeError(f"even a single gate at index {i} did not embed")
        segs.append((i, ans))
        i = ans
    return segs


def bmt_route(gates: list[tuple[int, int]], qubits: list[int],
              incumbent: list[dict[int, int]] | None) -> list[tuple[str, list[dict[int, int]]]]:
    """Return (tag, allocation sequence) for several segment caps."""
    routes = []
    caps: list[int | None] = [None, 8, 6, 5, 4, 3]
    for cap in caps:
        t0 = time.perf_counter()
        segs = _maximal_segments(gates, cap)
        choices: list[list[dict[int, int]]] = []
        empty = False
        for i, j in segs:
            maps = _segment_maps(gates, i, j, qubits, incumbent)
            if not maps:
                empty = True
                break
            choices.append(maps[:24])
        if empty:
            log(f"BMT cap={cap} produced a segment with no embedding")
            continue
        nseg = len(segs)
        cost = [[10**9] * len(choices[s]) for s in range(nseg)]
        back = [[-1] * len(choices[s]) for s in range(nseg)]
        for j in range(len(choices[0])):
            cost[0][j] = 0
        for s in range(1, nseg):
            for j, right in enumerate(choices[s]):
                for i, left in enumerate(choices[s - 1]):
                    _dist, piece = gap_lengths(left, right, qubits)
                    c = cost[s - 1][i] + piece
                    if c < cost[s][j]:
                        cost[s][j] = c
                        back[s][j] = i
        end = min(range(len(choices[-1])), key=lambda j: cost[-1][j])
        picked = [end]
        for s in range(nseg - 1, 0, -1):
            picked.append(back[s][picked[-1]])
        picked.reverse()
        maps = []
        for s, (i, j) in enumerate(segs):
            hold = choices[s][picked[s]]
            maps.extend([hold] * (j - i))
        lengths = [b - a for a, b in segs]
        lb = cost[-1][end]
        log(f"BMT cap={cap} segments={lengths} bridge_lb={lb} ({time.perf_counter() - t0:.1f}s)")
        routes.append((f"bmt_cap{cap if cap is not None else 'max'}", maps))
    return routes


def emit(name: str, program: list[tuple], tag: str, placement: dict[int, int], routed: list[tuple],
         extra: dict, sink: list[dict]) -> dict:
    off = official(program, placement, routed)
    rec = {
        "benchmark": name,
        "tag": tag,
        "official": off,
        "swaps_realized": sum(1 for op in routed if op[0] == "SWAP"),
        **extra,
    }
    log(f"{name} {tag}: valid={off['valid']} swaps={off['swap_count']} depth={off['depth']} score={off['score']} {off['message']}")
    sink.append(rec)
    if off["valid"]:
        payload = {
            "benchmark": name,
            "tag": tag,
            "official": off,
            "placement": placement,
            "routed": routed,
            **extra,
        }
        best_path = HERE / f"best_{name}.json"
        prev = None
        if best_path.exists():
            prev = json.loads(best_path.read_text(encoding="utf-8"))
        if prev is None or off["score"] < prev["official"]["score"]:
            best_path.write_text(json.dumps(payload), encoding="utf-8")
            log(f"  new best for {name} saved to {best_path.name}")
    return off


def run(name: str, seconds: float) -> None:
    global LOG_FH
    program = program_of(name)
    gates = gates_of(program)
    qubits = qubits_of(program)
    log_path = HERE / f"{name}_wagner.log"
    LOG_FH = log_path.open("a", encoding="utf-8")
    log(f"=== {name} gates={len(gates)} qubits={len(qubits)} tap_seconds={seconds} ===")
    records: list[dict] = []
    incumbent_maps = None
    loaded = load_current(name)
    if loaded is not None:
        placement, routed = loaded
        base = official(program, placement, routed)
        log(f"incumbent official score={base['score']} swaps={base['swap_count']} depth={base['depth']} valid={base['valid']}")
        incumbent_maps = mappings_from_routed(placement, routed, len(gates))
        dist_sum, lb = allocation_lb(incumbent_maps, qubits)
        log(f"incumbent allocation distance_sum={dist_sum} sum_ceil_L/2={lb} (actual swaps {base['swap_count']})")
        t0 = time.perf_counter()
        new_place, new_routed, how = realize(incumbent_maps, gates, qubits)
        log(f"re-TS of incumbent gaps: exact={how.count('exact')} approx={how.count('approx')} ({time.perf_counter() - t0:.1f}s)")
        emit(name, program, "retswap_incumbent", new_place, new_routed,
             {"distance_sum": dist_sum, "alloc_lb": lb, "gap_kinds": how}, records)

    skip_bmt = "tap-only" in sys.argv
    if not skip_bmt:
        t0 = time.perf_counter()
        for tag, maps in bmt_route(gates, qubits, incumbent_maps):
            dist_sum, lb = allocation_lb(maps, qubits)
            try:
                new_place, new_routed, how = realize(maps, gates, qubits)
            except RuntimeError as exc:
                log(f"{tag} realization failed: {exc}")
                continue
            emit(name, program, tag, new_place, new_routed,
                 {"distance_sum": dist_sum, "alloc_lb": lb, "approx_gaps": how.count("approx"),
                  "bmt_seconds": round(time.perf_counter() - t0, 3)}, records)

    if seconds > 0:
        workers = 1
        for arg in sys.argv:
            if arg.startswith("workers="):
                workers = int(arg.split("=", 1)[1])
        tap = solve_tap(gates, qubits, incumbent_maps, seconds, workers=workers)
        proven = math.ceil(tap["bound"] - 1e-9)
        log(f"TAP bound implies swaps >= {proven}")
        extra = {"status": tap["status"], "bound": tap["bound"], "swaps_at_least": proven,
                 "elapsed": tap["elapsed"], "objective": tap.get("objective")}
        if "maps" in tap:
            dist_sum, lb = allocation_lb(tap["maps"], qubits)
            extra["distance_sum"] = dist_sum
            extra["alloc_lb"] = lb
            try:
                new_place, new_routed, how = realize(tap["maps"], gates, qubits)
                extra["approx_gaps"] = how.count("approx")
                emit(name, program, "tap", new_place, new_routed, extra, records)
            except RuntimeError as exc:
                log(f"TAP realization failed: {exc}")
                records.append({"benchmark": name, "tag": "tap", "error": str(exc), **extra})
        else:
            log(f"TAP has no incumbent; bound says swaps >= {proven}")
            records.append({"benchmark": name, "tag": "tap_bound_only", **extra})

    out = HERE / "wagner_results.jsonl"
    with out.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    log(f"appended {len(records)} records to {out.name}")
    LOG_FH.close()
    LOG_FH = None


def solve_segment_model(gates: list[tuple[int, int]], qubits: list[int], segs: list[tuple[int, int]],
                        seconds: float) -> dict:
    """One allocation per segment. Every gate in a segment must be adjacent there.

    Minimizes the Miltzow lower bound on the bridges. With only a handful of
    allocations this stays small enough for CP-SAT to finish.
    """
    model = cp_model.CpModel()
    qindex = {q: i for i, q in enumerate(qubits)}
    qcount = len(qubits)
    scount = len(segs)
    pos = [[model.NewIntVar(0, N - 1, f"s_{s}_{i}") for i in range(qcount)] for s in range(scount)]
    flat = [D[i][j] for i in range(N) for j in range(N)]
    for s, (i, j) in enumerate(segs):
        model.AddAllDifferent(pos[s])
        seen_pairs = set()
        for a, b in gates[i:j]:
            pair = (qindex[a], qindex[b])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            model.AddAllowedAssignments([pos[s][pair[0]], pos[s][pair[1]]], EDGE_PAIRS)
    lbs = []
    for s in range(scount - 1):
        costs = []
        for i in range(qcount):
            idx = model.NewIntVar(0, N * N - 1, f"i_{s}_{i}")
            model.Add(idx == pos[s][i] * N + pos[s + 1][i])
            cost = model.NewIntVar(0, N, f"c_{s}_{i}")
            model.AddElement(idx, flat, cost)
            costs.append(cost)
        length = model.NewIntVar(0, qcount * N, f"L_{s}")
        piece = model.NewIntVar(0, qcount * N, f"lb_{s}")
        model.Add(length == sum(costs))
        model.Add(2 * piece >= length)
        model.Add(2 * piece <= length + 1)
        lbs.append(piece)
    model.Minimize(sum(lbs) if lbs else 0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.symmetry_level = 0
    if hasattr(solver.parameters, "max_memory_in_mb"):
        solver.parameters.max_memory_in_mb = 4096
    t0 = time.perf_counter()
    status = solver.Solve(model)
    elapsed = time.perf_counter() - t0
    out: dict = {"status": solver.StatusName(status), "elapsed": round(elapsed, 3),
                 "bound": solver.BestObjectiveBound()}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        maps = []
        for s, (i, j) in enumerate(segs):
            hold = {q: int(solver.Value(pos[s][qindex[q]])) for q in qubits}
            maps.extend([hold] * (j - i))
        out["objective"] = solver.ObjectiveValue()
        out["maps"] = maps
    log(f"segment TAP nseg={scount} status={out['status']} bound={out['bound']} "
        f"obj={out.get('objective')} elapsed={elapsed:.1f}s")
    return out


def run_segments(name: str, seconds: float) -> None:
    global LOG_FH
    program = program_of(name)
    gates = gates_of(program)
    qubits = qubits_of(program)
    log_path = HERE / f"{name}_segments.log"
    LOG_FH = log_path.open("a", encoding="utf-8")
    log(f"=== segment TAP {name} seconds={seconds} ===")
    records: list[dict] = []
    for cap in (None, 8, 6, 5):
        segs = _maximal_segments(gates, cap)
        lengths = [b - a for a, b in segs]
        log(f"cap={cap} segments={lengths}")
        tap = solve_segment_model(gates, qubits, segs, seconds)
        proven = math.ceil(tap["bound"] - 1e-9)
        extra = {"status": tap["status"], "bound": tap["bound"], "objective": tap.get("objective"),
                 "swaps_at_least_for_this_partition": proven, "segments": lengths, "elapsed": tap["elapsed"]}
        if "maps" not in tap:
            records.append({"benchmark": name, "tag": f"segment_cap{cap}", **extra})
            continue
        dist_sum, lb = allocation_lb(tap["maps"], qubits)
        extra["distance_sum"] = dist_sum
        extra["alloc_lb"] = lb
        try:
            new_place, new_routed, how = realize(tap["maps"], gates, qubits)
        except RuntimeError as exc:
            log(f"segment realization failed: {exc}")
            records.append({"benchmark": name, "tag": f"segment_cap{cap}", "error": str(exc), **extra})
            continue
        extra["approx_gaps"] = how.count("approx")
        emit(name, program, f"segment_cap{cap if cap is not None else 'max'}", new_place, new_routed, extra, records)
    with (HERE / "wagner_results.jsonl").open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps({k: v for k, v in rec.items() if k != "gap_kinds"}) + "\n")
    log(f"segment run wrote {len(records)} records")
    LOG_FH.close()
    LOG_FH = None


def run_slices(name: str, seconds_each: float, gaps_per_slice: int) -> None:
    global LOG_FH
    program = program_of(name)
    gates = gates_of(program)
    qubits = qubits_of(program)
    log_path = HERE / f"{name}_slices.log"
    LOG_FH = log_path.open("a", encoding="utf-8")
    log(f"=== slices {name} gaps_per_slice={gaps_per_slice} seconds_each={seconds_each} ===")
    loaded = load_current(name)
    hint = None
    if loaded is not None:
        placement, routed = loaded
        hint = mappings_from_routed(placement, routed, len(gates))
        dist_sum, lb = allocation_lb(hint, qubits)
        log(f"incumbent distance_sum={dist_sum} sum_ceil_L/2={lb}")
    rows = solve_slices(gates, qubits, hint, seconds_each, gaps_per_slice)
    total = sum(r["swaps_at_least"] for r in rows)
    rec = {"benchmark": name, "tag": "tap_slices", "gaps_per_slice": gaps_per_slice,
           "swaps_at_least": total, "slices": rows}
    with (HERE / "wagner_results.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    log(f"wrote slice record, swaps >= {total}")
    LOG_FH.close()
    LOG_FH = None


def _self_test() -> None:
    qubits = [0, 1, 2, 3]
    # 4-cycle rotation permutation, abstract, on a real 6-cycle of the hardware if we can find one.
    # Direct rotation check on an edge: two tokens swap.
    start = {0: 0, 1: 1}
    goal = {0: 1, 1: 0}
    seq, kind = connect(start, goal, [0, 1])
    assert kind == "exact" and len(seq) == 1, (kind, seq)
    # Slide one token three steps along 0-1-2-3, vacancy ahead.
    start = {0: 0}
    goal = {0: 3}
    seq, kind = connect(start, goal, [0])
    assert kind == "exact" and len(seq) == 3, (kind, seq)
    # Happy chain must reach a target that exact search also reaches, and not use fewer swaps than exact.
    rng = random.Random(0)
    occupied = [0, 1, 2, 4, 5, 6, 9, 10]
    tokens = list(range(8))
    pos = {q: occupied[i] for i, q in enumerate(tokens)}
    for _ in range(5):
        u, v = EDGES[rng.randrange(len(EDGES))]
        apply_swap(pos, u, v)
    goal = dict(pos)
    pos = {q: occupied[i] for i, q in enumerate(tokens)}
    exact = astar_swaps(pos, goal, tokens, node_cap=200_000, time_cap=5.0)
    assert exact is not None
    happy = happy_swaps(pos, goal, tokens)
    assert len(happy) >= len(exact)
    end = dict(pos)
    for u, v in happy:
        apply_swap(end, u, v)
    assert end == goal
    log("self-test token swapping: ok")
    run("chain_trotter", 20)
    best = json.loads((HERE / "best_chain_trotter.json").read_text(encoding="utf-8"))
    assert best["official"]["valid"] and best["official"]["score"] == 4.5, best["official"]
    run("ghz_star", 30)
    ghz = json.loads((HERE / "best_ghz_star.json").read_text(encoding="utf-8"))
    assert ghz["official"]["valid"], ghz["official"]
    log(f"self-test ghz best score={ghz['official']['score']}")
    log("self-test passed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: wagner_tap.py selftest | <benchmark> <tap_seconds>")
    if sys.argv[1] == "selftest":
        _self_test()
    else:
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
        if "segments" in sys.argv:
            run_segments(sys.argv[1], secs)
        elif "slices" in sys.argv:
            gaps = 4
            for arg in sys.argv:
                if arg.startswith("gaps="):
                    gaps = int(arg.split("=", 1)[1])
            run_slices(sys.argv[1], secs, gaps)
        else:
            run(sys.argv[1], secs)
