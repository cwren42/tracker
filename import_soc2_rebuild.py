#!/usr/bin/env python3
"""
SOC2 clean rebuild importer (2026-06).

Single source of truth:
  - Controls  -> SOC2Control            (soc2_models.py)
  - Evidence  -> StrikeGraphEvidence     (soc2_models.py)
  - Risks     -> Risk                    (models.py)
  - Risk<->Control links -> ControlRiskMapping (control_id now FKs soc2_control.id)

Sources (templates/ISMS-MANUAL/):
  - cirque_corporation-controls-6-4-2026-sg (1).csv   (55 controls)
  - Cirque-Control-Descriptions-v3-2026-05-08.xlsx     (33 Cirque descriptions, header row 4)
  - cirque_corporation-evidence-6-4-2026-sg (1).csv   (93 evidence)
  - cirque_corporation-risks-6-4-2026-sg.csv          (38 risks)

Idempotent: upsert by unique name. Re-runnable. Logs unresolved names; never drops records.
"""
import csv
import os
import sys
from datetime import datetime

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import app, db
from soc2_models import SOC2Control, StrikeGraphEvidence
from models import Risk, ControlRiskMapping, ISMSDocument
# Curated evidence-name -> control-name mapping the team already maintains.
# Used only as a fallback after exact-name match, and only when the target
# resolves to a control that actually exists in the current catalog (no guessing).
from load_strikegraph_evidence import get_control_mappings

SRC = "/var/www/tracker/templates/ISMS-MANUAL"
CONTROLS_CSV = os.path.join(SRC, "cirque_corporation-controls-6-4-2026-sg (1).csv")
CIRQUE_XLSX = os.path.join(SRC, "Cirque-Control-Descriptions-v3-2026-05-08.xlsx")
EVIDENCE_CSV = os.path.join(SRC, "cirque_corporation-evidence-6-4-2026-sg (1).csv")
RISKS_CSV = os.path.join(SRC, "cirque_corporation-risks-6-4-2026-sg.csv")

ISMS_MANUAL_SLUG = "isms-manual"


def _bool(v):
    return str(v).strip().upper() == "TRUE"


def parse_date(s):
    if not s or str(s).strip().lower() == "null":
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except Exception:
        return None


def parse_tsc(audit_alignment):
    """Normalize the Audit Alignment string into clean TSC refs (kept as text)."""
    if not audit_alignment:
        return None
    refs = [r.strip() for r in audit_alignment.split(",") if r.strip()]
    return ", ".join(refs) if refs else None


def load_cirque_descriptions():
    """Return {control_name: (cirque_description, authoritative_docs)} from the xlsx."""
    wb = openpyxl.load_workbook(CIRQUE_XLSX, data_only=True)
    ws = wb["Control Descriptions"]
    rows = list(ws.iter_rows(values_only=True))
    # header is row 4 (1-indexed) -> index 3; data starts index 4
    out = {}
    for r in rows[4:]:
        if not r or not r[0]:
            continue
        name = str(r[0]).strip()
        desc = (str(r[2]).strip() if r[2] else None)
        docs = (str(r[3]).strip() if r[3] else None)
        out[name] = (desc, docs)
    return out


def run():
    with app.app_context():
        manual = ISMSDocument.query.filter_by(slug=ISMS_MANUAL_SLUG).first()
        manual_id = manual.id if manual else None
        if not manual_id:
            print("WARN: ISMS manual document not found; isms_document_id will be NULL")

        cirque = load_cirque_descriptions()
        print(f"Cirque xlsx descriptions: {len(cirque)}")

        # ---- 1) Controls ----
        with open(CONTROLS_CSV, newline="", encoding="utf-8-sig") as f:
            control_rows = list(csv.DictReader(f))
        print(f"Controls CSV rows: {len(control_rows)}")

        cirque_applied = 0
        for row in control_rows:
            name = row["Control Name"].strip()
            sg_desc = (row["Control Description"] or "").strip() or None
            description = sg_desc
            authoritative_docs = None
            isms_doc = None
            if name in cirque:
                c_desc, c_docs = cirque[name]
                if c_desc:
                    description = c_desc  # Cirque overrides StrikeGraph
                authoritative_docs = c_docs
                isms_doc = manual_id
                cirque_applied += 1

            ctrl = SOC2Control.query.filter_by(control_name=name).first()
            if not ctrl:
                ctrl = SOC2Control(control_name=name)
                db.session.add(ctrl)
            ctrl.control_description = description
            ctrl.control_frequency = (row["Control Frequency"] or "").strip() or None
            ctrl.control_owner = (row["Control Owner"] or "").strip() or None
            ctrl.control_progress = (row["Control Progress"] or "").strip() or None
            ctrl.is_active = _bool(row["Inactive/Active"])
            ctrl.audit_alignment = parse_tsc(row["Audit Alignment"])
            ctrl.authoritative_docs = authoritative_docs
            ctrl.isms_document_id = isms_doc
        db.session.commit()
        print(f"Controls upserted: {SOC2Control.query.count()} (Cirque desc applied to {cirque_applied})")

        # control name -> id (case-insensitive map for resolution)
        controls = {c.control_name: c.id for c in SOC2Control.query.all()}
        controls_ci = {k.lower(): v for k, v in controls.items()}

        # ---- 2) Evidence ----
        with open(EVIDENCE_CSV, newline="", encoding="utf-8-sig") as f:
            ev_rows = list(csv.DictReader(f))
        print(f"Evidence CSV rows: {len(ev_rows)}")

        curated = get_control_mappings()
        evidence_unmatched = []
        ev_linked = 0
        ev_linked_curated = 0
        for row in ev_rows:
            name = row["Evidence Name"].strip()
            # 1) Confident link: evidence name exactly equals a control name.
            cid = controls.get(name) or controls_ci.get(name.lower())
            # 2) Fallback: curated mapping, but only if its target control exists.
            if not cid:
                tgt = curated.get(name)
                if tgt:
                    cid = controls.get(tgt) or controls_ci.get(tgt.lower())
                    if cid:
                        ev_linked_curated += 1
            if cid:
                ev_linked += 1
            else:
                evidence_unmatched.append(name)
            try:
                exp_schedule = int(row["Expiration Schedule"]) if row["Expiration Schedule"] else None
            except Exception:
                exp_schedule = None

            ev = StrikeGraphEvidence.query.filter_by(evidence_name=name).first()
            if not ev:
                ev = StrikeGraphEvidence(evidence_name=name)
                db.session.add(ev)
            ev.control_id = cid
            ev.evidence_description = (row["Evidence Description"] or "").strip() or None
            ev.evidence_type = (row["Type"] or "").strip() or None
            ev.expiration_schedule = exp_schedule
            ev.expiration_date = parse_date(row["Expiration Date"])
            ev.is_active = _bool(row["Inactive/Active"])
            ev.owner = (row["Evidence Owner"] or "").strip() or None
        db.session.commit()
        print(f"Evidence upserted: {StrikeGraphEvidence.query.count()} "
              f"(linked to a control: {ev_linked}; of which via curated fallback: {ev_linked_curated})")

        # ---- 3) Risks ----
        with open(RISKS_CSV, newline="", encoding="utf-8-sig") as f:
            risk_rows = list(csv.DictReader(f))
        print(f"Risks CSV rows: {len(risk_rows)}")

        for row in risk_rows:
            name = row["Risk Name"].strip()
            r = Risk.query.filter_by(risk_name=name).first()
            if not r:
                r = Risk(risk_name=name)
                db.session.add(r)
            r.risk_description = (row["Risk Description"] or "").strip() or None
            r.risk_treatment = (row["Risk Treatment"] or "").strip() or None
            r.risk_progress = (row["Risk Progress"] or "").strip() or None
            r.risk_category = (row["Risk Category"] or "").strip() or None
            r.risk_status = _bool(row["Risk Status"])
            r.risk_impact = (row["Risk Impact"] or "").strip() or None
            r.risk_likelihood = (row["Risk Likelihood"] or "").strip() or None
            r.risk_combined_score = (row["Risk Combined Score"] or "").strip() or None
            r.risk_owner = (row["Risk Owner"] or "").strip() or None
            r.active_controls = (row["Active Controls"] or "").strip() or None
        db.session.commit()
        print(f"Risks upserted: {Risk.query.count()}")

        # ---- 4) Risk <-> Control mappings (rebuild from Active Controls) ----
        ControlRiskMapping.query.delete()
        db.session.commit()
        risks = {r.risk_name: r.id for r in Risk.query.all()}
        mapping_unresolved = []
        seen = set()
        link_count = 0
        for row in risk_rows:
            rid = risks[row["Risk Name"].strip()]
            for cname in (row["Active Controls"] or "").split(","):
                cname = cname.strip()
                if not cname:
                    continue
                cid = controls.get(cname) or controls_ci.get(cname.lower())
                if not cid:
                    mapping_unresolved.append((row["Risk Name"].strip(), cname))
                    continue
                key = (cid, rid)
                if key in seen:
                    continue
                seen.add(key)
                db.session.add(ControlRiskMapping(control_id=cid, risk_id=rid))
                link_count += 1
        db.session.commit()
        print(f"Risk-control mappings created: {ControlRiskMapping.query.count()}")

        # ---- Report ----
        print("\n=== UNRESOLVED / NOTES ===")
        print(f"Evidence with NO confident control match ({len(evidence_unmatched)}):")
        for n in evidence_unmatched:
            print(f"  - {n}")
        print(f"\nRisk->Control names that did NOT resolve ({len(mapping_unresolved)}):")
        for rn, cn in mapping_unresolved:
            print(f"  - risk '{rn}' -> control '{cn}'")


if __name__ == "__main__":
    run()
