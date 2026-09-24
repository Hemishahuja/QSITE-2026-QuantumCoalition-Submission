# Demo script — Computational Track

Spoken-word script for a live 3–5 minute demo. Stage directions are in brackets. Everything else is meant to be said out loud.

Numbers below were reconfirmed on Sep 23, 2026:

- `python solution/verify_official.py --budget 20` — official `starter_kit.scorer`, default seed, 20s budget. Total **67.5**.
- `python solution/generate_demo_assets.py` — same `solve()` plus `lower_bound()` (25s). Lower bounds: ghz 6.5, chain 4.5, ladder 6.0, qaoa 6.0, dense 9.5, vqe 3.0. Sum of bounds **35.5**.

Reduction: (283.5 − 67.5) / 283.5 = 76.2%, stated as **76%**.

Assets live in `solution/demo_assets/`. Regenerate with `python solution/generate_demo_assets.py` from `Computational Track/`.

---

## 0:00–0:30 — The problem

[No slide yet, or the bare hardware graph if you already have the notebook open. Do not linger.]

You are compiling a quantum program onto a chip that is not fully connected. Twenty qubits. Each one is wired to one, two, or three neighbors. A two-qubit gate can only run across a wire that actually exists.

If the two qubits are not neighbors, you insert SWAPs and walk them together. Every SWAP costs one point. Every parallel time step costs half a point. Six programs, one total. Lower wins.

## 0:30–1:35 — What we did

[Still talking. Optional: glance at `results_chart.png` only if a judge looks lost. Do not read the chart yet.]

Three ideas.

First: sometimes you need zero SWAPs. This chip has a path through all twenty qubits. A chain, or a brick-layer circuit like VQE, sits directly on that path. Two of the six benchmarks finish with no SWAPs at all. The score is just half the circuit's own critical-path depth, and that is a floor on any hardware.

Second: when that embedding does not exist, we route with a beam search on the exact score. For each gate that is not adjacent, the two qubits can meet in a few ways. One walks the whole path, or they close in from both sides. Every partial route knows when each qubit is free, so the cost so far is the real score, not a guess. We keep the best few and look ahead at the gates still coming. Placement is lazy. A logical qubit gets a physical home the first time it is used. On the dense programs, locking the placement up front was worse.

Third: on the smaller instances we also run branch and bound. That proves a minimum SWAP count. A separate counting argument lower-bounds the full score. When our score hits that bound, the result is optimal. Not best-found. Optimal.

## 1:35–3:25 — The results

[Put up `scoreboard.png`. Leave it up through the first paragraph.]

This table is the official scorer. Twenty-second budget. Default seed. Baseline total, 283.5. Ours, 67.5. That is a 76 percent reduction. Same function on every benchmark. No special cases.

Three rows are green because the score equals the lower bound.

[Switch to `before_after_ghz_star.png`.]

ghz_star. Baseline 14: seven SWAPs, depth 14. Ours, 6.5: two SWAPs, depth 9. Red edges are the SWAPs. The hub has seven leaves and the chip's max degree is three, so it cannot sit next to everyone. Two SWAPs is what that argument requires, and the score lands on 6.5 exactly. That is the proof.

[If you have ten seconds, play `routing_ghz_star.gif`. Ten frames, one per second: initial placement, then each SWAP and gate. Skip it if you are behind.]

chain_trotter is 4.5, zero SWAPs, proven. vqe_layers is 3.0, zero SWAPs, proven. The baseline on vqe was 58.

The other three we will not call optimal.

ladder_trotter is 6.5. Three SWAPs, depth 7. We proved you cannot use fewer than three SWAPs. We have not proved the combined score is minimal. A route with four or five SWAPs and a shorter depth could still beat 6.5. The lower bound is 6.0. We are half a point above it.

qaoa_random is 11.5, against a baseline of 39 and a lower bound of 6. Best found.

[Switch to `before_after_dense_random.png`.]

dense_random is the hard one. Forty interactions on fourteen qubits. The baseline inserts 90 SWAPs at depth 64. Score 122. We got 24 SWAPs, depth 23, score 35.5. The lower bound is only 9.5. That gap is real. We are near the ceiling of this search. The next lever is more time on the same lazy routing, not another placement heuristic.

## 3:25–4:05 — Close

[Back to `scoreboard.png`, or stay on the dense panel if the clock is tight.]

So: 67.5 versus 283.5. Three benchmarks where the score matches a proof. Three where we show the best route we found, and the bound we could not close. Every answer is checked by the official scorer before we accept it.

Happy to take questions.

---

## If you are running long

Cut, in this order:

1. The GIF.
2. The beam-search paragraph (keep zero-SWAP and "optimal means we hit the bound").
3. The qaoa sentence. Do not cut the dense_random honesty or the ladder caveat.

## If you are running short

`layers_ghz_star.png` is the first four of nine layers on the same optimal routing, if a judge wants to see parallelism and you did not play the GIF.

---

## Q&A (not part of the timed script)

Have these ready. About a minute total if a judge asks one.

**"Is dense_random optimal?"**

No. 35.5 against a lower bound of 9.5. Twenty-four SWAPs, depth 23, baseline was 122. A diagnostic sweep said lazy placement beats every precomputed placement we tried, by a wide margin, so we do not think a cleverer initial layout is the missing piece. More time on the same search might still help. We have not proved it will. "Best found" is the claim.

**"Will the grader get 67.5?"**

With the default seed and the 20-second budget, yes, that is what the official scorer returned on the runs we checked today, including the one for this demo. It is an anytime search. Under a heavy machine it can stop on an earlier checkpoint. We saw qaoa_random come back 12.5 instead of 11.5 once, before we raised the budget from 10 seconds to 20. A different seed can land a point or two worse on the hard benchmarks. It will not come back invalid, and it will not go below the proven floors.

**"Did you do the stretch goals, decomposition and single-qubit optimization?"**

No. The bonus is a tenth of a point per gate saved. The routing gap on qaoa and dense_random is larger than that, so we spent the time there.

**If they push on ladder_trotter specifically:**

SWAP count 3 is proven minimal. The score 6.5 is not. Lower bound on the combined objective is 6.0.
