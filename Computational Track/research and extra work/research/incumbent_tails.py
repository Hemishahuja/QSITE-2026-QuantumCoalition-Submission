"""Re-route the tail of the dense incumbent from a fixed prefix.

The free-end sweep left the right-aligned 12-gate window unfinished, and it
re-routed other tails at width 512. This runs the production Router from the
incumbent mapping and ready profile after selected gates, at high width, and
scores each splice with starter_kit.scorer.score_summary.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from prefix_tail import route_witness

# route_witness replays whatever route it is given and beams the rest.


def main() -> None:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1024
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120
    slack = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    root = HERE.parents[1]
    state = json.loads((root / "solution" / "autopilot_state.json").read_text())
    row = state["rows"]["dense_random"]
    placement = {str(k): int(v) for k, v in row["placement"].items()}
    routed = [list(op) for op in row["routed"]]
    # Cut after a number of 2Q gates.
    for gates in (16, 24, 28, 32, 36):
        kept = []
        seen = 0
        for op in routed:
            kept.append(op)
            if op[0] == "2Q":
                seen += 1
                if seen >= gates:
                    break
        witness = {"placement": placement, "routed": kept, "gates": gates}
        print(f"=== incumbent tail from gate {gates} width {width} slack {slack} ===", flush=True)
        route_witness(witness, width, seconds, slack, tag=f"incumbent-{gates}-w{width}-s{slack}")


if __name__ == "__main__":
    main()
