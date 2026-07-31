import base64
import os
import unittest
from unittest.mock import patch

from vitamine.cloud_crypto import (
    PrivateDataDecryptionError,
    active_key_identifier,
    decrypt_private_data,
    encrypt_private_data,
    is_encrypted_private_data,
)


def encoded_key(byte: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * 32).decode("ascii")


class CloudCryptoTests(unittest.TestCase):
    def test_round_trip_is_randomized_and_context_bound(self):
        with patch.dict(os.environ, {"VITAMINE_DATA_ENCRYPTION_KEY": encoded_key(7)}, clear=False):
            first = encrypt_private_data(b"SQLite format 3\0private", context="member:cv")
            second = encrypt_private_data(b"SQLite format 3\0private", context="member:cv")
            self.assertNotEqual(first, second)
            self.assertTrue(is_encrypted_private_data(first))
            self.assertNotIn(b"SQLite format 3", first)
            self.assertEqual(decrypt_private_data(first, context="member:cv"), b"SQLite format 3\0private")
            with self.assertRaises(PrivateDataDecryptionError):
                decrypt_private_data(first, context="other:cv")

    def test_tampering_is_rejected(self):
        with patch.dict(os.environ, {"VITAMINE_DATA_ENCRYPTION_KEY": encoded_key(8)}, clear=False):
            encrypted = bytearray(encrypt_private_data(b"private", context="member:cv"))
            encrypted[-1] ^= 1
            with self.assertRaises(PrivateDataDecryptionError):
                decrypt_private_data(bytes(encrypted), context="member:cv")

    def test_previous_key_supports_safe_rotation(self):
        old_key = encoded_key(9)
        new_key = encoded_key(10)
        with patch.dict(os.environ, {"VITAMINE_DATA_ENCRYPTION_KEY": old_key}, clear=False):
            encrypted = encrypt_private_data(b"private", context="member:cv")
            old_identifier = active_key_identifier()
        with patch.dict(
            os.environ,
            {
                "VITAMINE_DATA_ENCRYPTION_KEY": new_key,
                "VITAMINE_DATA_ENCRYPTION_PREVIOUS_KEYS": old_key,
            },
            clear=False,
        ):
            self.assertNotEqual(active_key_identifier(), old_identifier)
            self.assertEqual(decrypt_private_data(encrypted, context="member:cv"), b"private")


if __name__ == "__main__":
    unittest.main()
