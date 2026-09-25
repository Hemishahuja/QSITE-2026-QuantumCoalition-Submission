"""QSITE 2026 Computational Track solution: placement + SWAP routing."""

from .solve import lower_bound, solve, solve_with_info
from .decompose import decompose
from .optimize_1q import optimize_1q

__all__ = ["lower_bound", "solve", "solve_with_info"]
__all__ += ["decompose", "optimize_1q"]
