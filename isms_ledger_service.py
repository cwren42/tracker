"""Generate the ALAP ISMS Management Ledgers workbook from live Tracker data.

The ledger is an ALAP-supplied Excel template ([IS-APM02-F02..F09]) that Cirque
files a few times a year. Every sheet is a real Excel Table (``Info``,
``Supplement``, ``Worker_List``, ``SoftWare``, ...) with calculated columns,
cross-sheet XLOOKUPs and dropdowns bound to the ``Masters`` sheet, so this
module *fills the template in place* rather than building a workbook from
scratch: it writes values into the table body, extends each table ``ref``, and
copies the calculated-column formulas down.

Rows already present in the uploaded template carry ISMS judgement calls
Tracker does not hold (risk scoring, protect class, purpose text). Those are
preserved per row and only Tracker-owned fields are refreshed. Rows whose
underlying asset/worker is gone from Tracker are kept and stamped
retired/departed in Remarks rather than dropped.
"""

from __future__ import annotations

import io
import re
from copy import copy
from datetime import date, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import MultiCellRange

from extensions import db


LEDGER_TEMPLATE_DIR = Path(__file__).resolve().parent / 'content' / 'isms' / 'ledgers'

# Header rows: r1 title, r2 metadata, r4/r5 headers, data starts at r6.
FIRST_DATA_ROW = 6

# Categories that belong on F04C. Deliberately excludes UniFi switches/APs,
# cameras, tablets and phone records -- matches the scope of the prior filing.
SUPPLEMENT_CATEGORIES = ('Laptop', 'Desktop', 'Workstation', 'Server', 'Computer', 'Mini PC')

RETIRED_ASSET_STATUSES = ('Retired', 'Disposed')

DEFAULT_SECURITY_MANAGER = 'Chris Wren'
DEFAULT_COMPANY = 'Cirque Corporation'

# Masters!N "Types of Assets" values.
ASSET_TYPE_BY_CATEGORY = {
    'Server': 'Server_Storage server',
    'Computer': 'Server_Storage server',
    'Laptop': 'Physical Object',
    'Desktop': 'Physical Object',
    'Workstation': 'Physical Object',
    'Mini PC': 'Physical Object',
}

PURPOSE_BY_CATEGORY = {
    'Laptop': 'Laptop /Workstation',
    'Desktop': 'Desktop /Workstation',
    'Workstation': 'Laptop /Workstation',
    'Mini PC': 'Desktop /Workstation',
    'Server': 'Server',
    'Computer': 'Server',
}

# Tracker's free-text asset.location values normalised onto the phrasing the
# ledger already uses for installation/storage location.
LOCATION_MAP = {
    'cirque-us': 'Cirque SLC office',
    'cirqueus': 'Cirque SLC office',
    'us': 'Cirque SLC office',
    'cirque us': 'Cirque SLC office',
    'cirque-taiwan': 'Cirque TW office',
    'cirque taiwan': 'Cirque TW office',
    'taiwan': 'Cirque TW office',
    'cirqueasia': 'Cirque TW office',
    'cirque-china': 'Cirque China office',
    'china': 'Cirque China office',
    'domain controllers': 'Cirque SLC server room',
    'server room': 'Cirque SLC server room',
    'cirque data center': 'Cirque SLC server room',
    'storage': 'Cirque SLC IT storage',
}

# Tracker license_type -> Masters!Q License_Type.
LICENSE_TYPE_MAP = {
    'subscription': 'Subscription',
    'retail': 'One-time Purchase',
    'per device': 'One-time Purchase',
    'perpetual': 'One-time Purchase',
    'open source': 'OSS(Open Source Software)',
}

# Tracker employee.work_type -> Masters!P Worker_Type.
WORKER_TYPE_MAP = {
    'local': 'full-time employee',
    'remote': 'full-time employee',
    'contractor': 'On-premises Outsourcing Contract',
    'temp': 'Temporary Staffing Agreement',
}

SHEET_SUPPLEMENT = 'F04C Asset- Supplement'
SHEET_WORKERS = 'F09A  Worker List'
SHEET_PARTNERS = 'F06B BusinessPartner'
SHEET_SOFTWARE = 'F08A Non-Std Software'
SHEET_WORKER_ACCESS = 'F09-A'
SHEET_ASSET_AREA = 'F02-A'
SHEET_PROTOTYPE = 'F03A Asset - Prototype'
SHEET_SOFTWARE_USERS = 'F09-B'
SHEET_INFO = 'F02B Asset - Info'

DATE_FORMAT = 'yyyy/mm/dd'

# Excel serial 55153 -- the placeholder "no expiry" contract date already used
# throughout the Worker List. Kept so preserved and generated rows agree.
CONTRACT_EXPIRE_PLACEHOLDER = date(2050, 12, 31)


# --------------------------------------------------------------------------
# template discovery
# --------------------------------------------------------------------------

def find_template():
    """Return the newest ledger template on disk, or None."""
    if not LEDGER_TEMPLATE_DIR.is_dir():
        return None
    candidates = [
        path for path in LEDGER_TEMPLATE_DIR.glob('*.xlsx')
        if not path.name.startswith('~$')
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def template_info():
    path = find_template()
    if path is None:
        return None
    stat = path.stat()
    return {
        'path': path,
        'name': path.name,
        'size_kb': round(stat.st_size / 1024),
        'modified': date.fromtimestamp(stat.st_mtime),
    }


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _clean(value):
    if value is None:
        return ''
    return str(value).strip()


def _key(value):
    return _clean(value).upper()


def _set(worksheet, row, column, value, *, number_format=None):
    cell = worksheet.cell(row=row, column=column)
    cell.value = value
    if number_format:
        cell.number_format = number_format
    return cell


def _set_date(worksheet, row, column, value):
    """Write a real date. The template stores raw Excel serials with a General
    format, which renders as ``45512``; normalising every date cell we own is
    strictly more readable and Excel reads it the same."""
    if value is None:
        return _set(worksheet, row, column, None)
    return _set(worksheet, row, column, value, number_format=DATE_FORMAT)


def _existing_date(cell_value):
    """Read a date back out of a template cell that may hold a real date, an
    Excel serial number, or a serial rendered as text."""
    if cell_value is None or cell_value == '':
        return None
    if isinstance(cell_value, date):
        return cell_value
    try:
        serial = int(float(str(cell_value).strip()))
    except (TypeError, ValueError):
        return None
    if serial <= 0:
        return None
    return date(1899, 12, 30) + timedelta(days=serial)


def _table_for(worksheet):
    """Return (name, table) for the sheet's single ListObject, or (None, None)."""
    for name in worksheet.tables:
        return name, worksheet.tables[name]
    return None, None


def _blank_row(worksheet, row, last_column):
    for column in range(1, last_column + 1):
        worksheet.cell(row=row, column=column).value = None


def _clone_row_style(worksheet, source_row, target_row, last_column):
    """Copy formatting + calculated-column formulas from a template row."""
    for column in range(1, last_column + 1):
        source = worksheet.cell(row=source_row, column=column)
        target = worksheet.cell(row=target_row, column=column)
        if source.has_style:
            target._style = copy(source._style)
        if isinstance(source.value, str) and source.value.startswith('='):
            target.value = _shift_formula(source.value, source_row, target_row)


_ROW_REF = re.compile(r'(?<![A-Za-z0-9_$])(\$?)([A-Z]{1,3})(\$?)(\d+)')


def _shift_formula(formula, source_row, target_row):
    """Shift relative row references so a copied formula matches its new row.

    Structured references (``Table[[#This Row],[Col]]``) are row-relative
    already, so only plain A1 refs need adjusting.
    """
    delta = target_row - source_row
    if not delta:
        return formula

    def replace(match):
        col_abs, col, row_abs, row = match.groups()
        if row_abs:
            return match.group(0)
        return f'{col_abs}{col}{row_abs}{int(row) + delta}'

    # Protect structured references from the A1 regex.
    parts = re.split(r'(\[[^\]]*\])', formula)
    return ''.join(
        part if part.startswith('[') else _ROW_REF.sub(replace, part)
        for part in parts
    )


# --------------------------------------------------------------------------
# ALAP template revisions
#
# ALAP issued a revision list against these ledgers (2026-08). Each item is the
# same class of defect: row 6 is right and rows 7+ were dragged out of step.
# They are applied at generation time rather than baked into the stored
# template, so they hold for whichever template revision is uploaded and
# re-applying them to an already-corrected workbook is a no-op.
# --------------------------------------------------------------------------

# Revision 1 -- Importance must be the max of the row's own C/I/A triple.
# The template ships it as a drag artifact spanning the whole table body
# (``MAX(S6:U95)`` on F02B, ``MAX(U6:W100)`` on F04C), which scales with the
# table: one asset scored 4 drags every row above it to 4. ALAP's own wording:
# "Incorrect: =MAX(L6:N[highest row]) / Correct: =MAX(L6:N6)".
IMPORTANCE_FIXUPS = {
    SHEET_INFO: {'column': 22, 'formula': '=MAX(S{row}:U{row})'},          # F02, V
    SHEET_PROTOTYPE: {'column': 22, 'formula': '=MAX(S{row}:U{row})'},     # F03, V
    SHEET_SUPPLEMENT: {'column': 24, 'formula': '=MAX(U{row}:W{row})'},    # F04, X
    SHEET_SOFTWARE: {'column': 15, 'formula': '=MAX(L{row}:N{row})'},      # F08, O
}


def _fix_importance_column(worksheet, last_data_row):
    fixup = IMPORTANCE_FIXUPS.get(worksheet.title)
    if not fixup:
        return

    column = fixup['column']
    for row in range(FIRST_DATA_ROW, last_data_row + 1):
        worksheet.cell(row=row, column=column).value = fixup['formula'].format(row=row)

    # Excel re-broadcasts a table's stored calculated-column formula on open, so
    # the stored copy has to be corrected too or the cells revert.
    _name, table = _table_for(worksheet)
    if table is None:
        return
    for table_column in table.tableColumns:
        if table_column.name == 'Importance' and table_column.calculatedColumnFormula is not None:
            table_column.calculatedColumnFormula.attr_text = (
                fixup['formula'].format(row=FIRST_DATA_ROW).lstrip('=')
            )


def _replace_validation(worksheet, match_formula, new_sqref, new_formula=None, *, drop=False):
    """Retarget (or remove) the data validation whose formula1 matches.

    openpyxl keeps validations as a flat list keyed by their ``sqref`` range,
    so a revision is expressed by rewriting that range rather than by editing
    individual cells.
    """
    validations = worksheet.data_validations.dataValidation
    for validation in list(validations):
        if _clean(validation.formula1) != match_formula:
            continue
        if drop:
            validations.remove(validation)
            return True
        validation.sqref = MultiCellRange(new_sqref)
        if new_formula is not None:
            validation.formula1 = new_formula
        return True
    return False


def _apply_validation_revisions(workbook):
    """Revisions 2 and 3 -- data-validation ranges that skip a column or bind
    the wrong list from row 7 down."""
    applied = []

    # Revision 2 [F02-A]: the "Area select" dropdown sits in column E on row 6
    # but column F from row 7 down, so the dependent INDIRECT lookup is one
    # column out too. Pull rows 7+ back into line with row 6 and drop the
    # stray dependent validation left in column G. ALAP's instruction is the
    # manual equivalent: delete E7 downward and shift the cells left.
    area = workbook[SHEET_ASSET_AREA]
    if _replace_validation(area, 'Store_Area', 'E6:E1048576'):
        applied.append('F02-A: Area select realigned to column E for all rows')
    if _replace_validation(area, 'INDIRECT($E6)', 'F6:F1048576'):
        applied.append('F02-A: dependent storage-location lookup realigned to column F')
    if _replace_validation(area, 'INDIRECT($F7)', None, drop=True):
        applied.append('F02-A: removed the stray column-G lookup left by the shift')

    # Revision 3 [F09-A][F09-B]: column A row 6 validates against Name_Mail
    # (``Name(email)``), but rows 7+ validate against Worker_Name (bare name).
    # The XLOOKUPs in both sheets key on Name (Mail), so the row-7+ rule
    # rejects the only value that actually resolves -- and it is the value this
    # generator writes.
    for sheet_name in (SHEET_WORKER_ACCESS, SHEET_SOFTWARE_USERS):
        worksheet = workbook[sheet_name]
        # Drop row 6's standalone Name_Mail rule first, so widening the
        # Worker_Name rule to cover column A does not leave two overlapping
        # validations on A6.
        _replace_validation(worksheet, 'Name_Mail', None, drop=True)
        if _replace_validation(worksheet, 'Worker_Name', 'A6:A1048576', 'Name_Mail'):
            applied.append(f'{sheet_name}: column A now validates against Name_Mail for all rows')

    return applied


def _resize_table(worksheet, last_data_row):
    """Extend/shrink the sheet's ListObject to cover the written rows."""
    name, table = _table_for(worksheet)
    if table is None:
        return
    start, end = table.ref.split(':')
    start_col = re.match(r'([A-Z]+)', start).group(1)
    end_col = re.match(r'([A-Z]+)', end).group(1)
    header_row = int(re.search(r'(\d+)', start).group(1))
    # A table must always contain at least one data row.
    final_row = max(last_data_row, header_row + 1)
    table.ref = f'{start_col}{header_row}:{end_col}{final_row}'
    if table.autoFilter is not None:
        table.autoFilter.ref = table.ref


def _write_rows(worksheet, rows, last_column, *, template_row=FIRST_DATA_ROW):
    """Write ``rows`` (list of {column_index: value_or_(value, fmt)}) starting at
    FIRST_DATA_ROW, cloning style/formulas from ``template_row``, then resize
    the table and clear any leftover rows below."""
    previous_last = worksheet.max_row
    style_source = template_row

    for offset, row_values in enumerate(rows):
        row = FIRST_DATA_ROW + offset
        if row != style_source:
            _clone_row_style(worksheet, style_source, row, last_column)
        for column, value in row_values.items():
            if isinstance(value, tuple):
                _set(worksheet, row, column, value[0], number_format=value[1])
            elif isinstance(value, date):
                _set_date(worksheet, row, column, value)
            else:
                _set(worksheet, row, column, value)

    last_data_row = FIRST_DATA_ROW + len(rows) - 1 if rows else FIRST_DATA_ROW - 1
    for row in range(max(last_data_row + 1, FIRST_DATA_ROW), previous_last + 1):
        _blank_row(worksheet, row, last_column)

    _resize_table(worksheet, last_data_row)
    _fix_importance_column(worksheet, last_data_row)
    return len(rows)


def _snapshot_rows(worksheet, key_column, last_column):
    """Index the template's existing data rows by a key column so curated
    values can be carried forward."""
    snapshot = {}
    for row in range(FIRST_DATA_ROW, worksheet.max_row + 1):
        key = _key(worksheet.cell(row=row, column=key_column).value)
        if not key:
            continue
        snapshot[key] = {
            column: worksheet.cell(row=row, column=column).value
            for column in range(1, last_column + 1)
        }
    return snapshot


def _carry(existing, column, fallback=None):
    """Prefer the curated template value; fall back to a Tracker-derived one."""
    if existing:
        value = existing.get(column)
        if value not in (None, '') and not (isinstance(value, str) and value.startswith('=')):
            return value
    return fallback


# --------------------------------------------------------------------------
# Tracker queries
# --------------------------------------------------------------------------

def _fetch(sql, params=None):
    return db.session.execute(db.text(sql), params or {}).mappings().all()


def _load_assets():
    return _fetch(
        """
        SELECT a.id, a.asset_tag, a.name, a.category, a.status, a.location,
               a.serial_number, a.model, a.manufacturer, a.os_version,
               e.name AS employee_name, e.department AS employee_department
        FROM asset a
        LEFT JOIN employee e ON e.id = a.employee_id
        WHERE a.category IN :categories
        ORDER BY a.name
        """,
        {'categories': tuple(SUPPLEMENT_CATEGORIES)},
    )


def _load_employees():
    return _fetch(
        """
        SELECT e.id, e.name, e.email, e.sam_account_name, e.department,
               e.job_title, e.work_type, e.start_date, e.location,
               e.ad_enabled, e.offboarded_at
        FROM employee e
        WHERE e.is_visible = TRUE AND e.offboarded_at IS NULL
        ORDER BY e.name
        """
    )


def _load_training():
    rows = _fetch(
        """
        SELECT trainee_name, trainee_email, employee_id,
               MAX(training_date) AS last_training,
               MAX(completion_status) AS completion_status
        FROM soc2_security_training_record
        WHERE completion_status ILIKE 'complete%'
        GROUP BY trainee_name, trainee_email, employee_id
        """
    )
    by_employee, by_email, by_name = {}, {}, {}
    for row in rows:
        if row['employee_id']:
            by_employee[row['employee_id']] = row
        if row['trainee_email']:
            by_email[row['trainee_email'].strip().lower()] = row
        if row['trainee_name']:
            by_name[_key(row['trainee_name'])] = row
    return by_employee, by_email, by_name


def _load_assigned_devices():
    """employee_id -> {'pc': asset_tag_or_name, 'other': [...]} for F09A Q/R/S."""
    rows = _fetch(
        """
        SELECT a.employee_id, a.asset_tag, a.name, a.category, a.status
        FROM asset a
        WHERE a.employee_id IS NOT NULL
          AND (a.status IS NULL OR a.status NOT IN :retired)
        ORDER BY a.name
        """,
        {'retired': RETIRED_ASSET_STATUSES},
    )
    devices = {}
    for row in rows:
        bucket = devices.setdefault(row['employee_id'], {'pc': [], 'other': []})
        label = _clean(row['asset_tag']) or _clean(row['name'])
        if row['category'] in SUPPLEMENT_CATEGORIES:
            bucket['pc'].append((label, _clean(row['name'])))
        else:
            bucket['other'].append(label)
    return devices


def _load_vendors():
    return _fetch(
        """
        SELECT vendor_name, service_description, vendor_type, criticality,
               owner, contract_status, last_review_date, next_review_date,
               data_access_scope, assurance_status, notes
        FROM soc2_vendor
        WHERE is_active = TRUE
        ORDER BY vendor_name
        """
    )


def _load_licenses():
    return _fetch(
        """
        SELECT l.id, l.software_name, l.vendor, l.license_type, l.total_licenses,
               l.status, l.notes,
               (SELECT s.version FROM rmm_software s
                 WHERE s.name = l.software_name AND s.version IS NOT NULL
                 GROUP BY s.version ORDER BY COUNT(*) DESC LIMIT 1) AS common_version
        FROM license l
        WHERE l.status IS NULL OR l.status <> 'Retired'
        ORDER BY l.software_name
        """
    )


def _load_partner_system_accounts():
    """Accounts Cirque staff hold on a business-partner-provided system.

    ALAP asks for these on F09-A: the worker, the partner system they were
    instructed to use, and their account on it. Tracker only holds this for
    Microsoft 365 (`m365_user`, linked to an employee and to the Microsoft row
    on F06B). Accounts on the other partner platforms -- GitLab, Cadence,
    Altium, Cliosoft, Arena/Omnify -- and on any customer-provided system are
    not recorded anywhere in Tracker, so they cannot be emitted here.
    """
    return _fetch(
        """
        SELECT v.vendor_name,
               e.name AS employee_name, e.email, e.sam_account_name,
               e.department, e.start_date,
               m.user_principal_name, m.is_admin
        FROM m365_user m
        JOIN employee e ON e.id = m.employee_id
        JOIN soc2_vendor v ON v.vendor_name = 'Microsoft' AND v.is_active = TRUE
        WHERE m.is_current = TRUE
          AND m.account_enabled = TRUE
          AND e.is_visible = TRUE
          AND e.offboarded_at IS NULL
        ORDER BY e.name
        """
    )


def _load_license_assignments():
    return _fetch(
        """
        SELECT l.software_name, e.name AS employee_name, e.email, e.sam_account_name,
               la.assigned_date, la.status
        FROM license_assignment la
        JOIN license l ON l.id = la.license_id
        JOIN employee e ON e.id = la.employee_id
        WHERE la.status IS NULL OR la.status = 'Active'
        ORDER BY l.software_name, e.name
        """
    )


# --------------------------------------------------------------------------
# derived values
# --------------------------------------------------------------------------

def _installation_location(asset):
    location = _clean(asset['location'])
    mapped = LOCATION_MAP.get(location.lower())
    if mapped:
        if asset['category'] in ('Laptop', 'Desktop', 'Workstation') and asset['employee_name']:
            return f"{asset['employee_name']}'s Desk"
        return mapped
    if location:
        return location
    if asset['employee_name']:
        return f"{asset['employee_name']}'s Desk"
    return 'Cirque SLC office'


def _name_mail(name, email):
    """Match the Worker_List ``Name (Mail)`` calculated column exactly -- the
    F09-A/F09-B XLOOKUPs key on it."""
    return f'{_clean(name)}({_clean(email)})'


def _worker_type(employee):
    return WORKER_TYPE_MAP.get(_clean(employee['work_type']).lower(), 'full-time employee')


def _license_type(value):
    return LICENSE_TYPE_MAP.get(_clean(value).lower(), 'Others')


# --------------------------------------------------------------------------
# sheet builders
# --------------------------------------------------------------------------

def _fill_supplement(worksheet):
    """F04C -- supporting assets (endpoints, servers, lab/conference PCs)."""
    last_column = 37  # AK
    existing = _snapshot_rows(worksheet, 3, last_column)  # keyed on Asset Name
    # Boxes get renamed (offboard -> repurpose), so the same physical asset can
    # sit under an old hostname in the template. The Cirque asset tag is the
    # stable identity; index on it as well so a rename updates the row instead
    # of leaving a duplicate ghost behind.
    existing_by_tag = {}
    for key, snapshot in existing.items():
        tag = _key(snapshot.get(2))
        if tag and tag not in ('0', 'NA'):
            existing_by_tag.setdefault(tag, key)

    assets = _load_assets()

    rows = []
    matched_keys = set()
    stats = {'total': 0, 'new': 0, 'carried': 0, 'retired': 0, 'renamed': 0}

    for asset in assets:
        key = _key(asset['name'])
        prior = existing.get(key)
        if prior is None:
            prior_key = existing_by_tag.get(_key(asset['asset_tag']))
            if prior_key:
                prior = existing[prior_key]
                matched_keys.add(prior_key)
                stats['renamed'] += 1
        if prior is not None:
            matched_keys.add(key)
        else:
            stats['new'] += 1

        retired = _clean(asset['status']) in RETIRED_ASSET_STATUSES
        remarks = _carry(prior, 35)
        if retired:
            note = f"Retired in Tracker (status: {_clean(asset['status'])})"
            remarks = note if not remarks else f'{remarks} | {note}'
            stats['retired'] += 1

        manager = asset['employee_name'] or _carry(prior, 6) or DEFAULT_SECURITY_MANAGER

        rows.append({
            2: _clean(asset['asset_tag']) or _carry(prior, 2),
            3: _clean(asset['name']),
            4: _carry(prior, 4, 'Normal'),
            5: _carry(prior, 5, 'B'),
            6: manager,
            7: _carry(prior, 7, 'Cirque'),
            8: _carry(prior, 8),
            9: _carry(prior, 9, PURPOSE_BY_CATEGORY.get(asset['category'], 'IT equipment')),
            10: _carry(prior, 10, 'No'),
            11: _carry(prior, 11, ASSET_TYPE_BY_CATEGORY.get(asset['category'], 'Physical Object')),
            12: _carry(prior, 12, 'C'),
            13: _carry(prior, 13),
            14: _carry(prior, 14, 'Physical'),
            15: _carry(prior, 15, _installation_location(asset)),
            16: _carry(prior, 16),
            17: _carry(prior, 17),
            18: _carry(prior, 18),
            19: _carry(prior, 19),
            20: _carry(prior, 20),
            21: _carry(prior, 21, 2),
            22: _carry(prior, 22, 1),
            23: _carry(prior, 23, 1),
            25: _carry(prior, 25, 2),
            26: _carry(prior, 26, 3),
            29: _carry(prior, 29),
            30: _carry(prior, 30),
            31: _existing_date(_carry(prior, 31)),
            32: _carry(prior, 32),
            33: _existing_date(_carry(prior, 33)),
            34: _carry(prior, 34),
            35: remarks,
        })

    # Template rows Tracker no longer knows about -- keep, stamp as retired.
    for key, prior in existing.items():
        if key in matched_keys:
            continue
        remarks = _carry(prior, 35)
        note = 'No matching Tracker asset at generation time - confirm still in service (renamed, retired, or a cloud service held outside the asset register)'
        row = {column: prior.get(column) for column in range(2, last_column + 1)
               if not (isinstance(prior.get(column), str) and str(prior.get(column)).startswith('='))}
        row[35] = note if not remarks else f'{remarks} | {note}'
        for column in (31, 33):
            row[column] = _existing_date(row.get(column))
        rows.append(row)
        stats['carried'] += 1

    stats['total'] = _write_rows(worksheet, rows, last_column)
    return stats


def _fill_workers(worksheet):
    """F09A -- worker list, with ISMS training dates and issued equipment."""
    last_column = 26  # Z
    existing = _snapshot_rows(worksheet, 5, last_column)  # keyed on worker name
    employees = _load_employees()
    training_by_employee, training_by_email, training_by_name = _load_training()
    devices = _load_assigned_devices()

    rows = []
    matched_keys = set()
    stats = {'total': 0, 'new': 0, 'departed': 0, 'with_training': 0, 'without_training': 0}

    for employee in employees:
        key = _key(employee['name'])
        prior = existing.get(key)
        if prior:
            matched_keys.add(key)
        else:
            stats['new'] += 1

        training = (
            training_by_employee.get(employee['id'])
            or training_by_email.get(_clean(employee['email']).lower())
            or training_by_name.get(key)
        )
        last_training = training['last_training'] if training else None
        if last_training:
            stats['with_training'] += 1
        else:
            stats['without_training'] += 1

        assigned = devices.get(employee['id'], {'pc': [], 'other': []})
        pc_label = assigned['pc'][0][0] if assigned['pc'] else _carry(prior, 17, 'NA')
        other_label = ', '.join(assigned['other']) if assigned['other'] else _carry(prior, 18, 'NA')

        rows.append({
            3: DEFAULT_COMPANY,
            4: _carry(prior, 4, 'N/A'),
            5: _clean(employee['name']),
            6: _clean(employee['email']),
            8: _carry(prior, 8, _worker_type(employee)),
            9: _clean(employee['sam_account_name']) or _carry(prior, 9),
            10: employee['start_date'] or _existing_date(_carry(prior, 10)),
            11: _existing_date(_carry(prior, 11)) or CONTRACT_EXPIRE_PLACEHOLDER,
            12: last_training or _existing_date(_carry(prior, 12)),
            13: 'Pass' if last_training else _carry(prior, 13),
            14: (last_training + timedelta(days=365)) if last_training else _existing_date(_carry(prior, 14)),
            15: _existing_date(_carry(prior, 15)),
            16: _carry(prior, 16, DEFAULT_SECURITY_MANAGER),
            17: pc_label,
            18: other_label,
            19: _carry(prior, 19, 'NA'),
            20: _carry(prior, 20, 'NA'),
            21: _carry(prior, 21, 'NA'),
            22: _carry(prior, 22, _clean(employee['name'])),
            23: _carry(prior, 23, 'NA'),
            24: _carry(prior, 24),
        })

    for key, prior in existing.items():
        if key in matched_keys:
            continue
        remarks = _carry(prior, 24)
        note = 'Departed - no active Tracker employee record at generation time'
        row = {column: prior.get(column) for column in range(3, last_column + 1)
               if not (isinstance(prior.get(column), str) and str(prior.get(column)).startswith('='))}
        for column in (10, 11, 12, 14, 15):
            row[column] = _existing_date(row.get(column))
        row[24] = note if not remarks else f'{remarks} | {note}'
        rows.append(row)
        stats['departed'] += 1

    stats['total'] = _write_rows(worksheet, rows, last_column)
    return stats


def _fill_partners(worksheet):
    """F06B -- business partners, from the SOC2 vendor register."""
    last_column = 28  # AB
    # Prior rows put the tool name in D ("Contact Department") and our own
    # company in C. Index on both so either convention matches.
    existing = {}
    for row in range(FIRST_DATA_ROW, worksheet.max_row + 1):
        snapshot = {column: worksheet.cell(row=row, column=column).value
                    for column in range(1, last_column + 1)}
        for column in (3, 4):
            key = _key(snapshot.get(column))
            if key and key != _key(DEFAULT_COMPANY):
                existing.setdefault(key, snapshot)

    vendors = _load_vendors()
    rows = []
    matched_keys = set()
    stats = {'total': 0, 'new': 0, 'carried': 0}

    def _match_partner(vendor_key):
        """Prior rows name the tool, not the company ("Cadence Tools" for the
        Cadence vendor), so fall back to word-level containment."""
        if vendor_key in existing:
            return vendor_key, existing[vendor_key]
        for candidate in existing:
            if vendor_key in candidate.split() or candidate.startswith(vendor_key + ' '):
                return candidate, existing[candidate]
        return None, None

    for vendor in vendors:
        key = _key(vendor['vendor_name'])
        matched_key, prior = _match_partner(key)
        if prior:
            matched_keys.add(matched_key)
        else:
            stats['new'] += 1

        rows.append({
            3: _clean(vendor['vendor_name']),
            4: _carry(prior, 4, _clean(vendor['vendor_type'])),
            5: _carry(prior, 5, 'IT'),
            6: _clean(vendor['service_description']) or _carry(prior, 6),
            7: _carry(prior, 7, 'Cloud Based'),
            8: _carry(prior, 8, DEFAULT_SECURITY_MANAGER),
            9: _existing_date(_carry(prior, 9)),
            10: _existing_date(_carry(prior, 10)),
            11: vendor['last_review_date'] or _existing_date(_carry(prior, 11)),
            12: _carry(prior, 12, 'Pass' if vendor['assurance_status'] else None),
            13: vendor['next_review_date'] or _existing_date(_carry(prior, 13)),
            14: _carry(prior, 14, 'none'),
            15: _carry(prior, 15, 3 if _clean(vendor['criticality']) in ('Critical', 'High') else 2),
            16: _carry(prior, 16, 1),
            18: _carry(prior, 18),
            19: _existing_date(_carry(prior, 19)),
            20: _existing_date(_carry(prior, 20)),
            21: _carry(prior, 21),
            22: _carry(prior, 22),
            23: _carry(prior, 23, 2),
            24: _carry(prior, 24, 'Yes'),
            25: _carry(prior, 25, 'Yes'),
            26: _carry(prior, 26, _clean(vendor['data_access_scope'])),
        })

    for key, prior in existing.items():
        if key in matched_keys:
            continue
        remarks = _carry(prior, 26)
        note = 'Not in Tracker vendor register at generation time'
        row = {column: prior.get(column) for column in range(3, last_column + 1)
               if not (isinstance(prior.get(column), str) and str(prior.get(column)).startswith('='))}
        for column in (9, 10, 11, 13, 19, 20):
            row[column] = _existing_date(row.get(column))
        row[26] = note if not remarks else f'{remarks} | {note}'
        rows.append(row)
        stats['carried'] += 1

    stats['total'] = _write_rows(worksheet, rows, last_column)
    return stats


def _fill_software(worksheet):
    """F08A -- licensed / non-standard software, from the license register."""
    last_column = 22  # V
    existing = _snapshot_rows(worksheet, 3, last_column)
    licenses = _load_licenses()

    rows = []
    stats = {'total': 0, 'new': 0}

    for entry in licenses:
        key = _key(entry['software_name'])
        prior = existing.get(key)
        if not prior:
            stats['new'] += 1

        rows.append({
            3: _clean(entry['software_name']),
            4: _clean(entry['common_version']) or _carry(prior, 4),
            5: _carry(prior, 5, _clean(entry['notes']) or 'Business / engineering tooling'),
            6: _carry(prior, 6, DEFAULT_SECURITY_MANAGER),
            7: _carry(prior, 7),
            8: _carry(prior, 8),
            9: _license_type(entry['license_type']),
            10: entry['total_licenses'],
            11: _carry(prior, 11, 'No'),
            12: _carry(prior, 12, 2),
            13: _carry(prior, 13, 1),
            14: _carry(prior, 14, 1),
            16: _carry(prior, 16, 2),
            17: _carry(prior, 17, 3),
            20: _carry(prior, 20, f"Vendor: {_clean(entry['vendor'])}" if entry['vendor'] else None),
        })

    stats['total'] = _write_rows(worksheet, rows, last_column)
    return stats


def _fill_worker_access(worksheet):
    """F09-A -- which worker can reach which supporting asset (device issue)."""
    last_column = 14  # N
    employees = {employee['id']: employee for employee in _load_employees()}
    devices = _load_assigned_devices()

    rows = []
    for employee_id, bucket in sorted(devices.items()):
        employee = employees.get(employee_id)
        if employee is None:
            continue
        for _tag, asset_name in bucket['pc']:
            if not asset_name:
                continue
            rows.append({
                1: _name_mail(employee['name'], employee['email']),
                3: 'SupplimentName',
                4: asset_name,
                6: _clean(employee['sam_account_name']) or _clean(employee['email']),
                7: employee['start_date'],
                8: 'Adminstrator' if _clean(employee['department']).lower() == 'it' else 'User',
                11: 'Assigned endpoint (Tracker asset register)',
            })

    # Partner-provided systems staff were instructed to use (ALAP's follow-up
    # request). Column D must match Business_Partner_List[Business Partner Name]
    # exactly -- the sheet's XLOOKUP resolves the partner's Auto_No from it.
    partner_accounts = _load_partner_system_accounts()
    for account in partner_accounts:
        rows.append({
            1: _name_mail(account['employee_name'], account['email']),
            3: 'BP_Name',
            4: _clean(account['vendor_name']),
            6: _clean(account['user_principal_name']),
            7: account['start_date'],
            8: 'Adminstrator' if account['is_admin'] else 'User',
            11: 'Microsoft 365 account (Entra ID, synced to Tracker)',
        })

    stats = {
        'total': _write_rows(worksheet, rows, last_column),
        'endpoints': len(rows) - len(partner_accounts),
        'partner_systems': len(partner_accounts),
    }
    return stats


def _fill_software_users(worksheet):
    """F09-B -- software user list, from license assignments."""
    last_column = 14  # N
    assignments = _load_license_assignments()

    rows = []
    for assignment in assignments:
        rows.append({
            1: _name_mail(assignment['employee_name'], assignment['email']),
            3: 'Software_Name',
            4: _clean(assignment['software_name']),
            6: _clean(assignment['sam_account_name']) or _clean(assignment['email']),
            7: assignment['assigned_date'],
            8: 'User',
            11: 'Named license assignment (Tracker license register)',
        })

    stats = {'total': _write_rows(worksheet, rows, last_column)}
    return stats


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def build_ledger_workbook(template_path=None):
    """Fill the ledger template from live Tracker data.

    Returns ``(BytesIO, summary_dict)``.
    """
    path = Path(template_path) if template_path else find_template()
    if path is None or not path.exists():
        raise FileNotFoundError(
            f'No ISMS ledger template found. Upload one into {LEDGER_TEMPLATE_DIR}.'
        )

    workbook = load_workbook(path)

    summary = {
        'template': path.name,
        'generated_on': date.today(),
        'sheets': {},
    }
    summary['sheets']['F04C Supporting Assets'] = _fill_supplement(workbook[SHEET_SUPPLEMENT])
    summary['sheets']['F09A Worker List'] = _fill_workers(workbook[SHEET_WORKERS])
    summary['sheets']['F06B Business Partners'] = _fill_partners(workbook[SHEET_PARTNERS])
    summary['sheets']['F08A Software'] = _fill_software(workbook[SHEET_SOFTWARE])
    summary['sheets']['F09-A Worker Access'] = _fill_worker_access(workbook[SHEET_WORKER_ACCESS])
    summary['sheets']['F09-B Software Users'] = _fill_software_users(workbook[SHEET_SOFTWARE_USERS])

    # F02B carries hand-curated information assets Tracker does not hold, so its
    # rows are left as filed -- but it shares the Importance formula defect, and
    # that drives the Risk column, so the formula alone is corrected.
    info_sheet = workbook[SHEET_INFO]
    info_rows = sum(
        1 for row in range(FIRST_DATA_ROW, info_sheet.max_row + 1)
        if _clean(info_sheet.cell(row=row, column=3).value)
    )
    _fix_importance_column(info_sheet, FIRST_DATA_ROW + info_rows - 1)
    summary['sheets']['F02B Information Assets (manual)'] = {'total': info_rows}

    # F03A (prototypes) is likewise not Tracker-held -- Cirque files no
    # prototype assets -- but carries the same Importance column.
    prototype_sheet = workbook[SHEET_PROTOTYPE]
    prototype_rows = sum(
        1 for row in range(FIRST_DATA_ROW, prototype_sheet.max_row + 1)
        if _clean(prototype_sheet.cell(row=row, column=3).value)
    )
    _fix_importance_column(prototype_sheet, max(FIRST_DATA_ROW, FIRST_DATA_ROW + prototype_rows - 1))
    summary['sheets']['F03A Prototypes (manual)'] = {'total': prototype_rows}

    summary['revisions'] = _apply_validation_revisions(workbook)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output, summary


def ledger_coverage():
    """Row counts Tracker would contribute, for the ledger page (no file work)."""
    training_by_employee, training_by_email, training_by_name = _load_training()
    employees = _load_employees()
    trained = sum(
        1 for employee in employees
        if training_by_employee.get(employee['id'])
        or training_by_email.get(_clean(employee['email']).lower())
        or training_by_name.get(_key(employee['name']))
    )
    assets = _load_assets()
    return {
        'assets': len(assets),
        'assets_retired': sum(1 for a in assets if _clean(a['status']) in RETIRED_ASSET_STATUSES),
        'assets_unassigned': sum(1 for a in assets if not a['employee_name']),
        'employees': len(employees),
        'employees_trained': trained,
        'employees_untrained': len(employees) - trained,
        'vendors': len(_load_vendors()),
        'licenses': len(_load_licenses()),
        'license_assignments': len(_load_license_assignments()),
    }


def export_filename(today=None):
    stamp = (today or date.today()).strftime('%Y%m%d')
    return f'ISMS-Management-Ledgers_CIRQUE_{stamp}.xlsx'
