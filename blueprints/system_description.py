from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from extensions import db
from models import SystemDescription, _log_audit
from soc2_artifact_service import (
    build_system_description_docx,
    build_system_description_markdown,
    build_system_description_pdf,
    enrich_system_description_sections,
    import_system_description_from_markdown,
)
from utils import admin_required


bp = Blueprint('system_description', __name__)


@bp.route('/soc2/system-description')
@login_required
@admin_required
def system_description_dashboard():
    sections = SystemDescription.query.order_by(SystemDescription.section_order.asc(), SystemDescription.id.asc()).all()
    populated_sections = sum(1 for section in sections if (section.content or '').strip())
    preview_lines = build_system_description_markdown().splitlines()[:20]
    return render_template(
        'soc2_system_description_dashboard.html',
        sections=sections,
        populated_sections=populated_sections,
        markdown_preview='\n'.join(preview_lines),
    )


@bp.route('/soc2/system-description/import-incoming', methods=['POST'])
@login_required
@admin_required
def import_incoming_system_description():
    result = import_system_description_from_markdown(
        updated_by=getattr(current_user, 'username', None) or 'system_import'
    )
    flash(
        f"Imported system description content: {result['matched']} matched sections, {result['updated']} updated.",
        'success',
    )
    return redirect(url_for('system_description.system_description_dashboard'))


@bp.route('/soc2/system-description/auto-fill', methods=['POST'])
@login_required
@admin_required
def auto_fill_system_description():
    result = enrich_system_description_sections(
        updated_by=getattr(current_user, 'username', None) or 'system_enrichment'
    )
    flash(f"Auto-filled {result['updated']} system description sections from Tracker data.", 'success')
    return redirect(url_for('system_description.system_description_dashboard'))


@bp.route('/soc2/system-description/<int:section_id>')
@login_required
@admin_required
def system_description_section_detail(section_id):
    section = SystemDescription.query.get_or_404(section_id)
    return render_template('soc2_system_description_detail.html', section=section)


@bp.route('/soc2/system-description/<int:section_id>/update', methods=['POST'])
@login_required
@admin_required
def update_system_description_section(section_id):
    section = SystemDescription.query.get_or_404(section_id)
    section.content = (request.form.get('content') or '').strip() or None
    section.updated_by = getattr(current_user, 'username', None) or 'system'
    _log_audit('system_description', section.id, 'update', {'section_title': section.section_title})
    db.session.commit()
    flash('System description section updated.', 'success')
    return redirect(url_for('system_description.system_description_section_detail', section_id=section.id))


@bp.route('/soc2/system-description/export/<string:file_format>')
@login_required
@admin_required
def export_system_description(file_format):
    if file_format == 'md':
        buffer = build_system_description_markdown().encode('utf-8')
        return send_file(
            __import__('io').BytesIO(buffer),
            as_attachment=True,
            download_name='system_description.md',
            mimetype='text/markdown',
        )
    if file_format == 'docx':
        return send_file(
            build_system_description_docx(),
            as_attachment=True,
            download_name='system_description.docx',
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
    if file_format == 'pdf':
        return send_file(
            build_system_description_pdf(),
            as_attachment=True,
            download_name='system_description.pdf',
            mimetype='application/pdf',
        )

    flash('Unsupported export format.', 'danger')
    return redirect(url_for('system_description.system_description_dashboard'))