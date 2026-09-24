"""Exact combinatorial optimization for SWAP routing via CP-SAT (Google OR-Tools).

This is a SEPARATE, OPTIONAL module. It never touches `solution/solve.py`'s existing
`solve()` pipeline; it is meant to be used standalone (see the CLI at the bottom) and,
once validated, wired into `solve()`'s portfolio as one extra, guarded candidate.

Why this exists
----------------
`solve()` (the beam search in `solve.py`) is a heuristic. This module brings exact
combinatorial search (CP-SAT) to bear on the same problem as a structurally different
technique, for two possible payoffs:
  (a) an actual better routing than the heuristic found (ideally with a proof of
      optimality), or
  (b) if a full solve isn't tractable, a genuinely tighter LOWER BOUND than the loose
      per-qubit-degree argument in `solve.py`'s `_lower_bound` -- even a CP-SAT run that
      times out without a feasible solution reports a valid dual bound on its own
      objective, which is informative on its own.

The key subtlety this module is built around (found by reading `starter_kit/scorer.py`
directly, not assumed from the problem prompt): `validate_routed_program` requires the
non-SWAP ops of the routed program, translated back to logical qubits, to equal the
*exact original program list* -- not just "each qubit's own gates stay in relative
order", but the full global order across every qubit. Concretely: if program = [g0, g1]
with g0, g1 acting on disjoint qubits, a routed program that emits g1 before g0 is
INVALID even though the two gates commute physically. (Verified directly against
`translate_back_to_logical`: it appends translated ops to `translated` in the exact
list-order it encounters them in `routed_program`, then checks `translated == program`
element-for-element.)

That means the GATE ORDER is never a decision variable -- it's exactly the input order,
one gate at a time. All the placement/routing freedom is in *how many SWAPs, and which
ones, get inserted in the gap immediately before each gate*. This is why the model below
looks like "solve.py's own `exact_min_swaps` DFS, but as a CP-SAT model" rather than a
general OLSQ time-expanded model with free gate scheduling: free gate reordering isn't
just unnecessary here, it would build invalid programs.

A second subtlety, worked out and used for the depth handling: `schedule_layers_ordered`
(ASAP layering) assigns each op a layer via a simple greedy pass over the emitted list,
and this is a textbook fact about greedy list-scheduling: the resulting layer numbers
(and therefore depth = max layer) do not depend on which valid ordering of *mutually
independent* ops (ops touching disjoint physical qubits) you emit them in. So once we've
decided which SWAPs happen in which gap, we do not need to fight over the exact emission
order among independent same-gap SWAPs to get the correct, official depth -- we hand the
result to the real scorer and trust it.

Model (Phase 1 -- exact minimum SWAP count, ignoring depth)
-------------------------------------------------------------
- Logical qubits are padded with dummy placeholders up to the physical qubit count N, so
  placement is always a genuine permutation of size N (`AddInverse`, not a partial map with
  sentinel -1 values -- that partial-with-sentinel version is what silently breaks: an
  "unclaimed" physical slot has no logical owner to swap consistently, so a swap touching
  it doesn't have a well defined effect. A full permutation with dummy occupants for unused
  physical qubits sidesteps the whole issue.).
- For each of the G two-qubit gates (in fixed program order) there is a "gap" of up to K
  slots immediately before it. Each slot is either inactive, or performs exactly one SWAP
  on one hardware edge (encoded as a set of mutually exclusive boolean indicators per edge).
- Snapshot s = i*K + k tracks the placement immediately after gap i's slot k has been
  applied (s=0 is the initial placement, a free variable). Gate j's two logical qubits
  must be adjacent (an `AddAllowedAssignments` table constraint over the hardware edges)
  in the snapshot right after gap j is done, i.e. snapshot (j+1)*K.
- Objective: minimize total active slots (= total SWAP count).
- K is a per-gap CAP, not a global time horizon. Any solution using at most K swaps in any
  single gap is representable; solve.py's own `exact_min_swaps` docstring argues (and we
  reuse the same argument) that for SWAP-COUNT-ONLY minimization, any SWAP that doesn't
  directly help the current front gate can always be delayed to whichever gap it actually
  becomes useful in without changing the total count -- so an optimal swap-count solution
  can always be rearranged so each gap only does the shortest-path swaps needed to satisfy
  ITS OWN gate, i.e. at most (distance between that gate's qubits) - 1 <= diameter - 1
  swaps. We pick K empirically (checked against both the diameter and the max per-gap swap
  count in every solution we've found) and say so plainly when reporting bounds -- see
  `find_min_swaps`'s docstring for exactly what is and isn't proven.

Depth (Phase 2)
---------------
Modeling the *exact* official ASAP depth inside CP-SAT is possible but involved (the
per-physical-qubit "last layer" chain depends on which edge a slot's swap lands on, which
is itself a decision variable). Instead of building that, once Phase 1 finds (or bounds)
the minimum swap count S*, Phase 2 re-solves with total SWAPs capped at S* (or S*+slack)
and collects many *distinct* feasible solutions (varying random seeds / search diversity)
-- each one gets translated to a real `(placement, routed_program)` and scored with the
actual, unmodified `starter_kit.scorer.score_summary`. We keep whichever real-scored
candidate is best. This is an explicit, sanctioned decomposition (exact swap-count search,
separate depth-minimization pass via real-scoring) rather than a single joint model, and
it means we NEVER trust CP-SAT's own notion of "depth" for a reported number -- only the
official scorer's.

Optionality
-----------
`ortools` is imported in a try/except. Every public function checks `ORTOOLS_AVAILABLE`
first and returns a clean "unavailable" result (never raises) if it's missing. Nothing in
`solution/solve.py`'s `solve()` contract depends on this module or on `ortools` being
importable.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from ortools.sat.python import cp_model

    ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised explicitly in tests by hiding ortools
    cp_model = None  # type: ignore[assignment]
    ORTOOLS_AVAILABLE = False

from .solve import Hardware, Program, build_output, cancel_redundant_swaps, is_valid, score_routed

# --------------------------------------------------------------------------------------
# Model construction
# --------------------------------------------------------------------------------------


def _edges_local(hw: Hardware) -> list[tuple[int, int]]:
    """Unique undirected edges in the Hardware object's local (dense 0..n-1) index space."""
    edges = []
    for u in range(hw.n):
        for v in hw.adj[u]:
            if u < v:
                edges.append((u, v))
    return edges


def _build_gap_model(prog: Program, hw: Hardware, K: int) -> dict[str, Any]:
    """Build the Phase-1 CP-SAT model: padded-permutation snapshots + K swap slots per gap.

    Returns a dict of model components (kept flat/explicit rather than a class, so it's
    easy to inspect from a REPL while debugging).
    """
    model = cp_model.CpModel()
    N = hw.n
    G = len(prog.gates)
    S = G * K + 1
    edges = _edges_local(hw)
    E = len(edges)
    incident: list[list[int]] = [[] for _ in range(N)]
    for idx, (u, v) in enumerate(edges):
        incident[u].append(idx)
        incident[v].append(idx)

    pos = [[model.NewIntVar(0, N - 1, f"pos_{s}_{q}") for q in range(N)] for s in range(S)]
    occ = [[model.NewIntVar(0, N - 1, f"occ_{s}_{p}") for p in range(N)] for s in range(S)]
    for s in range(S):
        model.AddInverse(pos[s], occ[s])
    # Dummy logicals (indices L..N-1) never appear in a gate, so they are
    # interchangeable. Fix their initial order; otherwise solution enumeration
    # burns its budget on relabelings of the unused physical qubits.
    for q in range(prog.L, N - 1):
        model.Add(pos[0][q] < pos[0][q + 1])

    active: dict[tuple[int, int], Any] = {}
    edge_bool: dict[tuple[int, int, int], Any] = {}
    for i in range(G):
        for k in range(1, K + 1):
            a_var = model.NewBoolVar(f"active_{i}_{k}")
            active[(i, k)] = a_var
            ebs = []
            for idx in range(E):
                b = model.NewBoolVar(f"edge_{i}_{k}_{idx}")
                edge_bool[(i, k, idx)] = b
                ebs.append(b)
            model.Add(sum(ebs) == a_var)

            s_prev = i * K + (k - 1)
            s_cur = i * K + k
            for idx, (u, v) in enumerate(edges):
                b = edge_bool[(i, k, idx)]
                model.Add(occ[s_cur][u] == occ[s_prev][v]).OnlyEnforceIf(b)
                model.Add(occ[s_cur][v] == occ[s_prev][u]).OnlyEnforceIf(b)
            for p in range(N):
                inc = incident[p]
                if not inc:
                    model.Add(occ[s_cur][p] == occ[s_prev][p])
                    continue
                touched = model.NewBoolVar(f"touched_{i}_{k}_{p}")
                model.Add(touched == sum(edge_bool[(i, k, idx)] for idx in inc))
                model.Add(occ[s_cur][p] == occ[s_prev][p]).OnlyEnforceIf(touched.Not())

    allowed_pairs = []
    for u, v in edges:
        allowed_pairs.append((u, v))
        allowed_pairs.append((v, u))
    for j, (a, b) in enumerate(prog.gates):
        s_j = (j + 1) * K
        model.AddAllowedAssignments([pos[s_j][a], pos[s_j][b]], allowed_pairs)

    return {
        "model": model,
        "pos": pos,
        "occ": occ,
        "active": active,
        "edge_bool": edge_bool,
        "S": S,
        "N": N,
        "G": G,
        "K": K,
        "edges": edges,
        "incident": incident,
    }


def _extract_l2t_moves(
    solver: "cp_model.CpSolver", built: dict[str, Any], prog: Program
) -> tuple[list[int], list[tuple[tuple[int, int], ...]]]:
    """Pull (l2t, moves) out of a solved model, in exactly the format `build_output` (and
    solve.py's own `exact_min_swaps`) expect."""
    N = built["N"]
    K = built["K"]
    G = built["G"]
    edges = built["edges"]
    pos0 = [solver.Value(built["pos"][0][q]) for q in range(N)]
    l2t = [pos0[l] for l in range(prog.L)]
    moves: list[tuple[tuple[int, int], ...]] = []
    for i in range(G):
        gap_moves = []
        for k in range(1, K + 1):
            if solver.Value(built["active"][(i, k)]):
                for idx, (u, v) in enumerate(edges):
                    if solver.Value(built["edge_bool"][(i, k, idx)]):
                        gap_moves.append((u, v))
                        break
        moves.append(tuple(gap_moves))
    return l2t, moves


def _apply_hint(
    model: "cp_model.CpModel",
    built: dict[str, Any],
    prog: Program,
    l2t: list[int],
    moves: list[tuple[tuple[int, int], ...]],
) -> bool:
    """Best-effort warm start from an existing (l2t, moves) witness (e.g. from the beam
    search or `exact_min_swaps`). Returns False (no-op) if the witness doesn't fit this
    model's K, rather than raising -- hints are a speed optimization, never required for
    correctness.
    """
    N, K, G = built["N"], built["K"], built["G"]
    if any(len(moves[i]) > K for i in range(min(G, len(moves)))):
        return False
    edge_index: dict[tuple[int, int], int] = {}
    for idx, (u, v) in enumerate(built["edges"]):
        edge_index[(u, v)] = idx
        edge_index[(v, u)] = idx

    pos_state = [-1] * N
    used_phys = set()
    for l in range(prog.L):
        pos_state[l] = l2t[l]
        used_phys.add(l2t[l])
    spare = [p for p in range(N) if p not in used_phys]
    for i, l in enumerate(range(prog.L, N)):
        pos_state[l] = spare[i]
    occ_state = [-1] * N
    for l in range(N):
        occ_state[pos_state[l]] = l

    hint_vars = []
    hint_vals = []
    for q in range(N):
        hint_vars.append(built["pos"][0][q])
        hint_vals.append(pos_state[q])
    for p in range(N):
        hint_vars.append(built["occ"][0][p])
        hint_vals.append(occ_state[p])

    for i in range(G):
        gmoves = moves[i] if i < len(moves) else ()
        for k in range(1, K + 1):
            if k <= len(gmoves):
                u, v = gmoves[k - 1]
                lu, lv = occ_state[u], occ_state[v]
                occ_state[u], occ_state[v] = lv, lu
                pos_state[lu], pos_state[lv] = v, u
                hint_vars.append(built["active"][(i, k)])
                hint_vals.append(1)
                idx = edge_index[(u, v)]
                for eidx in range(len(built["edges"])):
                    hint_vars.append(built["edge_bool"][(i, k, eidx)])
                    hint_vals.append(1 if eidx == idx else 0)
            else:
                hint_vars.append(built["active"][(i, k)])
                hint_vals.append(0)
                for eidx in range(len(built["edges"])):
                    hint_vars.append(built["edge_bool"][(i, k, eidx)])
                    hint_vals.append(0)
            s_cur = i * K + k
            for q in range(N):
                hint_vars.append(built["pos"][s_cur][q])
                hint_vals.append(pos_state[q])
            for p in range(N):
                hint_vars.append(built["occ"][s_cur][p])
                hint_vals.append(occ_state[p])

    # OR-Tools accepts AddHint(var, value) per variable only (not a bulk list form).
    for var, val in zip(hint_vars, hint_vals):
        model.AddHint(var, val)
    return True


# --------------------------------------------------------------------------------------
# Phase 1: exact minimum SWAP count
# --------------------------------------------------------------------------------------


def _solve_phase1(
    prog: Program,
    hw: Hardware,
    K: int,
    time_limit_s: float,
    num_workers: int,
    seed: int,
    hint: tuple[list[int], list[tuple[tuple[int, int], ...]]] | None,
) -> dict[str, Any]:
    built = _build_gap_model(prog, hw, K)
    model = built["model"]
    active_vars = list(built["active"].values())
    model.Minimize(sum(active_vars))

    if hint is not None:
        try:
            _apply_hint(model, built, prog, hint[0], hint[1])
        except Exception:
            pass

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = num_workers
    solver.parameters.random_seed = seed
    t0 = time.perf_counter()
    status = solver.Solve(model)
    wall = time.perf_counter() - t0

    result: dict[str, Any] = {
        "K": K,
        "status": solver.StatusName(status),
        "wall_time": wall,
        "best_bound": None,
        "swaps": None,
        "l2t": None,
        "moves": None,
    }
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["swaps"] = int(round(solver.ObjectiveValue()))
        result["l2t"], result["moves"] = _extract_l2t_moves(solver, built, prog)
    # Capture the dual bound whenever the solver exposes one -- including UNKNOWN
    # (timeout without a proven optimum). That bound is what makes a timed-out
    # dense_random run reportable even when no feasible improvement is found.
    try:
        bound = solver.BestObjectiveBound()
        # OR-Tools returns +/-inf when no bound is known yet; treat those as absent.
        if bound is not None and abs(bound) < 1e15:
            result["best_bound"] = float(bound)
    except Exception:
        result["best_bound"] = None
    return result


def find_min_swaps(
    program: list[tuple],
    hardware_graph,
    k_values: tuple[int, ...] = (2, 3, 4),
    time_limit_per_k: float = 60.0,
    num_workers: int = 8,
    seed: int = 1,
    warm_start: tuple[list[int], list[tuple[tuple[int, int], ...]]] | None = None,
) -> dict[str, Any]:
    """Escalating-K search for the true minimum SWAP count (Phase 1, depth ignored).

    IMPORTANT caveat on what the returned bound does and doesn't prove: each K value caps
    how many SWAPs may occur in any single gap between consecutive gates. A solution using
    at most K swaps per gap is always representable, and this cap is monotonically
    non-restrictive as K grows (any K-solution is also a (K+1)-solution). So:
      - `best_swaps` (and its witness `best_l2t`/`best_moves`) is always a genuine, valid
        upper bound on the true minimum SWAP count -- it's a real, checkable solution.
      - The per-K `best_bound` CP-SAT reports is only a valid lower bound *for that
        K-capped model*, not unconditionally for the true (uncapped) problem, unless K
        happens to be large enough that no true optimum needs more swaps in one gap than
        K allows. We do not claim the latter unless the evidence directly supports it
        (see `notes`).
    """
    if not ORTOOLS_AVAILABLE:
        return {"available": False, "reason": "ortools not importable"}

    prog = Program(program)
    hw = Hardware(hardware_graph)
    if not prog.gates:
        return {"available": True, "best_swaps": 0, "best_l2t": None, "best_moves": [], "per_k": [], "notes": ["no 2Q gates"]}
    if prog.L > hw.n:
        return {"available": True, "best_swaps": None, "best_l2t": None, "best_moves": None, "per_k": [], "notes": ["more logical qubits than physical qubits"]}

    per_k: list[dict[str, Any]] = []
    best_swaps: int | None = None
    best_l2t = None
    best_moves = None
    notes: list[str] = []
    hint = warm_start
    prev_swaps: int | None = None

    for K in k_values:
        r = _solve_phase1(prog, hw, K, time_limit_per_k, num_workers, seed, hint)
        per_k.append(r)
        if r["swaps"] is not None and (best_swaps is None or r["swaps"] < best_swaps):
            best_swaps = r["swaps"]
            best_l2t, best_moves = r["l2t"], r["moves"]
            hint = (best_l2t, best_moves)  # warm-start the next (larger) K from this witness
        if r["status"] == "OPTIMAL":
            if prev_swaps is not None and r["swaps"] == prev_swaps:
                notes.append(
                    f"K={K} proved OPTIMAL at {r['swaps']} swaps, matching K={k_values[per_k.index(r) - 1]}'s "
                    "optimum -- stabilized, stopping escalation early."
                )
                break
            prev_swaps = r["swaps"]

    return {
        "available": True,
        "best_swaps": best_swaps,
        "best_l2t": best_l2t,
        "best_moves": best_moves,
        "per_k": per_k,
        "notes": notes,
    }


# --------------------------------------------------------------------------------------
# Phase 2: fixed swap-count depth search via diverse solution collection
# --------------------------------------------------------------------------------------


class _CollectSolutions(cp_model.CpSolverSolutionCallback if ORTOOLS_AVAILABLE else object):
    def __init__(self, built: dict[str, Any], prog: Program, limit: int):
        super().__init__()
        self.built = built
        self.prog = prog
        self.limit = limit
        self.solutions: list[tuple[list[int], list[tuple[tuple[int, int], ...]]]] = []

    def on_solution_callback(self) -> None:  # noqa: D102 - ortools API
        l2t, moves = _extract_l2t_moves(self, self.built, self.prog)
        self.solutions.append((l2t, moves))
        if len(self.solutions) >= self.limit:
            self.StopSearch()


def collect_diverse_solutions(
    prog: Program,
    hw: Hardware,
    K: int,
    swap_cap: int,
    time_limit_s: float,
    num_solutions: int,
    seeds: tuple[int, ...],
    hint: tuple[list[int], list[tuple[tuple[int, int], ...]]] | None = None,
) -> list[tuple[list[int], list[tuple[tuple[int, int], ...]]]]:
    """Collect up to `num_solutions` distinct feasible witnesses using at most `swap_cap`
    total SWAPs, across several random seeds for diversity. No depth objective inside
    CP-SAT (see module docstring); the caller real-scores every witness afterward.
    """
    all_solutions: list[tuple[list[int], list[tuple[tuple[int, int], ...]]]] = []
    for seed in seeds:
        built = _build_gap_model(prog, hw, K)
        model = built["model"]
        active_vars = list(built["active"].values())
        model.Add(sum(active_vars) <= swap_cap)
        if hint is not None:
            try:
                _apply_hint(model, built, prog, hint[0], hint[1])
            except Exception:
                pass
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_search_workers = 1  # required for enumerate_all_solutions
        solver.parameters.enumerate_all_solutions = True
        solver.parameters.random_seed = seed
        solver.parameters.randomize_search = True
        callback = _CollectSolutions(built, prog, num_solutions)
        solver.Solve(model, callback)
        all_solutions.extend(callback.solutions)
    return all_solutions


# --------------------------------------------------------------------------------------
# Real-scoring glue (always via the actual scorer, never the model's own objective)
# --------------------------------------------------------------------------------------


def _score_with_official_scorer(program: list[tuple], hardware_graph, placement: dict, routed: list[tuple]) -> dict:
    from starter_kit.scorer import score_summary  # local import: keep starter_kit optional-ish too

    return score_summary(program, hardware_graph, placement, routed)


def _build_and_score(
    program: list[tuple],
    hardware_graph,
    prog: Program,
    hw: Hardware,
    l2t: list[int],
    moves: list[tuple[tuple[int, int], ...]],
) -> tuple[dict, list[tuple], dict] | None:
    placement, routed = build_output(prog, hw, l2t, moves)
    cleaned = cancel_redundant_swaps(routed)
    if len(cleaned) != len(routed) and is_valid(program, hardware_graph, placement, cleaned):
        routed = cleaned
    if not is_valid(program, hardware_graph, placement, routed):
        return None
    official = _score_with_official_scorer(program, hardware_graph, placement, routed)
    if not official["valid"]:
        return None
    return placement, routed, official


# --------------------------------------------------------------------------------------
# Top-level orchestration
# --------------------------------------------------------------------------------------


def solve_cpsat(
    program: list[tuple],
    hardware_graph,
    k_values: tuple[int, ...] = (2, 3, 4),
    time_limit_phase1: float = 60.0,
    time_limit_phase2: float = 60.0,
    num_solutions: int = 30,
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5),
    warm_start: tuple[dict, list[tuple]] | None = None,
    num_workers: int = 8,
    swap_slacks: tuple[int, ...] = (0, 1, 2),
) -> dict[str, Any]:
    """Full pipeline: Phase 1 (exact/best-known min swap count + valid dual bound) then
    Phase 2 (fixed-swap-count diverse solution collection, real-scored). Never raises;
    returns `{"available": False, ...}` if ortools isn't importable, and always validates
    every candidate against the actual `starter_kit.scorer.score_summary` before it's
    reported as a result.

    `warm_start`, if given, is an existing (placement, routed_program) pair (e.g. from
    `solution.solve.solve`) used to seed Phase 1's search.

    `swap_slacks` controls Phase 2: for each slack s we collect solutions using at most
    S*+s SWAPs. Extra swaps can still beat S*-only solutions on the joint objective when
    they buy enough depth reduction (e.g. 7 + 0.5*8 = 11.0 < 6 + 0.5*11 = 11.5).
    """
    if not ORTOOLS_AVAILABLE:
        return {"available": False, "reason": "ortools not importable"}

    prog = Program(program)
    hw = Hardware(hardware_graph)
    diagnostics: dict[str, Any] = {"available": True}

    hint = None
    if warm_start is not None and prog.gates:
        try:
            hint = _warm_start_to_l2t_moves(prog, hw, warm_start[0], warm_start[1])
        except Exception:
            hint = None

    phase1 = find_min_swaps(
        program,
        hardware_graph,
        k_values=k_values,
        time_limit_per_k=time_limit_phase1,
        num_workers=num_workers,
        seed=1,
        warm_start=hint,
    )
    diagnostics["phase1"] = phase1
    if not phase1.get("available") or phase1.get("best_swaps") is None:
        diagnostics["best_candidate"] = None
        return diagnostics

    best_swaps = phase1["best_swaps"]
    best_K = max(k_values)
    candidates: list[tuple[dict, list[tuple], dict]] = []

    witness = _build_and_score(program, hardware_graph, prog, hw, phase1["best_l2t"], phase1["best_moves"])
    if witness is not None:
        candidates.append(witness)

    phase2_pool_size = 0
    if prog.gates:
        time_per_call = time_limit_phase2 / max(1, len(seeds) * max(1, len(swap_slacks)))
        for slack in swap_slacks:
            pool = collect_diverse_solutions(
                prog,
                hw,
                best_K,
                best_swaps + slack,
                time_per_call,
                num_solutions,
                seeds,
                hint=(phase1["best_l2t"], phase1["best_moves"]),
            )
            phase2_pool_size += len(pool)
            for l2t, moves in pool:
                got = _build_and_score(program, hardware_graph, prog, hw, l2t, moves)
                if got is not None:
                    candidates.append(got)
        diagnostics["phase2_pool_size"] = phase2_pool_size
        diagnostics["phase2_swap_slacks"] = list(swap_slacks)

    if not candidates:
        diagnostics["best_candidate"] = None
        return diagnostics

    best = min(candidates, key=lambda c: c[2]["score"])
    diagnostics["best_candidate"] = {"placement": best[0], "routed": best[1], "official": best[2]}
    diagnostics["num_candidates_scored"] = len(candidates)
    return diagnostics


def _warm_start_to_l2t_moves(
    prog: Program, hw: Hardware, placement: dict, routed: list[tuple]
) -> tuple[list[int], list[tuple[tuple[int, int], ...]]] | None:
    """Convert an existing (placement, routed_program) result (e.g. from solve.py) into
    the (l2t, moves) witness format this module's models use, for hinting."""
    label_to_idx = hw.index
    l2t = [label_to_idx[placement[q]] for q in prog.logicals]
    moves: list[list[tuple[int, int]]] = [[] for _ in prog.gates]
    k = 0
    for op in routed:
        if op[0] == "SWAP":
            moves[k].append((label_to_idx[op[1]], label_to_idx[op[2]]))
        elif op[0] == "2Q":
            k += 1
    return l2t, [tuple(m) for m in moves]


def cpsat_candidate(
    program: list[tuple],
    hardware_graph,
    time_budget: float = 60.0,
    warm_start: tuple[dict, list[tuple]] | None = None,
) -> tuple[dict, list[tuple]] | None:
    """Thin wrapper meant for use as one extra, guarded candidate in solve.py's
    portfolio. Returns (placement, routed_program) already validated against the real
    scorer, or None if ortools is unavailable / nothing feasible was found in budget.
    Callers MUST still wrap this in try/except ImportError (or catch broadly) per the
    optional-dependency requirement -- this function itself never raises on missing
    ortools, but callers shouldn't assume that's the only possible failure mode.
    """
    if not ORTOOLS_AVAILABLE:
        return None
    result = solve_cpsat(
        program,
        hardware_graph,
        k_values=(2, 3),
        time_limit_phase1=time_budget * 0.5,
        time_limit_phase2=time_budget * 0.5,
        num_solutions=10,
        seeds=(1, 2, 3),
        warm_start=warm_start,
        swap_slacks=(0, 1, 2),
    )
    best = result.get("best_candidate")
    if best is None:
        return None
    return best["placement"], best["routed"]


# --------------------------------------------------------------------------------------
# Toy validation (hand-built, tiny instances -- sanity-check the model before trusting it
# on real benchmarks)
# --------------------------------------------------------------------------------------


def _toy_validation() -> None:
    import networkx as nx

    from starter_kit.scorer import score_summary

    print("=== toy validation ===")

    # Toy 1: a single gate on two qubits that are NOT adjacent on a 3-node path graph
    # (0-1-2). Optimal: place logical 0,1 at physical 0,2 -> needs exactly 1 SWAP to bring
    # them together (e.g. swap(0,1) then gate on (1,2), or swap(1,2) then gate on (0,1)).
    # Minimum swap count must be exactly 1 (they can never be zero-SWAP since the only
    # placement putting them directly on an edge trivially requires 0 swaps -- wait, check
    # both cases explicitly below).
    program1 = [("2Q", 0, 1)]
    graph1 = nx.path_graph(3)
    r1 = find_min_swaps(program1, graph1, k_values=(1, 2), time_limit_per_k=10.0)
    assert r1["available"]
    print("toy1 (2 logical qubits, path graph of 3): min_swaps =", r1["best_swaps"])
    assert r1["best_swaps"] == 0, "two qubits with one gate can always be placed directly on an edge"

    # Toy 2: 3-qubit chain program (0-1, 1-2) on a hardware graph that is a *disconnected*
    # pair of edges: physical 0-1 and physical 2-3 (no path between the two components).
    # Logical qubits 0,1,2 need 0->1->2 connectivity; hardware only offers two disjoint
    # edges, so logical 1 (the shared/center qubit) must sit on a physical qubit adjacent
    # to both others, which is impossible in a disconnected graph unless the mapping is
    # clever. Actually with 2 disjoint edges and 3 logical qubits needing a path, this is
    # infeasible with zero swaps but let's use a graph that actually allows a interesting
    # forced-swap case instead: a path graph of 3 physical qubits (0-1-2) with a 3-gate
    # chain program on 4 logical qubits (0-1, 1-2, 2-3) -- qubit 3 has no physical home
    # once 0,1,2 occupy the only three physical slots, so let's use a 4-node path instead.
    program2 = [("2Q", 0, 1), ("2Q", 1, 2), ("2Q", 2, 3)]
    graph2 = nx.path_graph(4)
    r2 = find_min_swaps(program2, graph2, k_values=(1, 2), time_limit_per_k=10.0)
    print("toy2 (chain of 4 on a path of 4): min_swaps =", r2["best_swaps"])
    assert r2["best_swaps"] == 0, "a chain program embeds with zero swaps on a path of the same length"

    # Toy 3: a star program (center talks to 3 leaves) on a path graph of 4 -- the center
    # has degree 3 in the program but the path graph's max degree is 2, so at least one
    # SWAP is unavoidable (this mirrors ghz_star's own lower-bound argument in miniature).
    program3 = [("2Q", 0, 1), ("2Q", 0, 2), ("2Q", 0, 3)]
    graph3 = nx.path_graph(4)
    r3 = find_min_swaps(program3, graph3, k_values=(1, 2, 3), time_limit_per_k=10.0)
    print("toy3 (star of degree 3 on a path of 4, max hw degree 2): min_swaps =", r3["best_swaps"])
    assert r3["best_swaps"] is not None and r3["best_swaps"] >= 1, "degree-3 center can't fit on a max-degree-2 hardware graph with zero swaps"

    # Now fully round-trip toy3 through build_output + the REAL official scorer.
    prog3 = Program(program3)
    hw3 = Hardware(graph3)
    placement, routed, official = _build_and_score(program3, graph3, prog3, hw3, r3["best_l2t"], r3["best_moves"])  # type: ignore[misc]
    print("toy3 official scorer result:", official)
    assert official["valid"]
    assert official["swap_count"] == r3["best_swaps"], "CP-SAT's own swap count must match the official scorer's count"
    double_check = score_summary(program3, graph3, placement, routed)
    assert double_check == official

    # Warm-start must actually apply (regression: bulk AddHint silently failed on ortools 9.x).
    hinted = find_min_swaps(
        program3,
        graph3,
        k_values=(2,),
        time_limit_per_k=5.0,
        warm_start=(r3["best_l2t"], r3["best_moves"]),
    )
    assert hinted["best_swaps"] == r3["best_swaps"]
    print("warm-start re-solve preserved min_swaps =", hinted["best_swaps"])

    print("All toy validations passed.")


def _run_benchmark_cli(names: list[str], phase1_s: float, phase2_s: float, k_values: tuple[int, ...]) -> None:
    import json

    from starter_kit.benchmarks import BENCHMARKS
    from starter_kit.hardware import build_hardware_graph

    graph = build_hardware_graph()
    state_path = Path(__file__).resolve().parent / "autopilot_state.json"
    warm_by_name: dict[str, tuple[dict, list[tuple]]] = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())
        for name, row in state.get("rows", {}).items():
            placement = {int(k): v for k, v in row["placement"].items()}
            routed = [tuple(op) for op in row["routed"]]
            warm_by_name[name] = (placement, routed)

    for name in names:
        program = BENCHMARKS[name]
        print(f"\n=== {name} ===")
        print(f"L={Program(program).L} G={len(Program(program).gates)} crit={Program(program).critical_path()}")
        warm = warm_by_name.get(name)
        result = solve_cpsat(
            program,
            graph,
            k_values=k_values,
            time_limit_phase1=phase1_s,
            time_limit_phase2=phase2_s,
            num_solutions=20,
            seeds=(1, 2, 3, 4, 5, 6, 7, 8),
            warm_start=warm,
            num_workers=8,
            swap_slacks=(0, 1, 2),
        )
        p1 = result.get("phase1", {})
        print("phase1 available:", p1.get("available"), "best_swaps:", p1.get("best_swaps"))
        for pk in p1.get("per_k", []):
            print(
                f"  K={pk['K']} status={pk['status']} swaps={pk['swaps']} "
                f"bound={pk['best_bound']} wall={pk['wall_time']:.1f}s"
            )
        for note in p1.get("notes", []):
            print("  note:", note)
        best = result.get("best_candidate")
        if best is None:
            print("no scored candidate")
        else:
            off = best["official"]
            print(
                f"best official: score={off['score']} swaps={off['swap_count']} "
                f"depth={off['depth']} valid={off['valid']} "
                f"(from {result.get('num_candidates_scored', 0)} candidates, "
                f"pool={result.get('phase2_pool_size', 0)})"
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CP-SAT routing solver (optional ortools)")
    parser.add_argument("--toy", action="store_true", help="run tiny hand-built validations")
    parser.add_argument(
        "--benchmark",
        nargs="+",
        choices=["ghz_star", "chain_trotter", "ladder_trotter", "qaoa_random", "dense_random", "vqe_layers"],
        help="run Phase1+Phase2 on named public benchmarks (warm-started from autopilot_state.json if present)",
    )
    parser.add_argument("--phase1", type=float, default=90.0, help="seconds per K in Phase 1")
    parser.add_argument("--phase2", type=float, default=90.0, help="total seconds for Phase 2")
    parser.add_argument("--k", type=int, nargs="+", default=[2, 3], help="per-gap swap caps to try")
    args = parser.parse_args()

    if not ORTOOLS_AVAILABLE:
        print("ortools is not importable in this environment -- nothing to validate.")
        raise SystemExit(0)

    if args.toy or not args.benchmark:
        _toy_validation()
    if args.benchmark:
        _run_benchmark_cli(args.benchmark, args.phase1, args.phase2, tuple(args.k))
