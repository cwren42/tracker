"""Sign agent self-update payloads so agents can verify authenticity before swapping.

WHY: the agent self-update is a fleet-global source-swap fetched over a TLS channel the
agent does NOT verify (CERT_NONE, because the internal cert doesn't match the hostname).
That made the update path a fleet-wide RCE vector — a network MITM (or tampered served
file) could feed a malicious agent_client.py that runs as SYSTEM. Signing closes that:
the server signs the served file with a private key that lives ONLY here
(.agent_signing/agent_update_key.pem, gitignored); the agent embeds the PUBLIC key and
verifies RSA-PKCS1v15(SHA-256(file)) with pure stdlib before applying an update. A forged
payload is rejected even over an unverified channel.

Agent-side verification is pure stdlib (hashlib + int pow) on purpose: the self-update
swaps agent_client.py source WITHOUT updating bundled libraries, so the agent cannot rely
on `cryptography` being present. The server signs with `cryptography` (available here).
"""
import functools
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".agent_signing", "agent_update_key.pem")


@functools.lru_cache(maxsize=1)
def _private_key():
    with open(_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def signing_available() -> bool:
    return os.path.isfile(_KEY_PATH)


# (path, mtime, size) -> hex signature, so we don't re-sign an unchanged file on every poll.
_cache: dict = {}


def sign_file(path: str) -> str:
    """RSA-PKCS1v15(SHA-256(file_bytes)) as hex. Cached by file stat."""
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    cached = _cache.get(key)
    if cached:
        return cached
    with open(path, "rb") as f:
        data = f.read()
    sig = _private_key().sign(data, padding.PKCS1v15(), hashes.SHA256()).hex()
    _cache.clear()
    _cache[key] = sig
    return sig
