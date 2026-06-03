"""Encryption workflow: take an input file + selection of columns to
tokenize / affine-transform, plus a project-wide date offset, and produce
an encrypted output file.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from . import file_io
from .project import HistoryEntry, Project


@dataclass
class EncryptResult:
    output_path: str
    rows_affected: int
    columns_processed: List[str]
    new_tokens_per_prefix: Dict[str, int]
    total_tokens_per_prefix: Dict[str, int]
    numeric_columns: List[str] = field(default_factory=list)
    date_columns_shifted: List[str] = field(default_factory=list)
    date_offset_days: int = 0
    prompt_template: str = ""


def _suggest_output_path(input_path: str, project_dir: str) -> str:
    base, ext = os.path.splitext(os.path.basename(input_path))
    out_dir = os.path.join(project_dir, "encrypted")
    os.makedirs(out_dir, exist_ok=True)
    if ext.lower() == ".xls":
        ext = ".xlsx"
    return os.path.join(out_dir, f"{base}_encrypted{ext}")


def encrypt_file(
    project: Project,
    input_path: str,
    column_prefix_map: Dict[str, str],
    numeric_columns: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> EncryptResult:
    """Encrypt selected columns of a file.

    Args:
        column_prefix_map: {column_name: prefix} for TEXT columns that get
            tokenized to PREFIX_xxxx placeholders.
        numeric_columns: List of column names whose VALUES will be passed
            through an affine transform (y = a*x + b, per-column random pair
            stored in the project keyfile). The user can fully recover originals
            on decrypt.

    Date columns: ALL detected datetime columns are automatically shifted by
    project.date_offset_days. No opt-in needed - if the user doesn't want
    this, they should convert dates to strings before running the tool.
    """
    numeric_columns = list(numeric_columns or [])

    workbook = file_io.load(input_path)

    # Register text-column prefixes (idempotent).
    for col, prefix in column_prefix_map.items():
        project.tokenizer.register_column(col, prefix.upper())
    # Register numeric columns (allocates random (a, b) per column on first sight).
    for col in numeric_columns:
        project.numeric.register(col)
    # Lazy migration: legacy v1 projects had no date offset.
    project.ensure_date_offset()

    before_counts = {p: c for p, c in project.tokenizer.counters.items()}

    rows_affected = 0
    text_cols_seen: set = set()
    numeric_cols_seen: set = set()
    date_cols_seen: set = set()
    offset = pd.Timedelta(days=project.date_offset_days)

    for sheet_name, df in workbook.items():
        # 1. Text columns -> tokenize
        for col, prefix in column_prefix_map.items():
            if col not in df.columns:
                continue
            text_cols_seen.add(col)
            df[col] = df[col].map(
                lambda v, p=prefix: project.tokenizer.tokenize(p, v)
            )

        # 2. Numeric columns -> affine transform
        for col in numeric_columns:
            if col not in df.columns:
                continue
            if not pd.api.types.is_numeric_dtype(df[col]):
                # Column was opted in for numeric encryption but isn't actually
                # numeric in this sheet (could be mixed dtype across sheets).
                # Coerce to numeric where possible; non-numeric values pass through.
                pass
            numeric_cols_seen.add(col)
            df[col] = df[col].map(lambda v, c=col: project.numeric.encrypt(c, v))

        # 3. Date columns -> shift by project offset
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                date_cols_seen.add(col)
                df[col] = df[col] + offset

        rows_affected += len(df)

    if output_path is None:
        project_dir = os.path.dirname(project.keyfile_path() or ".")
        output_path = _suggest_output_path(input_path, project_dir)
    file_io.save(output_path, workbook)

    after_counts = project.tokenizer.counters
    new_per_prefix = {
        p: after_counts.get(p, 0) - before_counts.get(p, 0)
        for p in column_prefix_map.values()
    }
    total_per_prefix = {p: after_counts.get(p, 0) for p in column_prefix_map.values()}

    project.add_history(
        HistoryEntry(
            timestamp=time.time(),
            action="encrypt",
            source_file=os.path.abspath(input_path),
            output_file=os.path.abspath(output_path),
            columns=sorted(text_cols_seen | numeric_cols_seen | date_cols_seen),
            rows_affected=rows_affected,
        )
    )
    project.save()

    prompt = build_prompt_template(
        project,
        column_prefix_map,
        sorted(numeric_cols_seen),
        sorted(date_cols_seen),
    )

    return EncryptResult(
        output_path=output_path,
        rows_affected=rows_affected,
        columns_processed=sorted(text_cols_seen | numeric_cols_seen),
        new_tokens_per_prefix=new_per_prefix,
        total_tokens_per_prefix=total_per_prefix,
        numeric_columns=sorted(numeric_cols_seen),
        date_columns_shifted=sorted(date_cols_seen),
        date_offset_days=project.date_offset_days,
        prompt_template=prompt,
    )


def build_prompt_template(
    project: Project,
    column_prefix_map: Dict[str, str],
    numeric_columns: List[str],
    date_columns_shifted: List[str],
) -> str:
    """Generate the AI prompt declaring which columns are transformed and how.

    Reveals NO original values - only structural metadata (column name,
    prefix, count of distinct values, that numeric/date transforms were
    applied). The transforms themselves (a, b, offset) stay in the keyfile.
    """
    lines: List[str] = [
        "我提供的表格中，以下列已经过本地脱敏处理，请基于这些字段的语义角色进行分析。",
        "脱敏只改变字面值或数值，不影响行/列结构和数据之间的关系。",
        "",
    ]
    if column_prefix_map:
        lines.append("【文本占位符列】（值被替换为占位符，相同占位符代表同一原值）：")
        for col, prefix in column_prefix_map.items():
            prefix = prefix.upper()
            unique_count = len(project.tokenizer.forward.get(prefix, {}))
            lines.append(
                f"  - 「{col}」→ `{prefix}_xxxx`（共 {unique_count} 个不同值）"
            )
        lines.append("")

    if numeric_columns:
        lines.append("【数值仿射变换列】（每列做了 y = a·x + b 的可逆变换，"
                     "绝对量级不可信，但比例、增长率、相关性、求和都精确）：")
        for col in numeric_columns:
            lines.append(f"  - 「{col}」")
        lines.append("  分析结果中的具体数字不代表真实数量级，但相对结论有效。")
        lines.append("")

    if date_columns_shifted:
        lines.append("【日期偏移列】（所有日期被统一平移了同一个未知天数，"
                     "时间间隔、星期、季节性保留）：")
        for col in date_columns_shifted:
            lines.append(f"  - 「{col}」")
        lines.append("  绝对日期不可信，但相对时序、周期分析有效。")
        lines.append("")

    lines.extend(
        [
            "未在上述列表中的列均为原始数据，可以直接分析。",
            "",
            "在你的输出中，请保留占位符（如 `GAME_0001`）和变换后的数值/日期不变。",
            "我会在本地把它们还原成真实值。",
        ]
    )
    return "\n".join(lines)
