"""The F02B information-asset register.

Customer and engineering information assets — documents, datasets, records —
as opposed to the hardware in the asset register. ALAP's ledger needs a
classification and risk score per asset, and until now those lived only in the
spreadsheet, which is why FY26's new columns sat empty across 89 of 90 rows.
"""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from extensions import db
from models import ISMSInformationAsset, AuditTrail
from utils import admin_required

bp = Blueprint('isms_assets', __name__)


# Masters-sheet vocabularies. These are the values the ledger's dropdowns
# validate against, so the register has to offer exactly these.
PROTECT_CLASSES = ['Normal', 'High', 'Very High', 'TISAX(Normal)', 'TISAX(High)',
                   'TISAX(Very High)', 'Other standard requirements', 'N/A']
CRITICAL_CLASSIFICATIONS = ['Customer Information', 'Personal Information(Internal)',
                            'Personal Information (External)',
                            'Important Technical Information', 'N/A']
INFORMATION_CATEGORIES = ['Information obtained from external',
                          'Processed information obtained from external',
                          'Information created internally', 'Public Information']
BUSINESS_AREAS = ['Corporate Strategy', 'Finance', 'ESG_Legal', 'HR_General Affairs',
                  'Sales', 'Engineering', 'Production', 'Procurement', 'Quality',
                  'Other', 'N/A']
MEDIA_FORMS = ['Electronic', 'Paper', 'Physical Object', 'N/A']
VIEWING_AUTHORITIES = ['S', 'A', 'B', 'C', 'D']
SCORE_OPTIONS = [4, 3, 2, 1]

TEXT_FIELDS = ('asset_name', 'required_protect_class', 'critical_classification',
               'customer_name', 'information_category', 'asset_manager',
               'owning_department', 'business_area', 'purpose', 'media_form',
               'stored_on', 'viewing_authority', 'permitted_scope_of_use',
               'other_requirements', 'remarks')
INT_FIELDS = ('confidentiality', 'integrity', 'availability',
              'threat_class', 'vulnerability_class')


def _log(record, action, details):
    try:
        db.session.add(AuditTrail(
            asset_id=None, action=action, table_name='isms_information_asset',
            record_id=record.id if record else None,
            old_values=None, new_values=details,
            changed_by=getattr(__import__('flask_login').current_user, 'username', None)))
    except Exception:
        pass


def _apply_form(record, form):
    """Update only the fields actually submitted.

    Writing every field unconditionally would null anything the form did not
    render or send -- a partial post would silently wipe values it never
    showed the user.
    """
    for field in TEXT_FIELDS:
        if field in form:
            setattr(record, field, (form.get(field) or '').strip() or None)
    for field in INT_FIELDS:
        if field in form:
            raw = (form.get(field) or '').strip()
            setattr(record, field, int(raw) if raw.isdigit() else None)
    if 'is_active' in form:
        record.is_active = form.get('is_active') != 'off'


@bp.route('/isms/information-assets')
@login_required
@admin_required
def information_assets():
    show = request.args.get('show', 'all')
    query = ISMSInformationAsset.query.filter_by(is_active=True)
    records = query.order_by(ISMSInformationAsset.asset_name.asc()).all()

    incomplete = [r for r in records if r.missing_ledger_fields]
    if show == 'incomplete':
        records = incomplete

    # Which required column is missing most often — tells you what to work on.
    tally = {}
    for record in incomplete:
        for field in record.missing_ledger_fields:
            tally[field] = tally.get(field, 0) + 1

    return render_template(
        'isms_information_assets.html',
        records=records,
        total=ISMSInformationAsset.query.filter_by(is_active=True).count(),
        incomplete_count=len(incomplete),
        tally=sorted(tally.items(), key=lambda kv: -kv[1]),
        show=show,
    )


@bp.route('/isms/information-assets/<int:record_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def information_asset_detail(record_id):
    record = ISMSInformationAsset.query.get_or_404(record_id)
    if request.method == 'POST':
        _apply_form(record, request.form)
        if not record.asset_name:
            flash('Asset name is required.', 'danger')
            return redirect(url_for('isms_assets.information_asset_detail', record_id=record.id))
        record.updated_at = datetime.utcnow()
        _log(record, 'isms_information_asset_update', record.asset_name)
        db.session.commit()
        flash('Information asset saved.', 'success')
        return redirect(url_for('isms_assets.information_asset_detail', record_id=record.id))

    return render_template(
        'isms_information_asset_detail.html',
        record=record,
        protect_classes=PROTECT_CLASSES,
        critical_classifications=CRITICAL_CLASSIFICATIONS,
        information_categories=INFORMATION_CATEGORIES,
        business_areas=BUSINESS_AREAS,
        media_forms=MEDIA_FORMS,
        viewing_authorities=VIEWING_AUTHORITIES,
        score_options=SCORE_OPTIONS,
    )


@bp.route('/isms/information-assets/new', methods=['POST'])
@login_required
@admin_required
def create_information_asset():
    name = (request.form.get('asset_name') or '').strip()
    if not name:
        flash('Asset name is required.', 'danger')
        return redirect(url_for('isms_assets.information_assets'))

    record = ISMSInformationAsset(asset_name=name, source='manual')
    db.session.add(record)
    db.session.flush()
    _log(record, 'isms_information_asset_create', name)
    db.session.commit()
    flash('Information asset created. Complete its ledger fields below.', 'success')
    return redirect(url_for('isms_assets.information_asset_detail', record_id=record.id))


@bp.route('/isms/information-assets/bulk', methods=['POST'])
@login_required
@admin_required
def bulk_set():
    """Set one field across every record still missing it.

    89 of the 90 rows need the same handful of columns, and most share an
    answer — filling them one page at a time would be the slowest possible way
    to close the gap.
    """
    field = (request.form.get('field') or '').strip()
    value = (request.form.get('value') or '').strip()
    only_blank = request.form.get('only_blank') != 'off'

    if field not in TEXT_FIELDS or not value:
        flash('Pick a field and a value.', 'warning')
        return redirect(url_for('isms_assets.information_assets'))

    records = ISMSInformationAsset.query.filter_by(is_active=True).all()
    changed = 0
    for record in records:
        if only_blank and (getattr(record, field) or '').strip():
            continue
        setattr(record, field, value)
        changed += 1
    db.session.commit()
    flash(f'Set {field.replace("_", " ")} on {changed} information asset(s).', 'success')
    return redirect(url_for('isms_assets.information_assets', show='incomplete'))
