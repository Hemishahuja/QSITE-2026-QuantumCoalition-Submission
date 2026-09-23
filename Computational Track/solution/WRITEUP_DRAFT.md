# Draft Submission: Exact-Objective Beam Routing with Zero-SWAP Embeddings

**Track:** Computational (quantum circuit compilation: placement, routing, scheduling)
**Status:** Draft (Sep 23, 2026). `solve()` beats the baseline on every benchmark we threw at it, and everything passes the official scorer.

## Results so far

Scored with the official `starter_kit.scorer` (`score = swaps + 0.5 * depth`, lower is better). LB is a provable lower bound we compute for each program. When our score matches the LB, that result is optimal.

| Benchmark | Baseline | Ours | SWAPs | Depth | Lower bound | Optimal? |
|---|---|---|---|---|---|---|
| ghz_star | 14.0 | **6.5** | 2 | 9 | 6.5 | yes |
| chain_trotter | 15.0 | **4.5** | 0 | 9 | 4.5 | yes |
| ladder_trotter | 35.5 | **6.5** | 3 | 7 | 4.0 | - |
| qaoa_random | 39.0 | **11.5** | 6 | 11 | 5.0 | - |
| dense_random | 122.0 | **35.5** | 24 | 23 | 9.5 | - |
| vqe_layers | 58.0 | **3.0** | 0 | 6 | 3.0 | yes |
| **Total** | **283.5** | **67.5** | | | 32.5 | 3 of 6 |

That's a 76% drop from baseline, and each benchmark only gets 10s of search.

## Approach

1. **Zero-SWAP check (subgraph monomorphism).** First thing we do is look for a placement that lands every interacting logical qubit pair on an actual hardware edge. Turns out the 20-qubit graph has a path running through all 20 qubits (`3-2-1-0-4-5-6-7-11-10-9-8-12-13-14-15-19-18-17-16`), so chain- and brick-layer style programs (`chain_trotter`, `vqe_layers`) don't need a single SWAP. Score there just falls out to the program's own critical-path depth, which happens to be optimal.
2. **Beam search on the exact objective.** When zero-SWAP isn't an option, we route gates in program order with a beam search. For each non-adjacent pair we enumerate the ways the two qubits could meet on a shortest (or near-shortest) path - one qubit walks the whole way, or they split the distance and close in from both sides. Every partial solution tracks each qubit's "ready layer," so we always know the exact `swaps + 0.5 * depth` so far, not a guess. We rank candidates by that cost plus a look-ahead on upcoming gate distances and keep the best *W*. Because depth is modeled exactly instead of approximated, the search naturally prefers SWAPs that can run in parallel on qubits that are sitting idle anyway.
3. **Lazy placement.** A logical qubit only gets pinned to a physical location the first time it's actually used - this folds placement into the same search as routing. We also seed a few other starting placements: zero-SWAP embeddings of the longest program prefix we can manage, and simulated annealing on distance cost.
4. **Portfolio under a time budget.** Beam widths escalate from 8 up to 2048, with forward/backward (SABRE-style) refinement and some randomized restarts thrown in. Search stops as soon as it hits the lower bound - no reason to keep burning time budget once you're already optimal.
5. **Safety.** Every candidate gets checked against the same rules as the official scorer. The greedy baseline is always there as a fallback, so `solve()` never hands back an invalid answer.

**Lower bounds.** Depth can't drop below the program's critical path - that's a hard floor. If there's no zero-SWAP placement, you need at least one SWAP, period. For a qubit with more interaction partners than the hardware's max degree Δ: a SWAP that moves that qubit adds at most Δ-1 new neighbors, while any other SWAP adds at most 1, and moving the qubit also tacks on depth to its own chain. Work through that for `ghz_star` and you get 6.5 as the floor: 2 SWAPs to move the hub, depth 9.

## Roadmap to the final submission

- Close the gap on `ladder_trotter`, `qaoa_random`, and `dense_random`: SWAPs that pre-position later gates' qubits during idle layers, wider beams, better placements.
- Tighter lower bounds, so we can prove more of these optimal instead of just "probably close."
- Robustness suite (random programs, 1Q gates, unusual qubit labels, other hardware graphs).
- Stretch goals if time allows: gate decomposition and single-qubit optimization.
- Final writeup with routing animations and a 3-5 minute demo.

## How to run

```bash
cd "Computational Track"
python solution/bench.py --budget 10
```

```python
from solution import solve
placement, routed = solve(program, hardware_graph)   # optional: time_budget=seconds
```
