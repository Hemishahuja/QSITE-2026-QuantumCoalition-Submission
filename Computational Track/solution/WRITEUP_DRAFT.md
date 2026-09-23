# Draft Submission: Exact-Objective Beam Routing with Zero-SWAP Embeddings

**Track:** Computational (quantum circuit compilation: placement, routing, scheduling)
**Status:** Draft (Sep 23, 2026). Working `solve()` beats the baseline on every benchmark, and all outputs pass the official scorer.

## Results so far

Scored with the official `starter_kit.scorer` (`score = swaps + 0.5 * depth`, lower is better). LB is a provable lower bound we compute for each program. When the score equals the LB, that result is optimal.

| Benchmark | Baseline | Ours | SWAPs | Depth | Lower bound | Optimal? |
|---|---|---|---|---|---|---|
| ghz_star | 14.0 | **6.5** | 2 | 9 | 6.5 | yes |
| chain_trotter | 15.0 | **4.5** | 0 | 9 | 4.5 | yes |
| ladder_trotter | 35.5 | **6.5** | 3 | 7 | 4.0 | - |
| qaoa_random | 39.0 | **11.5** | 6 | 11 | 5.0 | - |
| dense_random | 122.0 | **35.5** | 24 | 23 | 9.5 | - |
| vqe_layers | 58.0 | **3.0** | 0 | 6 | 3.0 | yes |
| **Total** | **283.5** | **67.5** | | | 32.5 | 3 of 6 |

This is **76% lower than the baseline**, with 10 s of search per benchmark.

## Approach

1. **Zero-SWAP check (subgraph monomorphism).** We look for a placement that puts every interacting pair of logical qubits on a hardware edge. The 20-qubit graph has a path through all 20 qubits (`3-2-1-0-4-5-6-7-11-10-9-8-12-13-14-15-19-18-17-16`), so chain- and brick-layer programs (`chain_trotter`, `vqe_layers`) need no SWAPs. Their score then equals the program's own critical-path depth, which is optimal.
2. **Beam search on the exact objective.** Otherwise, we route gates in program order. For a non-adjacent pair, we consider every way the two qubits can meet on a shortest (or nearly shortest) path. For example, one qubit can do all the moving, or both can move toward each other in parallel. Each partial solution tracks the per-qubit "ready layer", so it knows its exact `swaps + 0.5 * depth` so far. We rank partial solutions by that cost plus a look-ahead estimate of upcoming gate distances and depth, and keep the best *W*. Because depth is modeled exactly, the search favors SWAPs that run in parallel on idle qubits.
3. **Lazy placement.** A logical qubit is bound to a physical location only when it is first used. This merges placement into the routing search. Other starting placements are also tried: zero-SWAP embeddings of the longest possible program prefix, and simulated annealing on distance cost.
4. **Portfolio under a time budget.** Beam widths escalate from 8 to 2048, with forward/backward (SABRE-style) refinement and randomized restarts. The search stops early once it reaches the lower bound.
5. **Safety.** Every candidate is checked with the same rules as the official scorer. The greedy baseline is the fallback, so `solve()` never returns an invalid answer.

**Lower bounds.** Depth can never be less than the program's critical path. If no zero-SWAP placement exists, at least one SWAP is needed. For a qubit with more partners than the hardware's maximum degree Δ, a SWAP that moves the qubit adds at most Δ-1 new neighbors, and any other SWAP adds at most 1. Moving the qubit also adds depth to its chain. For `ghz_star`, this proves 6.5 optimal: 2 SWAPs that move the hub, for depth 9.

## Roadmap to the final submission

- Close the gap on `ladder_trotter`, `qaoa_random`, and `dense_random`: SWAPs that pre-position later gates' qubits during idle layers, wider beams, and better placements.
- Tighter lower bounds, to prove more results optimal.
- Robustness suite (random programs, 1Q gates, unusual qubit labels, other hardware graphs).
- Stretch goals: gate decomposition and single-qubit optimization.
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
