"""Autopilot: repeatedly search for a better routing than the current best-known
solution, and commit improvements automatically. Safe to run unattended for hours:

- Never trusts a result that doesn't pass the official validator (`run_bench` already
  scores everything with `starter_kit.scorer`).
- Only ever keeps/commits a STRICT improvement over the best score seen so far for that
  benchmark; a bad or unlucky run is simply discarded, never regresses the record.
- Every improvement is written to `autopilot_state.json` and committed immediately, so an
  interrupted run never loses progress.
- Periodically A/B tests alternate `DEFAULT_PARAMS` profiles across all benchmarks and, if
  one is a clear, valid, all-around win, records it as a recommendation (it does not edit
  solve.py itself -- that's a deliberate human/agent decision, not something this
  unattended loop should do on its own).

Usage (run from the `Computational Track` directory):
    python solution/autopilot.py                       # run until interrupted (Ctrl+C)
    python solution/autopilot.py --duration 480         # run for 480 minutes then stop
    python solution/autopilot.py --once                 # single pass, useful for testing
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import solution.solve  # noqa: E402
from solution.bench import run_bench  # noqa: E402

# `solution/__init__.py` does `from .solve import solve`, which rebinds the *attribute*
# `solution.solve` to that function, shadowing the submodule. Pull the real submodule
# straight out of sys.modules so we can safely read/patch its DEFAULT_PARAMS dict.
solve_module = sys.modules["solution.solve"]

STATE_PATH = Path(__file__).with_name("autopilot_state.json")
LOG_PATH = Path(__file__).with_name("autopilot_log.txt")

# Alternate DEFAULT_PARAMS profiles to A/B test against the current defaults.
# Empty dict == current defaults (always included as the baseline to compare against).
PARAM_PROFILES: list[dict] = [
    {},
    {"window": 8, "decay": 0.85},
    {"window": 20, "decay": 0.6},
    {"new_opts": 6, "pair_opts": 24, "route_cap": 40},
    {"alpha": 0.5},
    {"alpha": 1.5},
    {"slack": 2},
    {"paths": 3},
]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"best_total": None, "rows": {}, "history": [], "param_recommendation": None}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip()


def commit_if_changed(message: str) -> bool:
    rel = str(STATE_PATH.relative_to(ROOT))
    git("add", rel)
    status = git("status", "--porcelain", "--", rel)
    if not status:
        return False
    git("commit", "-m", message)
    return True


def current_total(state: dict, names: list[str] | None = None) -> float:
    if names is None:
        return sum(r["score"] for r in state["rows"].values())
    return sum(r["score"] for name, r in state["rows"].items() if name in names)


def merge_best(state: dict, result: dict, budget: float, seed: int, tag: str) -> tuple[bool, list[str]]:
    """Update state["rows"] with any per-benchmark strict improvement."""
    improved = False
    notes = []
    for row in result["rows"]:
        name = row["name"]
        if not row["valid"]:
            continue
        prev = state["rows"].get(name)
        if prev is None or row["score"] < prev["score"] - 1e-9:
            state["rows"][name] = {
                "score": row["score"],
                "swaps": row["swaps"],
                "depth": row["depth"],
                "lower_bound": row["lower_bound"],
                "method": row["method"],
                "budget": budget,
                "seed": seed,
                "tag": tag,
                "placement": row["placement"],
                "routed": row["routed"],
            }
            improved = True
            delta = "" if prev is None else f" (was {prev['score']:.1f})"
            notes.append(f"{name}: {row['score']:.1f}{delta}")
    return improved, notes


def run_pass(budget: float, seed: int, tag: str, state: dict, names: list[str] | None = None) -> None:
    # Scoped to `names` when given, so a partial-scope subtotal never gets compared against
    # the full six-benchmark total (that comparison would be apples to oranges).
    before = current_total(state, names) if state["rows"] else None
    # lb_budget is kept tiny here: the lower bound doesn't change run to run, so we don't
    # want to re-spend the whole iteration budget re-proving it every single pass.
    result = run_bench(budget=budget, seed=seed, quiet=True, lb_budget=1.0, names=names)
    if not result["all_valid"]:
        log(f"[{tag}] seed={seed} budget={budget:.1f}s produced an INVALID result -- discarded")
        return
    improved, notes = merge_best(state, result, budget, seed, tag)
    if improved:
        after = current_total(state, names)
        state["history"].append(
            {
                "time": time.time(),
                "tag": tag,
                "seed": seed,
                "budget": budget,
                "total_before": before,
                "total_after": after,
            }
        )
        save_state(state)
        before_str = f"{before:.1f}" if before is not None else "n/a"
        msg = f"autopilot: total {before_str} -> {after:.1f} [{tag} seed={seed} budget={budget:.1f}s] " + "; ".join(notes)
        committed = commit_if_changed(msg)
        log(msg + (" (committed)" if committed else " (no diff to commit)"))
    else:
        base = f"{before:.1f}" if before is not None else "n/a"
        log(f"[{tag}] seed={seed} budget={budget:.1f}s: total {result['total']:.1f}, no improvement over {base}")


def try_param_profiles(budget: float, state: dict) -> None:
    """A/B test DEFAULT_PARAMS profiles. Only ever *recommends* a change; never edits solve.py."""
    baseline_params = dict(solve_module.DEFAULT_PARAMS)
    totals: dict[str, tuple[float, bool]] = {}
    for i, profile in enumerate(PARAM_PROFILES):
        solve_module.DEFAULT_PARAMS.clear()
        solve_module.DEFAULT_PARAMS.update(baseline_params)
        solve_module.DEFAULT_PARAMS.update(profile)
        try:
            result = run_bench(budget=budget, seed=1234, quiet=True, lb_budget=0.5)
        finally:
            solve_module.DEFAULT_PARAMS.clear()
            solve_module.DEFAULT_PARAMS.update(baseline_params)
        totals[json.dumps(profile, sort_keys=True)] = (result["total"], result["all_valid"])
        log(f"[params] profile {i} {profile or '(baseline)'}: total={result['total']:.1f} valid={result['all_valid']}")

    valid_totals = {k: v[0] for k, v in totals.items() if v[1]}
    if not valid_totals:
        return
    baseline_key = json.dumps({}, sort_keys=True)
    baseline_total = valid_totals.get(baseline_key, math.inf)
    best_key = min(valid_totals, key=valid_totals.get)
    if best_key != baseline_key and valid_totals[best_key] < baseline_total - 1e-9:
        profile = json.loads(best_key)
        state["param_recommendation"] = {
            "profile": profile,
            "total": valid_totals[best_key],
            "baseline_total": baseline_total,
            "time": time.time(),
        }
        save_state(state)
        log(
            f"[params] RECOMMENDATION: DEFAULT_PARAMS update {profile} looks better "
            f"(total {baseline_total:.1f} -> {valid_totals[best_key]:.1f} at budget={budget:.1f}s). "
            "Not applied automatically -- review and apply by hand if it holds up."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration", type=float, default=None, help="total run time in minutes (default: run until interrupted)")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--min-budget", type=float, default=8.0)
    parser.add_argument("--max-budget", type=float, default=45.0)
    parser.add_argument(
        "--param-search-every", type=int, default=5, help="run a DEFAULT_PARAMS A/B pass every N iterations (0 to disable)"
    )
    parser.add_argument(
        "--names", nargs="*", default=None,
        help="only search these benchmarks (skip the rest entirely -- use this once some benchmarks are at their proven lower bound)",
    )
    args = parser.parse_args()

    state = load_state()
    log(
        f"Resuming; current best total = {current_total(state, args.names):.1f}"
        + (f" (scoped subtotal for {args.names})" if args.names else "")
        if state["rows"]
        else "Starting fresh (no prior state found)"
    )
    if args.names:
        log(f"Scoped to: {args.names} (not touching the rest -- they're assumed settled)")

    deadline = time.time() + args.duration * 60 if args.duration else math.inf
    rng = random.Random()
    iteration = 0
    try:
        while time.time() < deadline:
            iteration += 1
            budget = rng.uniform(args.min_budget, args.max_budget)
            seed = rng.randrange(1_000_000)
            run_pass(budget, seed, f"iter{iteration}", state, names=args.names)
            if args.param_search_every and iteration % args.param_search_every == 0 and not args.names:
                try_param_profiles(budget=min(args.max_budget, 20.0), state=state)
            if args.once:
                break
    except KeyboardInterrupt:
        log("Interrupted by user")
    final = f"{current_total(state, args.names):.1f}" if state["rows"] else "n/a"
    log(f"Stopping after {iteration} iteration(s). Final best total = {final}")


if __name__ == "__main__":
    main()
