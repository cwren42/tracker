from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import SOC2Vendor, SOC2VendorReview, _log_audit
from utils import admin_required


bp = Blueprint('vendor_management', __name__)


RISK_OPTIONS = ['Low', 'Medium', 'High']
CRITICALITY_OPTIONS = ['Low', 'Medium', 'High', 'Critical']
REVIEW_STATUS_OPTIONS = ['Completed', 'Open Findings', 'Pending', 'Rejected']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


@bp.route('/soc2/vendors')
@login_required
@admin_required
def vendors_dashboard():
    vendors = SOC2Vendor.query.filter_by(is_active=True).order_by(SOC2Vendor.vendor_name.asc()).all()
    return render_template(
        'soc2_vendors_dashboard.html',
        vendors=vendors,
        risk_options=RISK_OPTIONS,
        criticality_options=CRITICALITY_OPTIONS,
        review_status_options=REVIEW_STATUS_OPTIONS,
    )


@bp.route('/soc2/vendors/new', methods=['POST'])
@login_required
@admin_required
def create_vendor():
    vendor_name = (request.form.get('vendor_name') or '').strip()
    if not vendor_name:
        flash('Vendor name is required.', 'danger')
        return redirect(url_for('vendor_management.vendors_dashboard'))

    vendor_count = SOC2Vendor.query.count() + 1
    vendor = SOC2Vendor(
        vendor_key=f'vendor-{vendor_count:03d}',
        vendor_name=vendor_name,
        service_description=(request.form.get('service_description') or '').strip() or None,
        vendor_type=(request.form.get('vendor_type') or '').strip() or None,
        criticality=(request.form.get('criticality') or 'Medium').strip(),
        risk_level=(request.form.get('risk_level') or 'Medium').strip(),
        owner=(request.form.get('owner') or '').strip() or None,
        data_access_scope=(request.form.get('data_access_scope') or '').strip() or None,
        contract_status=(request.form.get('contract_status') or 'Active').strip() or None,
        assurance_status=(request.form.get('assurance_status') or '').strip() or None,
        last_review_date=_parse_date(request.form.get('last_review_date')),
        next_review_date=_parse_date(request.form.get('next_review_date')),
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
        notes=(request.form.get('notes') or '').strip() or None,
    )
    db.session.add(vendor)
    db.session.flush()
    _log_audit('soc2_vendor', vendor.id, 'create', {'vendor_name': vendor.vendor_name})
    db.session.commit()
    flash('Vendor created.', 'success')
    return redirect(url_for('vendor_management.vendor_detail', vendor_id=vendor.id))


@bp.route('/soc2/vendors/<int:vendor_id>')
@login_required
@admin_required
def vendor_detail(vendor_id):
    vendor = SOC2Vendor.query.get_or_404(vendor_id)
    return render_template(
        'soc2_vendor_detail.html',
        vendor=vendor,
        review_status_options=REVIEW_STATUS_OPTIONS,
    )


ISMS_TERMS_MONTHS = {'Critical': 6, 'High': 6, 'Medium': 12, 'Low': 12}


def _add_months(start, months):
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    import calendar
    return start.replace(year=year, month=month,
                         day=min(start.day, calendar.monthrange(year, month)[1]))


@bp.route('/soc2/vendors/<int:vendor_id>/isms', methods=['POST'])
@login_required
@admin_required
def update_vendor_isms(vendor_id):
    """Fields the ALAP business-partner ledger (F06B) needs that nothing else
    in Tracker records."""
    vendor = SOC2Vendor.query.get_or_404(vendor_id)

    vendor.contact_department = (request.form.get('contact_department') or '').strip() or None
    vendor.nda_executed_date = _parse_date(request.form.get('nda_executed_date'))
    vendor.isms_notified_date = _parse_date(request.form.get('isms_notified_date'))
    vendor.terms_reviewed_date = _parse_date(request.form.get('terms_reviewed_date'))
    vendor.onsite_access_scope = (request.form.get('onsite_access_scope') or '').strip() or None
    vendor.training_required = (request.form.get('training_required') or '').strip() or None
    vendor.data_return_on_termination = (request.form.get('data_return_on_termination') or '').strip() or None

    availability = (request.form.get('required_availability') or '').strip()
    vendor.required_availability = int(availability) if availability.isdigit() else None

    # Next terms review is risk-tiered off the last one unless given explicitly.
    explicit_next = _parse_date(request.form.get('terms_next_review_date'))
    if explicit_next:
        vendor.terms_next_review_date = explicit_next
    elif vendor.terms_reviewed_date:
        vendor.terms_next_review_date = _add_months(
            vendor.terms_reviewed_date,
            ISMS_TERMS_MONTHS.get(vendor.criticality, 12))
    else:
        vendor.terms_next_review_date = None

    _log_audit('soc2_vendor', vendor.id, 'update',
               {'vendor_name': vendor.vendor_name, 'section': 'isms_ledger_fields'})
    db.session.commit()
    flash('ISMS ledger fields saved.', 'success')
    return redirect(url_for('vendor_management.vendor_detail', vendor_id=vendor.id))


@bp.route('/soc2/vendors/<int:vendor_id>/reviews/new', methods=['POST'])
@login_required
@admin_required
def create_vendor_review(vendor_id):
    vendor = SOC2Vendor.query.get_or_404(vendor_id)
    review_date = _parse_date(request.form.get('review_date'))
    if not review_date:
        flash('Review date is required.', 'danger')
        return redirect(url_for('vendor_management.vendor_detail', vendor_id=vendor.id))

    review = SOC2VendorReview(
        vendor_id=vendor.id,
        review_date=review_date,
        review_type=(request.form.get('review_type') or 'Annual Review').strip(),
        status=(request.form.get('status') or 'Completed').strip(),
        reviewer=(request.form.get('reviewer') or '').strip() or None,
        summary=(request.form.get('summary') or '').strip() or None,
        findings=(request.form.get('findings') or '').strip() or None,
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
    )
    vendor.last_review_date = review.review_date
    db.session.add(review)
    _log_audit('soc2_vendor_review', vendor.id, 'create', {'vendor_name': vendor.vendor_name, 'review_date': review.review_date.isoformat()})
    db.session.commit()
    flash('Vendor review added.', 'success')
    return redirect(url_for('vendor_management.vendor_detail', vendor_id=vendor.id))