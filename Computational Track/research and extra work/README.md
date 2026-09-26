# Research and extra work

This folder is not the submission. The solver that should be graded is `Computational Track/solution/solve.py` (`solve`, `decompose`, `optimize_1q`). Nothing in that package imports this folder.

What lives here:

- `research/` — audits, exact-search scripts, beam and LNS experiments, witnesses, and logs.
- `cpsat_joint.py` and `cpsat_solver.py` — optional CP-SAT searches. They import `solution.solve` and warm-start from `solution/autopilot_state.json`. Reported scores are only rows with `"accepted": true` and an `official` dict from `starter_kit.scorer`.
- `cpsat_joint_results.jsonl` — append-only record of those runs.
- `_diagnose_dense_random.py` — placement-versus-routing diagnostic cited in the writeup.
- `logs/` — CP-SAT run logs.

Run research scripts from `Computational Track` so `solution` and `starter_kit` import:

```bash
python "research and extra work/research/prefix_tail.py"
python "research and extra work/cpsat_joint.py" --benchmark qaoa_random --time-limit 60 --workers 2
```
