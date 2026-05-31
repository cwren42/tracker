"""Central AI-provider config — switch between OpenAI and an on-site OpenAI-compatible
endpoint (Ollama) by changing settings, no code edits. Context-free (raw pg_db + secret_store)
so it works identically in request handlers, workflow daemon threads, and scripts.

Settings:
  ai_base_url         https://api.openai.com/v1  (OpenAI)  |  http://<host>:11434/v1  (Ollama)
  openai_api_key      bearer key (Fernet-encrypted). Local providers ignore it.
  openai_model        chat model       — gpt-4o | llama3.1:8b | qwen2.5 | …
  openai_embed_model  embeddings model — text-embedding-3-small | nomic-embed-text | mxbai-embed-large
"""
from pg_db import pg_connect
from secret_store import decrypt_secret

OPENAI_DEFAULT = 'https://api.openai.com/v1'


def _setting(key, default=None):
    db = pg_connect()
    try:
        r = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return r["value"] if (r and r["value"]) else default
    finally:
        db.close()


def base_url():
    return (_setting('ai_base_url') or OPENAI_DEFAULT).rstrip('/')


def is_ollama():
    """True when pointed at a local/non-OpenAI endpoint (no key required, no token limits)."""
    return 'api.openai.com' not in base_url()


def api_key():
    v = _setting('openai_api_key')
    if v:
        try:
            return decrypt_secret(v)
        except Exception:
            return v
    return 'ollama'   # local endpoints ignore the bearer; any value works


def ready():
    """Is an AI provider usable? True for any local endpoint, or OpenAI with a real key."""
    return is_ollama() or bool(_setting('openai_api_key'))


def chat_model():
    return _setting('openai_model') or 'gpt-4o'


def embed_model():
    return _setting('openai_embed_model') or 'text-embedding-3-small'
