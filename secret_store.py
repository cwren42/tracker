"""Transparent encryption-at-rest for UI-managed Setting secrets.

These secrets are configured through the Settings UI, so they must stay in the
DB (env would break the UI) — but they should not be plaintext. Values are
Fernet-encrypted with a key from the environment (SETTINGS_ENCRYPTION_KEY) and
tagged with an 'enc:v1:' marker.

decrypt_secret() is transparent: a plaintext (un-migrated) value or any
non-secret value is returned unchanged, so it is safe to wrap every read. This
lets read-paths deploy first (no-op on plaintext), then values are migrated.
"""
import os

_PREFIX = 'enc:v1:'

# Setting keys whose values are encrypted at rest.
ENCRYPTED_SETTING_KEYS = {
    'openai_api_key',
    'teamviewer_token',
    'unifi_password',
    'ad_bind_password',
    'smtp_password',
}


def _fernet():
    key = os.environ.get('SETTINGS_ENCRYPTION_KEY')
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def encrypt_secret(value):
    """Encrypt a string value (idempotent). Returns the value unchanged if it is
    empty, already encrypted, or no key is configured."""
    if not value or not isinstance(value, str) or value.startswith(_PREFIX):
        return value
    f = _fernet()
    if f is None:
        return value
    return _PREFIX + f.encrypt(value.encode()).decode()


def decrypt_secret(value):
    """Decrypt an 'enc:v1:'-tagged value. Plaintext / non-tagged / empty values
    pass through unchanged, so this is safe to call on any Setting value."""
    if not value or not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    f = _fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value[len(_PREFIX):].encode()).decode()
    except Exception:
        return value


def encrypt_if_secret(key, value):
    """Encrypt value only when key is a known secret key (for generic savers)."""
    return encrypt_secret(value) if key in ENCRYPTED_SETTING_KEYS else value
