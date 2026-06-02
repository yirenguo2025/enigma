"""Token generation and bidirectional mapping.

Token format: {PREFIX}_{ID}
  - PREFIX is user-chosen ASCII (e.g., GAME, TYPE), bound to a logical column.
  - ID is zero-padded base36 sequential (e.g., 0001, 0002, ... ZZZZ).
  - Sequential assignment ensures no collisions and is reproducible within a project.

The Tokenizer is the single source of truth for original-value <-> token mapping
within a project. It is serialized into the encrypted .keyfile.
"""

from __future__ import annotations

import re
from typing import Dict, Optional


PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,15}$")
TOKEN_RE = re.compile(r"^([A-Z][A-Z0-9]{0,15})_([0-9A-Z]{1,8})$")
BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Tokenizer:
    """Bidirectional mapping between original values and tokens.

    Persistent state (serialized into keyfile):
        prefixes: {column_name: prefix}            user-chosen prefix per logical column
        forward:  {prefix: {original_value: token}}
        counters: {prefix: int}                    next available counter for prefix

    A "column_name" is a user-defined logical name (e.g., "游戏名称").
    Multiple physical columns across multiple files can share a logical column
    so they get the same prefix and consistent tokens (project-wide consistency).
    """

    def __init__(self, data: Optional[dict] = None):
        data = data or {}
        self.prefixes: Dict[str, str] = dict(data.get("prefixes", {}))
        self.forward: Dict[str, Dict[str, str]] = {
            p: dict(m) for p, m in data.get("forward", {}).items()
        }
        self.counters: Dict[str, int] = dict(data.get("counters", {}))

    # ---------- serialization ----------

    def to_dict(self) -> dict:
        return {
            "prefixes": dict(self.prefixes),
            "forward": {p: dict(m) for p, m in self.forward.items()},
            "counters": dict(self.counters),
        }

    # ---------- prefix management ----------

    def register_column(self, column_name: str, prefix: str) -> None:
        """Bind a logical column name to a prefix. Idempotent if same prefix."""
        prefix = prefix.upper()
        if not PREFIX_RE.match(prefix):
            raise ValueError(
                f"Invalid prefix '{prefix}'. Must start with a letter, "
                "use only A-Z/0-9, and be at most 16 chars."
            )
        existing = self.prefixes.get(column_name)
        if existing and existing != prefix:
            raise ValueError(
                f"Column '{column_name}' already bound to prefix '{existing}'. "
                f"Cannot rebind to '{prefix}'."
            )
        # Make sure no two columns share the same prefix (would corrupt mapping).
        for col, pfx in self.prefixes.items():
            if pfx == prefix and col != column_name:
                raise ValueError(
                    f"Prefix '{prefix}' is already used by column '{col}'. "
                    "Choose a different prefix."
                )
        self.prefixes[column_name] = prefix
        self.forward.setdefault(prefix, {})
        self.counters.setdefault(prefix, 0)

    def get_prefix(self, column_name: str) -> Optional[str]:
        return self.prefixes.get(column_name)

    # ---------- tokenization ----------

    def tokenize(self, prefix: str, value) -> str:
        """Return the token for a value, allocating a new one if needed.

        Empty/None/NaN values pass through unchanged so AI can still see
        missing data as missing.
        """
        if value is None:
            return value
        # pandas NaN -> float('nan') compares unequal to itself
        if isinstance(value, float) and value != value:  # NaN check
            return value
        s = str(value)
        if s == "" or s.lower() == "nan":
            return value

        bucket = self.forward.setdefault(prefix, {})
        if s in bucket:
            return bucket[s]
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        token = f"{prefix}_{self._encode(self.counters[prefix])}"
        bucket[s] = token
        return token

    # ---------- reverse lookup ----------

    def reverse_map_for_prefix(self, prefix: str) -> Dict[str, str]:
        return {tok: orig for orig, tok in self.forward.get(prefix, {}).items()}

    def all_reverse(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for prefix, mapping in self.forward.items():
            for orig, tok in mapping.items():
                out[tok] = orig
        return out

    def stats(self) -> Dict[str, int]:
        """Returns {prefix: count_of_unique_values}."""
        return {p: len(m) for p, m in self.forward.items()}

    # ---------- helpers ----------

    @staticmethod
    def _encode(n: int) -> str:
        """Encode a positive int as zero-padded 4-char base36 (uppercase).
        Pads to 4 for n < 36^4 = 1,679,616. Beyond that, grows naturally.
        """
        if n <= 0:
            return "0001"
        out = ""
        while n > 0:
            out = BASE36[n % 36] + out
            n //= 36
        return out.zfill(4)
