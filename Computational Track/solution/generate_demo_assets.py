"""Generate the static images used in the demo (DEMO_SCRIPT.md references these by path).

Run from the `Computational Track` directory:
    python solution/generate_demo_assets.py

Writes PNGs (and one GIF) into solution/demo_assets/. Scores are re-derived by calling
solve() and the official scorer. Lower bounds are the Sep 25 audited floors in
AUDITED_LOWER_BOUNDS. solve().lower_bound() still returns the weaker in-budget numbers
(ladder 6.0, qaoa 6.0, dense 9.5) and must not be drawn on the slides.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solution.solve import solve  # noqa: E402
from starter_kit.baseline_routing import solve as baseline_solve  # noqa: E402
from starter_kit.benchmarks import BENCHMARKS  # noqa: E402
from starter_kit.hardware import build_hardware_graph  # noqa: E402
from starter_kit.scorer import schedule_layers_ordered, score_summary  # noqa: E402
from starter_kit.visualize import animate_routing, draw_hardware  # noqa: E402

OUT = Path(__file__).with_name("demo_assets")
OUT.mkdir(exist_ok=True)

# Visually clean and provably optimal -- the best story for a live demo.
DEMO_BENCHMARK = "ghz_star"
# The instance where the remaining gap to the lower bound is largest.
HARD_BENCHMARK = "dense_random"

# Floors proved outside solve(), as of FOLLOWUP_FINDINGS.md (2026-09-25).
# Do not replace these with solve().lower_bound().
#   ghz / chain / vqe: score matches the critical-path (and hub) argument.
#   ladder 6.5: joint exact search, T=12 infeasible; 3 SWAPs / depth 7 is a witness.
#   qaoa 9.0: at least 5 SWAPs, depth at least 8. A log field "proven_score_ge: 11.0"
#     is a timeout artifact (the search died inside its first threshold), not a proof.
#   dense 17.0: at least 11 SWAPs from Q-Synth's DAG relaxation, plus depth at least 12.
#     That SWAP floor relies on Q-Synth's optimality claim.
AUDITED_LOWER_BOUNDS = {
    "ghz_star": 6.5,
    "chain_trotter": 4.5,
    "ladder_trotter": 6.5,
    "qaoa_random": 9.0,
    "dense_random": 17.0,
    "vqe_layers": 3.0,
}


def _swap_edges(routed: list[tuple]) -> list[tuple[int, int]]:
    return [tuple(op[1:]) for op in routed if op[0] == "SWAP"]


def collect(graph) -> list[dict]:
    rows = []
    for name, program in BENCHMARKS.items():
        bl_placement, bl_routed = baseline_solve(program, graph)
        bl = score_summary(program, graph, bl_placement, bl_routed)
        our_placement, our_routed = solve(program, graph, time_budget=20)
        our = score_summary(program, graph, our_placement, our_routed)
        lb = float(AUDITED_LOWER_BOUNDS[name])
        proven = our["valid"] and abs(our["score"] - lb) < 1e-9
        rows.append(
            {
                "name": name,
                "program": program,
                "bl_placement": bl_placement,
                "bl_routed": bl_routed,
                "bl": bl,
                "our_placement": our_placement,
                "our_routed": our_routed,
                "our": our,
                "lb": lb,
                "proven": proven,
            }
        )
        print(
            f"{name:<15} baseline {bl['score']:.1f}  ours {our['score']:.1f}  "
            f"swaps {our['swap_count']}  depth {our['depth']}  lb {lb:.1f}  "
            f"{'PROVEN' if proven else 'best found'}"
        )
    return rows


def _row(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["name"] == name)


def make_before_after(graph, row: dict) -> None:
    bl, our = row["bl"], row["our"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.2))
    draw_hardware(
        graph=graph,
        placement=row["bl_placement"],
        highlight_edges=_swap_edges(row["bl_routed"]),
        ax=axes[0],
        title=f"Baseline: {bl['swap_count']} SWAPs, depth {bl['depth']}, score {bl['score']:.1f}",
    )
    draw_hardware(
        graph=graph,
        placement=row["our_placement"],
        highlight_edges=_swap_edges(row["our_routed"]),
        ax=axes[1],
        title=f"Ours: {our['swap_count']} SWAPs, depth {our['depth']}, score {our['score']:.1f}",
    )
    fig.suptitle(f"{row['name']}: red edges are inserted SWAPs", fontsize=13, fontweight="bold")
    fig.tight_layout()
    out_path = OUT / f"before_after_{row['name']}.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}  (baseline {bl['score']:.1f} -> ours {our['score']:.1f})")


def make_layer_snapshots(graph, row: dict, max_layers: int = 4) -> None:
    """A few frames of our routing's layer-by-layer execution, for a 'how it runs' slide."""
    layers = schedule_layers_ordered(row["our_routed"])
    n = min(max_layers, len(layers))
    fig, axes = plt.subplots(1, n, figsize=(3.6 * n, 4.2))
    if n == 1:
        axes = [axes]
    for i in range(n):
        draw_hardware(
            graph=graph,
            highlight_edges=[tuple(op[1:]) for op in layers[i]],
            ax=axes[i],
            title=f"Layer {i + 1}",
        )
    fig.suptitle(
        f"{row['name']}: first {n} of {len(layers)} layers (SWAPs and gates run in parallel where possible)",
        fontsize=12,
    )
    fig.tight_layout()
    out_path = OUT / f"layers_{row['name']}.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def make_routing_gif(graph, row: dict) -> None:
    """Live-demo animation: logical labels move as SWAPs and gates execute."""
    anim = animate_routing(row["our_placement"], row["our_routed"], graph=graph, interval=800)
    out_path = OUT / f"routing_{row['name']}.gif"
    anim.save(out_path, writer="pillow", fps=1)
    plt.close(anim._fig)
    print(f"wrote {out_path}")


def make_results_chart(rows: list[dict]) -> None:
    names = [row["name"] for row in rows]
    baseline_scores = [row["bl"]["score"] for row in rows]
    our_scores = [row["our"]["score"] for row in rows]
    bounds = [row["lb"] for row in rows]

    x = range(len(names))
    width = 0.28
    fig, ax = plt.subplots(figsize=(9.5, 5))
    ax.bar([i - width for i in x], baseline_scores, width, label="Baseline (provided)", color="#94a3b8")
    ax.bar(x, bounds, width, label="Provable lower bound", color="#fbbf24")
    ax.bar([i + width for i in x], our_scores, width, label="Ours (official scorer)", color="#16a34a")
    for i, row in enumerate(rows):
        if row["proven"]:
            ax.text(i + width, row["our"]["score"] + 1.5, "proven", ha="center", va="bottom", fontsize=8, color="#166534")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, rotation=20, ha="right")
    ax.set_ylabel("score = SWAPs + 0.5 x depth  (lower is better)")
    ax.set_title(
        f"Total: baseline {sum(baseline_scores):.1f}  |  lower bound {sum(bounds):.1f}  |  ours {sum(our_scores):.1f}"
    )
    ax.legend()
    fig.text(
        0.5,
        0.01,
        "dense floor 17.0 = at least 11 SWAPs (DAG-relaxation SAT) + depth 12.   qaoa floor 9.0 = 5 SWAPs + depth 8.",
        ha="center",
        fontsize=8,
        color="#334155",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out_path = OUT / "results_chart.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def make_scoreboard(rows: list[dict]) -> None:
    """One slide a presenter can leave up while reading the numbers."""
    cell = []
    for row in rows:
        status = "proven optimal" if row["proven"] else "best found"
        cell.append(
            [
                row["name"],
                f"{row['bl']['score']:.1f}",
                f"{row['our']['score']:.1f}",
                str(row["our"]["swap_count"]),
                str(row["our"]["depth"]),
                f"{row['lb']:.1f}",
                status,
            ]
        )
    bl_total = sum(row["bl"]["score"] for row in rows)
    our_total = sum(row["our"]["score"] for row in rows)
    lb_total = sum(row["lb"] for row in rows)
    proven_n = sum(1 for row in rows if row["proven"])
    cell.append(["TOTAL", f"{bl_total:.1f}", f"{our_total:.1f}", "", "", f"{lb_total:.1f}", f"{proven_n} of {len(rows)} proven"])

    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.axis("off")
    table = ax.table(
        cellText=cell,
        colLabels=["benchmark", "baseline", "ours", "SWAPs", "depth", "lower bound", "status"],
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.45)
    for (r, c), cell_obj in table.get_celld().items():
        if r == 0:
            cell_obj.set_facecolor("#1e293b")
            cell_obj.set_text_props(color="white", fontweight="bold")
        elif r == len(cell):
            cell_obj.set_facecolor("#e2e8f0")
            cell_obj.set_text_props(fontweight="bold")
        elif cell[r - 1][-1] == "proven optimal":
            cell_obj.set_facecolor("#dcfce7")
    ax.set_title("Official scorer, 20s budget, default seed. Lower is better.", fontsize=12, pad=12)
    fig.text(
        0.5,
        0.04,
        "Ladder 6.5 is an exhaustive proof. qaoa floor is 9.0 (5 SWAPs + depth 8). "
        "dense floor is 17.0 (at least 11 SWAPs in a relaxed model, plus depth 12). 35.5 is best found.",
        ha="center",
        fontsize=8,
        color="#334155",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    out_path = OUT / "scoreboard.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def main() -> None:
    graph = build_hardware_graph()
    rows = collect(graph)
    make_before_after(graph, _row(rows, DEMO_BENCHMARK))
    make_layer_snapshots(graph, _row(rows, DEMO_BENCHMARK))
    make_routing_gif(graph, _row(rows, DEMO_BENCHMARK))
    make_before_after(graph, _row(rows, HARD_BENCHMARK))
    make_results_chart(rows)
    make_scoreboard(rows)


if __name__ == "__main__":
    main()
