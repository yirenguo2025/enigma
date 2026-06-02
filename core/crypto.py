"""Password-based encryption for the project keyfile.

Format of the .keyfile (binary):
    [4]   magic = b"ENG1"
    [1]   version = 0x01
    [16]  salt
    [N]   Fernet ciphertext (Fernet itself contains: version|timestamp|IV|ct|HMAC)

Key derivation: PBKDF2-HMAC-SHA256, 480_000 iterations -> 32 bytes -> base64
                -> Fernet key.

Fernet provides AES-128-CBC + HMAC-SHA256 authentication. This is enough for
our threat model (a local file an attacker may have copied off the disk).
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


MAGIC = b"ENG1"
VERSION = 0x01
SALT_LEN = 16
PBKDF2_ITER = 480_000


class KeyfileError(Exception):
    pass


class WrongPassword(KeyfileError):
    pass


@dataclass
class KeyfileEnvelope:
    """Result of decryption: the in-memory project state plus a salt
    we should reuse on re-save (so the user doesn't have to re-enter password
    side-effects, and so the file changes are minimal)."""

    payload: dict
    salt: bytes


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITER,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def encrypt_keyfile(payload: dict, password: str, salt: Optional[bytes] = None) -> bytes:
    """Serialize+encrypt the project state. Returns bytes to write to disk."""
    if salt is None:
        salt = os.urandom(SALT_LEN)
    if len(salt) != SALT_LEN:
        raise KeyfileError(f"Salt must be {SALT_LEN} bytes")

    fernet = Fernet(_derive_key(password, salt))
    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ct = fernet.encrypt(plaintext)

    header = MAGIC + bytes([VERSION]) + salt
    return header + ct


def decrypt_keyfile(blob: bytes, password: str) -> KeyfileEnvelope:
    """Parse and decrypt a .keyfile. Raises WrongPassword on auth failure."""
    if len(blob) < len(MAGIC) + 1 + SALT_LEN:
        raise KeyfileError("File is too short to be a valid keyfile.")
    if blob[: len(MAGIC)] != MAGIC:
        raise KeyfileError("Not an Enigma keyfile (bad magic bytes).")
    version = blob[len(MAGIC)]
    if version != VERSION:
        raise KeyfileError(f"Unsupported keyfile version: {version}.")

    salt = blob[len(MAGIC) + 1 : len(MAGIC) + 1 + SALT_LEN]
    ct = blob[len(MAGIC) + 1 + SALT_LEN :]

    fernet = Fernet(_derive_key(password, salt))
    try:
        plaintext = fernet.decrypt(ct)
    except InvalidToken as e:
        raise WrongPassword("Password is incorrect or keyfile is corrupted.") from e

    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise KeyfileError("Keyfile content is not valid JSON.") from e

    return KeyfileEnvelope(payload=payload, salt=salt)


def save_keyfile(path: str, payload: dict, password: str, salt: Optional[bytes] = None) -> bytes:
    """Convenience: encrypt and atomically write to disk. Returns the salt used."""
    salt = salt or os.urandom(SALT_LEN)
    blob = encrypt_keyfile(payload, password, salt=salt)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    return salt


def load_keyfile(path: str, password: str) -> KeyfileEnvelope:
    with open(path, "rb") as f:
        blob = f.read()
    return decrypt_keyfile(blob, password)
