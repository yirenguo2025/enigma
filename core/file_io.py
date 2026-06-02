"""File IO: load and save xlsx / xls / csv / tsv as a uniform Workbook.

A Workbook is a dict of {sheet_name: pandas.DataFrame}.
CSV/TSV files have a single sheet named after the file (without extension).
"""

from __future__ import annotations

import os
from typing import Dict

import pandas as pd


SUPPORTED_EXTS = {".xlsx", ".xls", ".csv", ".tsv", ".txt"}


class FileFormatError(Exception):
    pass


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _infer_csv_sep(path: str) -> str:
    ext = _ext(path)
    if ext == ".tsv":
        return "\t"
    # peek first non-empty line to choose between , ; \t
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        sample = head.decode("utf-8-sig", errors="replace")
        first = next((ln for ln in sample.splitlines() if ln.strip()), "")
        counts = {",": first.count(","), ";": first.count(";"), "\t": first.count("\t")}
        return max(counts, key=counts.get) if max(counts.values()) > 0 else ","
    except Exception:
        return ","


def load(path: str) -> Dict[str, pd.DataFrame]:
    """Load any supported file into a Workbook (dict of sheets)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = _ext(path)
    if ext not in SUPPORTED_EXTS:
        raise FileFormatError(f"Unsupported file format: {ext}")

    if ext in (".xlsx", ".xls"):
        # sheet_name=None -> dict of all sheets. dtype=str preserves originals,
        # but we keep default dtype to allow numeric analysis later. Strings are
        # what we tokenize anyway.
        engine = "openpyxl" if ext == ".xlsx" else None
        sheets = pd.read_excel(path, sheet_name=None, engine=engine)
        return sheets

    # CSV/TSV/TXT
    sep = _infer_csv_sep(path)
    # utf-8-sig handles BOM; pandas falls back to default if not utf-8.
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            df = pd.read_csv(path, sep=sep, encoding=enc, dtype=object, keep_default_na=False)
            sheet_name = os.path.splitext(os.path.basename(path))[0]
            return {sheet_name: df}
        except UnicodeDecodeError:
            continue
    raise FileFormatError(f"Could not decode {path} with common encodings.")


def save(path: str, workbook: Dict[str, pd.DataFrame]) -> None:
    """Write a workbook back to disk, preserving format implied by extension."""
    ext = _ext(path)
    if ext not in SUPPORTED_EXTS:
        raise FileFormatError(f"Unsupported output format: {ext}")

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    if ext == ".xlsx":
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, df in workbook.items():
                # Excel sheet names limited to 31 chars and can't have []*?:/\
                safe_name = "".join(c for c in sheet_name if c not in r"[]*?:/\\")[:31] or "Sheet1"
                df.to_excel(writer, sheet_name=safe_name, index=False)
        return

    if ext == ".xls":
        # openpyxl can't write .xls. Force .xlsx instead, or refuse.
        raise FileFormatError(
            ".xls (legacy Excel) write is not supported. Save as .xlsx instead."
        )

    # CSV/TSV: only the first sheet is written; warn-by-design upstream if multi-sheet.
    sep = "\t" if ext in (".tsv",) else ","
    if len(workbook) > 1:
        # Prefix each filename with sheet name to preserve all data.
        base, _ = os.path.splitext(path)
        for sheet_name, df in workbook.items():
            safe = sheet_name.replace("/", "_").replace("\\", "_")
            df.to_csv(f"{base}__{safe}{ext}", sep=sep, index=False, encoding="utf-8-sig")
        return
    df = next(iter(workbook.values()))
    df.to_csv(path, sep=sep, index=False, encoding="utf-8-sig")


def list_columns(workbook: Dict[str, pd.DataFrame]) -> Dict[str, list]:
    """Return {sheet_name: [column_names]} for UI display."""
    return {sheet: list(df.columns) for sheet, df in workbook.items()}
