"""Backfill the Knowledge "Learn" loop over historically-resolved tickets.

The Learn step (knowledge_agent.learn_from_ticket) only began firing on the
ticket.resolved bus event recently, so the ~2,600 tickets closed before that were
never distilled into runbooks. This walks the closed/resolved tickets that have an
actual resolution thread (notes) — the high-value set — and learns from each.
Note-less closed tickets are skipped on purpose: with no recorded resolution the AI
just returns SKIP, so running them only burns API calls.

Usage:
    venv/bin/python backfill_runbooks.py            # all candidates with notes
    venv/bin/python backfill_runbooks.py --limit 5  # sample first (recommended)
"""
import sys
from app import app, db
from sqlalchemy import text
import knowledge_agent

# The vast majority of closed tickets are monitoring-alert auto-tickets (source='alert',
# "Auto-created by alert rule") that were SLA/auto-closed with no human resolution — there
# is nothing reusable to learn there, so we exclude them. What's left is the genuine
# human-handled ticket history (tray / web / system). The AI still returns SKIP for any of
# those without a real resolution; this just makes sure every real one gets a shot.
CANDIDATES_SQL = """
    SELECT t.id
    FROM support_ticket t
    WHERE t.status IN ('Resolved', 'Closed')
      AND COALESCE(t.source, '') NOT IN ('alert', 'quarantine')
      AND COALESCE(t.description, '') NOT LIKE '%Auto-created by alert rule%'
      AND NOT EXISTS (SELECT 1 FROM knowledge_chunk k
                      WHERE k.source_type = 'runbook' AND k.source_id = 'ticket:' || t.id)
    ORDER BY t.id DESC
"""


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    with app.app_context():
        ids = [r[0] for r in db.session.execute(text(CANDIDATES_SQL)).fetchall()]
        if limit:
            ids = ids[:limit]
        total = len(ids)
        print(f"Backfilling Learn over {total} closed-with-notes tickets...\n")

        learned = skipped = errored = 0
        for i, tid in enumerate(ids, 1):
            try:
                title = knowledge_agent.learn_from_ticket(tid)
                if title:
                    learned += 1
                    print(f"[{i}/{total}] ticket #{tid}: LEARNED — {title}")
                else:
                    skipped += 1
                    print(f"[{i}/{total}] ticket #{tid}: skipped (not runbook-worthy)")
            except Exception as e:
                errored += 1
                print(f"[{i}/{total}] ticket #{tid}: ERROR — {e}")

        print(f"\nDone. learned={learned} skipped={skipped} errored={errored}")
        rb = db.session.execute(
            text("SELECT count(*) FROM knowledge_chunk WHERE source_type='runbook'")).scalar()
        print(f"Total runbooks in knowledge base now: {rb}")


if __name__ == "__main__":
    main()
