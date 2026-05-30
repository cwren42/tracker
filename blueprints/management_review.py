from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from extensions import db
from models import SOC2ManagementReview, SOC2ManagementReviewAction, _log_audit
from utils import admin_required


bp = Blueprint('management_review', __name__)


REVIEW_STATUS_OPTIONS = ['Planned', 'Completed', 'Deferred', 'Cancelled']
ACTION_STATUS_OPTIONS = ['Open', 'In Progress', 'Closed']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


@bp.route('/soc2/management-reviews')
@login_required
@admin_required
def management_reviews_dashboard():
    reviews = SOC2ManagementReview.query.order_by(SOC2ManagementReview.review_date.desc(), SOC2ManagementReview.id.desc()).all()
    return render_template(
        'soc2_management_reviews_dashboard.html',
        reviews=reviews,
        review_status_options=REVIEW_STATUS_OPTIONS,
        action_status_options=ACTION_STATUS_OPTIONS,
    )


@bp.route('/soc2/management-reviews/new', methods=['POST'])
@login_required
@admin_required
def create_management_review():
    title = (request.form.get('title') or '').strip()
    review_date = _parse_date(request.form.get('review_date'))
    if not title or not review_date:
        flash('Review title and date are required.', 'danger')
        return redirect(url_for('management_review.management_reviews_dashboard'))

    review_count = SOC2ManagementReview.query.count() + 1
    review = SOC2ManagementReview(
        review_key=f'MR-{datetime.utcnow().strftime("%Y")}-{review_count:03d}',
        title=title,
        review_date=review_date,
        review_period_start=_parse_date(request.form.get('review_period_start')),
        review_period_end=_parse_date(request.form.get('review_period_end')),
        chairperson=(request.form.get('chairperson') or '').strip() or None,
        minute_taker=(request.form.get('minute_taker') or '').strip() or None,
        location=(request.form.get('location') or '').strip() or None,
        status=(request.form.get('status') or 'Planned').strip(),
        attendees=(request.form.get('attendees') or '').strip() or None,
        agenda_summary=(request.form.get('agenda_summary') or '').strip() or None,
        decisions_summary=(request.form.get('decisions_summary') or '').strip() or None,
        effectiveness_summary=(request.form.get('effectiveness_summary') or '').strip() or None,
        resource_summary=(request.form.get('resource_summary') or '').strip() or None,
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
    )
    db.session.add(review)
    db.session.flush()
    _log_audit('soc2_management_review', review.id, 'create', {'review_key': review.review_key, 'title': review.title})
    db.session.commit()
    flash('Management review created.', 'success')
    return redirect(url_for('management_review.management_review_detail', review_id=review.id))


@bp.route('/soc2/management-reviews/<int:review_id>')
@login_required
@admin_required
def management_review_detail(review_id):
    review = SOC2ManagementReview.query.get_or_404(review_id)
    return render_template(
        'soc2_management_review_detail.html',
        review=review,
        action_status_options=ACTION_STATUS_OPTIONS,
    )


@bp.route('/soc2/management-reviews/<int:review_id>/actions/new', methods=['POST'])
@login_required
@admin_required
def create_management_review_action(review_id):
    review = SOC2ManagementReview.query.get_or_404(review_id)
    title = (request.form.get('title') or '').strip()
    if not title:
        flash('Action title is required.', 'danger')
        return redirect(url_for('management_review.management_review_detail', review_id=review.id))

    action_count = SOC2ManagementReviewAction.query.count() + 1
    action = SOC2ManagementReviewAction(
        review_id=review.id,
        action_key=f'MRA-{datetime.utcnow().strftime("%Y")}-{action_count:04d}',
        title=title,
        owner=(request.form.get('owner') or '').strip() or None,
        due_date=_parse_date(request.form.get('due_date')),
        status=(request.form.get('status') or 'Open').strip(),
        notes=(request.form.get('notes') or '').strip() or None,
    )
    db.session.add(action)
    _log_audit('soc2_management_review_action', review.id, 'create', {'review_id': review.id, 'title': title})
    db.session.commit()
    flash('Management review action added.', 'success')
    return redirect(url_for('management_review.management_review_detail', review_id=review.id))