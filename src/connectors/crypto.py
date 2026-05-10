"""Cifrado AES-GCM para credenciales de conectores.

Diseño:
- DEK (Data Encryption Key): 32 bytes aleatorios, uno por ``connector_config``.
- KEK (Key Encryption Key): derivada de ``CONNECTOR_MASTER_KEY`` (env var, 32 bytes hex).
  En producción la KEK viene de un KMS; en dev/test se usa una clave fija.
- El campo ``encrypted_credentials`` almacenado en BD es:
    nonce_dek (12) || tag_dek (16) || dek_cifrado (32) || nonce_creds (12) || tag_creds (16) || creds_cifrado (N)

La función ``encrypt_credentials`` devuelve bytes listos para BYTEA.
La función ``decrypt_credentials`` recibe esos mismos bytes y devuelve el dict JSON.
"""

import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_AAD_DEK = b"chatpro-dek-v1"
_AAD_CREDS = b"chatpro-creds-v1"


def _get_kek() -> bytes:
    """Retorna la KEK de 32 bytes desde la variable de entorno."""
    raw = os.environ.get("CONNECTOR_MASTER_KEY", "")
    if raw:
        key = bytes.fromhex(raw)
        if len(key) == 32:
            return key
    # En dev/test se usa una clave fija predecible (no usar en producción).
    return b"chatpro-dev-key-0000000000000000"


def encrypt_credentials(credentials: dict) -> bytes:
    """Cifra ``credentials`` y retorna el blob para almacenar en BD."""
    kek = _get_kek()
    dek = secrets.token_bytes(32)

    # 1. Cifrar DEK con KEK.
    nonce_dek = secrets.token_bytes(12)
    aesgcm_kek = AESGCM(kek)
    dek_cifrado = aesgcm_kek.encrypt(nonce_dek, dek, _AAD_DEK)  # incluye tag al final

    # 2. Cifrar credenciales con DEK.
    nonce_creds = secrets.token_bytes(12)
    aesgcm_dek = AESGCM(dek)
    creds_bytes = json.dumps(credentials, ensure_ascii=False).encode()
    creds_cifrado = aesgcm_dek.encrypt(nonce_creds, creds_bytes, _AAD_CREDS)

    return nonce_dek + dek_cifrado + nonce_creds + creds_cifrado


def decrypt_credentials(blob: bytes) -> dict:
    """Descifra el blob y retorna las credenciales como dict."""
    kek = _get_kek()

    # DEK cifrado: nonce(12) + dek_cifrado(32+16=48)
    nonce_dek = blob[:12]
    dek_cifrado = blob[12:60]          # 12 + 48
    nonce_creds = blob[60:72]          # 12 bytes
    creds_cifrado = blob[72:]

    aesgcm_kek = AESGCM(kek)
    dek = aesgcm_kek.decrypt(nonce_dek, dek_cifrado, _AAD_DEK)

    aesgcm_dek = AESGCM(dek)
    creds_bytes = aesgcm_dek.decrypt(nonce_creds, creds_cifrado, _AAD_CREDS)

    return json.loads(creds_bytes.decode())
