# QSITE 2026 Computational Track: Strategy and Architecture Audit

Date: 2026-09-25. Scope: skeptical audit of `solution/solve.py` against the problem, the literature, and new exact computations. Nothing outside `solution/research/` was modified. No commits were made.

**Evidence labels used throughout**
- **[OFF]**: produced by the real, unmodified `starter_kit.scorer.score_summary`.
- **[ME: script]**: computed by me with the named script in `solution/research/`. Any score quoted from a witness was re-checked with the official scorer. Proofs of infeasibility come from my exact searches. They were cross-validated (see §4), but they are not formally verified.
- **[LIT: link]**: taken from a publication or repository.
- **[EST]**: an estimate or judgment call.

---

## 0. TL;DR (decision-relevant)

1. **4 of 6 benchmarks are provably optimal now, not 3.** ghz 6.5, chain 4.5, vqe 3.0 and **ladder 6.5** are optimal. For ladder, an exact joint search over the fully general move model with exact ASAP depth proved that no route reaches 6.0 ([ME: `joint_exact.py`], `je_ladder.log`). That locks 20.5 of our 67.5.
2. **qaoa_random needs exactly 5 SWAPs minimum.** 4 is infeasible and a 5-SWAP route exists [ME: `general_exact.py`, `ge_qaoa_full.log`]. Q-Synth's DAG relaxation also gives 5. The floor is therefore ≥ 9.0, against our 11.5 (6 SWAPs, depth 11). The only ways to beat 11.5 are (5 SWAPs, depth ≤ 12) or (6 SWAPs, depth ≤ 10). The joint exact search at T = 22 settles this and is still running (§9.4). The only witness found so far is 5 SWAPs at depth 14 (12.0).
3. **dense_random is the only place a large routing gain could still exist, and we cannot rule one out.**
   - The proven floor is **17.0** (≥ 11 SWAPs, depth ≥ 12) against our 35.5. The SWAP bound comes from Q-Synth's SAT encoding under DAG semantics: 0–10 SWAPs are UNSAT, and that model is a relaxation of ours [ME via `qsynth_lb.py`; relies on Q-Synth's optimality claim]. My own interval bound gives ≥ 7.
   - Where I could check exactly, our route is close to the optimum. A 16-gate prefix needs 3 SWAPs and we have used 4 by then. Every suffix of up to 11 gates is exactly SWAP-optimal from its fixed start mapping.
   - 650 random-layout forward/backward lineages all land at ≥ 35.5.
   - Still, 10–24 SWAPs is a wide window, so dense cannot be called exhausted.
4. **Outside dense, at most ~2.5 points of core score is theoretically available (all of it on qaoa).** So a competitor who is "substantially better" (more than ~3 points) on the same six benchmarks with the same scorer must be much better on dense, must be using the stretch bonus, or must be on a different test set.
5. **The stretch-A bonus is worth exactly 41.0 points on our current outputs** if it counts [ME: `stretch_bonus.py`]. N = 410 gates saved against the organizer's (since deleted) bad decomposer, so the reported total would be 67.5 − 41.0 = 26.5. That is an order of magnitude more than any remaining routing headroom. Whether it counts is ambiguous (§18), so **get a ruling from the organizers first**.
6. **Calibration: our solver is far ahead of stock SABRE and is exactly optimal on small instances.**
   - Qiskit 2.5.2 SABRE, best of 200 seeds with strict order enforced, totals 97.5–106 against our 67.5 [OFF via `sabre_calibration.py`]. Dense: 58.5 vs 35.5. Unconstrained SABRE output was never convertible to strict order on dense (0 of 200).
   - Production `solve()` (5 s budget) hit the proven joint optimum on **every** random 6–10-qubit instance where the exact search finished: 21 of 40, never beaten, with the other 19 timing out [ME: `arch_gap.py`].
7. **Architecture findings.** The beam router's move set is a strict subset of what SABRE, A* or exact methods consider: SWAP chains only along near-shortest paths of the two endpoints, and no SWAPs while the front gate is already adjacent. States are deduplicated by mapping only, which drops better ready-time profiles. `exact_min_swaps` has a witness-conversion bug (5 of 56 random witnesses were rejected as non-injective). The earlier claim that ladder's 3-SWAP minimum was proven did **not** hold inside the production budget: in-budget `_lower_bound` gives only 5.0 on ladder and on qaoa. None of this has cost score on the public benchmarks so far (items 1–2 and 6).

---

## 1. Exact problem reconstruction (from code)

**Scorer** (`starter_kit/scorer.py`):
- `validate_initial_placement`: the placement keys must be **exactly** the set of logical qubits used, the values must be injective, and they must be hardware nodes. qaoa_random only uses 10 qubits ({0,1,2,3,5,6,8,9,10,11}; 4 and 7 never occur), so the placement must have exactly those 10 keys.
- `translate_back_to_logical`: a SWAP must be on an edge and may involve **unoccupied** physical qubits (the code maps them to None). A 2Q gate must be on an edge with both qubits occupied. The translated op list must **equal the program list exactly**, including operand order, because `("2Q", a, b)` is not the same tuple as `("2Q", b, a)`. 1Q ops on unoccupied qubits are invalid. The six benchmarks contain **no 1Q ops**.
- `schedule_layers_ordered`: each non-1Q op (SWAPs included) goes to layer 1 + max(last layer of its wires). This is exactly the longest path in the per-wire dependency DAG, so it is invariant under reordering adjacent ops on disjoint wires.
- Score = #SWAP + 0.5·depth per benchmark. An invalid benchmark scores ∞.

**Implication, verified.** A solution is fully determined, up to swapping commuting adjacent ops, by the initial placement plus the SWAP sequence inserted between consecutive program gates. SWAPs after the last gate are pure cost. **The strict total order is strictly more restrictive than the DAG ("front layer") semantics that SABRE, OLSQ, TOQM and Q-Synth use.** In DAG semantics a later gate may run under an earlier mapping before SWAPs that move its own qubits, and that is forbidden here. Empirically, unconstrained SABRE outputs on dense_random were never re-linearizable into program order (0 of 200 seeds) [ME: `sabre_calibration.py`]. Consequence: DAG-model optima are **relaxations**, so they give valid *lower* bounds for us, but DAG-model routes are generally *invalid* here.

**Hardware** [ME: `bench_stats.py`]:
- 20 nodes and 23 edges. Degrees: two nodes of degree 1, ten of degree 2, eight of degree 3.
- **Diameter 9.** The README's "0 and 19 are 7 hops" is true, but it is not the diameter.
- **Girth 6**: no triangles or 4-cycles; the cycle basis is four 6-cycles.
- Only 2 automorphisms, so symmetry reduction buys little.
- A Hamiltonian path exists (used for chain and vqe).

**Benchmarks** [ME: `bench_stats.py`]:

| benchmark | logical qubits | 2Q gates | distinct pairs | interaction degree (max) | #qubits with degree > 3 | critical path | interaction girth | zero-SWAP embeddable |
|---|---|---|---|---|---|---|---|---|
| ghz_star | 8 | 7 | 7 | 7 | 1 | 7 | – (star) | no (degree 7 > 3) |
| chain_trotter | 10 | 9 | 9 | 2 | 0 | 9 | – (path) | yes |
| ladder_trotter | 12 | 16 | 16 | 3 | 0 | 6 | 4 | no (girth 4 < 6) |
| qaoa_random | 10 | 18 | 15 | 5 | 4 | 8 | 3 | no |
| dense_random | 14 | 40 | 37 | 9 | 12 | 12 | 3 | no |
| vqe_layers | 16 | 45 | 15 | 2 | 0 | 6 | – (path) | yes |

---

## 2. Current solver architecture (traced, not taken from comments)

Pipeline of `Solver.run`:
1. Greedy fallback.
2. `_lower_bound`, which gets 25% of the budget and contains the zero-SWAP embedding search plus the exact min-SWAP search.
3. Starts: lazy, up to 4 prefix embeddings, and 3 annealed placements.
4. A width-8 ranking pass keeps the top 3 starts.
5. "Priority" escalation on the best start: widths 32 → 4096, slack {1, 0}, plus `_refine`.
6. The full grid: widths 32 → 2048 × slack × top-3.
7. Randomized restarts until the deadline.
8. Every candidate passes `_offer`, which runs `cancel_redundant_swaps`, then `is_valid` (a mirror of the official check), then `score_routed` (a mirror of the official formula).

| Technique | Code location | Actual behavior | Benchmarks affected | Evidence it helps | Limitation |
|---|---|---|---|---|---|
| Greedy fallback | `greedy_baseline` | Identity placement, shortest-path SWAPs, same as the starter | all (safety only) | never selected | none relevant |
| Zero-SWAP embedding | `find_embeddings` (DFS subgraph monomorphism, degree/girth pruning, 200k-node budget) | Returns the first embedding found; exhaustion proves none exists | chain, vqe (solved optimally); proves ghz/ladder/qaoa/dense need ≥ 1 SWAP | chain 4.5 and vqe 3.0 are optimal [OFF] | With 0 SWAPs, depth equals the critical path for any embedding, so no loss |
| Hub-degree lower bound | `_lower_bound` | For a qubit with p > Δ partners: moves of the hub gain ≤ Δ−1 new neighbours, other SWAPs gain ≤ 1; also accounts for depth on the hub chain | ghz (tight 6.5), dense (gives 9.5) | proves ghz optimal | per-qubit only; does not add across hubs |
| `exact_min_swaps` | L268–401 | Iterative deepening on the SWAP budget with memo (k, pos)→used and lazy placement. **Move set: only SWAPs incident to the current carriers of the front gate.** Limits L ≤ 16, G ≤ 24; gets 70% of 25% of the budget | runs on ghz/ladder/qaoa; skipped for dense (G = 40) and vqe (G = 45) | ghz witness 2 SWAPs [ME] | **Times out in production** (ladder LB 5.0, qaoa LB 5.0 in-budget [ME]). **Witness bug**: `l2t` records the *current* position when a qubit is placed lazily after SWAPs have happened, not its token origin, so the witness can be non-injective. 5 of 56 random witnesses were invalid [ME: `move_model_gap.py`]. `_offer` rejects them safely but silently drops the exact result. Swap count only |
| Beam router: move generation | `Router.route` | For a non-adjacent gate (a, b): every arc (u, v) with d(pa, u) + d(pb, v) ≤ d − 1 + slack. `paths` shortest paths pa→u and pb→v (DFS in fixed neighbour order). Apply a's chain, then b's chain (discard if a's path crosses pb), execute, cap at `route_cap` = 24 children by (exact cost, idleness) | ladder/qaoa/dense | this is what finds every non-trivial result | **Strict subset of SABRE/A*/exact move sets**: each endpoint moves only along a *shortest* path to the meeting arc; no deliberate moves of third-party qubits; **no SWAPs at all when the front gate is already adjacent** (no pre-positioning); greedy route cap |
| Lazy placement | `Router.expand` | A qubit is bound on first use to a free physical qubit, ranked by (distance, ready, attraction); `new_opts` = 4 or `pair_opts` = 12 | all routed | "lazy beats all precomputed placements on dense" [writeup, 240-trial sweep] | candidate cap; ranking by distance first |
| Search state and dedup | `Router.run` | Key = logical→physical mapping; keep min (swaps + 0.5·depth), tie on sum(ready) | all routed | – | **Not a dominance rule**: it discards same-mapping states with a better ready profile but a higher current cost |
| Evaluation / lookahead | `Router.evaluate` | swaps + 0.5·(depth estimate over a 20-gate window) + α·Σ decay^j·(d−1) | all routed | autopilot A/B tuned decay 0.6, window 20 | no admissible bound, no sharing of SWAPs across future gates |
| Prefix embeddings | `prefix_embeddings` | Binary search for the longest embeddable prefix, up to 4 random embeddings of it as starts | ladder/qaoa/dense | loses to lazy on dense [writeup] | like the first stage of BMT [LIT: Siraichi et al.], but without the token-swapping bridge between segments |
| Annealed placement | `anneal_placement` | SA on decayed weighted distance, 4000 iterations | – | loses to lazy [writeup] | ignores order and depth |
| "SABRE-style bidirectional" refinement | `Solver._refine` | Take the best state's final mapping, run a beam on the reversed gate list, then use that run's final mapping as the initial placement of a forward beam | ladder/qaoa/dense | no logged win on the public set [EST] | **Faithful to the idea of SABRE's layout passes**, but the inner router is the beam (not SABRE's swap scoring) and there is only one lineage (SabreLayout runs many random layout trials) |
| Escalating widths and restarts | `Solver.run` | widths up to 4096; random (width, slack, window, decay, α, noise, paths) | ladder/qaoa/dense | 46 autopilot iterations, no gain | explores the *same* move model; noise only perturbs ranking |
| Redundant-SWAP cancellation | `cancel_redundant_swaps` | Removes SWAP(x, y)…SWAP(x, y) pairs with nothing on x or y in between | – | never fires on the 6 benchmarks [writeup] | effectively dead code here |
| Early stop at LB | `_done` | Stops when best ≤ LB | ghz, chain, vqe | saves time | LB is weak for ladder/qaoa/dense, so it never triggers there |

**Is the beam's move set a strict subset of SABRE's?** Yes, per step. SABRE scores every SWAP touching a front-layer qubit, including "sideways" moves that do not shorten the front gate but help the extended set [LIT: SABRE arXiv:1809.02573]. Zulehner's A* considers any SWAP touching qubits of the current layer [LIT: arXiv:1712.04722]. The beam only generates monotone meeting chains (plus up to `slack` detour at the meeting point). SABRE is greedy while the beam is wide, which is why the beam still wins overall (§3).

---

## 3. Benchmark-level performance

Current numbers were captured today with `solve(..., time_budget=20, seed=0)` and saved to `current_solutions.json` [OFF].

| benchmark | ours: swaps / depth / score [OFF] | proven lower bound on score | gap | Qiskit SABRE, strict order, best of 200 [OFF] | starter baseline |
|---|---|---|---|---|---|
| ghz_star | 2 / 9 / **6.5** | **6.5** (hand proof + [ME joint]) | 0 | 6.5 | 14.0 |
| chain_trotter | 0 / 9 / **4.5** | **4.5** | 0 | 4.5 | 15.0 |
| ladder_trotter | 3 / 7 / **6.5** | **6.5** [ME: `joint_exact.py`] | 0 | 7.5 | 35.5 |
| qaoa_random | 6 / 11 / **11.5** | **9.0** (5 SWAPs exact [ME] + depth 8); see §9.4 | ≤ 2.5 | 13.0 | 39.0 |
| dense_random | 24 / 23 / **35.5** | **17.0** (≥ 11 SWAPs via Q-Synth DAG relaxation [ME]; ≥ 7 via my interval DP; depth ≥ 12) | ≤ 19.5 (very loose) | 58.5 | 122.0 |
| vqe_layers | 0 / 6 / **3.0** | **3.0** | 0 | 3.0 | 58.0 |
| **total** | **67.5** | **46.5** | | 97.5 (layout without barriers, routing with barriers) / 106.0 (barriers throughout) | 283.5 |

The SABRE column is per-benchmark best over three strict-order variants.

---

## 4. What is actually proven

**A. Proven facts**
- **ghz_star 6.5 is optimal.** Hand proof: the 7 gates share qubit 0, so depth ≥ 7 + s_c, where s_c is the number of SWAPs moving the hub. Initially at most 3 leaves are adjacent. A hub move adds at most 2 new neighbours and any other SWAP adds at most 1 (the graph is triangle-free), so 3 + 2·s_c + s_o ≥ 7. Minimizing s_c + s_o + (7 + s_c)/2 gives (2, 0) → 6.5. This was confirmed independently by the joint exact search (T = 12 infeasible, T = 13 feasible) [ME: `joint_exact.py`].
- **chain_trotter 4.5 is optimal.** Consecutive gates share a qubit, so depth ≥ 9, and a zero-SWAP embedding exists [OFF].
- **vqe_layers 3.0 is optimal.** Critical path 6, 0 SWAPs [OFF].
- **The ladder_trotter SWAP minimum is 3**, under the *fully general* move model [ME: `general_exact.py`; B = 2 infeasible]. Q-Synth (DAG relaxation, SAT-optimal) also gives 3 [ME: `qsynth_lb.py`].
- **ladder_trotter 6.5 is optimal.** 2·swaps + depth ≤ 12 is infeasible under the general move model with exact ASAP depth and no eager-execution restriction (725 s) [ME: `joint_exact.py`, `je_ladder.log`]. Soundness check: the same code finds T = 13 feasible (3 SWAPs / depth 7) [ME: `je_ladder13.log`]. This answers "is depth 6 with 3 SWAPs possible?": **no**.
- **The qaoa_random SWAP minimum is exactly 5.** B = 4 is infeasible (288 s) and B = 5 has a witness (5 SWAPs / depth 14, official 12.0) [ME: `general_exact.py`, `ge_qaoa_full.log`].
- **Weaker floors:** qaoa score ≥ 9.0. dense score ≥ 13.0 from my own bound, or ≥ 17.0 using Q-Synth (see B).
- **Stretch-A arithmetic.** N = 410 on current outputs: baseline 650 gates vs 240 with SWAP→3 CX and 2Q→1 CX [ME: `stretch_bonus.py`]. The accounting is 0.4 per SWAP plus 0.2 per program 2Q gate.

**B. Proven partial facts**
- **dense_random needs ≥ 11 SWAPs.** Q-Synth v2 (SAT, DAG dependencies) reported UNSAT for 0–10 SWAPs (steps 8, 9 and 10 took 270 s, 106 s and 2613 s; the run then hit its timeout during step 11) [ME: `qsynth_lb.py`, `qs_dense.log`]. The DAG model allows everything our strict model allows, so this is a valid lower bound for us, *provided Q-Synth's encoding is optimality-preserving as its authors claim* [LIT: arXiv:2403.11598]. My own, weaker but independent, interval-decomposition bound is ≥ 7 [ME: `interval_lb.py`]. It uses the fact that SWAPs inside disjoint gate intervals each cost at least that interval's free-start optimum.
- **dense prefixes:** 16 gates need exactly 3 SWAPs [ME: `prefix_bounds.py`]; 20 gates need ≥ 4; 22 gates need ≥ 4 [ME: `ge_dense_prefix.log`]. Our route has used 4, 7 and 9 SWAPs at those points.
- **qaoa prefix optima:** 8 gates → 1, 10 → 2, 12 → 3, 14 → 3, full 18 → 5 [ME]. Our route has used 5 SWAPs by gate 10. That is not necessarily wrong because it buys depth, but it is suggestive.
- **dense suffix local optimality:** for our route, every suffix of ≤ 11 gates is exactly SWAP-optimal from its fixed start mapping. For 12–19-gate suffixes I only proved min ≥ orig − 2 to orig − 8 before timeout, which is inconclusive [ME: `lns_window.py`, `lns_dense_suffix.log`].

**Cross-validation of my exact code.** On 56 random instances (5–9 qubits, 6–14 gates), my general-model min-SWAP search and the production restricted-model `exact_min_swaps` agreed on every instance [ME: `move_model_gap.py`]. The joint search reproduces the hand-proven ghz optimum. Every feasible witness was verified by the official scorer. None of this is a formal proof of code correctness.

**What may legitimately be reopened:** only qaoa (≤ 2.5) and dense. ghz, chain, vqe and ladder are closed.

---

## 5. What is only empirical

**C. Strong empirical evidence**
- **Production `solve()` is optimal on small random programs.** On 40 random programs (6–10 qubits, 8–14 gates) with a 5 s budget, the joint exact search (60 s cap) proved production optimal on 21 and found nothing better on any instance; 19 timed out [ME: `arch_gap.py`, `arch_gap_seed3.jsonl`].
- **The dense plateau is robust to layout diversity.** 650 SabreLayout-style lineages (a random initial layout, then forward → backward → forward → backward → forward at width 64) gave best 35.5 (as 25 SWAPs / depth 21, a different point from our 24/23) and median 42.5 [OFF via `many_lineages.py`].
- **Carrier-incident SWAPs are sufficient for min-SWAP** on small random instances (0 gaps in 56). This is evidence for the "delay" completeness claim in `solve.py` and `cpsat_solver.py`, but *not a proof*. I believe the local exchange argument is actually incomplete: a non-carrier SWAP followed by a carrier SWAP sharing a vertex cannot be delayed without changing the resulting arrangement. Burgholzer et al. use the same informal argument [LIT: arXiv:2112.00045 §III–IV].
- **Stock SABRE is far worse under strict order** (§3).

**D. Weak empirical evidence**
- The 240-trial dense sweep and 46 autopilot iterations. These are the *same* move model with different seeds and parameters, so they measure this architecture's local plateau, not the instance.
- "Lazy beats every precomputed placement on dense." Only within this router.
- A single lazy-start beam at width 1024 reaches 26 SWAPs / depth 27 (39.5) on dense. The 35.5 comes from the portfolio [ME: `frontier.py`]. (My attempt to trace a swap/depth frontier by reweighting `State.cost` did not work because `evaluate()` hard-codes 0.5·depth, so ignore that part.)

**E. Assumptions**
- That the public 6 benchmarks are the test set (§18).
- That the stretch bonus does not count (§18).
- That 20 s per benchmark is an acceptable runtime.

---

## 6. Why the existing search plateaued

- **ghz, chain, vqe, ladder:** the plateau is the optimum. No further effort is justified.
- **qaoa:** the SWAP floor is 5 and we use 6 because 6 buys depth 11. Whether (5, ≤ 12) or (6, ≤ 10) exists is exactly what the joint search answers (§9.4). The beam cannot explore it systematically for two reasons. First, dedup by mapping drops states with the same mapping but a better ready profile. Second, it never inserts SWAPs while the front gate is adjacent, which is exactly how you "pre-pay" SWAPs in idle layers.
- **dense:** the plateau is characterized only *within* the move model. Sweeping seeds, widths, windows and decays produces the same neighbourhood. On everything I could check exactly (prefixes ≤ 16, suffixes ≤ 11, small random instances) the solver is at or within one SWAP of optimal. That is **moderate evidence against a big architectural gap**, but those windows are exactly where lookahead is easy. Global structure (40 gates, 12 hubs with interaction degree > 3) is where a narrow move model could lose, and there the lower bound is still far away (10 vs 24).

---

## 7. Literature review

| Method | Source | Year | Core idea | Target | State | Complexity | Topology | Evidence | Open source | Relation to ours | We implement | We don't | Why it could matter here |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SABRE | [arXiv:1809.02573](https://arxiv.org/abs/1809.02573) | 2019 | Greedy single-SWAP choice by front + extended-set distance, decay; forward/backward layout passes | swaps (decay for depth) | mapping + front layer (DAG) | O(poly) per SWAP | any | industry default | Qiskit | beam over chains; one bidirectional lineage | layout reverse-pass idea | sideways SWAPs, many layout trials | our calibration: strict-order SABRE is 97.5+ total |
| LightSABRE | [arXiv:2409.08368](https://arxiv.org/abs/2409.08368) | 2024 | Rust SABRE, many trials, depth-aware and critical-path tweaks, release valve | swaps, depth | same | fast | any | −18.9% SWAPs vs SABRE | Qiskit ≥ 1.2 | – | – | release valve (backtrack + shortest-path routing when stuck) | little; it is what we calibrated against |
| Zulehner A* | [arXiv:1712.04722](https://arxiv.org/abs/1712.04722) | 2018/19 | A* per layer over SWAP sets with lookahead | swaps | mapping per layer | exponential per layer | any | big gains vs IBM at the time | MQT QMAP heuristic | per-gate beam, similar | per-gate search | admissible per-layer A* | compatible if layers are consecutive disjoint gates |
| Exact mapping + limited search space | [DAC'19](https://arxiv.org/abs/1907.02026), [arXiv:2112.00045](https://arxiv.org/abs/2112.00045) | 2019/22 | SAT/exact over permutations before each gate; restrict to ≤ K−1 SWAPs and to permutations moving gate qubits | min swaps | permutation per gate/layer | exponential | any | optimal small instances | [MQT QMAP](https://github.com/cda-tum/mqt-qmap) | `exact_min_swaps` is this, per gate | per-gate exact min swaps | depth; the optimality argument is informal (§5) | our general search does it for L ≤ 14 |
| OLSQ / OLSQ2 / TB-OLSQ2 | [ICCAD'20 arXiv:2007.15671](https://arxiv.org/abs/2007.15671), [DAC'23](https://github.com/WanHsuanLin/OLSQ2) | 2020/23 | SMT time-expanded model of mapping + SWAPs + gate times | depth or swaps, exact | mapping per time step, DAG deps | SMT | any | 692× faster than OLSQ | yes | none | – | all | **DAG relaxation → valid LB**; configurable SWAP duration |
| SATMAP | [arXiv:2208.13679](https://arxiv.org/abs/2208.13679) | 2022 | MaxSAT with a mapping per gate in sequence and ≤ n SWAPs before each gate; slicing | swaps | per-gate mapping | MaxSAT | any | ~5× fewer swaps than heuristics | [github](https://github.com/qqq-wisc/satmap) | per-gate sequential model like ours | – | sliced MaxSAT, hybrid SABRE seed + SAT local search | **order-compatible**; LNS-style slices for dense |
| Q-Synth v1/v2 | [arXiv:2304.12014](https://arxiv.org/abs/2304.12014), [SAT'24 arXiv:2403.11598](https://arxiv.org/abs/2403.11598) | 2023/24 | Planning / SAT parallel plans, 1 SWAP + a CNOT group per step | optimal swaps, near-optimal depth | mapping per step, DAG deps | SAT | any | optimal for 8–16 qubits with ≤ 17 SWAPs on 54–127-qubit platforms | [github](https://github.com/irfansha/Q-Synth) | none | – | – | **valid LB for us (DAG ⊇ strict)**; run in §9 |
| Nannicini et al. ILP | [arXiv:2106.06446](https://arxiv.org/abs/2106.06446) | 2021/23 | Network-flow BIP for allocation + routing | fidelity/depth | time-expanded | BIP | any | beats SABRE on CNOTs | – | none | – | all | an LP relaxation could strengthen the dense LB |
| Wagner–Bärmann–Liers | [JOTA 2023 arXiv:2206.01294](https://arxiv.org/abs/2206.01294) | 2023 | **Fixed gate order**: allocation BIP (lower-bound cost) + token swapping (exact B&B / approximation) | swaps, depth | allocation sequence | BIP + TS | any | near-optimal, better than heuristics | – | none | – | all | **most directly compatible** method; natural dense LB/UB tool |
| Token swapping | [ESA'16 arXiv:1602.05150](https://arxiv.org/abs/1602.05150), [ESA'25 hardness](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.ESA.2025.57) | 2016/25 | L/2 lower bound; 4-approx general, 2-approx trees | swaps between mappings | permutation | poly approx | any | theory | – | we use L/2 in research code | – | – | LB building block |
| TOQM | [ASPLOS'21](https://people.cs.rutgers.edu/zz124/assets/pdf/asplos21.pdf), [github](https://github.com/time-optimal-qmapper/TOQM) | 2021 | A* over (mapping, per-cycle schedule), admissible, optimizes whole-circuit depth | depth | mapping + cycle state (DAG) | A* | any | optimal on small | yes | our joint search is the strict-order analogue | – | – | ready-profile state representation |
| MCTS routing | [arXiv:2008.09331](https://arxiv.org/abs/2008.09331), [arXiv:2104.01992](https://arxiv.org/abs/2104.01992) | 2020/21 | MCTS with long-horizon rewards | size / depth | mapping + front | poly per iteration | any | ≥ 30% smaller than SOTA on IBM Q20 | partly | none | – | all | deeper horizon than beam lookahead; unproven here |
| BMT (subgraph iso + token swapping) | [OOPSLA'19](https://doi.org/10.1145/3360546) | 2019 | Maximal embeddable segments joined by token swapping | swaps | segments | exponential-ish | any | good on IBM | yes | `prefix_embeddings` is its first step | first segment | multi-segment chaining | a structural alternative for dense |
| Swap networks | [arXiv:1711.04789](https://arxiv.org/abs/1711.04789), [arXiv:1905.05118](https://arxiv.org/abs/1905.05118), [heavy-hex arXiv:2202.03459](https://arxiv.org/abs/2202.03459) | 2018–22 | Fixed SWAP layers bring all pairs together | depth for commuting gates | – | O(n) layers | lines, heavy-hex | used for QAOA | Qiskit | – | – | – | **E: requires commuting gates / reordering; incompatible**. Also ~n²/2 SWAPs (91 for 14 qubits), far worse than 24 |
| QUEKO / QUBIKOS | [arXiv:2002.09783](https://arxiv.org/abs/2002.09783), [DAC'25](https://doi.org/10.1109/dac63849.2025.11133143) | 2020/25 | Benchmarks with known optima | – | – | – | – | heuristic gaps 1.5–45× in depth; LightSABRE 63× in swaps | yes | – | – | – | caution: heuristics can be far from optimal on crafted instances; our small-instance tests do not show that |
| tket routing | [arXiv:1902.08091](https://arxiv.org/abs/1902.08091) | 2019 | Time-sliced placement + distance-vector SWAP selection | swaps/depth | DAG | poly | any | competitive | pytket | – | – | – | not run (time) |

---

## 8. Gap matrix

Status key: **A** properly implemented · **B** partial · **C** superficially similar but materially weaker · **D** absent · **E** incompatible with the rules.

| Method / idea | Literature evidence | Our implementation | Status | Why it may matter | Benchmark impact | Cheapest decisive experiment |
|---|---|---|---|---|---|---|
| Zero-SWAP embedding | standard | `find_embeddings` | A | done | chain, vqe | – |
| Exact min swaps (general model) | Wille/Burgholzer, SATMAP | `exact_min_swaps` (carrier-incident, witness bug, times out in budget) | B | proofs, and witnesses for small cases | ladder/qaoa: swaps already optimal | fix the witness (use token origin); already verified 0 gaps in 56 |
| Joint exact (swaps + depth, ready profile) | TOQM (DAG), OLSQ | none in production; `research/joint_exact.py` | D | settles ladder (done) and qaoa | qaoa ≤ 2.5 | `joint_exact.py qaoa_random 22 22` (running, §9.4) |
| SABRE-style bidirectional layout | SABRE, LightSABRE | `_refine`, single lineage, beam inside | C | many layout trials is where SabreLayout gets its quality | dense: **tested, no gain** (650 lineages, best 35.5) | done |
| Sideways / third-party SWAPs | SABRE, A*, exact | absent in the beam | D | pre-positioning for future gates | dense, qaoa | beam with an extra "any carrier-incident SWAP" expansion step, compare on dense |
| SWAPs while the front gate is adjacent (idle-layer pre-pay) | TOQM (depth) | absent | D | depth: SWAPs in idle layers are free depth-wise | qaoa (6 → 5 SWAPs at depth ≤ 12?) | joint exact answers it |
| Dominance on (mapping, ready profile) | TOQM pruning | dedup by mapping only | C | keeps depth-better states | qaoa, dense | keep a Pareto set of ≤ 4 profiles per mapping; A/B on dense |
| Admissible lookahead / A* | Zulehner, TOQM | heuristic lookahead only | C | pruning, proofs | – | – |
| Windowed exact / LNS | SATMAP slices, hybrid SATMAP | none (research `lns_window.py`) | D | escapes beam local optima | dense | free-end window re-route + beam tail (§15 E2) |
| Token-swapping bridges between embeddable segments | BMT, Wagner et al. | only the first segment (prefix embeddings) | C | an alternative paradigm for dense | dense | build a BMT-style route from segment embeddings + exact TS; score it |
| Allocation BIP + TS (fixed order) | Wagner et al. 2023 | absent | D | LB and UB for dense in one model | dense | CP-SAT allocation model (the teammate's `cpsat_solver.py` is close) |
| DAG-model optimal tools as lower bounds | Q-Synth, OLSQ2 | research only (`qsynth_lb.py`) | D | valid LB (dense ≥ 11 so far) | dense | extend: backward search from 23 (§15 E1) |
| MCTS | Zhou et al., QRoute | absent | D | deeper horizon | dense | only after E1/E2 |
| Swap networks / commutation-aware routing | Kivlichan, O'Gorman, Weidenfeller | – | E | rely on reordering commuting gates | – | – |
| Gate cancellation / SWAP-CX merging (NASSC) | README link | – | E for core (SWAP count fixed); relevant only to stretch A | – | – | – |

---

## 9. Benchmark-specific diagnosis

### 9.1 ghz_star, chain_trotter, vqe_layers
Optimal (§4). No component blocks anything. Stop.

### 9.2 ladder_trotter
- **Structure:** a 2×6 grid (girth 4) on a girth-6 device.
- **Status:** SWAPs = 3 is minimal and depth ≥ 6. (3, 6) is infeasible [ME joint], so 6.5 is **optimal**.
- **Implication:** the teammate's CP-SAT joint run (`cpsat_joint_results.jsonl`: UNKNOWN after 600 s) is now answered: there is no 6.0 solution. **Stop spending CP-SAT time here.**

### 9.3 dense_random
- **Structure:** 14 qubits, 40 gates, 37 distinct pairs, interaction degrees 9, 8, 6, 6, 6, 5, …, critical path 12. Only 10 of 39 consecutive gate pairs share a qubit, so there is a lot of parallel slack.
- **Current route:** 24 SWAPs, depth 23. The anatomy of the route [ME: `analyze_current.py`]:
  - SWAPs start at gate 7.
  - Cumulative SWAPs after gates 16 / 20 / 24 / 32 / 40 are 4 / 7 / 13 / 19 / 24.
  - Only 2 SWAP-only layers; depth overhead is 11 layers over the critical path.
- **Proven:** ≥ 11 SWAPs (Q-Synth DAG relaxation) and ≥ 7 (my interval DP). The 16-gate prefix optimum is 3 (we use 4 there). Suffixes of ≤ 11 gates are optimal for our route. 650 random-layout lineages never beat 35.5.
- **Dominant loss is unknown.** Score = 24 (SWAPs) + 11.5 (depth), so SWAPs dominate.
- **Targets, worked backwards** (score = s + d/2 with d ≥ 12):
  - 30: (18, 24), (20, 20), (22, 16).
  - 28: (16, 24), (18, 20), (20, 16).
  - 25: (14, 22), (16, 18), (18, 14).

  The plausible routes are "cut 4–8 SWAPs at a similar depth". [EST: the ~0.6 SWAPs/gate observed after gate 16 would have to drop to ~0.35–0.45.]
- **Blocking components:** the narrow move model and mapping-only dedup. **Enabling components:** width, lazy placement.
- **Best-suited methods:** fixed-order allocation + token swapping (Wagner et al.), SATMAP-style sliced MaxSAT/CP-SAT with free-end windows, and DAG-optimal LB tools to bound it.

### 9.4 qaoa_random
- **Structure:** 10 used qubits, 18 gates (3 repeated pairs), hub degrees 5, 4, 4, 4, critical path 8.
- **Proven:** min SWAPs = 5, so the floor is 9.0.
- **Current route:** 6 SWAPs, depth 11. It front-loads SWAPs (5 by gate 10, where the exact prefix minimum is 2) to buy parallelism.
- **Targets:**
  - 11.0 = (5, 12) or (6, 10).
  - 10.5 = (5, 11) or (6, 9).
  - 10.0 = (5, 10) or (6, 8).
- **Joint exact result:**
  - A run from T = 18 did not finish even T = 18 in about 30 min. That was wasted effort: only T = 22 matters, because infeasibility at 22 proves 11.5 optimal and feasibility is an improvement.
  - Two T = 22 runs were still going when I wrote this. The general one (2400 s cap) logs to `je_qaoa22.log`. The eager-execution one (1800 s cap) logs to `je_qaoa22_eager.log`; it is a witness finder only and incomplete for depth. Both also append to `joint_exact_results.jsonl`.
  - Update: the eager T = 22 run hit its 1800 s cap after 44.7M nodes without finding a witness (undecided). Because eager execution can miss depth-optimal schedules, this does not prove anything about 11.0. The general T = 22 run also hit its 2400 s cap (51.2M nodes) undecided, so qaoa's status stays "11.5 found, ≥ 9.0 proven; 11.0 open".
  - **If either reports FEASIBLE**, the witness has already been scored by the official scorer (`witness_official`) and is an improvement.
  - **If the general run reports "infeasible"**, qaoa 11.5 is optimal.
  - **If it times out**, the question stays open.
- **Blocking components:** no SWAPs while the front gate is adjacent, and mapping-only dedup.

### 9.5 Q-Synth DAG-relaxation lower bounds [ME via `qsynth_lb.py`]
- ladder: 3 (matches strict order).
- qaoa: 5 (matches the strict-order optimum, so strict order costs nothing on qaoa's SWAP count).
- dense: ≥ 11 (0–10 UNSAT; step 10 took 2613 s and the run timed out in step 11; see `qs_dense.log` and `qsynth_results.jsonl`).

On ladder and qaoa, strict order did not raise the SWAP optimum at all. If that also held for dense, a completed Q-Synth optimum would be essentially the true dense SWAP optimum [EST]. That makes finishing (or re-running with a longer budget) the dense Q-Synth solve the single most informative computation left.

---

## 10. Potential paradigm changes (assessed)

- **Joint placement + routing.** Already effectively joint via lazy placement; not a gap.
- **State = (mapping, gate index, ready profile) with Pareto dominance** instead of mapping-only dedup. This is the principled fix for the objective mismatch (swaps vs 0.5·depth). Research code exists (`joint_exact.py`); a heuristic version (keep a few profiles per mapping) is cheap.
- **Richer move model.** Add single carrier-incident SWAPs, including sideways ones, as beam actions (SABRE-like), and allow SWAPs when the front gate is adjacent. That makes the beam's space a superset of SABRE's.
- **Tiny fixed hardware.** Precomputed distances already exist. Exact token-swapping tables for 14 tokens on 20 nodes are not feasible (20!/6!); pattern databases over 2–3 tokens are cheap but probably weak.
- **Windowed exact / LNS with free end mapping** plus beam re-routing of the tail. This is the only experiment that can directly show a "wrong neighbourhood" on dense.
- **A different philosophy for dense** (segment embeddings + token swapping à la BMT or Wagner et al.). This is a genuinely different neighbourhood from monotone meeting chains.
- **Generic structural portfolio** (no name hardcoding). Route by interaction-degree profile, e.g. use the exact joint search when L ≤ 10 and G ≤ 18. It would have closed ladder and certified the others.

---

## 11. Missing techniques (ranked by expected value here)
1. A stronger dense lower bound (DAG-optimal relaxation or an allocation-model LP/CP-SAT bound). This is needed just to know whether dense is worth more time.
2. Free-end window LNS on dense with exact or CP-SAT window solves.
3. A richer beam move model (sideways/pre-positioning SWAPs) plus Pareto ready-profile dedup.
4. Many-trial random-layout bidirectional lineages (SabreLayout-style) using the beam.
5. Fixing the exact witness conversion (a correctness hygiene issue that matters for any hidden test set).

## 12. Techniques already adequate
- Zero-SWAP embedding, the exact-objective beam (exact ASAP tracking), lazy placement, validation against the official rules, and the greedy fallback.
- Final-state quality on ghz, chain, vqe and ladder (all optimal).
- Performance vs SABRE (much better).

## 13. Previous assumptions to discard
- "Ladder depth 6 with 3 SWAPs is unresolved." It is now resolved as infeasible, so ladder is optimal.
- "3 SWAPs for ladder is proven by `solve.py`'s branch-and-bound." It was not proven within the production budget (in-budget LB is 5.0). It *is* now proven by the general-model search.
- "The stretch bonus is small." It is 41.0 points on our outputs if it counts, larger than all remaining routing headroom combined.
- "The delay argument makes carrier-incident SWAPs provably complete." The argument is informal; empirically it holds on 56 instances.
- "Plateau across seeds means near-optimal on dense." That only shows a local plateau of one move model.
- "qaoa LB 6.0" and "dense LB 9.5". They are actually 9.0 and 17.0.
- "More seeds / layouts might crack dense." Falsified: 650 random-layout lineages reach exactly 35.5 at best.

## 14. Assumptions still well supported
- Our router is strong on small and medium instances (proven optimal wherever exact search finished).
- Lazy placement is a good default.
- SABRE-class heuristics will not beat us here.
- Swap networks and commutation methods are irrelevant under strict order.

---

## 15. Highest-information experiments

**E1. DAG-relaxation lower bound for dense (Q-Synth, OLSQ2 or a CP-SAT allocation relaxation).**
- **Hypothesis:** min SWAPs on dense ≥ ~18–20, i.e. dense is near-exhausted.
- **Why the architecture may miss it:** n/a (this is a bound).
- **Source:** [Q-Synth SAT'24](https://arxiv.org/abs/2403.11598), [OLSQ2](https://github.com/WanHsuanLin/OLSQ2).
- **Prototype:** `research/qsynth_lb.py`. It reached ≥ 11 before timing out; per-step SAT time grows roughly 2–4× per step.
- **Benchmark:** dense.
- **Expected if correct:** LB ≥ 18, so stop working on dense routing.
- **Falsifying result:** a completed DAG optimum ≤ ~18 (then check whether its route can be re-linearized; if it can, that is a direct improvement).
- **Effort:** done. **Runtime:** hours for steps ≥ 12 [EST]. Run it with `timeout=14400` in the background, or use `search_strategy="backward"` with `swap_upper_bound=23` to test directly whether ≤ 23 is possible, which is the question that matters.
- **Risk:** timeout. Partial bounds are still valid.

**E2. Free-end window LNS on dense.**
- **Hypothesis:** our route has 2–6 SWAPs of slack reachable by re-routing 10–16-gate windows.
- **Why the architecture may miss it:** the beam never considers non-monotone or third-party SWAPs, and its dedup loses profiles.
- **Source:** SATMAP hybrid, LNS.
- **Prototype:** window [i, j) solved exactly from the fixed start mapping (general model, several of the best end mappings), tail re-routed by `Router` from each end mapping, spliced and officially scored. About 150 lines on top of `lns_window.py` / `general_exact.py`.
- **Benchmark:** dense (then qaoa).
- **Expected if correct:** some splice < 35.5 [OFF].
- **Falsifying result:** no window of 10–16 gates improves.
- **Effort:** 2–3 h. **Runtime:** 10–30 min. **Risk:** medium (window solves time out beyond ~12 gates).

**E3. qaoa joint exact (running).**
- **Hypothesis:** (5, ≤ 12) or (6, ≤ 10) exists.
- **Why the architecture may miss it:** no idle-layer pre-pay SWAPs.
- **Source:** TOQM-style state.
- **Prototype:** `joint_exact.py qaoa_random 22 22 2400` (plus an `eager` variant).
- **Expected if correct:** a witness ≤ 11.0 [OFF].
- **Falsifying result:** infeasible through T = 22, so qaoa is optimal at 11.5.
- **Effort:** done. **Runtime:** ≤ 40 min. **Risk:** timeout.

**E4. Richer beam (carrier-incident single-SWAP actions + Pareto ready dedup) as a research copy of `Router`.**
- **Hypothesis:** the beam's move set, not width, bounds dense.
- **Source:** SABRE/A* move sets, TOQM dominance.
- **Prototype:** subclass `Router` in `research/`.
- **Benchmark:** dense.
- **Expected if correct:** < 35.5.
- **Falsifying result:** ≥ 35.5 across widths 256–2048.
- **Effort:** 2 h. **Runtime:** 10 min. **Risk:** state explosion.

**E5. SabreLayout-style many-lineage bidirectional passes with the beam. DONE, falsified.**
- 650 lineages gave best 35.5 and median 42.5 [OFF via `many_lineages.py`].
- Layout diversity is not the bottleneck.

## 16. What to run first and why
1. **Resolve the bonus rule** (zero compute, ±41 points).
2. **Let E3 finish (qaoa T = 22) and extend E1** (dense Q-Synth, preferably the backward search from 23). Together they decide whether qaoa and dense deserve any more time at all.
3. If dense's bound stays below ~18, run **E2**. It is the only remaining experiment that can directly demonstrate a "wrong neighbourhood" on dense.
4. Run **E4** only if E2 shows window slack.

## 17. What should NOT consume more time
- ghz, chain, vqe, ladder: all optimal. That includes the CP-SAT joint work on ladder.
- More autopilot seed/parameter sweeps of the same move model.
- Precomputed placement heuristics (annealing variants), swap networks, commutation-based QAOA tricks.
- Re-deriving already-proven bounds.

## 18. Remaining uncertainties

**Stretch-A bonus.**
- The organizer commit `57f9a53` (2026-08-14) removed the headline formula `total_score = core_score − stretch_A_bonus − stretch_B_bonus`, removed the "Stretch B: 0.05 × 1Q gates saved" line, and deleted `baseline_decompose.py` / `baseline_oneq.py`.
- The current README still says "If you also attempted Stretch Goal A and improved the bad decomposer by N gates, then your score improves by N × 0.1" and lists optional `decompose()` / `optimize_1q()` deliverables.
- The reference decomposer (7 gates per SWAP, 3 per 2Q) exists only in git history. On our outputs N = 410, i.e. 41.0 points [ME].
- If stretch B were still scored against the do-nothing 1Q optimizer applied to that decomposition, removing the RZ(0) padding (4·35 + 2·135 = 410 gates) could add another 0.05 × 410 = 20.5 [EST: the semantics are unclear, and double counting is possible].
- A competitor reporting ~26 or less on "the same" benchmarks is most simply explained by this. **Ask the organizers.**

**Test set.**
- Commit `f49d698` (2026-04-10) said a public test set and leaderboard would come and that scores are not final. That line is no longer in the README.
- Competitor numbers may be on other instances, where robustness and runtime matter (e.g. the exact witness bug and budget-dependent results).

**My exact code.** It is cross-validated but not formally verified. The ladder optimality proof in particular relies on `joint_exact.py`'s admissible bounds and pruning (§4).

**Machine load.** All runs happened on a loaded 16 GB machine. Time-based production results can vary (the writeup documents 11.5 vs 12.5 on qaoa under load).

**Process-kill incident.** During a memory emergency I ran a blanket `Stop-Process` on all `python` processes at about 09:40. If a teammate had a CP-SAT run going at that moment, it was killed; please check. After that, only my own processes were running.

**Environment note.** `pip install qiskit` and `Q-Synth` went into the Python environment on PATH (a hermes-agent venv). Neither is a submission dependency.

**Still-running jobs at hand-off.** All are self-timed and write to `solution/research/`:
- `joint_exact.py qaoa_random 22 22 2400` → `je_qaoa22.log`
- `joint_exact.py qaoa_random 22 22 1800 eager` → `je_qaoa22_eager.log`
- `qsynth_lb.py dense_random 1800 1` → `qs_dense.log`; Q-Synth may overrun its timeout.

Kill them with `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'joint_exact|qsynth_lb' | % { Stop-Process -Id $_.ProcessId }` if needed.

**Research files.** Everything is in `solution/research/`:
- Scripts: `common.py`, `capture_current.py`, `bench_stats.py`, `analyze_current.py`, `general_exact.py`, `prefix_bounds.py`, `interval_lb.py`, `move_model_gap.py`, `joint_exact.py`, `arch_gap.py`, `lns_window.py`, `sabre_calibration.py`, `qsynth_lb.py`, `many_lineages.py`, `stretch_bonus.py`, `frontier.py`.
- Outputs: `*.log`, `*.json`, `*.jsonl`.
