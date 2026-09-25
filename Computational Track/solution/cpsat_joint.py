"""Joint SWAP-count and official-depth CP-SAT model.

This file does not replace `cpsat_solver.py`. The gap model there is a complete
encoding of minimum SWAP *count* (a SWAP that does not help the current front
gate can be delayed without raising the count). That delay can raise official
depth, so a joint optimum is a different search. This module minimizes
`2 * swaps + depth`, i.e. the official score `swaps + 0.5 * depth`.

Horizon
-------
`depth` is the ASAP layer of the last gate under `schedule_layers_ordered`.
Any solution with score strictly better than an incumbent C has
`2 * swaps + depth <= 2*C - 1`, hence `depth <= 2*C - 1`. A solve with
`T >= 2*C - 1` and no extra swap cap is a complete proof for "nothing beats C".
A smaller T, or a swap cap, is an incomplete search unless a separate proof
shows every improving solution sits inside that band.

Ladder is the special case. SWAP count >= 3 is already proven, and the
program critical path is 6, so depth >= 6. The only point that beats 6.5 is
exactly 3 SWAPs and depth 6 (`2*3+6 = 12`). `T = 6` and `swaps == 3` is a
complete proof search for that one point, not a complete model of every
routing.

Depth encoding
--------------
`schedule_layers_ordered` assigns a layer from physical-qubit last-use, so
disjoint gates in program order can share a layer. A model that fires at most
one gate per layer cannot represent the known depth-7 ladder route (16 gates).
Here several gates may share a layer. They stay in program order, they are
pairwise disjoint from each other and from that layer's SWAPs, and every op
at layer t > 1 touches at least one physical qubit that was used at layer t-1.
That is the ASAP rule. The predecessor has to be orderable before the op: a
SWAP, or an earlier program gate. A later gate on fresh wires may still sit
on an earlier layer, but it cannot be what pushes an earlier gate forward.
Gates are emitted before the SWAPs of the same layer, because a 2Q does not
move tokens and must see the pre-SWAP placement.
Every reported number is re-checked with `starter_kit.scorer.score_summary`.
If official depth disagrees with the model, the candidate is rejected.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from ortools.sat.python import cp_model

    ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    cp_model = None  # type: ignore[assignment]
    ORTOOLS_AVAILABLE = False

from .solve import Hardware, Program

# Improving ladder solutions are exactly this point (see module docstring).
_LADDER_T = 6
_LADDER_SWAPS = 3


def _edges(hw: Hardware) -> list[tuple[int, int]]:
    edges = []
    for u in range(hw.n):
        for v in hw.adj[u]:
            if u < v:
                edges.append((u, v))
    return edges


def _build_joint_model(
    prog: Program,
    hw: Hardware,
    T: int,
    min_swaps: int | None = None,
    max_swaps: int | None = None,
) -> dict[str, Any]:
    """Time-indexed joint model. Layers are 1..T. Snapshot 0 is the initial placement."""
    if not ORTOOLS_AVAILABLE:
        raise RuntimeError("ortools is not importable")
    model = cp_model.CpModel()
    N = hw.n
    L = prog.L
    G = len(prog.gates)
    edges = _edges(hw)
    E = len(edges)
    incident: list[list[int]] = [[] for _ in range(N)]
    for idx, (u, v) in enumerate(edges):
        incident[u].append(idx)
        incident[v].append(idx)
    allowed = [(u, v) for u, v in edges] + [(v, u) for u, v in edges]

    pos = [[model.NewIntVar(0, N - 1, f"pos_{t}_{q}") for q in range(N)] for t in range(T + 1)]
    occ = [[model.NewIntVar(0, N - 1, f"occ_{t}_{p}") for p in range(N)] for t in range(T + 1)]
    for t in range(T + 1):
        model.AddInverse(pos[t], occ[t])
    for q in range(L, max(L, N - 1)):
        model.Add(pos[0][q] < pos[0][q + 1])

    # at[t][q][p] only for real logicals; dummies are carried by the permutation.
    at: list[list[list[Any]]] = [[[] for _ in range(L)] for _ in range(T + 1)]
    for t in range(T + 1):
        for q in range(L):
            row = []
            for p in range(N):
                b = model.NewBoolVar(f"at_{t}_{q}_{p}")
                row.append(b)
                model.Add(pos[t][q] == p).OnlyEnforceIf(b)
                model.Add(pos[t][q] != p).OnlyEnforceIf(b.Not())
            model.Add(sum(row) == 1)
            at[t][q] = row

    swap: list[list[Any]] = [[model.NewBoolVar(f"swap_{t}_{e}") for e in range(E)] for t in range(T + 1)]
    fire: list[list[Any]] = [[model.NewBoolVar(f"fire_{t}_{j}") for j in range(G)] for t in range(T + 1)]
    # t = 0 is not a layer.
    for e in range(E):
        model.Add(swap[0][e] == 0)
    for j in range(G):
        model.Add(fire[0][j] == 0)
        model.Add(sum(fire[t][j] for t in range(1, T + 1)) == 1)

    layers = [model.NewIntVar(1, T, f"layer_{j}") for j in range(G)]
    for j in range(G):
        model.Add(layers[j] == sum(t * fire[t][j] for t in range(1, T + 1)))
    # How many program gates precede each SWAP. Used to keep predecessors
    # causally before the op that reads them.
    gap = [[model.NewIntVar(0, G, f"gap_{t}_{e}") for e in range(E)] for t in range(T + 1)]
    used_gap = [[model.NewIntVar(0, G, f"ug_{t}_{p}") for p in range(N)] for t in range(T + 1)]
    # Layers need not increase along the program. A later gate on fresh
    # physical qubits is officially layer 1 even if earlier gates already
    # reached a higher layer. Depth is the maximum layer, not the last gate.
    depth = model.NewIntVar(0 if G == 0 else 1, max(T, 1), "depth")
    if G == 0:
        model.Add(depth == 0)
    else:
        model.AddMaxEquality(depth, layers)
        crit = prog.critical_path()
        if crit > 0:
            model.Add(depth >= min(crit, T))

    swap_prev: list[Any] | None = None
    gate_prev: list[Any] | None = None
    touch_hits: list[list[list[Any]]] = [[[] for _ in range(N)] for _ in range(G)]
    for t in range(1, T + 1):
        for p in range(N):
            model.Add(sum(swap[t][e] for e in incident[p]) <= 1)
        for j, (a, b) in enumerate(prog.gates):
            model.AddAllowedAssignments([pos[t - 1][a], pos[t - 1][b]], allowed).OnlyEnforceIf(fire[t][j])

        swap_touch = []
        gate_on = []
        for p in range(N):
            st = model.NewBoolVar(f"st_{t}_{p}")
            model.Add(st == sum(swap[t][e] for e in incident[p]))
            swap_touch.append(st)
            go = model.NewBoolVar(f"go_{t}_{p}")
            gate_on.append(go)
        for p in range(N):
            hits = []
            for j, (a, b) in enumerate(prog.gates):
                for q in (a, b):
                    both = model.NewBoolVar(f"hit_{t}_{j}_{q}_{p}")
                    model.AddMultiplicationEquality(both, [fire[t][j], at[t - 1][q][p]])
                    model.Add(used_gap[t][p] == j).OnlyEnforceIf(both)
                    touch_hits[j][p].append(both)
                    hits.append(both)
            model.Add(gate_on[p] == sum(hits))
        for p in range(N):
            model.Add(swap_touch[p] + gate_on[p] <= 1)

        for idx, (u, v) in enumerate(edges):
            b = swap[t][idx]
            model.Add(occ[t][u] == occ[t - 1][v]).OnlyEnforceIf(b)
            model.Add(occ[t][v] == occ[t - 1][u]).OnlyEnforceIf(b)
            model.Add(used_gap[t][u] == gap[t][idx]).OnlyEnforceIf(b)
            model.Add(used_gap[t][v] == gap[t][idx]).OnlyEnforceIf(b)
        for p in range(N):
            model.Add(occ[t][p] == occ[t - 1][p]).OnlyEnforceIf(swap_touch[p].Not())

        if t > 1:
            if swap_prev is None or gate_prev is None:
                raise RuntimeError("missing previous-layer use")
            # Predecessor touch must be a SWAP inserted at or before this op,
            # or an earlier gate. Later gates do not count: program order cannot
            # emit them first, so they cannot raise this op's ASAP layer.
            for j, (a, bq) in enumerate(prog.gates):
                lits = [fire[t][j].Not()]
                for q in (a, bq):
                    for p in range(N):
                        at_p = at[t - 1][q][p]
                        sok = model.NewBoolVar(f"ps_{t}_{j}_{q}_{p}")
                        model.AddImplication(sok, at_p)
                        model.AddImplication(sok, swap_prev[p])
                        model.Add(used_gap[t - 1][p] <= j).OnlyEnforceIf(sok)
                        lits.append(sok)
                        gok = model.NewBoolVar(f"pg_{t}_{j}_{q}_{p}")
                        model.AddImplication(gok, at_p)
                        model.AddImplication(gok, gate_prev[p])
                        model.Add(used_gap[t - 1][p] <= j - 1).OnlyEnforceIf(gok)
                        lits.append(gok)
                model.AddBoolOr(lits)
            for idx, (u, v) in enumerate(edges):
                lits = [swap[t][idx].Not()]
                for p in (u, v):
                    sok = model.NewBoolVar(f"ss_{t}_{idx}_{p}")
                    model.AddImplication(sok, swap_prev[p])
                    model.Add(used_gap[t - 1][p] <= gap[t][idx]).OnlyEnforceIf(sok)
                    lits.append(sok)
                    gok = model.NewBoolVar(f"sg_{t}_{idx}_{p}")
                    model.AddImplication(gok, gate_prev[p])
                    model.Add(used_gap[t - 1][p] + 1 <= gap[t][idx]).OnlyEnforceIf(gok)
                    lits.append(gok)
                model.AddBoolOr(lits)

        activity = sum(fire[t]) + sum(swap[t])
        at_or_before = model.NewBoolVar(f"depth_ge_{t}")
        model.Add(depth >= t).OnlyEnforceIf(at_or_before)
        model.Add(depth <= t - 1).OnlyEnforceIf(at_or_before.Not())
        model.Add(activity >= 1).OnlyEnforceIf(at_or_before)
        model.Add(sum(swap[t]) == 0).OnlyEnforceIf(at_or_before.Not())
        swap_prev = swap_touch
        gate_prev = gate_on

    # Ops that share a physical qubit must increase in layer along program order.
    # A later gate on a fresh wire may still be layer 1. A later gate on a wire
    # an earlier gate or an earlier SWAP already used may not.
    if G:
        touch = [[model.NewBoolVar(f"tch_{j}_{p}") for p in range(N)] for j in range(G)]
        for j in range(G):
            for p in range(N):
                hits_jp = touch_hits[j][p]
                model.Add(touch[j][p] == (sum(hits_jp) if hits_jp else 0))
        for i in range(G):
            for j in range(i + 1, G):
                for p in range(N):
                    model.Add(layers[i] < layers[j]).OnlyEnforceIf([touch[i][p], touch[j][p]])
        for t in range(1, T + 1):
            for ei, (u, v) in enumerate(edges):
                for j in range(G):
                    before = model.NewBoolVar(f"bf_{t}_{ei}_{j}")
                    model.Add(gap[t][ei] <= j).OnlyEnforceIf(before)
                    model.Add(gap[t][ei] >= j + 1).OnlyEnforceIf(before.Not())
                    for p in (u, v):
                        share = [swap[t][ei], touch[j][p]]
                        model.Add(layers[j] >= t + 1).OnlyEnforceIf([*share, before])
                        model.Add(layers[j] <= t - 1).OnlyEnforceIf([*share, before.Not()])

    total_swaps = sum(swap[t][e] for t in range(1, T + 1) for e in range(E))
    if min_swaps is not None:
        model.Add(total_swaps >= min_swaps)
    if max_swaps is not None:
        model.Add(total_swaps <= max_swaps)
    obj = model.NewIntVar(0, 2 * E * T + T, "obj")
    model.Add(obj == 2 * total_swaps + depth)
    model.Minimize(obj)
    return {
        "model": model,
        "pos": pos,
        "occ": occ,
        "at": at,
        "swap": swap,
        "fire": fire,
        "layers": layers,
        "depth": depth,
        "obj": obj,
        "edges": edges,
        "gap": gap,
        "T": T,
        "N": N,
        "L": L,
        "G": G,
    }


class _IndexedView:
    def __init__(self, mapping: dict[int, int]) -> None:
        self._mapping = mapping

    def Value(self, var: Any) -> int:
        return self._mapping[var.Index()]


def _capture_assignment(reader: Any, built: dict[str, Any]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    T, N, G = built["T"], built["N"], built["G"]
    for t in range(T + 1):
        for q in range(N):
            var = built["pos"][t][q]
            mapping[var.Index()] = int(reader.Value(var))
        if t == 0:
            continue
        for j in range(G):
            var = built["fire"][t][j]
            mapping[var.Index()] = int(reader.Value(var))
        for e in range(len(built["edges"])):
            var = built["swap"][t][e]
            mapping[var.Index()] = int(reader.Value(var))
    for j in range(G):
        var = built["layers"][j]
        mapping[var.Index()] = int(reader.Value(var))
    depth = built["depth"]
    mapping[depth.Index()] = int(reader.Value(depth))
    return mapping


def _schedule_view(view: Any, built: dict[str, Any], prog: Program, hw: Hardware) -> dict[str, Any]:
    """Placement, gate layers, and SWAP layers for one captured incumbent."""
    labels = hw.labels
    depth = int(view.Value(built["depth"]))
    placement = {
        prog.logicals[q]: labels[int(view.Value(built["pos"][0][q]))] for q in range(built["L"])
    }
    gate_layers = [int(view.Value(built["layers"][j])) for j in range(built["G"])]
    swaps = []
    for t in range(1, depth + 1):
        for e, (u, v) in enumerate(built["edges"]):
            if int(view.Value(built["swap"][t][e])):
                swaps.append({"layer": t, "qubits": [labels[u], labels[v]]})
    return {"placement": placement, "depth": depth, "gate_layers": gate_layers, "swaps": swaps}


def _checkpoint_incumbent(
    view: Any,
    built: dict[str, Any],
    prog: Program,
    hw: Hardware,
    program: list[tuple],
    graph: Any,
    *,
    benchmark: str,
    objective: int,
    solver_time: float,
    dual_bound: float | None,
    workers: int,
) -> None:
    """Emit and officially score one improving incumbent. Never raises."""
    t0 = time.perf_counter()
    schedule = None
    placement = None
    routed = None
    official = None
    accepted = False
    reject_reason = None
    try:
        schedule = _schedule_view(view, built, prog, hw)
        placement, routed, swaps, depth = _emit(view, built, prog, hw)
        official = _score(program, graph, placement, routed)
        accepted = bool(
            official["valid"]
            and official["swap_count"] == swaps
            and official["depth"] == depth
            and abs(official["score"] - objective / 2) < 1e-6
        )
        if not accepted:
            reject_reason = (
                f"score mismatch model {swaps}+0.5*{depth} vs official "
                f"{official['swap_count']}+0.5*{official['depth']}"
            )
    except Exception as exc:
        reject_reason = str(exc)
        conflict = getattr(exc, "conflict", None)
        if schedule is not None and conflict is not None:
            schedule = {**schedule, "conflict": conflict}
    row: dict[str, Any] = {
        "record": "incumbent_snapshot",
        "final": False,
        "benchmark": benchmark,
        "solver_time": solver_time,
        "checkpoint_ms": (time.perf_counter() - t0) * 1000.0,
        "objective": objective,
        "model_score": objective / 2,
        "dual_bound": dual_bound,
        "dual_score": None if dual_bound is None else dual_bound / 2,
        "workers": workers,
        "accepted": accepted,
        "official": None
        if official is None
        else {k: official[k] for k in ("valid", "message", "swap_count", "depth", "score")},
        "placement": placement if placement is not None else (None if schedule is None else schedule["placement"]),
        "routed": [list(op) for op in routed] if routed is not None else None,
        "diagnostic": schedule,
        "reject_reason": reject_reason,
    }
    try:
        _append_result(row)
    except Exception as exc:
        print(f"checkpoint write failed: {exc}", flush=True)


class _Heartbeat(cp_model.CpSolverSolutionCallback if ORTOOLS_AVAILABLE else object):
    def __init__(
        self,
        built: dict[str, Any],
        prog: Program,
        hw: Hardware,
        program: list[tuple],
        graph: Any,
        benchmark: str,
        workers: int,
    ) -> None:
        super().__init__()
        self.built = built
        self.prog = prog
        self.hw = hw
        self.program = program
        self.graph = graph
        self.benchmark = benchmark
        self.workers = workers
        self.best_obj: float | None = None
        self.snapshots: list[tuple[int, dict[int, int]]] = []

    def on_solution_callback(self) -> None:
        try:
            obj = self.ObjectiveValue()
            if self.best_obj is not None and obj >= self.best_obj - 1e-6:
                return
            self.best_obj = obj
            objective = int(round(obj))
            solver_time = float(self.WallTime())
            try:
                bound = self.BestObjectiveBound()
                if bound is None or abs(bound) > 1e15:
                    bound = None
            except Exception:
                bound = None
            print(
                f"heartbeat incumbent obj={objective} score={objective / 2:.1f} bound={bound} "
                f"t={solver_time:.1f}s",
                flush=True,
            )
            mapping = _capture_assignment(self, self.built)
            self.snapshots.append((objective, mapping))
            _checkpoint_incumbent(
                _IndexedView(mapping),
                self.built,
                self.prog,
                self.hw,
                self.program,
                self.graph,
                benchmark=self.benchmark,
                objective=objective,
                solver_time=solver_time,
                dual_bound=None if bound is None else float(bound),
                workers=self.workers,
            )
        except Exception as exc:
            print(f"checkpoint callback failed: {exc}", flush=True)


def _emit(solver: "cp_model.CpSolver", built: dict[str, Any], prog: Program, hw: Hardware) -> tuple[dict, list[tuple], int, int]:
    """Emit gates in program order. Insert a SWAP when doing so now gives it its model layer.

    Bucket-by-layer emission reorders the program whenever a later gate is officially
    on an earlier layer, which the scorer rejects.
    """
    N = built["N"]
    L = built["L"]
    labels = hw.labels
    pos0 = [solver.Value(built["pos"][0][q]) for q in range(N)]
    placement = {prog.logicals[q]: labels[pos0[q]] for q in range(L)}
    depth = int(solver.Value(built["depth"]))
    try:
        return _emit_by_layer(solver, built, prog, hw, placement, depth)
    except RuntimeError:
        return _emit_in_program_order(solver, built, prog, hw, placement, depth)


def _emit_by_layer(solver, built, prog, hw, placement, depth):
    labels = hw.labels
    routed: list[tuple] = []
    swaps = 0
    gate_i = 0
    for t in range(1, depth + 1):
        fired = [j for j in range(built["G"]) if solver.Value(built["fire"][t][j])]
        for j in fired:
            if gate_i != j:
                raise RuntimeError("layer order would reorder gates")
            a, b = prog.gates[j]
            pa = int(solver.Value(built["pos"][t - 1][a]))
            pb = int(solver.Value(built["pos"][t - 1][b]))
            routed.append(("2Q", labels[pa], labels[pb]))
            gate_i += 1
        for e, (u, v) in enumerate(built["edges"]):
            if solver.Value(built["swap"][t][e]):
                routed.append(("SWAP", labels[u], labels[v]))
                swaps += 1
    if gate_i != built["G"]:
        raise RuntimeError("layer order missed a gate")
    return placement, routed, swaps, depth


def _emit_in_program_order(solver, built, prog, hw, placement, depth):
    """Replay model layers without reordering gates.

    A SWAP is emitted only when its ASAP layer equals the model layer. Pending
    SWAPs stay in layer order, so an earlier SWAP is emitted before a later one
    and before any later gate. A gate is emitted before a SWAP of the same
    layer, matching the pre-SWAP placement the model reads. Emitting a later
    ready SWAP (or a later ready gate) first can touch a shared physical qubit
    and push an earlier SWAP past the only layer the model allowed for it.
    """
    labels = hw.labels
    gate_layer = [int(solver.Value(built["layers"][j])) for j in range(built["G"])]
    pending: list[tuple[int, int, int]] = []
    for t in range(1, depth + 1):
        for e, (u, v) in enumerate(built["edges"]):
            if solver.Value(built["swap"][t][e]):
                pending.append((t, u, v))

    def asap(wires: tuple[int, ...], last: dict[int, int]) -> int:
        return 1 + max((last.get(w, 0) for w in wires), default=0)

    last: dict[int, int] = {}
    routed: list[tuple] = []
    next_gate = 0
    guard = 0
    limit = (built["G"] + len(pending) + 2) ** 2
    while next_gate < built["G"] or pending:
        guard += 1
        if guard > limit:
            raise RuntimeError("emit could not realize model layers in program order")

        gate_layer_now = None
        gate_asap = None
        pa = pb = None
        gate_ready = False
        if next_gate < built["G"]:
            gate_layer_now = gate_layer[next_gate]
            a, b = prog.gates[next_gate]
            pa = int(solver.Value(built["pos"][gate_layer_now - 1][a]))
            pb = int(solver.Value(built["pos"][gate_layer_now - 1][b]))
            gate_asap = asap((labels[pa], labels[pb]), last)
            gate_ready = gate_asap == gate_layer_now

        min_swap_layer = pending[0][0] if pending else None
        if gate_ready and (min_swap_layer is None or gate_layer_now <= min_swap_layer):
            routed.append(("2Q", labels[pa], labels[pb]))
            last[labels[pa]] = last[labels[pb]] = gate_layer_now
            next_gate += 1
            continue

        placed = False
        # A later SWAP must not jump ahead of the next gate. Its layer is only
        # legal after that gate's wires have already reached the previous layer.
        if min_swap_layer is not None and (gate_layer_now is None or min_swap_layer <= gate_layer_now):
            for i, (t, u, v) in enumerate(pending):
                if t != min_swap_layer:
                    break
                if asap((labels[u], labels[v]), last) == t:
                    routed.append(("SWAP", labels[u], labels[v]))
                    last[labels[u]] = last[labels[v]] = t
                    pending.pop(i)
                    placed = True
                    break
        if placed:
            continue
        exc = RuntimeError(
            "emit could not realize model layers in program order"
            f" (gate={next_gate} layer={gate_layer_now} asap={gate_asap}"
            f" earliest_swap={min_swap_layer} pending={len(pending)})"
        )
        exc.conflict = {
            "gate": next_gate,
            "model_layer": gate_layer_now,
            "asap": gate_asap,
            "physical": None if pa is None else [labels[pa], labels[pb]],
            "last": dict(last),
            "emitted": [list(op) for op in routed],
            "pending_swaps": [[t, labels[u], labels[v]] for t, u, v in pending],
            "gate_layers": gate_layer,
        }
        raise exc
    return placement, routed, len(routed) - built["G"], depth


def _score(program, graph, placement, routed) -> dict:
    from starter_kit.scorer import score_summary

    return score_summary(program, graph, placement, routed)


def _replay_hint(prog: Program, hw: Hardware, placement: dict, routed: list[tuple]) -> dict[str, Any] | None:
    """Replay an official routing into per-layer fires and SWAPs.

    Returns None if the route is not a layered matching the model can represent
    (gates in a layer must all see the placement from the start of that layer).
    """
    from starter_kit.scorer import schedule_layers_ordered

    N = hw.n
    L = prog.L
    labels = hw.labels
    index = hw.index
    l2p = [index[placement[prog.logicals[q]]] for q in range(L)]
    used = set(l2p)
    for p in range(N):
        if p not in used:
            l2p.append(p)
    if any(l2p[q] >= l2p[q + 1] for q in range(L, N - 1)):
        print("replay: dummy initial order is not sorted", flush=True)
        return None
    occ = [-1] * N
    for q, p in enumerate(l2p):
        occ[p] = q

    scheduled = schedule_layers_ordered(routed)
    depth = len(scheduled)
    last: dict[int, int] = {}
    annotated: list[tuple[int, tuple]] = []
    for op in routed:
        if op[0] == "1Q":
            continue
        layer = 1 + max((last.get(w, 0) for w in op[1:]), default=0)
        for w in op[1:]:
            last[w] = layer
        if not 1 <= layer <= depth:
            return None
        annotated.append((layer, op))

    edges = _edges(hw)
    edge_index = {}
    for ei, (u, v) in enumerate(edges):
        edge_index[(u, v)] = ei
        edge_index[(v, u)] = ei
    # Swaps in one layer are a matching, so they commute. Apply them in layer order
    # to get the placement each later layer reads.
    pos_state = l2p[:]
    occ_state = occ[:]
    snapshots = [pos_state[:]]
    swaps: dict[tuple[int, int], int] = {}
    for t in range(1, depth + 1):
        for layer, op in annotated:
            if layer != t or op[0] != "SWAP":
                continue
            u, v = index[op[1]], index[op[2]]
            ei = edge_index.get((u, v))
            if ei is None or swaps.get((t, ei), 0):
                return None
            swaps[(t, ei)] = 1
            lu, lv = occ_state[u], occ_state[v]
            occ_state[u], occ_state[v] = lv, lu
            if lu >= 0:
                pos_state[lu] = v
            if lv >= 0:
                pos_state[lv] = u
        snapshots.append(pos_state[:])

    fires: dict[tuple[int, int], int] = {}
    gate_i = 0
    for layer, op in annotated:
        if op[0] != "2Q":
            continue
        if gate_i >= len(prog.gates):
            return None
        ga, gb = prog.gates[gate_i]
        pu, pv = index[op[1]], index[op[2]]
        if {snapshots[layer - 1][ga], snapshots[layer - 1][gb]} != {pu, pv}:
            print("replay: gate does not see the pre-swap placement of its layer", op, flush=True)
            return None
        fires[(layer, gate_i)] = 1
        gate_i += 1
    if gate_i != len(prog.gates):
        return None
    swap_gaps: dict[tuple[int, int], int] = {}
    seen_gates = 0
    for layer, op in annotated:
        if op[0] == "SWAP":
            u, v = index[op[1]], index[op[2]]
            ei = edge_index.get((u, v))
            if ei is not None:
                swap_gaps[(layer, ei)] = seen_gates
        elif op[0] == "2Q":
            seen_gates += 1
    return {
        "depth": depth,
        "snapshots": snapshots,
        "fires": fires,
        "swaps": swaps,
        "swaps_count": sum(swaps.values()),
        "swap_gaps": swap_gaps,
    }


def _apply_hint(built: dict[str, Any], prog: Program, hw: Hardware, replay: dict[str, Any]) -> bool:
    T = built["T"]
    if replay["depth"] > T:
        return False
    if replay["swaps_count"] > built["T"] * len(built["edges"]):
        return False
    model = built["model"]
    N = built["N"]
    L = built["L"]
    depth = replay["depth"]
    snaps = replay["snapshots"]
    # Hold the final placement through unused horizon layers.
    while len(snaps) < T + 1:
        snaps.append(snaps[-1][:])
    try:
        for t in range(T + 1):
            occ = [-1] * N
            for q in range(N):
                occ[snaps[t][q]] = q
            for q in range(N):
                model.AddHint(built["pos"][t][q], snaps[t][q])
            for p in range(N):
                model.AddHint(built["occ"][t][p], occ[p])
            for q in range(L):
                for p in range(N):
                    model.AddHint(built["at"][t][q][p], 1 if snaps[t][q] == p else 0)
        for t in range(1, T + 1):
            for e in range(len(built["edges"])):
                model.AddHint(built["swap"][t][e], 1 if replay["swaps"].get((t, e), 0) else 0)
                model.AddHint(built["gap"][t][e], replay["swap_gaps"].get((t, e), 0))
            for j in range(built["G"]):
                model.AddHint(built["fire"][t][j], 1 if replay["fires"].get((t, j), 0) else 0)
        model.AddHint(built["depth"], depth)
    except Exception:
        return False
    return True


def solve_joint(
    program: list[tuple],
    hardware_graph,
    T: int,
    time_limit_s: float,
    min_swaps: int | None = None,
    max_swaps: int | None = None,
    warm_start: tuple[dict, list[tuple]] | None = None,
    num_workers: int | None = None,
    complete: bool = False,
    note: str = "",
    benchmark: str = "",
) -> dict[str, Any]:
    """Minimize 2*swaps+depth. Never raises if ortools is missing."""
    if not ORTOOLS_AVAILABLE:
        return {"available": False, "reason": "ortools not importable"}
    prog = Program(program)
    hw = Hardware(hardware_graph)
    if prog.L > hw.n:
        return {"available": True, "status": "INFEASIBLE", "reason": "more logical qubits than physical"}
    built = _build_joint_model(prog, hw, T, min_swaps, max_swaps)
    hinted = False
    if warm_start is not None and prog.gates:
        replay = _replay_hint(prog, hw, warm_start[0], warm_start[1])
        if replay is not None:
            hinted = _apply_hint(built, prog, hw, replay)
    workers = num_workers or 8
    cb = _Heartbeat(built, prog, hw, program, hardware_graph, benchmark, workers)
    t0 = time.perf_counter()
    deadline = t0 + time_limit_s
    status = cp_model.UNKNOWN
    solver = cp_model.CpSolver()
    name = "UNKNOWN"
    bound = None
    accepted_pack = None
    reject_reason = None
    for _attempt in range(40):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = workers
        solver.parameters.log_search_progress = False
        cb.best_obj = None
        cb.snapshots.clear()
        status = solver.Solve(built["model"], cb)
        name = solver.StatusName(status)
        try:
            bound = solver.BestObjectiveBound()
            if bound is None or abs(bound) > 1e15:
                bound = None
        except Exception:
            bound = None
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        views: list[tuple[int, Any]] = [(_obj, _IndexedView(_mapping)) for _obj, _mapping in cb.snapshots]
        if not views:
            views = [(int(round(solver.ObjectiveValue())), solver)]
        incumbent_obj = int(round(solver.ObjectiveValue()))
        chosen = None
        for obj, view in reversed(views):
            try:
                placement, routed, swaps, depth = _emit(view, built, prog, hw)
            except RuntimeError as exc:
                reject_reason = str(exc)
                continue
            official = _score(program, hardware_graph, placement, routed)
            if (
                official["valid"]
                and official["swap_count"] == swaps
                and official["depth"] == depth
                and abs(official["score"] - obj / 2) < 1e-6
            ):
                chosen = (obj, placement, routed, official)
                break
            reject_reason = (
                f"score mismatch model {swaps}+0.5*{depth} vs official "
                f"{official['swap_count']}+0.5*{official['depth']}"
            )
        if chosen is not None and (accepted_pack is None or chosen[0] < accepted_pack[0]):
            accepted_pack = chosen
        if chosen is not None and chosen[0] == incumbent_obj:
            break
        _forbid_fire_swap(built, solver)
    wall = time.perf_counter() - t0
    out: dict[str, Any] = {
        "available": True,
        "status": name,
        "wall_time": wall,
        "T": T,
        "min_swaps": min_swaps,
        "max_swaps": max_swaps,
        "workers": workers,
        "hinted": hinted,
        "complete_search": complete,
        "note": note,
        "objective": None,
        "model_score": None,
        "dual_bound": bound,
        "dual_score": None if bound is None else bound / 2,
        "official": None,
        "placement": None,
        "routed": None,
        "accepted": False,
    }
    if accepted_pack is None:
        out["reject_reason"] = reject_reason
        return out
    obj, placement, routed, official = accepted_pack
    out["objective"] = obj
    out["model_score"] = obj / 2
    out["official"] = {k: official[k] for k in ("valid", "message", "swap_count", "depth", "score")}
    out["placement"] = placement
    out["routed"] = routed
    out["accepted"] = True
    return out


def _forbid_fire_swap(built: dict[str, Any], solver: "cp_model.CpSolver") -> None:
    """Cut off one fire/swap pattern that could not be emitted as a valid route."""
    ones = []
    for t in range(1, built["T"] + 1):
        for j in range(built["G"]):
            if solver.Value(built["fire"][t][j]):
                ones.append(built["fire"][t][j])
        for e in range(len(built["edges"])):
            if solver.Value(built["swap"][t][e]):
                ones.append(built["swap"][t][e])
    if ones:
        built["model"].Add(sum(ones) <= len(ones) - 1)


def _fix_and_check_hint(program, graph, T: int, warm: tuple[dict, list[tuple]]) -> bool:
    """Return True if the incumbent is a feasible point of the model at this T."""
    prog = Program(program)
    hw = Hardware(graph)
    replay = _replay_hint(prog, hw, warm[0], warm[1])
    if replay is None or replay["depth"] > T:
        print(f"hint replay failed or depth {None if replay is None else replay['depth']} > T={T}", flush=True)
        return False
    built = _build_joint_model(prog, hw, T, min_swaps=None, max_swaps=None)
    if not _apply_hint(built, prog, hw, replay):
        print("AddHint failed", flush=True)
        return False
    # Pin the decision variables so a bad encoding is INFEASIBLE rather than repaired.
    N, L, G = built["N"], built["L"], built["G"]
    snaps = replay["snapshots"]
    while len(snaps) < T + 1:
        snaps.append(snaps[-1][:])
    model = built["model"]
    for t in range(T + 1):
        for q in range(N):
            model.Add(built["pos"][t][q] == snaps[t][q])
    for t in range(1, T + 1):
        for e in range(len(built["edges"])):
            model.Add(built["swap"][t][e] == (1 if replay["swaps"].get((t, e), 0) else 0))
        for j in range(G):
            model.Add(built["fire"][t][j] == (1 if replay["fires"].get((t, j), 0) else 0))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    ok = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    print(f"hint acceptance T={T}: {solver.StatusName(status)}", flush=True)
    return ok


def _toy_validation() -> None:
    import networkx as nx

    print("=== joint toy validation ===", flush=True)
    # Four 2Q ops on a path of 4. The repeated edge still embeds with 0 SWAPs.
    program = [("2Q", 0, 1), ("2Q", 1, 2), ("2Q", 2, 3), ("2Q", 1, 2)]
    graph = nx.path_graph(4)
    crit = Program(program).critical_path()
    r = solve_joint(program, graph, T=crit, time_limit_s=20, complete=True, note="toy chain")
    print("toy chain", r["status"], r["official"], "model", r["model_score"], flush=True)
    assert r["accepted"], r.get("reject_reason")
    assert r["official"]["swap_count"] == 0
    assert r["official"]["depth"] == crit
    assert r["status"] == "OPTIMAL"

    program3 = [("2Q", 0, 1), ("2Q", 0, 2), ("2Q", 0, 3)]
    graph3 = nx.path_graph(4)
    r3 = solve_joint(program3, graph3, T=6, time_limit_s=20, complete=True, note="toy star")
    print("toy star", r3["status"], r3["official"], "model", r3["model_score"], flush=True)
    assert r3["accepted"], r3.get("reject_reason")
    assert r3["official"]["swap_count"] >= 1
    assert abs(r3["official"]["score"] - r3["model_score"]) < 1e-6

    # More SWAPs beating minimum SWAP count: a path long enough that one SWAP
    # leaves a deep serial tail, while two SWAPs (an early idle move plus the
    # necessary one) collapse depth. Searched explicitly below; if this
    # instance does not separate the objectives we say so.
    _toy_swap_vs_score()
    print("All joint toy checks passed.", flush=True)


def _toy_swap_vs_score() -> None:
    """Search a few tiny instances for score < min-swap score. Do not fake one."""
    import networkx as nx

    from .cpsat_solver import find_min_swaps

    candidates = [
        ([("2Q", 0, 1), ("2Q", 2, 3), ("2Q", 0, 2), ("2Q", 1, 3), ("2Q", 0, 3)], nx.path_graph(4)),
        ([("2Q", 0, 1), ("2Q", 2, 3), ("2Q", 1, 2), ("2Q", 0, 3), ("2Q", 0, 2), ("2Q", 1, 3)], nx.path_graph(6)),
        ([("2Q", 0, 2), ("2Q", 1, 3), ("2Q", 0, 1), ("2Q", 2, 3), ("2Q", 0, 3)], nx.path_graph(5)),
    ]
    for program, graph in candidates:
        gap = find_min_swaps(program, graph, k_values=(3,), time_limit_per_k=8.0, num_workers=4)
        joint = solve_joint(program, graph, T=8, time_limit_s=15, num_workers=4, note="toy separation")
        if not joint.get("accepted") or gap.get("best_swaps") is None:
            continue
        # Score the gap witness too.
        from .cpsat_solver import _build_and_score

        prog, hw = Program(program), Hardware(graph)
        scored = _build_and_score(program, graph, prog, hw, gap["best_l2t"], gap["best_moves"])
        if scored is None:
            continue
        gap_score = scored[2]["score"]
        joint_score = joint["official"]["score"]
        print(
            f"separation try gap_swaps={gap['best_swaps']} gap_score={gap_score} "
            f"joint_score={joint_score} joint_swaps={joint['official']['swap_count']}",
            flush=True,
        )
        if joint["official"]["swap_count"] > gap["best_swaps"] and joint_score < gap_score - 1e-6:
            print("toy separation: joint model used extra SWAPs to cut the official score", flush=True)
            return
    print(
        "toy separation: no tiny instance in the hand list had a strictly better score "
        "with more SWAPs than the gap model's minimum. Not faking one.",
        flush=True,
    )


_RESULT_LOCK = threading.Lock()


def _append_result(row: dict) -> None:
    path = Path(__file__).resolve().parent / "cpsat_joint_results.jsonl"
    slim = {k: v for k, v in row.items() if k not in ("placement", "routed")}
    if row.get("placement") is not None and (row.get("accepted") or row.get("record") == "incumbent_snapshot"):
        slim["placement"] = row["placement"]
    if row.get("routed") is not None and (row.get("accepted") or row.get("record") == "incumbent_snapshot"):
        slim["routed"] = [list(op) for op in row["routed"]]
    line = json.dumps(slim)
    with _RESULT_LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()


def _load_warm() -> dict[str, tuple[dict, list[tuple]]]:
    path = Path(__file__).resolve().parent / "autopilot_state.json"
    if not path.exists():
        return {}
    state = json.loads(path.read_text())
    out = {}
    for name, row in state.get("rows", {}).items():
        placement = {int(k): v for k, v in row["placement"].items()}
        routed = [tuple(op) for op in row["routed"]]
        out[name] = (placement, routed)
    return out


_PRESETS: dict[str, dict[str, Any]] = {
    "ladder_trotter": {
        "T": _LADDER_T,
        "min_swaps": _LADDER_SWAPS,
        "max_swaps": _LADDER_SWAPS,
        "time": 600.0,
        "complete": True,
        "note": (
            "Complete for beating 6.5. Proven swaps>=3 and critical path 6 imply the only "
            "improving solution is 3 SWAPs and depth 6. INFEASIBLE or OPTIMAL with "
            "model score>=6.5 proves the incumbent. A feasible accepted score of 6.0 improves it."
        ),
    },
    "qaoa_random": {
        "T": 22,
        "min_swaps": None,
        "max_swaps": None,
        "time": 3600.0,
        "complete": True,
        "note": "T=22 equals 2*11.5-1, the full horizon for any score strictly better than 11.5.",
    },
    "dense_random": {
        "T": 30,
        "min_swaps": None,
        "max_swaps": 28,
        "time": 14400.0,
        "complete": False,
        "note": (
            "Incomplete search. The full proof horizon is depth<=70 (2*35.5-1). "
            "This run uses T=30 and swaps<=28."
        ),
    },
    "ghz_star": {
        "T": 9,
        "min_swaps": None,
        "max_swaps": None,
        "time": 120.0,
        "complete": False,
        "note": "Regression at the known optimal depth. Not a new proof.",
    },
    "chain_trotter": {
        "T": 9,
        "min_swaps": 0,
        "max_swaps": 0,
        "time": 120.0,
        "complete": False,
        "note": "Regression, 0 SWAPs, known depth 9.",
    },
    "vqe_layers": {
        "T": 6,
        "min_swaps": 0,
        "max_swaps": 0,
        "time": 180.0,
        "complete": False,
        "note": "Regression, 0 SWAPs, known depth 6.",
    },
}


def _run_benchmark(name: str, time_limit: float | None, workers: int) -> None:
    from starter_kit.benchmarks import BENCHMARKS
    from starter_kit.hardware import build_hardware_graph

    preset = _PRESETS[name]
    program = BENCHMARKS[name]
    graph = build_hardware_graph()
    warm = _load_warm().get(name)
    limit = preset["time"] if time_limit is None else time_limit
    print(f"\n=== {name} T={preset['T']} complete={preset['complete']} ===", flush=True)
    print(preset["note"], flush=True)
    if warm is not None and name == "ladder_trotter":
        # The proof horizon is depth 6; the incumbent is depth 7. Check the
        # model accepts that incumbent at T=7 before trusting an INFEASIBLE at T=6.
        print("checking incumbent is feasible at its own depth", flush=True)
        ok = _fix_and_check_hint(program, graph, T=7, warm=warm)
        if not ok:
            print("MODEL REJECTS THE INCUMBENT. Not running the proof search.", flush=True)
            return
    result = solve_joint(
        program,
        graph,
        T=preset["T"],
        time_limit_s=limit,
        min_swaps=preset["min_swaps"],
        max_swaps=preset["max_swaps"],
        warm_start=warm,
        num_workers=workers,
        complete=preset["complete"],
        note=preset["note"],
        benchmark=name,
    )
    result["benchmark"] = name
    result["record"] = "final"
    result["final"] = True
    _append_result(result)
    off = result.get("official")
    print(
        f"status={result['status']} obj={result['objective']} model_score={result['model_score']} "
        f"bound={result['dual_bound']} bound_score={result['dual_score']} "
        f"wall={result['wall_time']:.1f}s T={result['T']} "
        f"swaps=[{result['min_swaps']},{result['max_swaps']}] workers={result['workers']} "
        f"hinted={result['hinted']} accepted={result['accepted']}",
        flush=True,
    )
    if off:
        print(
            f"official valid={off['valid']} score={off['score']} swaps={off['swap_count']} depth={off['depth']}",
            flush=True,
        )
    if result.get("reject_reason"):
        print("rejected:", result["reject_reason"], flush=True)
    if name == "ladder_trotter" and result["status"] == "INFEASIBLE" and preset["complete"]:
        print("ladder_trotter: no 3-SWAP depth-6 route. 6.5 is optimal.", flush=True)


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Joint CP-SAT routing (swaps + official depth)")
    parser.add_argument("--toy", action="store_true")
    parser.add_argument(
        "--benchmark",
        nargs="+",
        choices=list(_PRESETS),
    )
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 8)))
    args = parser.parse_args()
    if not ORTOOLS_AVAILABLE:
        print("ortools is not importable")
        raise SystemExit(0)
    if args.toy or not args.benchmark:
        _toy_validation()
    if args.benchmark:
        for name in args.benchmark:
            _run_benchmark(name, args.time_limit, args.workers)
