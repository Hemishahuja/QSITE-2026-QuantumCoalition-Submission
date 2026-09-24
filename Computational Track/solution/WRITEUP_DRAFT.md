# Draft Submission: Exact-Objective Beam Routing with Zero-SWAP Embeddings

**Track:** Computational (quantum circuit compilation: placement, routing, scheduling)
**Status:** Draft (Sep 23, 2026). `solve()` beats the baseline on every benchmark we threw at it, and everything passes the official scorer.

## Results so far

Scored with the official `starter_kit.scorer` (`score = swaps + 0.5 * depth`, lower is better). LB is a provable lower bound we compute for each program. When our score matches the LB, that result is optimal.

| Benchmark | Baseline | Ours | SWAPs | Depth | Lower bound | Optimal? |
|---|---|---|---|---|---|---|
| ghz_star | 14.0 | **6.5** | 2 | 9 | 6.5 | **yes, proven** |
| chain_trotter | 15.0 | **4.5** | 0 | 9 | 4.5 | **yes, proven** |
| ladder_trotter | 35.5 | **6.5** | 3 | 7 | 6.0 | no -- best found, SWAP count (3) is proven minimal, depth isn't |
| qaoa_random | 39.0 | **11.5** | 6 | 11 | 6.0 | no -- best found |
| dense_random | 122.0 | **35.5** | 24 | 23 | 9.5 | no -- best found |
| vqe_layers | 58.0 | **3.0** | 0 | 6 | 3.0 | **yes, proven** |
| **Total** | **283.5** | **67.5** | | | 35.5 | 3 of 6 proven |

76% below baseline. On "Optimal?": we're being deliberately precise here because it matters --
"proven" means we have an actual argument that no valid solution can beat that score for the
official `swaps + 0.5*depth` objective (see Lower Bounds below). For the other three, "best
found" is a different, weaker claim: we know the true minimum SWAP count for `ladder_trotter`
exactly (a branch-and-bound search proved 3 is the floor, no fewer works), but we have NOT
proven the combined score is minimal -- there could be a 4-or-5-swap solution with a shallower
depth that beats 6.5 and we just haven't found it. Don't read "best found" as "optimal."

## Approach

1. **Zero-SWAP check (subgraph monomorphism).** First thing we do is look for a placement that lands every interacting logical qubit pair on an actual hardware edge. Turns out the 20-qubit graph has a path running through all 20 qubits (`3-2-1-0-4-5-6-7-11-10-9-8-12-13-14-15-19-18-17-16`), so chain- and brick-layer style programs (`chain_trotter`, `vqe_layers`) don't need a single SWAP. Score there just falls out to the program's own critical-path depth, which happens to be optimal.
2. **Beam search on the exact objective.** When zero-SWAP isn't an option, we route gates in program order with a beam search. For each non-adjacent pair we enumerate the ways the two qubits could meet on a shortest (or near-shortest) path - one qubit walks the whole way, or they split the distance and close in from both sides. Every partial solution tracks each qubit's "ready layer," so we always know the exact `swaps + 0.5 * depth` so far, not a guess. We rank candidates by that cost plus a look-ahead on upcoming gate distances and keep the best *W*. Because depth is modeled exactly instead of approximated, the search naturally prefers SWAPs that can run in parallel on qubits that are sitting idle anyway.
3. **Lazy placement.** A logical qubit only gets pinned to a physical location the first time it's actually used - this folds placement into the same search as routing. We also seed a few other starting placements (zero-SWAP embeddings of the longest program prefix we can manage, simulated annealing on distance cost) and rank them all in a quick pass. Turns out, on the denser benchmarks, lazy placement alone beats every precomputed placement we tried by a wide margin -- we checked this properly with a 240-trial sweep (see `_diagnose_dense_random.py`, not part of the submission, just our own scratch work), varying placement and routing search independently. Committing to physical qubits before you've seen how routing actually plays out just loses, on this kind of instance.
4. **Exact minimum-SWAP search.** For smaller instances we also run a branch-and-bound search that proves the true minimum SWAP count (ignoring depth), not just a heuristic guess. It's what let us confirm `ladder_trotter` needs at least 3 SWAPs, period, and it occasionally hands back the winning routed program outright (that's what happened for `ghz_star`).
5. **SWAP cancellation.** A cheap cleanup pass at the end: if a SWAP(x,y) is later undone by another SWAP(x,y) with nothing touching x or y in between, both cancel out for free. Doesn't show up on the 6 public benchmarks so far, but it's free and can only help, so it's always on.
6. **Portfolio under a time budget.** Beam widths escalate from 8 up to 2048, with forward/backward (SABRE-style) refinement and randomized restarts. We also bias restarts toward whichever placement strategy is winning so far rather than spreading time evenly -- if one approach is dominating (like lazy does on `dense_random`), it gets more of the budget. Search stops early once it hits the lower bound - no reason to keep burning time once you're already optimal.
7. **Safety.** Every candidate gets checked against the same rules as the official scorer before it's accepted. The greedy baseline is always there as a fallback, so `solve()` never hands back an invalid answer -- we verified this against the *actual, unmodified* `starter_kit.scorer` (see `verify_official.py`), not just our own internal reimplementation, and against 122+ generated edge cases (`test_robustness.py`): empty programs, disconnected components, relabeled/oversized hardware graphs, a 50-qubit scale test, all valid.

**Lower bounds.** Depth can't drop below the program's critical path - that's a hard floor, true for any hardware no matter how well-connected. If no zero-SWAP placement exists, you need at least one SWAP. For a qubit with more interaction partners than the hardware's max degree Δ: a SWAP that moves that qubit adds at most Δ-1 new neighbors, any other SWAP adds at most 1, and moving the qubit also adds depth to its own chain (it's occupying that physical slot for another operation). Work through that for `ghz_star` and you land on exactly 6.5: 2 SWAPs to move the hub, depth 9 -- and since our solution hits 6.5 on the nose, that's a real proof, not a coincidence. Where we don't have a matching proof (`ladder_trotter`, `qaoa_random`, `dense_random`), we say so plainly rather than rounding up to "optimal."

**A note on reproducibility.** `solve()` is a time-boxed randomized search, so it's an anytime algorithm: more compute can only make the answer the same or better, never worse, for a *fixed* seed -- but under heavy system load, fewer search iterations complete before the deadline, and that can leave you on an earlier, worse checkpoint. We had an independent check catch this on `qaoa_random` (11.5 vs. 12.5 across two runs under a loaded machine) and bumped the default time budget from 10s to 20s for more headroom. With the default seed (0, i.e. whatever you get from calling `solve(program, hardware_graph)` exactly as specified, no extra arguments), every run we've done today -- dozens, across budgets from 10s to 30s -- reproduces the 67.5 total. Passing a different seed explicitly can land a point or two worse on the harder benchmarks; it will never come back invalid or below the proven lower bounds.

## What's done vs. what's left

Done since the first draft: robustness suite (122+ cases), the exact minimum-SWAP proof, SWAP cancellation, an autopilot loop that keeps searching in the background and only ever commits strict, officially-validated improvements, and an independent audit pass that re-derived the optimality claims from scratch and confirmed them.

Still open:
- Close more of the gap on `ladder_trotter` / `qaoa_random` / `dense_random`. A 240-trial diagnostic sweep says `dense_random` is placement-*strategy* limited (lazy beats every precomputed placement by 3.5-40+ points) and that we're likely near this search paradigm's ceiling -- next lever is scaling the lazy-seeded search further, not more placement heuristics. That's a 240-trial sample at a ~20s cap each, not exhaustive proof; worth re-checking with a much longer budget if time allows.
- Stretch goals (gate decomposition / 1Q optimization) -- not attempted, small bonus (`N × 0.1`) relative to remaining routing headroom.
- 3-5 minute demo.

## How to run

```bash
cd "Computational Track"
python solution/bench.py --budget 20
python solution/verify_official.py       # calls the real starter_kit.scorer directly, no shortcuts
python solution/test_robustness.py       # full edge-case + random-program suite
```

```python
from solution import solve
placement, routed = solve(program, hardware_graph)   # optional: time_budget=seconds, seed=int
```
