"""Decryption workflow: take an AI-processed file, find tokens (with fuzzy
matching), and replace them with original values. Robust to:

  - case variation (game_a1 -> GAME_A1)
  - extra whitespace inside the token (GAME _ A1)
  - punctuation around the token (`"GAME_A1"`, `GAME_A1.`, etc.)
  - tokens AI hallucinated that were never in our mapping
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from . import file_io
from .project import HistoryEntry, Project


# Token-like substring: letters/digits, optional whitespace around the underscore.
# We DON'T anchor to known prefixes here; we check membership after normalization.
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]{0,15})\s*_\s*([A-Za-z0-9]{1,8})(?![A-Za-z0-9_])"
)


UNKNOWN_TAG = "[⚠未识别]"


@dataclass
class DecryptResult:
    output_path: str
    rows_processed: int
    tokens_restored: int
    unknown_tokens: Dict[str, int] = field(default_factory=dict)  # token -> count
    columns_touched: List[str] = field(default_factory=list)


def _suggest_output_path(input_path: str, project_dir: str) -> str:
    base, ext = os.path.splitext(os.path.basename(input_path))
    out_dir = os.path.join(project_dir, "decrypted")
    os.makedirs(out_dir, exist_ok=True)
    if ext.lower() == ".xls":
        ext = ".xlsx"
    return os.path.join(out_dir, f"{base}_restored{ext}")


def _replace_in_text(
    text: str,
    reverse_map: Dict[str, str],
    known_prefixes: Set[str],
    stats: Dict[str, int],
    unknowns: Dict[str, int],
) -> str:
    """Find every token-like substring in text and replace by original.
    Updates stats and unknowns as side effects.
    """

    def repl(match: re.Match) -> str:
        prefix = match.group(1).upper()
        ident = match.group(2).upper()
        # Pad ID up to 4 chars to match our canonical encoding (we always
        # generate at least 4-char IDs). Longer IDs left as-is.
        if len(ident) < 4:
            ident = ident.zfill(4)
        canonical = f"{prefix}_{ident}"

        if prefix not in known_prefixes:
            # Looks like a token but uses an unknown prefix - probably not ours.
            return match.group(0)

        if canonical in reverse_map:
            stats["restored"] = stats.get("restored", 0) + 1
            return reverse_map[canonical]

        # Known prefix, unknown ID -> AI hallucinated or typo
        unknowns[canonical] = unknowns.get(canonical, 0) + 1
        return f"{UNKNOWN_TAG}{match.group(0)}"

    return _TOKEN_RE.sub(repl, text)


def decrypt_file(
    project: Project,
    input_path: str,
    output_path: Optional[str] = None,
) -> DecryptResult:
    """Read input_path, replace tokens with originals, write output."""
    workbook = file_io.load(input_path)
    reverse_map = project.tokenizer.all_reverse()
    known_prefixes = set(project.tokenizer.forward.keys())

    stats: Dict[str, int] = {}
    unknowns: Dict[str, int] = {}
    rows = 0
    cols_touched: Set[str] = set()

    for sheet_name, df in workbook.items():
        rows += len(df)
        for col in df.columns:
            # Only process object/string columns; numeric columns can't contain tokens.
            if not pd.api.types.is_object_dtype(df[col]) and not pd.api.types.is_string_dtype(df[col]):
                continue
            had_token = False

            def transform(v, _col=col):
                nonlocal had_token
                if v is None:
                    return v
                if isinstance(v, float) and v != v:  # NaN
                    return v
                s = str(v)
                if not s:
                    return v
                new_s = _replace_in_text(s, reverse_map, known_prefixes, stats, unknowns)
                if new_s != s:
                    had_token = True
                return new_s

            df[col] = df[col].map(transform)
            if had_token:
                cols_touched.add(col)

    if output_path is None:
        project_dir = os.path.dirname(project.keyfile_path() or ".")
        output_path = _suggest_output_path(input_path, project_dir)
    file_io.save(output_path, workbook)

    project.add_history(
        HistoryEntry(
            timestamp=time.time(),
            action="decrypt",
            source_file=os.path.abspath(input_path),
            output_file=os.path.abspath(output_path),
            columns=sorted(cols_touched),
            rows_affected=rows,
        )
    )
    project.save()

    return DecryptResult(
        output_path=output_path,
        rows_processed=rows,
        tokens_restored=stats.get("restored", 0),
        unknown_tokens=unknowns,
        columns_touched=sorted(cols_touched),
    )
