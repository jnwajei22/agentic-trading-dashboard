from __future__ import annotations

import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    pass


class VersionedSecretCipher:
    def __init__(self, keys: dict[str, str], active_version: str) -> None:
        if active_version not in keys:
            raise ValueError("The active encryption key version is unavailable.")
        self.keys = keys
        self.active_version = active_version

    @staticmethod
    def _fernet(secret: str) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def encrypt(self, plaintext: str) -> tuple[bytes, str]:
        return self._fernet(self.keys[self.active_version]).encrypt(plaintext.encode()), self.active_version

    def decrypt(self, ciphertext: bytes, version: str) -> str:
        secret = self.keys.get(version)
        if not secret:
            raise SecretDecryptionError("The credential encryption key version is unavailable.")
        try:
            return self._fernet(secret).decrypt(ciphertext).decode()
        except InvalidToken:
            raise SecretDecryptionError("Stored credentials cannot be decrypted.") from None

    def reencrypt(self, ciphertext: bytes, version: str) -> tuple[bytes, str]:
        return self.encrypt(self.decrypt(ciphertext, version))
