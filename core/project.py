"""Project: in-memory state of an Enigma project.

A project owns:
    - a Tokenizer (text-column bidirectional mapping)
    - a NumericTransformer (numeric-column affine transforms)
    - a project-wide date_offset_days (random shift for all date columns)
    - metadata (name, version, timestamps)
    - history of operations (audit log, all local)

Persistence: the entire project state is serialized to JSON and encrypted
into a single .keyfile via core.crypto.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import List, Optional

from . import crypto
from .tokenizer import Tokenizer
from .transformer import NumericTransformer


PROJECT_VERSION = 2  # bumped: added numeric + date_offset_days
DATE_OFFSET_RANGE_DAYS = 365 * 5  # +/- 5 years of jitter


def _new_date_offset() -> int:
    """Random non-zero shift between -5y and +5y, in whole days."""
    while True:
        n = random.randint(-DATE_OFFSET_RANGE_DAYS, DATE_OFFSET_RANGE_DAYS)
        if abs(n) >= 30:  # avoid tiny shifts that wouldn't actually obscure anything
            return n


@dataclass
class HistoryEntry:
    timestamp: float
    action: str          # "encrypt" | "decrypt"
    source_file: str
    output_file: str
    columns: List[str]   # column names that were transformed
    rows_affected: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(**d)


@dataclass
class Project:
    name: str
    tokenizer: Tokenizer = field(default_factory=Tokenizer)
    numeric: NumericTransformer = field(default_factory=NumericTransformer)
    # Project-wide whole-day shift applied to ALL detected datetime columns.
    # 0 means "no shift" (legacy projects). New projects get a non-zero value.
    date_offset_days: int = 0
    history: List[HistoryEntry] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    version: int = PROJECT_VERSION
    # Path of the .keyfile this project loaded from / will save to.
    # Not serialized into the keyfile itself.
    _path: Optional[str] = None
    _password: Optional[str] = None
    _salt: Optional[bytes] = None

    # ---------- serialization ----------

    def to_payload(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "created_at": self.created_at,
            "tokenizer": self.tokenizer.to_dict(),
            "numeric": self.numeric.to_dict(),
            "date_offset_days": self.date_offset_days,
            "history": [h.to_dict() for h in self.history],
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "Project":
        return cls(
            name=payload.get("name", "Untitled"),
            tokenizer=Tokenizer(payload.get("tokenizer", {})),
            numeric=NumericTransformer(payload.get("numeric", {})),
            # Legacy v1 projects have no date_offset_days field; default to 0.
            date_offset_days=payload.get("date_offset_days", 0),
            history=[HistoryEntry.from_dict(h) for h in payload.get("history", [])],
            created_at=payload.get("created_at", time.time()),
            version=payload.get("version", PROJECT_VERSION),
        )

    # ---------- file ops ----------

    @classmethod
    def create(cls, path: str, name: str, password: str) -> "Project":
        """Create a brand new project, write an empty keyfile, return the project."""
        if os.path.exists(path):
            raise FileExistsError(f"A keyfile already exists at {path}.")
        proj = cls(name=name, date_offset_days=_new_date_offset())
        proj._path = path
        proj._password = password
        proj.save()
        return proj

    @classmethod
    def open(cls, path: str, password: str) -> "Project":
        env = crypto.load_keyfile(path, password)
        proj = cls.from_payload(env.payload)
        proj._path = path
        proj._password = password
        proj._salt = env.salt
        return proj

    def save(self) -> None:
        if not self._path or self._password is None:
            raise RuntimeError("Project has no associated keyfile path or password.")
        salt = crypto.save_keyfile(
            self._path, self.to_payload(), self._password, salt=self._salt
        )
        self._salt = salt

    # ---------- helpers ----------

    def ensure_date_offset(self) -> None:
        """Lazily generate a date offset for migrated v1 projects on first use."""
        if self.date_offset_days == 0:
            self.date_offset_days = _new_date_offset()

    def add_history(self, entry: HistoryEntry) -> None:
        self.history.append(entry)

    def keyfile_path(self) -> Optional[str]:
        return self._path
