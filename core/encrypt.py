"""Encryption workflow: take an input file + column->prefix mapping,
produce a tokenized output file and update the project mapping.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from . import file_io
from .project import HistoryEntry, Project


@dataclass
class EncryptResult:
    output_path: str
    rows_affected: int
    columns_processed: List[str]
    new_tokens_per_prefix: Dict[str, int]   # prefix -> number of NEW tokens added
    total_tokens_per_prefix: Dict[str, int] # prefix -> total tokens after this op
    prompt_template: str


def _suggest_output_path(input_path: str, project_dir: str) -> str:
    base, ext = os.path.splitext(os.path.basename(input_path))
    out_dir = os.path.join(project_dir, "encrypted")
    os.makedirs(out_dir, exist_ok=True)
    # Always force .xlsx if input was .xls (we can't write .xls).
    if ext.lower() == ".xls":
        ext = ".xlsx"
    return os.path.join(out_dir, f"{base}_encrypted{ext}")


def encrypt_file(
    project: Project,
    input_path: str,
    column_prefix_map: Dict[str, str],
    output_path: Optional[str] = None,
) -> EncryptResult:
    """Encrypt selected columns of a file.

    column_prefix_map: {column_name: prefix}, where column_name MUST exist in
    every sheet that contains it. Columns not in the map are left untouched.
    Prefixes are user-chosen and shared project-wide (same prefix used in
    different files will produce consistent tokens for the same value).
    """
    workbook = file_io.load(input_path)

    # Register all column->prefix bindings before tokenizing so we fail fast
    # on conflicts.
    for col, prefix in column_prefix_map.items():
        project.tokenizer.register_column(col, prefix.upper())

    before_counts = {p: c for p, c in project.tokenizer.counters.items()}

    rows_affected = 0
    cols_seen: set = set()
    for sheet_name, df in workbook.items():
        for col, prefix in column_prefix_map.items():
            if col not in df.columns:
                continue
            cols_seen.add(col)
            # Apply tokenization element-wise. We coerce to string for hashing
            # consistency (the tokenizer itself preserves None/NaN/empty).
            df[col] = df[col].map(lambda v, p=prefix: project.tokenizer.tokenize(p, v))
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

    # Persist updated mapping immediately - one of the most important guarantees:
    # we never have a tokenized file on disk whose mapping isn't saved yet.
    project.add_history(
        HistoryEntry(
            timestamp=time.time(),
            action="encrypt",
            source_file=os.path.abspath(input_path),
            output_file=os.path.abspath(output_path),
            columns=sorted(cols_seen),
            rows_affected=rows_affected,
        )
    )
    project.save()

    prompt = build_prompt_template(project, column_prefix_map)

    return EncryptResult(
        output_path=output_path,
        rows_affected=rows_affected,
        columns_processed=sorted(cols_seen),
        new_tokens_per_prefix=new_per_prefix,
        total_tokens_per_prefix=total_per_prefix,
        prompt_template=prompt,
    )


def build_prompt_template(project: Project, column_prefix_map: Dict[str, str]) -> str:
    """Generate the prompt the user should paste into their AI tool, declaring
    which columns are encrypted and what each prefix means semantically.

    The prompt does NOT reveal any original values - only structural metadata
    (column name, prefix, count of unique values).
    """
    lines: List[str] = [
        "我提供的表格中，以下列已经过本地脱敏处理，脱敏只是把字面值替换为占位符，",
        "不影响数据之间的关系。请基于这些字段的语义角色进行分析。",
        "",
    ]
    for col, prefix in column_prefix_map.items():
        prefix = prefix.upper()
        unique_count = len(project.tokenizer.forward.get(prefix, {}))
        lines.append(
            f"- 列「{col}」：原本是该业务实体名称，已替换为 `{prefix}_xxx` 形式占位符。"
            f"共 {unique_count} 个不同取值；相同占位符代表同一个实体。"
        )
    lines.extend(
        [
            "",
            "未在上述列表中的列均为原始数据，可以直接分析。",
            "",
            "在你的输出中，请直接保留占位符（如 `GAME_0001`）。",
            "我会在本地把它们还原成真实名称。",
        ]
    )
    return "\n".join(lines)
