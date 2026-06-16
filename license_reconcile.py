"""Software Asset Management reconciliation.

Maps entries in the `license` catalog (what we OWN / pay for) to what is actually
INSTALLED across the fleet (`rmm_software`, populated by the RMM agent inventory),
so /licenses can show owned-vs-used, flag over-deployment, and surface big-ticket
software that is installed but untracked.

The matching is deliberately curated, not fuzzy: each catalog entry has an include
list (regexes that identify the *real application*) and an exclude list (regexes
that strip the runtimes / SDK fragments / helper components that share the vendor
name but are NOT a paid seat — e.g. "SOLIDWORKS Document Manager API",
"Adobe Acrobat Reader", "NI LabVIEW Run-Time Engine"). All regexes are matched
case-insensitively against rmm_software.name.

A device counts as "using" a license if any installed software name matches an
include regex and none of the exclude regexes. Seats-used = distinct devices
(per-device licenses) which we also resolve to distinct employees for the per-user
view.
"""
import re

# license.id -> matcher. Keyed by id so a rename of software_name doesn't break it.
LICENSE_MATCHERS = {
    13: {  # SolidWorks (Dassault) — paid per-device CAD app
        "include": [r"^SOLIDWORKS\s+\d{4}"],
        "exclude": [r"Document Manager", r"graphics support", r"\bAPI\b",
                    r"Login Manager", r"\bCEF\b", r"3DEXPERIENCE",
                    r"Marketplace", r"Exchange", r"Toolbox", r"Explorer"],
    },
    12: {  # Adobe Acrobat (paid) — exclude the free Reader + helper services
        "include": [r"^Adobe Acrobat\b"],
        "exclude": [r"Reader", r"Refresh Manager", r"Genuine", r"Dictionaries"],
    },
    11: {  # Adobe Creative Cloud
        "include": [r"Adobe Creative Cloud"],
        "exclude": [],
    },
    10: {  # Autodesk AutoCAD LT — the app, not shared/lang-pack/helpers
        "include": [r"AutoCAD LT\s+\d{4}"],
        "exclude": [r"Shared", r"Language Pack", r"Open in Desktop",
                    r"Private", r"Performance Feedback"],
    },
    16: {  # MATLAB — the product, not the redistributable runtime
        "include": [r"^MATLAB\s+R\d{4}"],
        "exclude": [r"Runtime", r"Component", r"\bNI\b"],
    },
    15: {  # NI LabVIEW dev IDE — strip the many runtime/support/broker SKUs
        "include": [r"^NI LabVIEW\s+\d{4}"],
        "exclude": [r"Run-?Time", r"Runtime", r"Support", r"Broker", r"NBFifo",
                    r"Web Server", r"C Interface", r"Assistant", r"\bRTE\b",
                    r"\bSSL\b", r"Real-Time"],
    },
    19: {  # PTC Creo
        "include": [r"\bCreo\b", r"^PTC Creo"],
        "exclude": [],
    },
    17: {  # AMD/Xilinx Vivado ML
        "include": [r"Vivado"],
        "exclude": [r"driver", r"DocNav", r"Information Center", r"\bECM\b"],
    },
    14: {  # Microsoft Visual Studio Professional (paid; Community is free)
        "include": [r"Visual Studio Professional\s+\d{4}"],
        "exclude": [r"Tools for", r"Phone", r"Team Foundation", r"Office"],
    },
    18: {  # SAP Crystal Reports desktop (exclude the dotted SDK fragments)
        "include": [r"Crystal Reports\b"],
        "exclude": [r"sdkplugins", r"businessview", r"boe\.", r"\.cpp", r"\.java"],
    },
    9: {  # Microsoft 365 — Office Click-to-Run is the on-device proxy. The
          # authoritative seat count is the M365 subscription; treat installs as
          # a coverage signal only.
        "include": [r"^Microsoft 365 Apps", r"Office 16 Click-to-Run Extensibility"],
        "exclude": [r"Localization", r"Licensing"],
    },
}

# Big-ticket software we should TRACK but have no catalog entry for. Surfaced on
# the page as "untracked — needs a license record". (include/exclude same scheme.)
UNTRACKED_DISCOVERY = [
    {"label": "Altium Designer", "vendor": "Altium",
     "include": [r"^Altium Designer\s+\d"], "exclude": [r"Library Loader"]},
    {"label": "Siemens EDA (PADS / Mentor)", "vendor": "Siemens",
     "include": [r"^Siemens Products", r"^Mentor Graphics Products"],
     "exclude": [r"License", r"Software Center", r"Runtime", r"CAMCAD"]},
    {"label": "Keil MDK (uVision)", "vendor": "Arm/Keil",
     "include": [r"Keil.*Vision"], "exclude": [r"Driver Package"]},
    {"label": "KiCad", "vendor": "KiCad",
     "include": [r"^KiCad\s+\d"], "exclude": []},
    {"label": "Visual Studio Community", "vendor": "Microsoft",
     "include": [r"Visual Studio Community\s+\d{4}"], "exclude": []},
]


def _compile(matcher):
    inc = [re.compile(p, re.I) for p in matcher.get("include", [])]
    exc = [re.compile(p, re.I) for p in matcher.get("exclude", [])]
    return inc, exc


def _name_matches(name, inc, exc):
    if not any(p.search(name) for p in inc):
        return False
    if any(p.search(name) for p in exc):
        return False
    return True


def installs_for(cur, inc, exc):
    """Return {agent_id: matched_name} for the broadest include set, filtered in
    Python by the precise include/exclude. We pre-filter in SQL with a coarse
    ILIKE OR-set to keep the row count down, then apply the real regex here."""
    # Coarse SQL prefilter: any include's first \w token.
    like_terms = set()
    for p in inc:
        # crude: pull the first alphanumeric run from the pattern source
        m = re.search(r"[A-Za-z0-9]{3,}", p.pattern)
        if m:
            like_terms.add(m.group(0).lower())
    if not like_terms:
        return {}
    sql = "SELECT agent_id, name FROM rmm_software WHERE " + \
          " OR ".join(["LOWER(name) LIKE %s"] * len(like_terms))
    cur.execute(sql, tuple(f"%{t}%" for t in like_terms))
    out = {}
    for agent_id, name in cur.fetchall():
        if name and _name_matches(name, inc, exc):
            out.setdefault(agent_id, name)
    return out


def reconcile(cur):
    """Compute reconciliation rows. cur is a live psycopg2 cursor.

    Returns dict with 'tracked' (one row per license) and 'untracked' (discovered
    big-ticket installs with no license record)."""
    # agent_id -> (asset_id, employee_id, asset_name)
    cur.execute("""SELECT a.agent_id, a.asset_id, ast.employee_id, ast.name
                   FROM rmm_agent a LEFT JOIN asset ast ON ast.id = a.asset_id""")
    agent_meta = {r[0]: {"asset_id": r[1], "employee_id": r[2], "asset_name": r[3]}
                  for r in cur.fetchall()}

    cur.execute("""SELECT id, software_name, vendor, license_type, total_licenses,
                          status, expiry_date FROM license ORDER BY software_name""")
    lic_rows = cur.fetchall()

    tracked = []
    for (lid, sw, vendor, ltype, total, status, expiry) in lic_rows:
        m = LICENSE_MATCHERS.get(lid)
        if m:
            inc, exc = _compile(m)
            installs = installs_for(cur, inc, exc)
        else:
            installs = {}
        agents = sorted(installs.keys())
        employees = {agent_meta.get(a, {}).get("employee_id") for a in agents}
        employees.discard(None)
        used = len(agents)
        owned = total or 0
        if not m:
            flag = "unmatched"          # catalog entry we don't yet know how to count
        elif lid == 9:
            flag = "subscription"       # M365 — counted elsewhere
        elif used > owned:
            flag = "over"               # installed on more devices than owned
        elif owned and used == 0:
            flag = "unused"             # paying for it, nobody has it installed
        elif used < owned:
            flag = "under"              # headroom (fine)
        else:
            flag = "ok"
        tracked.append({
            "license_id": lid, "software_name": sw, "vendor": vendor,
            "license_type": ltype, "owned": owned, "used": used,
            "employees": len(employees), "status": status,
            "expiry_date": expiry.isoformat() if expiry else None,
            "flag": flag,
            "devices": [{"agent_id": a,
                         "asset_id": agent_meta.get(a, {}).get("asset_id"),
                         "asset_name": agent_meta.get(a, {}).get("asset_name") or a,
                         "software": installs[a]} for a in agents],
        })

    untracked = []
    for d in UNTRACKED_DISCOVERY:
        inc, exc = _compile(d)
        installs = installs_for(cur, inc, exc)
        if not installs:
            continue
        agents = sorted(installs.keys())
        untracked.append({
            "label": d["label"], "vendor": d["vendor"], "used": len(agents),
            "devices": [{"agent_id": a,
                         "asset_id": agent_meta.get(a, {}).get("asset_id"),
                         "asset_name": agent_meta.get(a, {}).get("asset_name") or a,
                         "software": installs[a]} for a in agents],
        })
    untracked.sort(key=lambda x: -x["used"])
    return {"tracked": tracked, "untracked": untracked}


if __name__ == "__main__":
    import os, psycopg2
    c = psycopg2.connect(os.environ["DATABASE_URL"]); cur = c.cursor()
    rec = reconcile(cur)
    print("=== TRACKED (catalog) ===")
    for t in rec["tracked"]:
        print(f"  [{t['flag']:11s}] {t['software_name'][:34]:34s} owned={t['owned']:3d} "
              f"used={t['used']:3d} users={t['employees']:3d}  {t['vendor']}")
    print("\n=== UNTRACKED (installed, no license record) ===")
    for u in rec["untracked"]:
        print(f"  {u['used']:3d} devices  {u['label']:28s} ({u['vendor']})")
    c.close()
