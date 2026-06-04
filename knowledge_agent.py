"""Knowledge Agent — the brain's "what to do" half (the graph is the "what's true" half).

Semantic search + RAG over the ISMS policies and system documentation. pgvector isn't
available on this Postgres, and the corpus is small (~600 sections), so we store OpenAI
embeddings as JSONB and brute-force cosine similarity in Python at query time — fast enough
for hundreds of chunks, zero new infrastructure.

Pipeline: reindex() pulls the corpus (policy_section / system_description / isms_document
versions) -> embeds each chunk -> knowledge_chunk(embedding JSONB). search() embeds the
query and ranks by cosine. answer() does retrieval-augmented generation: top-k chunks ->
the chat model, grounded + cited. As the brain resolves incidents it can later write its
own runbooks back into this corpus (the "Learn" step). See docs/AGENTIC_IT_OS_GAMEPLAN.md.
"""
import json, logging, math
from datetime import datetime

import requests as _http

from pg_db import pg_connect

log = logging.getLogger("knowledge_agent")

EMBED_MODEL = "text-embedding-3-small"
EMBED_URL = "https://api.openai.com/v1/embeddings"
EMBED_BATCH = 96
CHUNK_CHARS = 1500
TOP_K = 6


def _db():
    return pg_connect()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # local time (TZ=America/Denver), see now_mst


def ensure_schema():
    db = _db()
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_chunk ("
            " id SERIAL PRIMARY KEY,"
            " source_type VARCHAR(40) NOT NULL,"
            " source_id VARCHAR(80),"
            " title TEXT,"
            " content TEXT NOT NULL,"
            " embedding JSONB,"
            " updated_at TIMESTAMP)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_src ON knowledge_chunk(source_type, source_id)")
        # Library categorization (GitHub-style folders). Only meaningful for the editable
        # library types; policy/ISMS/system chunks keep NULL. Idempotent migration + backfill.
        db.execute("ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS category VARCHAR(60)")
        db.execute("UPDATE knowledge_chunk SET category='Runbooks' "
                   "WHERE category IS NULL AND source_type IN ('runbook','manual')")
        db.commit()
    finally:
        db.close()


def _api_key():
    """Bearer for the configured provider (OpenAI or on-site Ollama). Context-free."""
    import ai_config
    if not ai_config.ready():
        raise ValueError("AI not configured — set an OpenAI key or point ai_base_url at Ollama (Settings → AI)")
    return ai_config.api_key()


def _embed(texts):
    """Embed a list of strings -> list of float vectors, via the configured provider. Batched."""
    if not texts:
        return []
    import ai_config
    key = _api_key()
    url = ai_config.base_url() + "/embeddings"
    model = ai_config.embed_model()
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:8000] for t in texts[i:i + EMBED_BATCH]]  # cap per-input length
        resp = _http.post(
            url, json={"model": model, "input": batch},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=120,
        )
        resp.raise_for_status()
        out.extend([d["embedding"] for d in resp.json()["data"]])
    return out


def _chunks(text, size=CHUNK_CHARS):
    """Split on paragraph boundaries into ~size-char windows."""
    paras = text.split("\n\n")
    buf, out = "", []
    for p in paras:
        if buf and len(buf) + len(p) > size:
            out.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf.strip():
        out.append(buf)
    return out or [text[:size]]


def _gather_corpus():
    """(source_type, source_id, title, content) tuples from the ISMS/system corpus.
    Must run inside an app context (uses the ORM)."""
    from models import (PolicySection, Policy, SystemDescription,
                        ISMSDocument, ISMSDocumentVersion)
    items = []
    pol_titles = {p.id: p.title for p in Policy.query.all()}
    for s in PolicySection.query.all():
        body = (s.section_content or "").strip()
        if not body:
            continue
        title = f"{pol_titles.get(s.policy_id, 'Policy')} — {s.section_title}"
        items.append(("policy_section", str(s.id), title, body))
    for sd in SystemDescription.query.all():
        body = (sd.content or "").strip()
        if body:
            items.append(("system_description", str(sd.id), sd.section_title, body))
    for doc in ISMSDocument.query.all():
        ver = ISMSDocumentVersion.query.get(doc.current_version_id) if doc.current_version_id else None
        body = ((ver.markdown_body if ver else "") or "").strip()
        if not body:
            continue
        for j, chunk in enumerate(_chunks(body)):
            items.append(("isms_document", f"{doc.id}.{j}", doc.title, chunk))
    return items


def reindex():
    """Rebuild the knowledge index from the corpus. Returns chunk count. App context required."""
    ensure_schema()
    items = _gather_corpus()
    vectors = _embed([f"{t}\n\n{c}" for (_st, _sid, t, c) in items])
    db = _db()
    try:
        # Rebuild ONLY the static corpus; preserve generated entries (learned runbooks +
        # operator-authored manual notes).
        db.execute("DELETE FROM knowledge_chunk WHERE source_type IN "
                   "('policy_section','system_description','isms_document')")
        for (st, sid, title, content), vec in zip(items, vectors):
            db.execute(
                "INSERT INTO knowledge_chunk (source_type, source_id, title, content, embedding, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (st, sid, title, content, json.dumps(vec), _now()),
            )
        db.commit()
    finally:
        db.close()
    log.info("knowledge reindex: %d chunks", len(items))
    return len(items)


def reembed_all():
    """Re-embed EVERY chunk with the CURRENT provider/model. Required after switching embedding
    providers — OpenAI (1536-dim) and Ollama models (e.g. 768/1024-dim) aren't comparable, so a
    mixed corpus would break cosine search. One-time, best-effort per chunk."""
    db = _db()
    try:
        rows = db.execute("SELECT id, title, content FROM knowledge_chunk").fetchall()
    finally:
        db.close()
    n = 0
    for r in rows:
        try:
            vec = _embed([f"{r['title']}\n\n{r['content']}"])[0]
        except Exception:
            log.exception("re-embed failed for chunk %s", r["id"])
            continue
        d2 = _db()
        try:
            d2.execute("UPDATE knowledge_chunk SET embedding=?, updated_at=? WHERE id=?",
                       (json.dumps(vec), _now(), r["id"]))
            d2.commit()
        finally:
            d2.close()
        n += 1
    log.info("re-embedded %d chunks", n)
    return n


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(query, k=TOP_K):
    """Top-k corpus chunks by cosine similarity to the query."""
    qv = _embed([query])[0]
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, source_type, source_id, title, content, embedding FROM knowledge_chunk"
        ).fetchall()
    finally:
        db.close()
    scored = []
    for r in rows:
        emb = r["embedding"]
        if isinstance(emb, (str, bytes, bytearray)):
            emb = json.loads(emb)
        if not emb:
            continue
        scored.append((_cosine(qv, emb), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": round(s, 3), "id": r["id"], "source_type": r["source_type"],
             "source_id": r["source_id"], "title": r["title"], "content": r["content"]}
            for s, r in scored[:k]]


def answer(query, k=TOP_K):
    """Retrieval-augmented answer grounded in the corpus, with cited sources."""
    hits = search(query, k)
    if not hits:
        return {"answer": "The knowledge base is empty — click **Reindex** to build it from the "
                          "ISMS policies and system documentation.", "sources": []}
    context = "\n\n".join(f"[{i + 1}] {h['title']}\n{h['content'][:1200]}" for i, h in enumerate(hits))
    from email_agent import run_chat
    system = ("You are the IT knowledge assistant for this organization. Answer the question using ONLY "
              "the provided context excerpts from the ISMS policies and system documentation. Cite the "
              "sources you use as [n]. If the answer isn't in the context, say so plainly rather than "
              "guessing. Be concise and practical; use Markdown.")
    user = f"Question: {query}\n\nContext excerpts:\n{context}"
    try:
        content, _model = run_chat(system, user, max_tokens=600)
    except Exception as e:
        content = f"_(Retrieved {len(hits)} sources, but could not synthesize an answer: {e})_"
    return {"answer": content, "sources": hits}


def count(source_type=None):
    try:
        db = _db()
        try:
            if source_type:
                row = db.execute("SELECT COUNT(*) AS n FROM knowledge_chunk WHERE source_type=?",
                                 (source_type,)).fetchone()
            else:
                row = db.execute("SELECT COUNT(*) AS n FROM knowledge_chunk").fetchone()
            return row["n"] if row else 0
        finally:
            db.close()
    except Exception:
        return 0


def learn_from_ticket(ticket_id):
    """The 'Learn' step — distill a resolved ticket into a reusable runbook and file it into
    the knowledge base, so the corpus compounds from real operations. Best-effort; returns
    the runbook title or None (None when the AI judges the ticket not runbook-worthy). App
    context required (reads the ORM + calls the AI)."""
    from models import SupportTicket, TicketNote
    t = SupportTicket.query.get(ticket_id)
    if not t:
        return None
    notes = (TicketNote.query.filter_by(ticket_id=ticket_id)
             .order_by(TicketNote.created_at).all())
    convo = "\n".join(f"- {'[internal] ' if n.is_internal else ''}{n.content}"
                      for n in notes if (n.content or '').strip())
    body = (f"Subject: {t.subject}\nCategory: {t.category}\n"
            f"Description: {t.description or ''}\n\nResolution thread:\n{convo or '(no notes recorded)'}")

    from email_agent import run_chat
    system = (
        "You curate an IT runbook library. Turn this RESOLVED support ticket into a concise, reusable "
        "runbook for next time. DEFAULT to writing one. Reply with exactly 'SKIP' ONLY when there is "
        "nothing reusable to capture — i.e. no actual resolution was recorded, or it's a pure one-off with "
        "no generalizable procedure (e.g. a hardware swap). When you write it, generalize away the specific "
        "person/asset and reply in Markdown with: a single short title line, then '## Problem', "
        "'## Resolution' (numbered steps), and '## Notes'. No preamble."
    )
    try:
        content, _model = run_chat(system, body, max_tokens=500)
    except Exception:
        log.exception("learn_from_ticket: AI call failed for ticket %s", ticket_id)
        return None
    content = (content or "").strip()
    if not content or content.upper().startswith("SKIP"):
        return None
    title = (content.splitlines()[0].lstrip("# ").strip() or f"Runbook: {t.subject}")[:200]
    try:
        vec = _embed([f"{title}\n\n{content}"])[0]
    except Exception:
        log.exception("learn_from_ticket: embed failed for ticket %s", ticket_id)
        return None
    db = _db()
    try:
        # Upsert: replace any prior runbook learned from this same ticket.
        db.execute("DELETE FROM knowledge_chunk WHERE source_type='runbook' AND source_id=?",
                   (f"ticket:{ticket_id}",))
        db.execute(
            "INSERT INTO knowledge_chunk (source_type, source_id, title, content, embedding, updated_at) "
            "VALUES ('runbook',?,?,?,?,?)",
            (f"ticket:{ticket_id}", title, content, json.dumps(vec), _now()),
        )
        db.commit()
    finally:
        db.close()
    log.info("learned runbook from ticket %s: %s", ticket_id, title)
    return title


# Human-readable label per email decision, for the runbook prompt + title.
_EMAIL_DECISIONS = {
    "released":         "RELEASED to the inbox (treated as a false positive / safe)",
    "kept_quarantined": "KEPT in quarantine (the quarantine was correct)",
    "blocked":          "BLOCKED (confirmed malicious / unwanted)",
    "deleted":          "DELETED (confirmed malicious / unwanted)",
}


def learn_from_email_decision(message_id, decision, actor=None):
    """The email-security half of 'Learn' — distill a *human decision* on a quarantined
    message into a reusable email-triage runbook, so the brain's future verdicts are
    grounded in what real operators actually did. Keyed source_id='qmsg:<id>' so a later
    decision on the same message refreshes it. App context required (ORM + AI). Returns the
    runbook title, or None when there's nothing generalizable to capture."""
    from models import QuarantineMessage
    from email_agent import build_message_summary, run_chat
    msg = QuarantineMessage.query.filter_by(message_id=message_id).first()
    if not msg:
        return None
    decision_label = _EMAIL_DECISIONS.get(decision, decision)
    summary = build_message_summary(msg, include_headers=False)
    body = (f"DECISION: A human {decision_label}.\n"
            f"{'Actioned by: ' + actor if actor else ''}\n\n"
            f"MESSAGE:\n{summary}")
    system = (
        "You curate an email-security triage runbook library. Turn this HUMAN DECISION on a "
        "quarantined/blocked email into a concise, reusable triage rule for next time. DEFAULT to "
        "writing one. Reply with exactly 'SKIP' ONLY when there's nothing generalizable (e.g. a pure "
        "one-off with no pattern). Generalize away the individual recipient; KEEP the signal that drove "
        "the call — sender domain, SPF/DKIM/DMARC posture, threat/quarantine reason, subject pattern, "
        "URL/attachment shape. Reply in Markdown with: a single short title line, then '## Signal' "
        "(what this kind of mail looks like), '## Decision' (what was done and why), and "
        "'## When to apply'. No preamble."
    )
    try:
        content, _model = run_chat(system, body, max_tokens=500)
    except Exception:
        log.exception("learn_from_email_decision: AI call failed for %s", message_id)
        return None
    content = (content or "").strip()
    if not content or content.upper().startswith("SKIP"):
        return None
    title = (content.splitlines()[0].lstrip("# ").strip()
             or f"Email triage: {msg.sender_domain or 'unknown sender'}")[:200]
    try:
        vec = _embed([f"{title}\n\n{content}"])[0]
    except Exception:
        log.exception("learn_from_email_decision: embed failed for %s", message_id)
        return None
    db = _db()
    try:
        db.execute("DELETE FROM knowledge_chunk WHERE source_type='runbook' AND source_id=?",
                   (f"qmsg:{message_id}",))
        db.execute(
            "INSERT INTO knowledge_chunk (source_type, source_id, title, content, embedding, updated_at) "
            "VALUES ('runbook',?,?,?,?,?)",
            (f"qmsg:{message_id}", title, content, json.dumps(vec), _now()),
        )
        db.commit()
    finally:
        db.close()
    log.info("learned email runbook from %s (%s): %s", message_id, decision, title)
    return title


def add_manual(title, content):
    """Operator-authored knowledge entry (a how-to / SOP written directly). Embeds + stores
    as source_type='manual'. Returns the chunk id. App/raw context-agnostic."""
    title = (title or "").strip()
    content = (content or "").strip()
    if not content:
        raise ValueError("Content is required.")
    if not title:
        title = content[:80]
    vec = _embed([f"{title}\n\n{content}"])[0]
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO knowledge_chunk (source_type, source_id, title, content, embedding, updated_at) "
            "VALUES ('manual', NULL, ?, ?, ?, ?)",
            (title, content, json.dumps(vec), _now()),
        )
        cid = cur.lastrowid
        db.commit()
    finally:
        db.close()
    log.info("manual knowledge added: %s (#%s)", title, cid)
    return cid


# Operational/learned knowledge — the runbook library. 'runbook' = distilled from a
# resolved ticket/email or hand-authored; 'manual' = an operator-written note. These
# are the editable, human-browsable knowledge; ISMS policy + system docs live in their
# own subsystems (ISMS Manual / Settings → Systems) and are not part of this library.
LIBRARY_TYPES = ("runbook", "manual")

# Starter folders for the library. Free-form — operators can type a new one when
# authoring; this list just seeds the picker and the empty-folder display.
CATEGORIES = ["Runbooks", "Domain/AD", "Backups", "Network", "Email",
              "Security", "Hardware", "Software", "Microsoft 365", "General"]


def list_knowledge(types=LIBRARY_TYPES, category=None):
    """Library listing of the learned/operational knowledge, newest first. No embeddings
    (kept out of the payload — they're large). Optionally filter to one category."""
    db = _db()
    try:
        ph = ",".join("?" for _ in types)
        sql = ("SELECT id, source_type, source_id, title, category, "
               "LEFT(content, 280) AS excerpt, LENGTH(content) AS len, updated_at "
               f"FROM knowledge_chunk WHERE source_type IN ({ph})")
        params = list(types)
        if category is not None:
            sql += " AND COALESCE(category,'Runbooks')=?"
            params.append(category)
        sql += " ORDER BY updated_at DESC"
        rows = db.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def category_counts(types=LIBRARY_TYPES):
    """[(category, count)] over the library — the GitHub-style 'folders'. NULL maps to
    'Runbooks'. Sorted by the CATEGORIES order first, then any extras alphabetically."""
    db = _db()
    try:
        ph = ",".join("?" for _ in types)
        rows = db.execute(
            f"SELECT COALESCE(category,'Runbooks') AS category, COUNT(*) AS n "
            f"FROM knowledge_chunk WHERE source_type IN ({ph}) "
            f"GROUP BY COALESCE(category,'Runbooks')",
            tuple(types),
        ).fetchall()
        counts = {r["category"]: r["n"] for r in rows}
        order = {c: i for i, c in enumerate(CATEGORIES)}
        cats = sorted(counts.keys(), key=lambda c: (order.get(c, 999), c.lower()))
        return [(c, counts[c]) for c in cats]
    finally:
        db.close()


def get_chunk(chunk_id):
    """Full single entry (with content) for the reader/editor. None if missing."""
    db = _db()
    try:
        r = db.execute(
            "SELECT id, source_type, source_id, title, content, category, updated_at "
            "FROM knowledge_chunk WHERE id=?", (chunk_id,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def update_chunk(chunk_id, title, content, category=None):
    """Edit a runbook/manual entry in place and re-embed. Raises ValueError if the entry
    is missing or is a managed-elsewhere type (policy/ISMS/system doc)."""
    title = (title or "").strip()
    content = (content or "").strip()
    category = (category or "").strip() or "Runbooks"
    if not content:
        raise ValueError("Content is required.")
    if not title:
        title = content[:80]
    db = _db()
    try:
        r = db.execute("SELECT source_type FROM knowledge_chunk WHERE id=?", (chunk_id,)).fetchone()
        if not r:
            raise ValueError("Entry not found.")
        if r["source_type"] not in LIBRARY_TYPES:
            raise ValueError(f"{r['source_type']} entries are managed in their own subsystem and can't be edited here.")
        vec = _embed([f"{title}\n\n{content}"])[0]   # network call; ok to hold the conn
        db.execute(
            "UPDATE knowledge_chunk SET title=?, content=?, category=?, embedding=?, updated_at=? WHERE id=?",
            (title, content, category, json.dumps(vec), _now(), chunk_id),
        )
        db.commit()
    finally:
        db.close()
    log.info("knowledge entry %s updated: %s [%s]", chunk_id, title, category)
    return True


def delete_chunk(chunk_id):
    """Delete a runbook/manual entry. Raises ValueError for managed-elsewhere types.
    Returns False if the entry didn't exist."""
    db = _db()
    try:
        r = db.execute("SELECT source_type FROM knowledge_chunk WHERE id=?", (chunk_id,)).fetchone()
        if not r:
            return False
        if r["source_type"] not in LIBRARY_TYPES:
            raise ValueError(f"{r['source_type']} entries are managed in their own subsystem and can't be deleted here.")
        db.execute("DELETE FROM knowledge_chunk WHERE id=?", (chunk_id,))
        db.commit()
    finally:
        db.close()
    log.info("knowledge entry %s deleted", chunk_id)
    return True


def add_runbook(title, content, category=None, source_id=None):
    """Author a runbook (source_type='runbook') directly into the library. Upserts by
    source_id when one is given (e.g. 'manual:my-slug'); otherwise inserts a new row."""
    title = (title or "").strip()
    content = (content or "").strip()
    category = (category or "").strip() or "Runbooks"
    if not content:
        raise ValueError("Content is required.")
    if not title:
        title = content.splitlines()[0][:120] if content else "Untitled runbook"
    vec = _embed([f"{title}\n\n{content}"])[0]
    db = _db()
    try:
        if source_id:
            db.execute("DELETE FROM knowledge_chunk WHERE source_type='runbook' AND source_id=?", (source_id,))
        cur = db.execute(
            "INSERT INTO knowledge_chunk (source_type, source_id, title, content, category, embedding, updated_at) "
            "VALUES ('runbook', ?, ?, ?, ?, ?, ?)",
            (source_id, title, content, category, json.dumps(vec), _now()),
        )
        cid = cur.lastrowid
        db.commit()
    finally:
        db.close()
    log.info("runbook authored: %s [%s] (#%s)", title, category, cid)
    return cid


def add_system_doc(system_id, title, content, doc_key=None):
    """Attach a Markdown doc to an IT system — embedded into the knowledge base as
    source_type='system_doc', source_id='system:<id>[:doc_key]'. Upserts on (system,doc_key)
    so re-saving a doc replaces it. Returns the chunk id. reindex() preserves these."""
    title = (title or "").strip()
    content = (content or "").strip()
    if not content:
        raise ValueError("Content is required.")
    if not title:
        title = content[:80]
    sid = f"system:{system_id}" + (f":{doc_key}" if doc_key else "")
    vec = _embed([f"{title}\n\n{content}"])[0]
    db = _db()
    try:
        db.execute("DELETE FROM knowledge_chunk WHERE source_type='system_doc' AND source_id=?", (sid,))
        cur = db.execute(
            "INSERT INTO knowledge_chunk (source_type, source_id, title, content, embedding, updated_at) "
            "VALUES ('system_doc', ?, ?, ?, ?, ?)",
            (sid, title, content, json.dumps(vec), _now()),
        )
        cid = cur.lastrowid
        db.commit()
    finally:
        db.close()
    log.info("system doc added: %s (#%s)", title, cid)
    return cid


def system_docs(system_id):
    """List the docs attached to a system (id, title, source_id, updated_at)."""
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, title, source_id, content, updated_at FROM knowledge_chunk "
            "WHERE source_type='system_doc' AND (source_id=? OR source_id LIKE ?) ORDER BY id",
            (f"system:{system_id}", f"system:{system_id}:%"),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def learn_from_ticket_async(flask_app, ticket_id):
    """Fire-and-forget the learn step in a background thread with an app context."""
    import threading

    def _run():
        try:
            with flask_app.app_context():
                learn_from_ticket(ticket_id)
        except Exception:
            log.exception("learn_from_ticket_async failed for ticket %s", ticket_id)

    threading.Thread(target=_run, daemon=True, name=f"learn-ticket-{ticket_id}").start()
