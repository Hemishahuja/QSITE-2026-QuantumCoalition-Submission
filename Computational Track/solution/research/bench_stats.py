"""Structural statistics of the 6 benchmarks and the hardware graph.

    python solution/research/bench_stats.py
"""

from __future__ import annotations

from collections import Counter

import networkx as nx

from common import BENCHMARKS, GRAPH, critical_path, gates_of

print("hardware: nodes", GRAPH.number_of_nodes(), "edges", GRAPH.number_of_edges(),
      "degrees", sorted(Counter(dict(GRAPH.degree()).values()).items()),
      "diameter", nx.diameter(GRAPH), "girth", min(len(c) for c in nx.minimum_cycle_basis(GRAPH)),
      "cycle basis lengths", sorted(len(c) for c in nx.minimum_cycle_basis(GRAPH)))
print("hardware automorphisms:",
      sum(1 for _ in nx.algorithms.isomorphism.GraphMatcher(GRAPH, GRAPH).isomorphisms_iter()))

for name, program in BENCHMARKS.items():
    g = gates_of(program)
    ig = nx.Graph()
    ig.add_edges_from(g)
    mult = Counter(tuple(sorted(e)) for e in g)
    degs = sorted((d for _, d in ig.degree()), reverse=True)
    try:
        girth = min(len(c) for c in nx.minimum_cycle_basis(ig))
    except ValueError:
        girth = None
    print(f"\n{name}: qubits={ig.number_of_nodes()} gates={len(g)} distinct_pairs={ig.number_of_edges()} "
          f"repeated_pairs={sum(1 for v in mult.values() if v > 1)} critical_path={critical_path(g)}")
    print(f"  interaction degree seq={degs} girth={girth} n_deg>3={sum(1 for d in degs if d > 3)}")
    print(f"  gates={g}")
    # how many consecutive gates share a qubit (sequentiality)
    share = sum(1 for x, y in zip(g, g[1:]) if set(x) & set(y))
    print(f"  consecutive gates sharing a qubit: {share}/{len(g)-1}")
