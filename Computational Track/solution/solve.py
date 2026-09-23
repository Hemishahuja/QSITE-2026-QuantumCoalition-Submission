"""Qubit placement + SWAP routing for the QSITE 2026 Computational Track.

Roughly how it works:
1. Zero-SWAP check: subgraph-monomorphism search of the program's interaction graph into
   the hardware graph. If it embeds, no SWAPs are needed and the score is just the depth
   lower bound (provably optimal).
2. Otherwise, beam search over (mapping, gate index) that scores partial solutions with the
   exact objective (swaps + 0.5 * depth under ASAP layering). Starts from a handful of
   initial placements -- lazy placement, zero-SWAP prefix embeddings, simulated annealing --
   plus forward/backward (SABRE-style) refinement passes. Runs as a portfolio under a time
   budget and stops early once it hits the lower bound.
3. Result gets validated with the same rules as the official scorer. Greedy baseline is the
   fallback if nothing better turns up.

Only depends on networkx + stdlib so it can be pasted straight into a notebook.
"""

from __future__ import annotations

import bisect
import heapq
import math
import random
import time
from collections import deque

import networkx as nx

DEFAULT_TIME_BUDGET = 10.0
_INF = 10**6


# --------------------------------------------------------------------------------------
# Problem representation
# --------------------------------------------------------------------------------------


class Hardware:
    def __init__(self, graph: nx.Graph):
        try:
            labels = sorted(graph.nodes)
        except TypeError:
            labels = list(graph.nodes)
        self.labels = labels
        self.index = {p: i for i, p in enumerate(labels)}
        self.n = len(labels)
        adj = [set() for _ in range(self.n)]
        for u, v in graph.edges:
            if u != v:
                iu, iv = self.index[u], self.index[v]
                adj[iu].add(iv)
                adj[iv].add(iu)
        self.adjset = adj
        self.adj = [sorted(a) for a in adj]
        self.degree = [len(a) for a in adj]
        self.max_degree = max(self.degree, default=0)
        self.arcs = [(u, v) for u in range(self.n) for v in self.adj[u]]
        self.num_edges = len(self.arcs) // 2
        self.dist = [self._bfs(s) for s in range(self.n)]
        self.girth = _girth(self.adj)
        self._paths: dict[tuple[int, int, int], list[tuple[int, ...]]] = {}

    def _bfs(self, source: int) -> list[int]:
        dist = [_INF] * self.n
        dist[source] = 0
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for nb in self.adj[node]:
                if dist[nb] == _INF:
                    dist[nb] = dist[node] + 1
                    queue.append(nb)
        return dist

    def paths(self, source: int, target: int, limit: int) -> list[tuple[int, ...]]:
        """Up to `limit` shortest paths from source to target (inclusive)."""
        key = (source, target, limit)
        cached = self._paths.get(key)
        if cached is not None:
            return cached
        result: list[tuple[int, ...]] = []
        if self.dist[source][target] < _INF:
            dist_t = [self.dist[x][target] for x in range(self.n)]
            path = [source]

            def rec(node: int) -> None:
                if len(result) >= limit:
                    return
                if node == target:
                    result.append(tuple(path))
                    return
                for nb in self.adj[node]:
                    if dist_t[nb] == dist_t[node] - 1:
                        path.append(nb)
                        rec(nb)
                        path.pop()

            rec(source)
        self._paths[key] = result
        return result


def _girth(adj: list[list[int]] | list[set[int]]) -> int:
    n = len(adj)
    best = _INF
    for root in range(n):
        dist = [-1] * n
        parent = [-1] * n
        dist[root] = 0
        queue = deque([root])
        while queue:
            u = queue.popleft()
            if 2 * dist[u] + 1 >= best:
                break
            for w in adj[u]:
                if dist[w] == -1:
                    dist[w] = dist[u] + 1
                    parent[w] = u
                    queue.append(w)
                elif parent[u] != w:
                    best = min(best, dist[u] + dist[w] + 1)
    return best


class Program:
    def __init__(self, program: list[tuple]):
        self.ops = [tuple(op) for op in program]
        self.supported = all(
            (op[0] == "2Q" and len(op) == 3 and op[1] != op[2]) or (op[0] == "1Q" and len(op) == 2)
            for op in self.ops
        )
        self.logicals = sorted({q for op in self.ops for q in op[1:]})
        self.lindex = {q: i for i, q in enumerate(self.logicals)}
        self.L = len(self.logicals)
        self.gates = [(self.lindex[op[1]], self.lindex[op[2]]) for op in self.ops if op[0] == "2Q"]

    def critical_path(self) -> int:
        ready = [0] * self.L
        depth = 0
        for a, b in self.gates:
            t = max(ready[a], ready[b]) + 1
            ready[a] = ready[b] = t
            depth = max(depth, t)
        return depth


class GateSeq:
    def __init__(self, gates: list[tuple[int, int]], L: int):
        self.gates = gates
        self.G = len(gates)
        self.occ: list[list[int]] = [[] for _ in range(L)]
        for j, (a, b) in enumerate(gates):
            self.occ[a].append(j)
            self.occ[b].append(j)


# --------------------------------------------------------------------------------------
# Subgraph monomorphism (zero-SWAP placements)
# --------------------------------------------------------------------------------------


def find_embeddings(
    hw: Hardware,
    gates: list[tuple[int, int]],
    deadline: float,
    limit: int = 1,
    rng: random.Random | None = None,
    node_budget: int = 200_000,
) -> tuple[list[dict[int, int]], bool]:
    """Injective maps logical -> physical index that put every interacting pair on an edge.

    Returns (embeddings, exhausted). `exhausted` is True when the search finished, so an
    empty list is a proof that no zero-SWAP placement exists.
    """
    pat: dict[int, set[int]] = {}
    for a, b in gates:
        pat.setdefault(a, set()).add(b)
        pat.setdefault(b, set()).add(a)
    nodes = list(pat)
    if not nodes:
        return [{}], True
    num_edges = sum(len(v) for v in pat.values()) // 2
    pdeg = {x: len(pat[x]) for x in nodes}
    if len(nodes) > hw.n or num_edges > hw.num_edges or max(pdeg.values()) > hw.max_degree:
        return [], True
    hw_degs = sorted(hw.degree, reverse=True)
    for i, d in enumerate(sorted(pdeg.values(), reverse=True)):
        if d > hw_degs[i]:
            return [], True
    if hw.girth < _INF and _girth(_dense_adj(pat, nodes)) < hw.girth:
        return [], True

    order: list[int] = []
    placed: set[int] = set()
    remaining = set(nodes)
    while remaining:
        candidates = [x for x in remaining if pat[x] & placed]
        pool = candidates or list(remaining)
        x = max(pool, key=lambda v: (len(pat[v] & placed), pdeg[v], -v))
        order.append(x)
        placed.add(x)
        remaining.discard(x)

    image: dict[int, int] = {}
    used = [False] * hw.n
    found: list[dict[int, int]] = []
    budget = [node_budget]
    aborted = [False]

    def rec(i: int) -> bool:
        if i == len(order):
            found.append(dict(image))
            return len(found) >= limit
        budget[0] -= 1
        if budget[0] <= 0 or (budget[0] % 512 == 0 and time.perf_counter() > deadline):
            aborted[0] = True
            return True
        x = order[i]
        mapped = [image[y] for y in pat[x] if y in image]
        if mapped:
            cands = set(hw.adjset[mapped[0]])
            for m in mapped[1:]:
                cands &= hw.adjset[m]
            cands = [c for c in cands if not used[c]]
        else:
            cands = [c for c in range(hw.n) if not used[c]]
        unmapped_nbrs = sum(1 for y in pat[x] if y not in image)
        cands = [
            c
            for c in cands
            if hw.degree[c] >= pdeg[x] and sum(1 for z in hw.adj[c] if not used[z]) >= unmapped_nbrs
        ]
        if rng is not None:
            rng.shuffle(cands)
        else:
            cands.sort()
        for c in cands:
            image[x] = c
            used[c] = True
            if rec(i + 1):
                return True
            used[c] = False
            del image[x]
        return False

    rec(0)
    return found, not aborted[0]


def _dense_adj(pat: dict[int, set[int]], nodes: list[int]) -> list[list[int]]:
    idx = {x: i for i, x in enumerate(nodes)}
    return [[idx[y] for y in pat[x]] for x in nodes]


# --------------------------------------------------------------------------------------
# Exact minimum-SWAP search (branch and bound)
# --------------------------------------------------------------------------------------


def exact_min_swaps(
    prog: Program, hw: Hardware, deadline: float, max_s: int = 12, max_l: int = 16, max_g: int = 24
) -> tuple[int, list[int] | None, list[tuple[tuple[int, int], ...]] | None]:
    """Branch-and-bound search for the true minimum SWAP count (ignoring depth).

    Processes 2Q gates in program order; logical qubits are placed lazily (free choice of
    any unused physical qubit) the first time they appear, exactly as the main router does.
    Once both qubits of a gate are placed, only SWAPs incident to one of their *current*
    physical positions are considered.

    This restriction is sound for minimizing total SWAP count alone: any SWAP that doesn't
    touch the current front gate's two carrier qubits cannot help satisfy that gate, so it
    can always be delayed until the step where it does touch a front gate's carriers, without
    changing the total count. So a search restricted this way is complete for this objective,
    and any feasible schedule it finds at budget S is a valid witness that the true minimum is
    <= S; iterating S upward from 0 and taking the first feasible S gives the exact minimum.

    Returns (result, l2t, moves):
      - If the true minimum was proven within `deadline`: (min_swaps, l2t, moves) with a
        witness routing in the same format `build_output` expects.
      - Otherwise: (largest S proven infeasible, None, None), i.e. min_swaps > that value.
        -1 means nothing was proven (too large, or no time).

    Bounded to small instances (`max_l`, `max_g`) because the search is exponential; this is
    an optional extra, never required for correctness (the caller always validates).
    """
    if prog.L > max_l or len(prog.gates) > max_g or not prog.gates:
        return -1, None, None
    gates = prog.gates
    G = len(gates)
    L = prog.L
    dist = hw.dist
    proven_infeasible = -1

    for s_budget in range(max_s + 1):
        if time.perf_counter() > deadline:
            return proven_infeasible, None, None
        memo: dict[tuple, int] = {}
        trail: list[tuple] = []

        def dfs(k: int, pos: tuple[int, ...], used: int) -> bool:
            if k == G:
                return True
            if time.perf_counter() > deadline:
                raise TimeoutError
            key = (k, pos)
            prev = memo.get(key)
            if prev is not None and prev <= used:
                return False
            if len(memo) > 2_000_000:
                memo.clear()
            memo[key] = used

            a, b = gates[k]
            pa, pb = pos[a], pos[b]
            if pa < 0 or pb < 0:
                occupied = {p for p in pos if p >= 0}
                opts: list[tuple[int, int]] = []
                if pa < 0 and pb < 0:
                    for u, v in hw.arcs:
                        if u not in occupied and v not in occupied:
                            opts.append((u, v))
                elif pa < 0:
                    for u in range(hw.n):
                        if u not in occupied and dist[u][pb] - 1 <= s_budget - used:
                            opts.append((u, pb))
                else:
                    for v in range(hw.n):
                        if v not in occupied and dist[pa][v] - 1 <= s_budget - used:
                            opts.append((pa, v))
                opts.sort(key=lambda uv: dist[uv[0]][uv[1]])
                for u, v in opts:
                    p = list(pos)
                    p[a], p[b] = u, v
                    trail.append(("place", a, u, b, v))
                    if dfs(k, tuple(p), used):
                        return True
                    trail.pop()
                return False

            d = dist[pa][pb]
            if d == 1:
                trail.append(("gate", k))
                if dfs(k + 1, pos, used):
                    return True
                trail.pop()
                return False
            if used + d - 1 > s_budget:
                return False
            p2l = {p: logical for logical, p in enumerate(pos) if p >= 0}
            pairs = {(min(x, y), max(x, y)) for x in (pa, pb) for y in hw.adj[x]}
            ordered = []
            for x, y in pairs:
                p = list(pos)
                lx, ly = p2l.get(x), p2l.get(y)
                if lx is not None:
                    p[lx] = y
                if ly is not None:
                    p[ly] = x
                ordered.append((dist[p[a]][p[b]], (x, y), tuple(p)))
            ordered.sort()
            for nd, sw, p in ordered:
                # Equivalent to the entry-prune the recursive call would apply immediately
                # (used+1) + (nd-1) > s_budget; skipping here just avoids the call overhead.
                if used + nd > s_budget:
                    continue
                trail.append(("swap", k, sw))
                if dfs(k, p, used + 1):
                    return True
                trail.pop()
            return False

        try:
            ok = dfs(0, tuple([-1] * L), 0)
        except TimeoutError:
            return proven_infeasible, None, None

        if ok:
            l2t = [-1] * L
            moves: list[list[tuple[int, int]]] = [[] for _ in range(G)]
            k = 0
            for step in trail:
                if step[0] == "place":
                    _, a, u, b, v = step
                    if l2t[a] < 0:
                        l2t[a] = u
                    if l2t[b] < 0:
                        l2t[b] = v
                elif step[0] == "swap":
                    _, k, sw = step
                    moves[k].append(sw)
            return s_budget, l2t, [tuple(m) for m in moves]
        proven_infeasible = s_budget

    return proven_infeasible, None, None


# --------------------------------------------------------------------------------------
# Lower bound
# --------------------------------------------------------------------------------------


def _lower_bound(
    prog: Program, hw: Hardware, deadline: float
) -> tuple[float, dict[int, int] | None, tuple[int, list[int], list[tuple]] | None]:
    if not prog.gates:
        return 0.0, None, None
    cp = prog.critical_path()
    lb = 0.5 * cp
    embs, exhausted = find_embeddings(hw, prog.gates, deadline)
    if embs:
        return lb, embs[0], None
    if exhausted:
        lb = max(lb, 1.0 + 0.5 * cp)
    partners: list[set[int]] = [set() for _ in range(prog.L)]
    count = [0] * prog.L
    for a, b in prog.gates:
        partners[a].add(b)
        partners[b].add(a)
        count[a] += 1
        count[b] += 1
    delta = hw.max_degree
    for q in range(prog.L):
        p = len(partners[q])
        if p <= delta:
            continue
        best = math.inf
        for s_c in range(p + 1):
            s_o = max(0, p - delta - s_c * (delta - 1))
            best = min(best, s_c + s_o + 0.5 * max(cp, count[q] + s_c))
        lb = max(lb, best)

    # Tighten with the exact minimum-SWAP search when the instance is small enough.
    # Leave a little slack in its sub-deadline so callers keep some time for the beam search.
    exact_deadline = min(deadline, time.perf_counter() + max(0.0, (deadline - time.perf_counter()) * 0.7))
    exact: tuple[int, list[int], list[tuple]] | None = None
    try:
        s_result, l2t, moves = exact_min_swaps(prog, hw, exact_deadline)
    except Exception:
        s_result, l2t, moves = -1, None, None
    if l2t is not None:
        lb = max(lb, s_result + 0.5 * cp)
        exact = (s_result, l2t, moves)
    elif s_result >= 0:
        lb = max(lb, (s_result + 1) + 0.5 * cp)
    return lb, None, exact


def lower_bound(program: list[tuple], hardware_graph: nx.Graph, time_limit: float = 5.0) -> float:
    """Provable lower bound on swap_count + 0.5 * depth for any valid routing."""
    return _lower_bound(Program(program), Hardware(hardware_graph), time.perf_counter() + time_limit)[0]


# --------------------------------------------------------------------------------------
# Beam-search router
# --------------------------------------------------------------------------------------


class State:
    """Tokens model physical occupants: token t starts on physical index t.

    A logical qubit is bound to a token either up front (fixed placement) or lazily when it
    is first used, so the token id is its initial physical position.
    """

    __slots__ = ("t2p", "p2t", "l2t", "t2l", "ready", "swaps", "depth", "hist")

    def copy(self) -> "State":
        s = State.__new__(State)
        s.t2p = self.t2p[:]
        s.p2t = self.p2t[:]
        s.l2t = self.l2t[:]
        s.t2l = self.t2l[:]
        s.ready = self.ready[:]
        s.swaps = self.swaps
        s.depth = self.depth
        s.hist = self.hist
        return s

    def swap(self, x: int, y: int) -> None:
        tx, ty = self.p2t[x], self.p2t[y]
        self.p2t[x], self.p2t[y] = ty, tx
        self.t2p[tx], self.t2p[ty] = y, x
        r = max(self.ready[x], self.ready[y]) + 1
        self.ready[x] = self.ready[y] = r
        if r > self.depth:
            self.depth = r
        self.swaps += 1

    def execute(self, x: int, y: int) -> None:
        r = max(self.ready[x], self.ready[y]) + 1
        self.ready[x] = self.ready[y] = r
        if r > self.depth:
            self.depth = r

    def assign(self, logical: int, token: int) -> None:
        self.l2t[logical] = token
        self.t2l[token] = logical

    @property
    def cost(self) -> float:
        return self.swaps + 0.5 * self.depth


def initial_state(n: int, L: int, placement: list[int] | None = None) -> State:
    s = State.__new__(State)
    s.t2p = list(range(n))
    s.p2t = list(range(n))
    s.l2t = [-1] * L
    s.t2l = [-1] * n
    s.ready = [0] * n
    s.swaps = 0
    s.depth = 0
    s.hist = None
    if placement is not None:
        for logical, phys in enumerate(placement):
            if phys >= 0:
                s.assign(logical, phys)
    return s


DEFAULT_PARAMS = {
    "width": 64,
    "slack": 1,
    "paths": 2,
    # window/decay retuned by autopilot A/B testing (see autopilot.py, autopilot_state.json
    # "param_recommendation"): decay=0.6/window=20 matched or beat decay=0.75/window=12 on
    # every benchmark across 3 seeds at a 15s budget, never worse.
    "window": 20,
    "decay": 0.6,
    "alpha": 1.0,
    "noise": 0.0,
    "new_opts": 4,
    "pair_opts": 12,
    "route_cap": 24,
}


class Router:
    def __init__(self, hw: Hardware, seq: GateSeq, L: int, params: dict, rng: random.Random):
        self.hw = hw
        self.seq = seq
        self.L = L
        p = dict(DEFAULT_PARAMS)
        p.update(params)
        self.width = p["width"]
        self.slack = p["slack"]
        self.path_limit = p["paths"]
        self.window = p["window"]
        self.decay = p["decay"]
        self.alpha = p["alpha"]
        self.noise = p["noise"]
        self.new_opts = p["new_opts"]
        self.pair_opts = p["pair_opts"]
        self.route_cap = p["route_cap"]
        self.rng = rng
        self.pow = [self.decay**i for i in range(self.window + 1)]

    def attraction(self, st: State, x: int, p: int, k: int) -> float:
        occ = self.seq.occ[x]
        gates = self.seq.gates
        dist = self.hw.dist[p]
        total = 0.0
        for j in occ[bisect.bisect_left(occ, k) :]:
            if j >= k + self.window:
                break
            a, b = gates[j]
            y = b if a == x else a
            ty = st.l2t[y]
            if ty >= 0:
                total += self.pow[j - k] * dist[st.t2p[ty]]
        return total

    def expand(self, st: State, k: int) -> list[State]:
        a, b = self.seq.gates[k]
        hw = self.hw
        ta, tb = st.l2t[a], st.l2t[b]
        rng = self.rng
        if ta >= 0 and tb >= 0:
            return self.route(st, a, b)
        free = [p for p in range(hw.n) if st.t2l[st.p2t[p]] < 0]
        seeds: list[State] = []
        if ta >= 0 or tb >= 0:
            placed, new = (a, b) if ta >= 0 else (b, a)
            if not free:
                return []
            pp = st.t2p[st.l2t[placed]]
            dist = hw.dist[pp]
            # Prefer close, then idle (low ready => leaves room to run future SWAPs in
            # parallel), then the lookahead attraction toward upcoming partners.
            ranked = sorted(
                free, key=lambda p: (dist[p], st.ready[p], self.attraction(st, new, p, k), rng.random())
            )
            dmin = dist[ranked[0]]
            for p in [p for p in ranked if dist[p] <= dmin + self.slack][: self.new_opts]:
                ch = st.copy()
                ch.assign(new, ch.p2t[p])
                seeds.append(ch)
        else:
            if len(free) < 2:
                return []
            freeset = set(free)
            pairs = [(u, v) for u in free for v in hw.adj[u] if v in freeset]
            if not pairs:
                dmin = min(hw.dist[u][v] for u in free for v in free if u != v)
                pairs = [(u, v) for u in free for v in free if u != v and hw.dist[u][v] == dmin]
            ranked = sorted(
                pairs,
                key=lambda uv: (
                    st.ready[uv[0]] + st.ready[uv[1]],
                    self.attraction(st, a, uv[0], k) + self.attraction(st, b, uv[1], k),
                    rng.random(),
                ),
            )
            for u, v in ranked[: self.pair_opts]:
                ch = st.copy()
                ch.assign(a, ch.p2t[u])
                ch.assign(b, ch.p2t[v])
                seeds.append(ch)
        out: list[State] = []
        for s in seeds:
            out.extend(self.route(s, a, b))
        return out

    def route(self, s: State, a: int, b: int) -> list[State]:
        hw = self.hw
        dist = hw.dist
        pa = s.t2p[s.l2t[a]]
        pb = s.t2p[s.l2t[b]]
        d = dist[pa][pb]
        if d == 0 or d >= _INF:
            return []
        if d == 1:
            ch = s.copy()
            ch.execute(pa, pb)
            ch.hist = (s.hist, ())
            return [ch]
        limit = d - 1 + self.slack
        # (exact cost, idleness of touched qubits, state) so we can rank candidates by the
        # real objective plus a depth-aware tie-break before capping the branching factor.
        scored: list[tuple[float, int, State]] = []
        da, db = dist[pa], dist[pb]
        for u, v in hw.arcs:
            ca = da[u]
            if ca > limit:
                continue
            if ca + db[v] > limit:
                continue
            for path_a in hw.paths(pa, u, self.path_limit):
                for path_b in hw.paths(pb, v, self.path_limit):
                    ch = s.copy()
                    moves = []
                    touched: set[int] = set()
                    for i in range(len(path_a) - 1):
                        ch.swap(path_a[i], path_a[i + 1])
                        moves.append((path_a[i], path_a[i + 1]))
                        touched.update((path_a[i], path_a[i + 1]))
                    if ch.t2p[ch.l2t[b]] != pb:
                        continue
                    for i in range(len(path_b) - 1):
                        ch.swap(path_b[i], path_b[i + 1])
                        moves.append((path_b[i], path_b[i + 1]))
                        touched.update((path_b[i], path_b[i + 1]))
                    qa = ch.t2p[ch.l2t[a]]
                    qb = ch.t2p[ch.l2t[b]]
                    if qb not in hw.adjset[qa]:
                        continue
                    ch.execute(qa, qb)
                    touched.update((qa, qb))
                    ch.hist = (s.hist, tuple(moves))
                    # Idle qubits (low post-move `ready`) leave more room for other chains
                    # to run in the same layer, so prefer SWAP chains that land on them.
                    idle = sum(ch.ready[q] for q in touched)
                    scored.append((ch.cost, idle, ch))
        if len(scored) > self.route_cap:
            scored.sort(key=lambda item: (item[0], item[1]))
            scored = scored[: self.route_cap]
        return [item[2] for item in scored]

    def evaluate(self, st: State, k_next: int) -> float:
        seq = self.seq
        dist = self.hw.dist
        l2t, t2p, ready = st.l2t, st.t2p, st.ready
        end = min(seq.G, k_next + self.window)
        r: dict[int, int] = {}
        depth = st.depth
        est = 0.0
        for j in range(k_next, end):
            a, b = seq.gates[j]
            ta, tb = l2t[a], l2t[b]
            if ta >= 0 and tb >= 0:
                pa, pb = t2p[ta], t2p[tb]
                extra = dist[pa][pb] - 1
                t = max(r.get(pa, ready[pa]), r.get(pb, ready[pb])) + 1 + (extra + 1) // 2
                r[pa] = r[pb] = t
                est += self.pow[j - k_next] * extra
            elif ta >= 0 or tb >= 0:
                pa = t2p[ta if ta >= 0 else tb]
                t = r.get(pa, ready[pa]) + 1
                r[pa] = t
            else:
                continue
            if t > depth:
                depth = t
        value = st.swaps + 0.5 * depth + self.alpha * est
        if self.noise:
            value += self.noise * self.rng.random()
        return value

    def run(self, init: State, deadline: float) -> State | None:
        beam = [init]
        for k in range(self.seq.G):
            best: dict[tuple, tuple[float, int, State]] = {}
            for st in beam:
                for ch in self.expand(st, k):
                    key = tuple(ch.t2p[t] if t >= 0 else -1 for t in ch.l2t)
                    cost = ch.cost
                    tie = sum(ch.ready)
                    old = best.get(key)
                    if old is None or cost < old[0] or (cost == old[0] and tie < old[1]):
                        best[key] = (cost, tie, ch)
            if not best:
                return None
            scored = [(self.evaluate(ch, k + 1), i, ch) for i, (_, _, ch) in enumerate(best.values())]
            if len(scored) > self.width:
                scored = heapq.nsmallest(self.width, scored)
            beam = [item[2] for item in scored]
            if time.perf_counter() > deadline:
                return None
        return min(beam, key=lambda s: (s.cost, s.swaps))


def unwind(state: State) -> list[tuple[tuple[int, int], ...]]:
    moves = []
    node = state.hist
    while node is not None:
        node, step = node
        moves.append(step)
    moves.reverse()
    return moves


# --------------------------------------------------------------------------------------
# Initial placements
# --------------------------------------------------------------------------------------


def anneal_placement(
    hw: Hardware, gates: list[tuple[int, int]], L: int, rng: random.Random, decay: float = 1.0, iters: int = 4000
) -> list[int] | None:
    weights: dict[tuple[int, int], float] = {}
    f = 1.0
    for a, b in gates:
        key = (a, b) if a < b else (b, a)
        weights[key] = weights.get(key, 0.0) + f
        f *= decay
    active = sorted({q for g in gates for q in g})
    if not active or len(active) > hw.n:
        return None
    nbrs: dict[int, list[tuple[int, float]]] = {q: [] for q in active}
    for (a, b), w in weights.items():
        nbrs[a].append((b, w))
        nbrs[b].append((a, w))
    phys = list(range(hw.n))
    rng.shuffle(phys)
    pos = {q: phys[i] for i, q in enumerate(active)}
    occ = [-1] * hw.n
    for q, p in pos.items():
        occ[p] = q
    dist = hw.dist

    def local(q: int, p: int) -> float:
        return sum(w * dist[p][pos[m]] for m, w in nbrs[q])

    cost = sum(w * dist[pos[a]][pos[b]] for (a, b), w in weights.items())
    best_cost, best_pos = cost, dict(pos)
    t_hi = max(weights.values()) * 2.0
    t_lo = t_hi * 0.005
    for it in range(iters):
        temp = t_hi * (t_lo / t_hi) ** (it / iters)
        q = active[rng.randrange(len(active))]
        p_old = pos[q]
        p_new = rng.randrange(hw.n)
        if p_new == p_old:
            continue
        m = occ[p_new]
        before = local(q, p_old) + (local(m, p_new) if m >= 0 else 0.0)
        pos[q] = p_new
        occ[p_new], occ[p_old] = q, m
        if m >= 0:
            pos[m] = p_old
        after = local(q, p_new) + (local(m, p_old) if m >= 0 else 0.0)
        delta = after - before
        if delta <= 0 or rng.random() < math.exp(-delta / temp):
            cost += delta
            if cost < best_cost - 1e-12:
                best_cost, best_pos = cost, dict(pos)
        else:
            pos[q] = p_old
            occ[p_old], occ[p_new] = q, m
            if m >= 0:
                pos[m] = p_new
    placement = [-1] * L
    for q, p in best_pos.items():
        placement[q] = p
    return placement


def prefix_embeddings(
    hw: Hardware, gates: list[tuple[int, int]], L: int, deadline: float, rng: random.Random, count: int = 4
) -> list[list[int]]:
    lo, hi = 0, len(gates)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        embs, _ = find_embeddings(hw, gates[:mid], deadline, node_budget=50_000)
        if embs:
            lo = mid
        else:
            hi = mid - 1
    if lo == 0:
        return []
    result: list[list[int]] = []
    seen = set()
    for _ in range(count * 3):
        if len(result) >= count or time.perf_counter() > deadline:
            break
        embs, _ = find_embeddings(hw, gates[:lo], deadline, rng=rng, node_budget=50_000)
        if not embs:
            continue
        key = tuple(sorted(embs[0].items()))
        if key in seen:
            continue
        seen.add(key)
        placement = [-1] * L
        for q, p in embs[0].items():
            placement[q] = p
        result.append(placement)
    return result


# --------------------------------------------------------------------------------------
# Output, validation, scoring
# --------------------------------------------------------------------------------------


def build_output(
    prog: Program, hw: Hardware, l2t: list[int], moves: list[tuple[tuple[int, int], ...]]
) -> tuple[dict, list[tuple]]:
    labels = hw.labels
    used = {t for t in l2t if t >= 0}
    spare = [t for t in range(hw.n) if t not in used]
    l2p = []
    for t in l2t:
        l2p.append(t if t >= 0 else spare.pop(0))
    placement = {prog.logicals[l]: labels[p] for l, p in enumerate(l2p)}
    p2l = [-1] * hw.n
    for l, p in enumerate(l2p):
        p2l[p] = l
    pos = l2p[:]
    routed: list[tuple] = []
    k = 0
    for op in prog.ops:
        if op[0] == "2Q":
            for x, y in moves[k]:
                routed.append(("SWAP", labels[x], labels[y]))
                lx, ly = p2l[x], p2l[y]
                p2l[x], p2l[y] = ly, lx
                if lx >= 0:
                    pos[lx] = y
                if ly >= 0:
                    pos[ly] = x
            routed.append(("2Q", labels[pos[prog.lindex[op[1]]]], labels[pos[prog.lindex[op[2]]]]))
            k += 1
        else:
            routed.append(("1Q", labels[pos[prog.lindex[op[1]]]]))
    return placement, routed


def is_valid(program: list[tuple], graph: nx.Graph, placement: dict, routed: list[tuple]) -> bool:
    """Same acceptance rules as starter_kit.scorer.validate_routed_program."""
    logicals = {q for op in program for q in op[1:]}
    if set(placement) != logicals:
        return False
    phys = list(placement.values())
    if len(phys) != len(set(phys)) or not set(phys) <= set(graph.nodes):
        return False
    p2l = {p: l for l, p in placement.items()}
    translated = []
    for op in routed:
        kind = op[0]
        if kind == "SWAP":
            _, x, y = op
            if not graph.has_edge(x, y):
                return False
            p2l[x], p2l[y] = p2l.get(y), p2l.get(x)
        elif kind == "2Q":
            _, x, y = op
            if not graph.has_edge(x, y) or p2l.get(x) is None or p2l.get(y) is None:
                return False
            translated.append(("2Q", p2l[x], p2l[y]))
        elif kind == "1Q":
            if p2l.get(op[1]) is None:
                return False
            translated.append(("1Q", p2l[op[1]]))
        else:
            return False
    return translated == [tuple(op) for op in program]


def score_routed(routed: list[tuple]) -> float:
    """Same formula as starter_kit.scorer.core_score."""
    last: dict = {}
    depth = 0
    swaps = 0
    for op in routed:
        if op[0] == "1Q":
            continue
        if op[0] == "SWAP":
            swaps += 1
        t = 1 + max(last.get(op[1], 0), last.get(op[2], 0))
        last[op[1]] = last[op[2]] = t
        depth = max(depth, t)
    return swaps + 0.5 * depth


def cancel_redundant_swaps(routed: list[tuple]) -> list[tuple]:
    """Remove SWAP(x, y) ... SWAP(x, y) pairs with nothing touching x or y in between --
    they compose to the identity, so both can be deleted for free (fewer swaps, and often
    less depth too, since the ops "between" them shift earlier relative to x/y's chain).

    Our beam search never generates this within a single gate's routing (route() doesn't
    build back-and-forth paths), but different gates get routed against a shared, evolving
    state, and separate portfolio members (forward pass, backward refinement pass, a fresh
    random restart, ...) can independently insert swaps that happen to undo each other
    across gates. This is a pure post-processing cleanup pass, safe to always apply: the
    caller re-validates with the official rules before accepting any result either way.
    """
    ops = list(routed)
    changed = True
    while changed:
        changed = False
        removed = [False] * len(ops)
        n = len(ops)
        for i in range(n):
            if removed[i] or ops[i][0] != "SWAP":
                continue
            x, y = ops[i][1], ops[i][2]
            for j in range(i + 1, n):
                if removed[j]:
                    continue
                op = ops[j]
                if op[0] == "1Q":
                    touches = op[1] in (x, y)
                else:
                    touches = op[1] in (x, y) or op[2] in (x, y)
                if not touches:
                    continue
                if op[0] == "SWAP" and {op[1], op[2]} == {x, y}:
                    removed[i] = removed[j] = True
                    changed = True
                break  # first touch of x or y after i, cancelling or not, stop scanning
        if changed:
            ops = [op for keep, op in zip((not r for r in removed), ops) if keep]
    return ops


def greedy_baseline(program: list[tuple], graph: nx.Graph) -> tuple[dict, list[tuple]]:
    """Identity placement + shortest-path SWAPs (same idea as the starter baseline)."""
    logicals = sorted({q for op in program for q in op[1:]})
    try:
        phys = sorted(graph.nodes)
    except TypeError:
        phys = list(graph.nodes)
    placement = {l: phys[i] for i, l in enumerate(logicals) if i < len(phys)}
    pos = dict(placement)
    p2l = {p: l for l, p in pos.items()}
    routed: list[tuple] = []
    for op in program:
        if op[0] == "1Q":
            routed.append(("1Q", pos[op[1]]))
            continue
        _, a, b = op
        if not graph.has_edge(pos[a], pos[b]):
            path = nx.shortest_path(graph, pos[a], pos[b])
            for x, y in zip(path[:-2], path[1:-1]):
                routed.append(("SWAP", x, y))
                lx, ly = p2l.get(x), p2l.get(y)
                p2l[x], p2l[y] = ly, lx
                if lx is not None:
                    pos[lx] = y
                if ly is not None:
                    pos[ly] = x
        routed.append(("2Q", pos[a], pos[b]))
    return placement, routed


# --------------------------------------------------------------------------------------
# Portfolio driver
# --------------------------------------------------------------------------------------


class Solver:
    def __init__(self, program: list[tuple], hardware_graph: nx.Graph, seed: int = 0):
        self.program = [tuple(op) for op in program]
        self.graph = hardware_graph
        self.prog = Program(self.program)
        self.hw = Hardware(hardware_graph)
        self.rng = random.Random(seed)
        self.seq = GateSeq(self.prog.gates, self.prog.L)
        self.rev_seq = GateSeq(self.prog.gates[::-1], self.prog.L)
        self.best: tuple[float, dict, list[tuple], str] | None = None
        self.best_state: State | None = None
        self.lb = 0.0
        self.log: list[tuple[float, float, str]] = []
        self.t0 = time.perf_counter()

    def _offer(self, placement: dict, routed: list[tuple], method: str) -> bool:
        cleaned = cancel_redundant_swaps(routed)
        if len(cleaned) != len(routed) and is_valid(self.program, self.graph, placement, cleaned):
            routed = cleaned
            method = f"{method} + swap-cancel"
        if not is_valid(self.program, self.graph, placement, routed):
            return False
        score = score_routed(routed)
        if self.best is None or score < self.best[0] - 1e-9:
            self.best = (score, placement, routed, method)
            self.log.append((time.perf_counter() - self.t0, score, method))
            return True
        return False

    def _offer_state(self, state: State | None, method: str) -> None:
        if state is None:
            return
        placement, routed = build_output(self.prog, self.hw, state.l2t, unwind(state))
        if self._offer(placement, routed, method):
            self.best_state = state

    def _done(self, deadline: float) -> bool:
        return time.perf_counter() > deadline or (self.best is not None and self.best[0] <= self.lb + 1e-9)

    def _beam(self, placement: list[int] | None, params: dict, deadline: float, reverse: bool = False) -> State | None:
        seq = self.rev_seq if reverse else self.seq
        router = Router(self.hw, seq, self.prog.L, params, self.rng)
        return router.run(initial_state(self.hw.n, self.prog.L, placement), deadline)

    def _final_placement(self, state: State) -> list[int]:
        return [state.t2p[t] if t >= 0 else -1 for t in state.l2t]

    def _refine(self, state: State, params: dict, deadline: float, rounds: int = 2) -> None:
        """SABRE-style bidirectional passes: the final mapping of a backward run seeds a forward run."""
        for _ in range(rounds):
            if self._done(deadline):
                return
            back = self._beam(self._final_placement(state), params, deadline, reverse=True)
            if back is None:
                return
            fwd = self._beam(self._final_placement(back), params, deadline)
            if fwd is None:
                return
            self._offer_state(fwd, f"bidirectional(w={params['width']})")
            state = fwd

    def run(self, deadline: float) -> tuple[dict, list[tuple]]:
        prog, hw = self.prog, self.hw
        try:
            self._offer(*greedy_baseline(self.program, self.graph), "greedy baseline")
        except Exception:
            pass
        if not prog.supported or prog.L > hw.n:
            return self._result()
        if not prog.gates:
            placement = {q: hw.labels[i] for i, q in enumerate(prog.logicals)}
            self._offer(placement, [("1Q", placement[op[1]]) for op in prog.ops], "no 2Q gates")
            return self._result()

        self.lb, embedding, exact = _lower_bound(
            prog, hw, min(deadline, time.perf_counter() + 0.25 * (deadline - time.perf_counter()))
        )
        if embedding is not None:
            placement = [-1] * prog.L
            for q, p in embedding.items():
                placement[q] = p
            state = initial_state(hw.n, prog.L, placement)
            self._offer(*build_output(prog, hw, state.l2t, [()] * len(prog.gates)), "zero-SWAP embedding")
            if self._done(deadline):
                return self._result()
        elif exact is not None:
            s_exact, exact_l2t, exact_moves = exact
            self._offer(*build_output(prog, hw, exact_l2t, exact_moves), f"exact IDA* (min swaps={s_exact})")
            if self._done(deadline):
                return self._result()

        starts: list[tuple[str, list[int] | None]] = [("lazy", None)]
        for i, pl in enumerate(prefix_embeddings(hw, prog.gates, prog.L, deadline, self.rng)):
            starts.append((f"prefix-embed{i}", pl))
        for decay in (1.0, 0.9, 0.7):
            pl = anneal_placement(hw, prog.gates, prog.L, self.rng, decay=decay)
            if pl is not None:
                starts.append((f"anneal(decay={decay})", pl))

        # Quick pass over every start to rank them.
        ranked: list[tuple[float, int, str, list[int] | None]] = []
        for i, (name, pl) in enumerate(starts):
            if self._done(deadline):
                return self._result()
            st = self._beam(pl, {"width": 8, "slack": 1}, deadline)
            if st is not None:
                self._offer_state(st, f"beam(w=8) from {name}")
                ranked.append((st.cost, i, name, pl))
        ranked.sort()
        top = ranked[:3]

        # Escalate beam width on the most promising starts.
        for width in (32, 128, 512, 2048):
            for slack in (1, 0):
                for _, _, name, pl in top:
                    if self._done(deadline):
                        return self._result()
                    st = self._beam(pl, {"width": width, "slack": slack}, deadline)
                    self._offer_state(st, f"beam(w={width},slack={slack}) from {name}")
            if self.best_state is not None and not self._done(deadline):
                self._refine(self.best_state, {"width": max(32, width // 4)}, deadline)

        # Randomized restarts until the budget runs out.
        pool = [pl for _, _, _, pl in ranked] or [None]
        while not self._done(deadline):
            params = {
                "width": self.rng.choice((16, 32, 64, 128, 256)),
                "slack": self.rng.choice((0, 1, 1, 2)),
                "window": self.rng.choice((6, 10, 14, 20)),
                "decay": self.rng.choice((0.6, 0.75, 0.9)),
                "alpha": self.rng.choice((0.5, 1.0, 1.5)),
                "noise": self.rng.choice((0.0, 0.25, 0.5)),
                "paths": self.rng.choice((1, 2, 3)),
            }
            start = self.rng.choice(pool + [None])
            st = self._beam(start, params, deadline)
            self._offer_state(st, f"random restart {params}")
            if st is not None and self.rng.random() < 0.3:
                self._refine(st, {"width": 32}, deadline, rounds=1)
        return self._result()

    def _result(self) -> tuple[dict, list[tuple]]:
        if self.best is None:
            return greedy_baseline(self.program, self.graph)
        return self.best[1], self.best[2]

    def info(self) -> dict:
        return {
            "score": self.best[0] if self.best else math.inf,
            "method": self.best[3] if self.best else "none",
            "lower_bound": self.lb,
            "log": self.log,
        }


def solve_with_info(
    program: list[tuple], hardware_graph: nx.Graph, time_budget: float | None = None, seed: int = 0
) -> tuple[dict, list[tuple], dict]:
    budget = DEFAULT_TIME_BUDGET if time_budget is None else time_budget
    solver = Solver(program, hardware_graph, seed=seed)
    placement, routed = solver.run(time.perf_counter() + budget)
    return placement, routed, solver.info()


def solve(program: list[tuple], hardware_graph: nx.Graph, time_budget: float | None = None, seed: int = 0):
    """Return (initial_placement, routed_program) for the given program and hardware graph."""
    placement, routed, _ = solve_with_info(program, hardware_graph, time_budget=time_budget, seed=seed)
    return placement, routed
