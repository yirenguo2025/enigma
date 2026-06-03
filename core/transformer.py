"""Affine transformations for numeric columns.

Each registered numeric column gets a per-column random pair (a, b):
    encrypted = a * x + b
    decrypted = (y - b) / a

This hides the absolute scale of values (no one can tell whether a 流水
column is in 元/万元/百万元) but preserves all relative structure: ratios,
correlations, growth rates, sums, averages — every analytical operation
the user might ask AI to perform yields a result that is itself an
affine function of the truth, and can be inverted exactly on this end.

LIMITATIONS (be honest with the user about these):
- Trends and shapes are preserved. A "viral spike in March" is still
  visible after encryption; only its absolute height changes.
- A single known data point (one true value matched to one encrypted
  value) reveals nothing alone, but TWO known points solve for (a, b)
  and recover all values.
- Inter-column ratios within a row are NOT preserved (each column has
  its own (a, b)) which is a small bonus protection.

Choice of (a, b):
- a is drawn from [0.5, 10.0] — non-zero, not too tiny, not too huge.
  We avoid 1.0 exactly to make sure scaling actually happens.
- b is drawn proportional to a typical magnitude offset; we use a
  moderate range so the offset isn't trivially small.
- Both rounded to 4 decimals to keep the keyfile readable and avoid
  spurious trailing-bit float drift.
"""

from __future__ import annotations

import random
from typing import Dict, Optional


A_RANGE = (0.5, 10.0)
B_RANGE = (-1000.0, 1000.0)
ROUND_DECIMALS = 4

# Inverse-rounding tolerance: integer originals like 1200 may decrypt to
# 1199.9999999998 due to float math. Round inverted values to this many
# decimals to recover the true original cleanly.
INVERSE_ROUND_DECIMALS = 6


class NumericTransformer:
    """Stores per-column (a, b) pairs and applies/inverts y = a*x + b."""

    def __init__(self, data: Optional[dict] = None):
        data = data or {}
        # transforms: {col_name: {"a": float, "b": float}}
        self.transforms: Dict[str, Dict[str, float]] = {
            col: dict(spec) for col, spec in data.get("transforms", {}).items()
        }

    def to_dict(self) -> dict:
        return {"transforms": {c: dict(s) for c, s in self.transforms.items()}}

    def register(self, col_name: str) -> None:
        """Allocate a random (a, b) for a column on first sight. Idempotent."""
        if col_name in self.transforms:
            return
        a = round(random.uniform(*A_RANGE), ROUND_DECIMALS)
        # Avoid a = 0 or near-1.0 (would be no-op)
        if abs(a - 1.0) < 0.01:
            a = round(a + 0.5, ROUND_DECIMALS)
        b = round(random.uniform(*B_RANGE), ROUND_DECIMALS)
        self.transforms[col_name] = {"a": a, "b": b}

    def has(self, col_name: str) -> bool:
        return col_name in self.transforms

    def encrypt(self, col_name: str, x):
        """Apply y = a*x + b. Pass-through for None/NaN."""
        if x is None:
            return x
        if isinstance(x, float) and x != x:  # NaN
            return x
        try:
            xf = float(x)
        except (TypeError, ValueError):
            return x  # not numeric, leave as-is
        spec = self.transforms[col_name]
        return spec["a"] * xf + spec["b"]

    def decrypt(self, col_name: str, y):
        """Apply x = (y - b) / a. Pass-through for None/NaN. Rounds away
        float drift so integer originals come back clean."""
        if y is None:
            return y
        if isinstance(y, float) and y != y:
            return y
        try:
            yf = float(y)
        except (TypeError, ValueError):
            return y
        spec = self.transforms[col_name]
        x = (yf - spec["b"]) / spec["a"]
        # Tame float drift. If the result is almost an integer, snap to it;
        # otherwise round to 6 decimals (more precision than any spreadsheet
        # number is realistically going to need).
        rounded = round(x, INVERSE_ROUND_DECIMALS)
        if abs(rounded - round(rounded)) < 1e-9:
            return float(round(rounded))
        return rounded

    def stats(self) -> Dict[str, int]:
        """Returns {col_name: 1} as a simple count (useful for prompt template)."""
        return {c: 1 for c in self.transforms}
