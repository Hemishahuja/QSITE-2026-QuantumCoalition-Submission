commit 57f9a537d24c69328dedf915ad58d1d2bf505135
Author: Ben McDonough <benmcdonough20@gmail.com>
Date:   Fri Aug 14 08:20:39 2026 -0600

    Removed J_1 coupling in scientific track

diff --git a/Computational Track/README.md b/Computational Track/README.md
index 2695a4c..db7d576 100644
--- a/Computational Track/README.md	
+++ b/Computational Track/README.md	
@@ -80,18 +80,9 @@ Executable Circuit
 ## How You're Scored
 
 ```
-total_score = core_score - stretch_A_bonus - stretch_B_bonus      (lower is better)
+score = Σ_benchmarks [ swap_count + 0.5 × depth ]
 ```
 
-**Core score** (required):
-```
-core_score = Σ_benchmarks [ swap_count + 0.5 × depth ]
-```
-
-**Stretch bonuses** (optional, subtracted as rewards):
-- Stretch A: `0.1 × (gates saved vs. bad decomposer)`
-- Stretch B: `0.05 × (1Q gates saved vs. bad 1Q optimizer)`
-
 ## Essential Resources
 
 - [PostQuantum: Routing Quantum Information](https://postquantum.com/quantum-computing/routing-quantum-information/) - visual intro to SWAP routing
@@ -290,113 +281,8 @@ def solve(program, hardware_graph):
     """
 ```
 
----
-
-# Stretch Goals - For Extra Points
-
-## Stretch Goal A: Gate Decomposition
-
-### Introduction
-
-Real quantum hardware can only execute a small set of **native gates** - think of them like the assembly instructions of a CPU. For this challenge, the native set is `{RZ, SX, CNOT}`:
-
-- `RZ(θ)` - a single-qubit rotation (one parameter)
-- `SX` - a fixed single-qubit gate (no parameters)
-- `CNOT` - a two-qubit gate (the only native two-qubit operation)
-
-Every other gate (Hadamard, SWAP, Toffoli, etc.) must be **decomposed** into sequences of these three gates.
-
-### Why the Bad Baseline Is Bad
-
-The provided `baseline_decompose.py` wraps every operation with pointless `RZ(0.0)` identity rotations (a zero-angle rotation does nothing):
-
-```
-SWAP gate:
-  Good decomposition:  CNOT(a,b) → CNOT(b,a) → CNOT(a,b)                                       [3 gates]
-  Bad baseline:        RZ(0) → CNOT(a,b) → RZ(0) → CNOT(b,a) → RZ(0) → CNOT(a,b) → RZ(0)     [7 gates]
-
-2Q gate (treated as CNOT):
-  Good decomposition:  CNOT(a,b)                                                                 [1 gate]
-  Bad baseline:        RZ(0) → CNOT(a,b) → RZ(0)                                                [3 gates]
-
-1Q gate:
-  Good decomposition:  remove entirely (if identity) or fuse with neighbors                      [≤1 gate]
-  Bad baseline:        RZ(0) → SX → RZ(0)                                                       [3 gates]
-```
-
-Every `RZ(0.0)` is a no-op and can be eliminated. On circuits with many gates, these add up quickly.
-
-### How to Beat It
-
-**Easy (use existing tools)**: PennyLane's `qml.transforms.decompose(gate_set={"RZ", "SX", "CNOT"})` or `qml.compile(basis_set=["CNOT", "RX", "RY", "RZ"])` will massively improve over the bad baseline with minimal code.
-
-See the [PennyLane Circuit Compilation demo](https://pennylane.ai/qml/demos/tutorial_circuit_compilation) for a full walkthrough.
-
-**Harder (custom optimization)**: choose decompositions that create opportunities for cancellation with neighboring gates. This is context-aware instruction selection - a real compiler optimization problem.
-
-## Stretch Goal B: Single-Qubit Optimization
-
-### What It Is
-
-After decomposition, the circuit often has long chains of single-qubit gates on the same qubit that could be simplified:
-
-```
-Before optimization:  RZ(0.3) → RZ(0.5) → RZ(0.2) → SX → SX → RZ(0.0)
-After optimization:   RZ(1.0) → X                                        
-```
-
-### The Patterns to Look For
-
-| Pattern | Rule | Example |
-|---|---|---|
-| **Same-axis merge** | `RZ(a) · RZ(b) = RZ(a+b)` | `RZ(0.3) · RZ(0.5)` → `RZ(0.8)` |
-| **Self-inverse cancel** | `H · H = I`, `X · X = I`, `SX · SX = X` | Two adjacent Hadamards → remove both |
-| **Zero rotation eliminate** | `RZ(0) = I` | `RZ(0.0)` → remove entirely |
-| **Full rotation eliminate** | `RZ(2π) = I` (up to global phase) | `RZ(6.283...)` → remove |
-
-For teams who know some linear algebra: **any sequence of single-qubit gates on the same qubit can be fused into at most 3 rotations** using the ZYZ decomposition. PennyLane provides `qml.transforms.single_qubit_fusion` for this.
-
-### How to Beat It
-
-The bad baseline literally does nothing (`return circuit`). **Any** simplification gets you bonus points:
-
-- **Easy**: merge adjacent same-axis rotations, eliminate zero/full rotations
-- **Medium**: use PennyLane's `qml.transforms.merge_rotations` and `qml.transforms.cancel_inverses`
-- **Hard**: implement full single-qubit fusion (ZYZ decomposition) from scratch or via `qml.transforms.single_qubit_fusion`
-
----
-
 # Strategy Guide & Resources
 
-## Suggested Timeline
-
-| Phase | Hours | Focus |
-|---|---|---|
-| **Understand** | 0–6 | Read this handout, explore the starter notebook, run the baseline, understand the scorer. Try manual placement on `ghz_star`. |
-| **Core iteration** | 6–18 | Build and iterate your placement + routing algorithms. Start with small benchmarks, scale up. Try different placement strategies. |
-| **Optimize & extend** | 18–30 | Add scheduling for depth reduction. Attempt stretch goals if core is solid. Try your solution on all benchmarks. |
-| **Polish** | 30–36 | Final benchmarking runs, writeup, prepare demo. |
-
-## Approach Cheat Sheet
-
-| Approach | Difficulty | Expected Quality | Good First Step? |
-|---|---|---|---|
-| Random placement + greedy routing | Easy | Poor (but it works!) | ✅ Start here |
-| Degree-matching placement | Easy | Decent | ✅ Quick improvement |
-| Simulated annealing placement | Medium | Good | After greedy works |
-| Look-ahead routing (consider next K gates) | Medium | Good | After placement works |
-| SABRE-style bidirectional routing | Hard | Very good | If you want top scores |
-| DAG-based parallel scheduling | Medium | Good depth reduction | After routing works |
-| Stretch Goal A via `qml.compile` | Easy | Easy bonus points | When core is solid |
-| Stretch Goal B via rotation merging | Easy-Medium | Easy bonus points | When core is solid |
-
-## If You're Stuck
-
-- **"My routing produces invalid output"** → print your routed program and check each 2Q gate: is it on an edge? Use `hardware_graph.has_edge(p, q)` to verify.
-- **"My SWAP tracking is wrong"** → maintain a `placement` dict that you update after every SWAP. Print it frequently.
-- **"I can't beat the baseline on the dense benchmarks"** → that's expected - dense programs need many SWAPs no matter what. Focus on placement optimization; even small improvements compound across many gates.
-- **"I don't know where to start"** → implement the greedy baseline yourself (don't just use ours). Understanding *why* it's bad will give you ideas for improvement.
-
 ## Key Resources
 
 ### For the Core Challenge
@@ -414,3 +300,10 @@ The bad baseline literally does nothing (`return circuit`). **Any** simplificati
 - 📄 Li et al., "Tackling the Qubit Mapping Problem for NISQ-Era Quantum Devices" (ASPLOS 2019) - the original SABRE paper
 - 📄 [RL-based transpilation (arXiv:2405.13196)](https://arxiv.org/abs/2405.13196) - reinforcement learning for SWAP selection
 - 📄 [NASSC: Not All SWAPs Have the Same Cost (HPCA 2022)](https://hzhou.wordpress.ncsu.edu/files/2022/12/HPCA22_NASSC.pdf) - choosing SWAPs that enable downstream gate cancellation
+
+## If You're Stuck
+
+- **"My routing produces invalid output"** → print your routed program and check each 2Q gate: is it on an edge? Use `hardware_graph.has_edge(p, q)` to verify.
+- **"My SWAP tracking is wrong"** → maintain a `placement` dict that you update after every SWAP. Print it frequently.
+- **"I can't beat the baseline on the dense benchmarks"** → that's expected - dense programs need many SWAPs no matter what. Focus on placement optimization; even small improvements compound across many gates.
+- **"I don't know where to start"** → implement the greedy baseline yourself (don't just use ours). Understanding *why* it's bad will give you ideas for improvement.
