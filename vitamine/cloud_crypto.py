"""Versioned authenticated encryption for private hosted artifacts."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"VITAMINE-ENC\x01"
KEY_ID_BYTES = 8
NONCE_BYTES = 12


class PrivateDataDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataKey:
    identifier: bytes
    value: bytes


def _decode_key(value: str) -> bytes:
    text = value.strip()
    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("VITAMINE_DATA_ENCRYPTION_KEY must be URL-safe base64.") from exc
    if len(decoded) != 32:
        raise RuntimeError("VITAMINE_DATA_ENCRYPTION_KEY must decode to exactly 32 bytes.")
    return decoded


def _key_id(value: bytes) -> bytes:
    return hashlib.sha256(b"vitamine-data-key-id-v1\0" + value).digest()[:KEY_ID_BYTES]


def configured_data_keys() -> list[DataKey]:
    active = str(os.environ.get("VITAMINE_DATA_ENCRYPTION_KEY") or "").strip()
    previous = str(os.environ.get("VITAMINE_DATA_ENCRYPTION_PREVIOUS_KEYS") or "").strip()
    raw_keys: list[bytes] = []
    if active:
        raw_keys.append(_decode_key(active))
    else:
        # Compatibility fallback for local/test deployments. Production should
        # configure a dedicated data key so session/OAuth and CV keys can rotate
        # independently.
        pepper = str(os.environ.get("VITAMINE_CLOUD_PEPPER") or "")
        if not pepper:
            raise RuntimeError("VITAMINE_DATA_ENCRYPTION_KEY is required for private CV storage.")
        raw_keys.append(hashlib.sha256(f"vitamine-data-key-v1\0{pepper}".encode()).digest())
    for item in previous.split(","):
        if item.strip():
            raw_keys.append(_decode_key(item))
    keys: list[DataKey] = []
    seen: set[bytes] = set()
    for value in raw_keys:
        identifier = _key_id(value)
        if identifier not in seen:
            keys.append(DataKey(identifier, value))
            seen.add(identifier)
    return keys


def is_encrypted_private_data(value: bytes | memoryview) -> bool:
    return bytes(value).startswith(MAGIC)


def encrypted_key_identifier(value: bytes | memoryview) -> str | None:
    encoded = bytes(value)
    if not encoded.startswith(MAGIC) or len(encoded) < len(MAGIC) + KEY_ID_BYTES:
        return None
    offset = len(MAGIC)
    return encoded[offset : offset + KEY_ID_BYTES].hex()


def encrypt_private_data(value: bytes, *, context: str) -> bytes:
    key = configured_data_keys()[0]
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key.value).encrypt(nonce, value, context.encode("utf-8"))
    return MAGIC + key.identifier + nonce + ciphertext


def decrypt_private_data(value: bytes | memoryview, *, context: str, allow_plaintext: bool = False) -> bytes:
    encoded = bytes(value)
    if not encoded.startswith(MAGIC):
        if allow_plaintext:
            return encoded
        raise PrivateDataDecryptionError("Private data is not encrypted.")
    offset = len(MAGIC)
    minimum = offset + KEY_ID_BYTES + NONCE_BYTES + 16
    if len(encoded) < minimum:
        raise PrivateDataDecryptionError("Encrypted private data is truncated.")
    key_id = encoded[offset : offset + KEY_ID_BYTES]
    nonce_start = offset + KEY_ID_BYTES
    nonce = encoded[nonce_start : nonce_start + NONCE_BYTES]
    ciphertext = encoded[nonce_start + NONCE_BYTES :]
    key = next((candidate for candidate in configured_data_keys() if candidate.identifier == key_id), None)
    if key is None:
        raise PrivateDataDecryptionError("No configured key can decrypt this private data.")
    try:
        return AESGCM(key.value).decrypt(nonce, ciphertext, context.encode("utf-8"))
    except InvalidTag as exc:
        raise PrivateDataDecryptionError("Encrypted private data failed authentication.") from exc


def active_key_identifier() -> str:
    return configured_data_keys()[0].identifier.hex()
