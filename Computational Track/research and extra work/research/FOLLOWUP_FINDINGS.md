# Follow-up findings (2026-09-25, after STRATEGY_AUDIT.md)

Labels as in `STRATEGY_AUDIT.md`: **[OFF]** = real `starter_kit.scorer.score_summary`; **[ME]** = my research code (witnesses re-scored [OFF]); **[EST]** = judgment. Nothing outside `solution/research/` was touched; no commits.

## 1. Task 1: correctness review of `joint_exact.py`

**Verdict:**
- The **ladder_trotter = 6.5 optimal** claim is trustworthy.
- The **"qaoa_random ≥ 11.0" claim is NOT established.** It is an artifact of how the log line reads.

**Why ladder = 6.5 holds** (traced in the code, then checked independently):
- **Move model is fully general.** At every point between two program gates, `_dfs` tries every one of the 23 edges as a SWAP. It skips only:
  - an immediate undo of the previous SWAP, which returns to the same mapping with +1 on both wires and +2 SWAPs, so it is strictly dominated;
  - a SWAP of two positions holding no already-placed qubit. Removing it leaves every placed position the same and ready times pointwise ≤, and lazy placement can still put the later qubit anywhere empty.

  In non-eager mode (used for ladder), SWAPs are also tried while the front gate is already adjacent.
- **Validity matches `translate_back_to_logical` / `validate_routed_program`.**
  - Gates run strictly in program order and only on adjacent positions.
  - Operand order is preserved: `("2Q", pos[a], pos[b])`.
  - SWAPs may touch unoccupied qubits.
  - The benchmarks have no 1Q ops.
- **Depth equals `schedule_layers_ordered`.** `ready[]` is per physical wire. Each SWAP or gate sets both wires to max + 1, SWAPs count, and depth is `max(ready)`. That is exactly the scorer's per-wire ASAP rule.
- **Lazy placement plus witness conversion is correct.**
  - Placing a new qubit at an empty position u is recorded as initial position `p2t[u]`, the token origin. This is not the production bug (current position).
  - The placement is therefore injective.
  - The `witness_official` field is a real `score_summary` call (`common.official`) on the replayed routed program.
- **The bound is admissible.**
  - `swap_lb`: the front-gate distance minus 1 plus the suffix interval bound, or ⌈Φ/2⌉ for vertex-disjoint future pairs plus the suffix bound. The SWAP time-spans counted are disjoint.
  - `depth_lb`: a logical ASAP chain from current wire ready times, plus the front gate's d − 1 SWAPs split optimally over the a and b chains.

  I checked `interval_lb.py`'s interval semantics too: each interval covers "strictly after gate i−1 … up to gate j−1", so the intervals are disjoint. The ladder proof uses no interval table anyway, because no `interval_lb_ladder_trotter.json` exists.
- **The memo is sound.** It stores failures under key (gate, positions, ready profile) with (s, T), and prunes only if s ≥ s_prev and T ≤ T_prev. The key omits `last`, which is the classic graph-history-interaction risk. I checked it with a max-slack / earliest-finishing argument: a wrongly failed visit whose optimal continuation starts with the forbidden undo implies its parent failed wrongly with ≥ 4 more slack. It is sound.
- **Independent cross-check [ME: `xcheck_joint.py`].**
  - I brute-forced all placements × all SWAP insertions (≤ 3–4 SWAPs, including undo and empty-empty moves) on **65 random small graphs** (5–7 nodes, 3–5 qubits, 4–7 gates). I compared against `joint_exact.joint_search` (the original code, graph globals monkeypatched) and my `je_por.py`.
  - Result: **0 discrepancies**. Every witness was valid under `score_summary` on the small graph and matched the brute-force optimum.
  - Logs: `xcheck_joint.log`, `xcheck_joint_hard.log`, `xcheck_joint_results.jsonl`.
- **The ladder log really completed T = 12.** `je_ladder.log` contains `T=12 (score 6.0): infeasible (725.1s)`, and infeasibility at T covers every T′ < T. `je_ladder13.log` has a T = 13 witness [OFF: valid, 3 SWAPs, depth 7, 6.5].

**Why qaoa ≥ 11.0 is not established:**
- `joint_search` initializes `proven = T_start - 1` and returns it unchanged on `Timeout`.
- Both `je_qaoa22.log` and `je_qaoa22_eager.log` started at T_start = 22 and **timed out inside T = 22** before finishing it. Neither log has a `T=22: …` line.
- So `"proven_f_gt": 21, "proven_score_ge": 11.0` is just 22 − 1, **not a proof**. (The eager run is also incomplete for depth in any case.)
- The audit's own §9.4 already said "undecided", so the audit was right and the fresh-info summary misread the JSON.
- **The proven qaoa floor is still 9.0** (≥ 5 SWAPs [ME: `general_exact.py`] and depth ≥ 8). The headroom is ≤ 2.5, not ≤ 0.5.
- **Do not cite "qaoa ≥ 11.0" in the writeup.**

**Bugs found in `joint_exact.py`:** none that affect correctness. One small reporting pitfall: a run that times out in its first threshold reports `proven_f_gt = T_start − 1`, which looks like a result. My new `je_por.py` reports `"timeout"` explicitly instead.

**Correction on the Q-Synth log (Task 3 input).** `qs_dense.log` shows `Finished step 10 … Result: False` (2613 s), *then* `Timeout reached`, *then* the crash in Q-Synth's result printer. Steps 0–10 are all UNSAT, so **dense ≥ 11 SWAPs** (score floor 11 + 12/2 = 17.0). That is the audit's number, not "≥ 10". It relies on Q-Synth's optimality claim [LIT: arXiv:2403.11598].

## 2. Task 2: qaoa_random 11.0 vs 11.5

**Still genuinely unresolved.**

- **New tool: `je_por.py`.** It uses the same model and bound as `joint_exact.py`, plus:
  - trace-normal-form partial-order reduction (disjoint SWAPs in increasing edge order; a gate may not run right after a SWAP that is disjoint from its wires);
  - gate 0 placed only on adjacent pairs, one per hardware-automorphism orbit (23 roots, 2 automorphisms);
  - the last action included in the memo key, so there is no graph-history-interaction issue;
  - per-root parallel split and resume.

  It passed the 65-instance brute-force cross-check. It reproduced ghz: T = 12 proven infeasible over all 23 roots in 10 s; T = 13 feasible [OFF 6.5].
- **The T = 22 run** (3 workers, 1200 s per root) died of `MemoryError` after about 8 minutes, with **no root decided**.
- **T = 21** would genuinely prove ≥ 11.0, so I relaunched on that (2 workers, memo cap 150k). It also died of `MemoryError` with no root decided.
- **Cause.** The machine's available RAM swung between 0.7 and 2 GB (browser, IDE and a teammate's `verify_official.py` run). This is environmental, not a logic failure.
- **To retry on a quieter machine** (resumable per root; the output file keeps decided roots):
  - `python -u je_por.py qaoa_random 21 1500 2 _a 150000` → proves score ≥ 11.0 if all 23 roots come back infeasible.
  - `python -u je_por.py qaoa_random 22 2400 2 _b 150000` → proves 11.5 optimal if all are infeasible. Any `feasible` row carries an [OFF]-scored 11.0 witness.
- **[EST]** Each root needs more than 8 CPU-minutes at T = 21–22. Budget several CPU-hours for a full decision.

## 3. Task 3: Q-Synth crash and the dense SWAP lower bound

- **Crash fixed** in the new wrapper `qsynth_resume.py`; I did not edit `qsynth_lb.py`.
  - It patches Q-Synth's forward search to start at a given step (the low-level `layout_synthesis(start=…)`).
  - It replaces the result printer with one that logs `Q-Synth returned no result` instead of crashing on `None`.
  - Smoke test on qaoa from step 4: step 4 UNSAT, step 5 found, `dag_optimal_swaps: 5`. This matches the earlier full run.
- **Dense run resumed at step 11** (timeout 10800 s, started ~12:25, log `qs_dense_resume.log`). After roughly 70 minutes inside step 11, **the process died silently**: no traceback and no result line. The exit code was 3221226505 = 0xC0000409, a native fail-fast abort inside the SAT solver, not a Python error. The same machine-wide memory starvation killed my other jobs (§2, §4), and the native SAT solver most likely failed an allocation [EST]. Steps 8–10 took 270 s, 106 s and 2613 s, so step 11 plausibly needs hours on a quiet machine [EST].
- **The tightest proven dense bound is unchanged: ≥ 11 SWAPs** (score ≥ 17.0, via the DAG relaxation).
- **Relaunch** (skips steps 0–10):
  `$env:PYTHONIOENCODING="utf-8"; python -u qsynth_resume.py dense_random 11 10800 1 *> qs_dense_resume.log`
  `Finished step 11 … Result: False` would mean dense ≥ 12 SWAPs.

## 4. Task 4: free-end-mapping window LNS on dense_random (audit E2)

**No splice beat 35.5.** Every candidate was scored [OFF]; best splice 35.5.

**Script:** `lns_free.py`. Results are in `lns_free_dense_random.jsonl`; logs are `lns_free_dense*.log`. The method per window:
1. **Start** from the current route's exact mapping and per-wire ready profile after gate i − 1. Source: `current_solutions.json`, [OFF] 24 SWAPs / depth 23 / 35.5.
2. **Enumerate end mappings** by iterative deepening on the window SWAP budget B = 0 … w_orig (w_orig = the SWAPs the current route spends inside the window).
   - Any SWAP on any edge is allowed.
   - Gates execute when adjacent. That does not change which end mappings a given SWAP sequence reaches.
   - A memo on (gate, mapping, ready profile) removes duplicates.
   - The **end mapping is free**. For each end mapping I keep the best window route by (swaps + ½·max ready, sum ready).
3. **Re-route the tail** with the production `Router` (read-only import from `solution.solve`), starting from the true mapping *and* ready profile:
   - width 8 for every end state;
   - width 512 for the 6 best.
4. **Splice** prefix + window + tail and score with `score_summary`.

**Sweep (final):**
- **44 windows**:
  - K = 6 at starts 0, 3, …, 33;
  - K = 8 and K = 10 at starts 0, 3, …, 30;
  - K = 12 at starts 0, 3, …, 21;
  - K = 14 at starts 0 and 6.
- **2,926 distinct end states** re-routed and officially scored.
- About 9.5 CPU-minutes of window work, in about 45 minutes of wall-clock time.
- In **33 of 44 windows**, every budget level up to w_orig was exhausted, so the free-end enumeration is complete there. For the other 11 windows (K = 6–12 windows with 8–14 SWAPs), only budgets ≤ 7–8 were exhausted within the 40 s per window. In one of them (K = 12 at gate 21, 14 SWAPs), no end state was found.
- **Not covered:** the right-aligned K = 12 window (28–40), K = 12 at starts 24–27, most of K = 14, and all of K = 16. Both sweep stages died of `MemoryError` at those points (environmental: available RAM fell to 0.7 GB). Resume with `python -u lns_free.py dense_random 12,14,16 3 40 1 0 6 512`, which skips windows already in the jsonl.

**What the sweep shows:**
- **No window beats 35.5.** Three windows reach 35.5 with a different point (25 SWAPs, depth 21).
- **12 of 44 windows can be routed with 1–3 fewer SWAPs than the current route spends there**, from the same start and with a free end. Examples:
  - gates 15–23: 4 SWAPs instead of 7;
  - gates 21–31: 8 SWAPs instead of 11;
  - gates 12–24: 8 SWAPs instead of 10.

  But **every end mapping reached with fewer SWAPs leads to a tail that costs more** than the saving. For example, window 21–31 at 8 SWAPs gives a best splice of 36.0. The production route deliberately pre-pays SWAPs for later gates, and those trade-offs hold up.
- **In the other windows, the free-end window minimum equals what the route spends**, so the route is locally SWAP-optimal even with the end mapping free.

(Gate ranges above are half-open, [i, i + K).)

**Honest read [EST].** This technique, at windows of 6–12 gates (plus two K = 14 windows) with the tail re-routed by the same beam, looks exhausted.
- Caveats:
  - K ≥ 10 windows could not be enumerated completely at the top budget level.
  - The tail is re-routed by the *same* beam, so a "wrong neighbourhood" inside the tail itself is not tested.
- Things a larger sweep could still add: complete K = 12–16 enumeration (minutes per window); `extra = 1`, which allows one more window SWAP than the route; or two chained windows.
- I would not expect a cheap win: 2.9k officially scored splices, including window routes 3 SWAPs cheaper, never went below 35.5.
- **No `solve.py` change is justified by this evidence.**

## 5. Recommendation

**Stop production-code work on qaoa and dense routing.**
- qaoa's remaining question (11.0 vs 11.5) is worth at most 0.5 points and needs several CPU-hours of exact search on a quiet machine. Run the commands in §2 overnight if the proof itself is wanted.
- dense resisted free-end LNS, 650 layout lineages and the seed sweeps. The only live dense lever would be a different paradigm (segment embeddings plus token swapping, or a CP-SAT allocation model), and that is a multi-hour bet with no evidence of payoff.
- **Put the remaining time into:**
  - the writeup and demo;
  - the stretch-bonus ruling (±41 points, `STRATEGY_AUDIT.md` §18).
- **In the writeup:**
  - "ladder_trotter proven optimal (computer-assisted exact search, cross-validated against brute force on 65 instances)" is fair to cite.
  - "qaoa ≥ 11.0" is **not** proven; cite qaoa ≥ 9.0.
  - Cite dense ≥ 11 SWAPs as resting on Q-Synth's DAG relaxation.

## 6. Running at hand-off: nothing

All of my jobs have exited: the qaoa T = 22 and T = 21 searches, both LNS stages, and Q-Synth step 11. The first four died of `MemoryError`; Q-Synth died silently. **No python process of mine is running.** I only ever stopped processes by specific PID: the mislaunched T = 21 run (PIDs 19432 and 8832 plus its pool children), which I stopped myself and relaunched. A teammate's `solution/verify_official.py` (PIDs 3696/29812) was running during my session. I never touched it, and it had exited by the end.

All three long jobs are resumable. Run them one at a time on a machine with at least 3 GB free, with `$env:OPENBLAS_NUM_THREADS="1"`:
```powershell
cd "Computational Track\solution\research"
$env:OPENBLAS_NUM_THREADS="1"; $env:PYTHONIOENCODING="utf-8"
python -u qsynth_resume.py dense_random 11 10800 1 *> qs_dense_resume.log   # dense >= 12 SWAPs?
python -u je_por.py qaoa_random 21 1500 2 _a 150000 *> je_por_qaoa21.log    # qaoa >= 11.0?
python -u lns_free.py dense_random 12,14,16 3 40 1 0 6 512 *> lns_free_dense4.log  # finish E2 sweep
```
To stop one later, target only its own PIDs:
`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -match 'qsynth_resume|je_por|lns_free'`
lists the parents. Stop their `--multiprocessing-fork` children (matching `ParentProcessId`) and then the parents. Never use a blanket `Stop-Process -Name python`.

## New files (all in `solution/research/`)
- `qsynth_resume.py`: Q-Synth forward search from a given step, with the `None`-result crash fixed.
- `je_por.py`: joint exact search with partial-order reduction, root split, automorphism reduction and resume.
- `xcheck_joint.py`: brute-force cross-validation of `joint_exact.py` and `je_por.py`.
- `lns_free.py`: free-end window LNS with production-`Router` tails and official scoring.
- Outputs: `xcheck_joint*.log`, `xcheck_joint_results.jsonl`, `je_por_ghz_star_T1{2,3}_test.jsonl`, `je_por_qaoa22.log`, `je_por_qaoa21.log/.err`, `qs_dense_resume.log`, `lns_free_dense*.log`, `lns_free_dense_random.jsonl`.
