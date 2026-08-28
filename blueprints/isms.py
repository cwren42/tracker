import html
import io
import re
from difflib import HtmlDiff, SequenceMatcher
from pathlib import Path

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import login_required, current_user
import markdown as markdown_lib
from openpyxl import load_workbook
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from docx import Document
from werkzeug.utils import secure_filename

from extensions import db
from models import Asset, AuditTrail, ISMSDocument, ISMSDocumentVersion, ISMSExportRun
from utils import admin_required
import isms_ledger_service


bp = Blueprint('isms', __name__)


GLOCALIZATION_SURVEY_TEMPLATE_PATH = Path(
    '/var/www/tracker/templates/ISMS-MANUAL/_Temp-Files/Glocalization-Survey-Cirque-Filled-V7-2026-05-08.xlsx'
)

GLOCALIZATION_SURVEY_OVERRIDES = {
    43: {
        'status': 'Exist & No corrections',
        'reason': 'CSIRT function covered by Cirque Incident Management Policy and Incident Response Procedures, including the severity matrix and breach notification timelines.',
    },
    45: {
        'status': 'Exist & No corrections',
        'reason': 'Backup and recovery covered by Cirque Backup and Restoration Procedure with retention and recovery objectives defined in the current manual set.',
    },
    71: {
        'status': 'Exist & No corrections',
        'reason': 'Incident handling covered by Cirque Incident Response Procedures (Global, US, and Asia localized), including severity classification and jurisdiction-specific breach clocks.',
    },
}


def _current_actor():
    return getattr(current_user, 'display_name', None) or getattr(current_user, 'username', None) or 'system'


def _current_actor_username():
    return getattr(current_user, 'username', None) or _current_actor()


def _get_current_version(document):
    version = document.current_version
    if version is None and document.versions:
        version = document.versions[0]
    return version


def _create_version(document, markdown_body, change_summary, *, is_restore=False, restored_from=None):
    latest_version = _get_current_version(document)
    next_version_number = 1 if latest_version is None else latest_version.version_number + 1
    new_version = ISMSDocumentVersion(
        document_id=document.id,
        version_number=next_version_number,
        markdown_body=markdown_body,
        rendered_html=render_markdown(markdown_body),
        change_summary=change_summary,
        is_restore=is_restore,
        restored_from_version_id=restored_from.id if restored_from else None,
        created_by=_current_actor_username(),
    )
    db.session.add(new_version)
    db.session.flush()

    document.current_version = new_version
    document.updated_by = _current_actor_username()
    document.updated_at = datetime.utcnow()
    return new_version


def _log_action(document, action, details):
    try:
        db.session.add(
            AuditTrail(
                asset_id=None,
                action=action,
                table_name='isms_document',
                record_id=document.id,
                old_values=None,
                new_values=details,
                changed_by=_current_actor_username(),
            )
        )
    except Exception:
        pass


def _download_filename(document, version_number, extension):
    return f"{document.slug or 'isms-document'}_v{version_number}.{extension}"


def _slugify_heading(text_value, seen_slugs=None):
    slug = re.sub(r'[^a-z0-9]+', '-', text_value.lower()).strip('-') or 'section'
    if seen_slugs is None:
        return slug

    base_slug = slug
    suffix = 2
    while slug in seen_slugs:
        slug = f'{base_slug}-{suffix}'
        suffix += 1
    seen_slugs.add(slug)
    return slug


def _flatten_toc_tokens(tokens, level=1):
    items = []
    for token in tokens or []:
        items.append({
            'id': token.get('id'),
            'title': token.get('name', ''),
            'level': level,
        })
        if token.get('children'):
            items.extend(_flatten_toc_tokens(token['children'], level + 1))
    return items


def _normalize_markdown_source(markdown_body):
    normalized = re.sub(r'^---\s*\n.*?\n---\s*\n?', '', markdown_body, flags=re.DOTALL)
    normalized = re.sub(r'^\\newpage\s*$', '', normalized, flags=re.MULTILINE)
    normalized = re.sub(r'\\([`*_{}\[\]()#+\-.!|])', r'\1', normalized)
    return normalized.strip()


def _extract_metadata_fields(line):
    fields = []
    cursor = 0

    while cursor < len(line):
        start = line.find('**', cursor)
        if start == -1:
            break
        end = line.find('**', start + 2)
        if end == -1:
            return []

        token = line[start + 2:end].strip()
        cursor = end + 2
        if ':' in token:
            label, inline_value = token.split(':', 1)
        else:
            label = token
            inline_value = ''
            if cursor >= len(line) or line[cursor] != ':':
                return []
            cursor += 1

        next_start = line.find('**', cursor)
        trailing_value = line[cursor:next_start if next_start != -1 else len(line)].strip()
        value = ' '.join(part for part in [inline_value.strip(), trailing_value] if part)
        fields.append((label.strip(), value.strip()))

        if next_start == -1:
            break
        cursor = next_start

    return fields


def _normalize_metadata_fields(fields, heading_text):
    normalized_fields = []
    normalized_heading = re.sub(r'^IS-[A-Z0-9-]+:\s*', '', heading_text).strip().lower()

    label_map = {
        'Document': 'Document ID',
        'Standards Name': 'Document Title',
        'Standard Type': 'Document Type',
    }
    suppressed_labels = {'Standard Retention', 'Document ID'}

    for label, value in fields:
        label = label_map.get(label, label)
        if label in suppressed_labels:
            continue

        normalized_value = value.strip()
        if label == 'Document Title' and normalized_value.lower() == normalized_heading:
            continue
        normalized_fields.append((label, normalized_value))

    return normalized_fields


def _build_metadata_block(fields, heading_text):
    normalized_fields = _normalize_metadata_fields(fields, heading_text)
    items = []
    for label, value in normalized_fields:
        items.append(
            '<div class="isms-section-meta-item">'
            f'<div class="isms-section-meta-label">{html.escape(label)}</div>'
            f'<div class="isms-section-meta-value">{_render_inline(value)}</div>'
            '</div>'
        )
    return '<div class="isms-section-meta">' + ''.join(items) + '</div>'


def _prepare_reader_markdown(markdown_body):
    normalized = _normalize_markdown_source(markdown_body)
    lines = normalized.split('\n')
    prepared = []
    index = 0

    while index < len(lines):
        line = lines[index]
        heading_match = re.match(r'^(#{1,6})\s+(.+?)\s*$', line)
        prepared.append(line)
        index += 1

        if not heading_match:
            continue

        heading_text = heading_match.group(2).strip()

        while index < len(lines) and not lines[index].strip():
            prepared.append(lines[index])
            index += 1

        while index < len(lines):
            duplicate_heading = re.match(r'^(#{1,6})\s+(.+?)\s*$', lines[index].strip())
            if not duplicate_heading or duplicate_heading.group(2).strip() != heading_text:
                break
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1

        if index < len(lines):
            duplicate_match = re.match(r'^\*\*(.+?)\*\*\s*$', lines[index].strip())
            if duplicate_match and duplicate_match.group(1).strip() == heading_text:
                index += 1
                while index < len(lines) and not lines[index].strip():
                    index += 1

        metadata_fields = []
        metadata_index = index
        while metadata_index < len(lines):
            candidate = lines[metadata_index].strip()
            if not candidate:
                break
            if re.match(r'^#{1,6}\s+', candidate):
                break
            parsed_fields = _extract_metadata_fields(candidate)
            if not parsed_fields:
                break
            metadata_fields.extend(parsed_fields)
            metadata_index += 1

        if metadata_fields:
            prepared.append('')
            prepared.append(_build_metadata_block(metadata_fields, heading_text))
            prepared.append('')
            index = metadata_index

    return '\n'.join(prepared)


def _iter_markdown_blocks(markdown_body):
    for raw_line in markdown_body.replace('\r\n', '\n').split('\n'):
        line = raw_line.strip()
        if not line:
            yield ('blank', '')
        elif line.startswith('### '):
            yield ('heading3', line[4:].strip())
        elif line.startswith('## '):
            yield ('heading2', line[3:].strip())
        elif line.startswith('# '):
            yield ('heading1', line[2:].strip())
        elif line.startswith('- '):
            yield ('bullet', line[2:].strip())
        else:
            yield ('paragraph', line)


def _create_export_run(document, version, export_format):
    export_run = ISMSExportRun(
        document_id=document.id,
        document_version_id=version.id,
        export_format=export_format,
        status='completed',
        generated_by=_current_actor_username(),
    )
    db.session.add(export_run)
    _log_action(document, 'isms_export', f'Exported version {version.version_number} as {export_format}')
    db.session.commit()


def _build_markdown_export(document, version):
    output = io.BytesIO(version.markdown_body.encode('utf-8'))
    output.seek(0)
    return send_file(
        output,
        mimetype='text/markdown; charset=utf-8',
        as_attachment=True,
        download_name=_download_filename(document, version.version_number, 'md'),
    )


def _build_docx_export(document, version):
    doc = Document()
    doc.add_heading(document.title, level=0)
    doc.add_paragraph(f'Exported from Tracker on {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')

    for block_type, text_value in _iter_markdown_blocks(version.markdown_body):
        if block_type == 'blank':
            continue
        if block_type == 'heading1':
            doc.add_heading(text_value, level=1)
        elif block_type == 'heading2':
            doc.add_heading(text_value, level=2)
        elif block_type == 'heading3':
            doc.add_heading(text_value, level=3)
        elif block_type == 'bullet':
            doc.add_paragraph(text_value, style='List Bullet')
        else:
            doc.add_paragraph(text_value)

    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=_download_filename(document, version.version_number, 'docx'),
    )


def _build_pdf_export(document, version):
    output = io.BytesIO()
    pdf = SimpleDocTemplate(
        output,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ISMSExportTitle',
        parent=styles['Heading1'],
        fontSize=18,
        spaceAfter=16,
    )
    heading1_style = ParagraphStyle(
        'ISMSExportHeading1',
        parent=styles['Heading2'],
        fontSize=14,
        spaceBefore=12,
        spaceAfter=8,
    )
    heading2_style = ParagraphStyle(
        'ISMSExportHeading2',
        parent=styles['Heading3'],
        fontSize=12,
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        'ISMSExportBody',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        spaceAfter=8,
    )
    bullet_style = ParagraphStyle(
        'ISMSExportBullet',
        parent=body_style,
        leftIndent=18,
        firstLineIndent=-8,
    )

    story = [
        Paragraph(html.escape(document.title), title_style),
        Paragraph(html.escape(f'Exported from Tracker on {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}'), body_style),
        Spacer(1, 0.15 * inch),
    ]

    for block_type, text_value in _iter_markdown_blocks(version.markdown_body):
        if block_type == 'blank':
            story.append(Spacer(1, 0.08 * inch))
        elif block_type == 'heading1':
            story.append(Paragraph(_render_inline(text_value), heading1_style))
        elif block_type == 'heading2':
            story.append(Paragraph(_render_inline(text_value), heading2_style))
        elif block_type == 'heading3':
            story.append(Paragraph(_render_inline(text_value), body_style))
        elif block_type == 'bullet':
            story.append(Paragraph(f'&bull; {_render_inline(text_value)}', bullet_style))
        else:
            story.append(Paragraph(_render_inline(text_value), body_style))

    pdf.build(story)
    output.seek(0)
    return send_file(
        output,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=_download_filename(document, version.version_number, 'pdf'),
    )


def _parse_front_matter(markdown_body):
    if not markdown_body.startswith('---\n'):
        return {}

    parts = markdown_body.split('\n---\n', 1)
    if len(parts) != 2:
        return {}

    metadata = {}
    for line in parts[0].splitlines()[1:]:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        metadata[key.strip().lower()] = value.strip().strip('"').strip("'")
    return metadata


def _parse_manual_version_label(markdown_body):
    match = re.search(r'^\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*\d{4}-\d{2}-\d{2}\s*\|', markdown_body, re.MULTILINE)
    return match.group(1) if match else '1.0'


def _normalize_retention_status(retention_value):
    normalized = ' '.join(str(retention_value or '').strip().lower().split())
    status_map = {
        'exist and no corrections': 'Exist & No corrections',
        'exist & no corrections': 'Exist & No corrections',
        'exist and corrections': 'Exist & corrections',
        'exist & corrections': 'Exist & corrections',
    }
    return status_map.get(normalized)


def _normalize_effective_date(date_value):
    raw_value = str(date_value or '').strip()
    if not raw_value:
        return None

    for fmt in ('%Y-%m-%d', '%B %Y', '%b %Y'):
        try:
            parsed = datetime.strptime(raw_value, fmt)
            if fmt in ('%B %Y', '%b %Y'):
                parsed = parsed.replace(day=1)
            return parsed.strftime('%Y-%m-%d')
        except ValueError:
            continue

    return raw_value


def _parse_isms_manual_documents(markdown_body):
    lines = markdown_body.replace('\r\n', '\n').split('\n')
    documents = {}

    for index, raw_line in enumerate(lines):
        heading_match = re.match(r'^##\s+(IS-[A-Z0-9-]+):\s*(.+)$', raw_line.strip())
        if not heading_match:
            continue

        heading_document_id, heading_title = heading_match.groups()
        metadata = {}
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor].strip()
            if candidate.startswith('## '):
                break
            parsed_fields = _extract_metadata_fields(candidate)
            if parsed_fields:
                for label, value in parsed_fields:
                    metadata[label.strip()] = value.strip().strip('*').strip()
            cursor += 1

        document_id = metadata.get('Document ID') or metadata.get('Document') or heading_document_id
        documents[document_id] = {
            'document_id': document_id,
            'title': metadata.get('Document Title') or metadata.get('Standards Name') or heading_title.strip(),
            'effective_date': _normalize_effective_date(metadata.get('Effective Date')),
            'category': metadata.get('Category'),
            'division': metadata.get('Division'),
            'document_type': metadata.get('Document Type') or metadata.get('Standard Type'),
            'version': metadata.get('Version'),
            'retention_status': _normalize_retention_status(metadata.get('Standard Retention')),
        }

    return documents


def _extract_document_ids(cell_value):
    document_ids = []
    last_prefix = None

    for raw_token in re.split(r'\s*\+\s*', str(cell_value or '')):
        token = raw_token.strip()
        if not token:
            continue

        full_match = re.search(r'IS-[A-Z0-9-]+', token)
        if full_match:
            document_id = full_match.group(0)
            document_ids.append(document_id)
            prefix_match = re.match(r'^(IS-[A-Z0-9]+-)', document_id)
            if prefix_match:
                last_prefix = prefix_match.group(1)
            continue

        shorthand_match = re.match(r'^(CIRQ[0-9]{2}-[A-Z0-9]+)$', token)
        if shorthand_match and last_prefix:
            document_ids.append(f'{last_prefix}{shorthand_match.group(1)}')

    return document_ids


def _refresh_inventory_sheet(worksheet, manual_documents, manual_date, manual_version_label):
    worksheet['A2'] = (
        f'Source: ISMS-Manual.docx v{manual_version_label} '
        f'(effective {manual_date}, A4, Arial 10pt, parent-compliant format)'
    )

    for row in range(6, worksheet.max_row + 1):
        cirque_document_id = worksheet.cell(row=row, column=2).value
        if not cirque_document_id:
            continue

        manual_document = manual_documents.get(str(cirque_document_id).strip())
        if manual_document is None:
            continue

        if manual_document.get('title'):
            worksheet.cell(row=row, column=6, value=manual_document['title'])
        if manual_document.get('retention_status'):
            worksheet.cell(row=row, column=7, value=manual_document['retention_status'])
        if manual_document.get('effective_date'):
            worksheet.cell(row=row, column=9, value=manual_document['effective_date'])


def _refresh_survey_sheet(worksheet, manual_documents, tracked_asset_count):
    total_rows = 0
    correction_rows = 0

    for row in range(3, worksheet.max_row + 1):
        reference_number = worksheet.cell(row=row, column=1).value
        if reference_number in (None, ''):
            continue

        total_rows += 1
        mapped_document_ids = _extract_document_ids(worksheet.cell(row=row, column=10).value)
        mapped_documents = [manual_documents[document_id] for document_id in mapped_document_ids if document_id in manual_documents]
        effective_dates = [item['effective_date'] for item in mapped_documents if item.get('effective_date')]
        if effective_dates:
            worksheet.cell(row=row, column=11, value=max(effective_dates))

        mapped_statuses = {item['retention_status'] for item in mapped_documents if item.get('retention_status')}
        if mapped_statuses and mapped_statuses == {'Exist & No corrections'}:
            worksheet.cell(row=row, column=7, value='Exist & No corrections')

        if tracked_asset_count and worksheet.cell(row=row, column=10).value and 'IS-AAR01-CIRQ01-F01A' in str(worksheet.cell(row=row, column=10).value):
            current_reason = str(worksheet.cell(row=row, column=8).value or '').strip()
            asset_suffix = f' Tracker currently maintains {tracked_asset_count} assets.'
            if current_reason and asset_suffix not in current_reason:
                worksheet.cell(row=row, column=8, value=current_reason.rstrip('.') + '.' + asset_suffix)

        override = GLOCALIZATION_SURVEY_OVERRIDES.get(reference_number)
        if override:
            if override.get('status'):
                worksheet.cell(row=row, column=7, value=override['status'])
            if override.get('reason'):
                worksheet.cell(row=row, column=8, value=override['reason'])
            if override.get('plan_date'):
                worksheet.cell(row=row, column=11, value=override['plan_date'])

        status = str(worksheet.cell(row=row, column=7).value or '').strip()
        if status == 'Exist & corrections':
            correction_rows += 1

    return total_rows, correction_rows


def _refresh_progress_sheet(worksheet, total_rows, correction_rows):
    completed_rows = max(total_rows - correction_rows, 0)
    worksheet['C5'] = 1
    worksheet['D5'] = 1
    worksheet['E5'] = '=D5/C5'
    worksheet['F5'] = 'Filled and ready; generated from the current Tracker workbook template'

    worksheet['C6'] = 0
    worksheet['D6'] = 0
    worksheet['E6'] = 'N/A'
    worksheet['F6'] = 'Cirque has no Global Version adoptions; not applicable'

    worksheet['C7'] = total_rows
    worksheet['D7'] = completed_rows
    worksheet['E7'] = '=D7/C7' if total_rows else 'N/A'
    worksheet['F7'] = (
        f'{completed_rows}/{total_rows} ready as-is; {correction_rows} pending edits'
        if correction_rows else
        'All localized/local rows align with the current manual metadata'
    )

    worksheet['C8'] = total_rows
    worksheet['D8'] = completed_rows
    worksheet['E8'] = '=D8/C8' if total_rows else 'N/A'
    worksheet['F8'] = 'Approval tracked in Cirque Inventory'

    worksheet['C9'] = total_rows
    worksheet['D9'] = 0
    worksheet['E9'] = '=D9/C9' if total_rows else 'N/A'
    worksheet['F9'] = 'None uploaded yet'


def _build_glocalization_survey_export(document, version):
    if not GLOCALIZATION_SURVEY_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f'Glocalization survey template not found: {GLOCALIZATION_SURVEY_TEMPLATE_PATH}')

    workbook = load_workbook(GLOCALIZATION_SURVEY_TEMPLATE_PATH)
    manual_front_matter = _parse_front_matter(version.markdown_body)
    manual_documents = _parse_isms_manual_documents(version.markdown_body)
    manual_date = manual_front_matter.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
    manual_version_label = _parse_manual_version_label(version.markdown_body)
    tracked_asset_count = Asset.query.count()

    progress_sheet = workbook['Progress']
    inventory_sheet = workbook['Cirque Inventory']
    survey_sheet = workbook['Survey Sheet']

    _refresh_inventory_sheet(inventory_sheet, manual_documents, manual_date, manual_version_label)
    total_rows, correction_rows = _refresh_survey_sheet(survey_sheet, manual_documents, tracked_asset_count)
    _refresh_progress_sheet(progress_sheet, total_rows, correction_rows)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _build_diff_table(from_version, to_version):
    diff = HtmlDiff(wrapcolumn=100)
    return diff.make_table(
        from_version.markdown_body.splitlines(),
        to_version.markdown_body.splitlines(),
        fromdesc=f'v{from_version.version_number}',
        todesc=f'v{to_version.version_number}',
        context=True,
        numlines=3,
    )


def _build_diff_summary(from_version, to_version):
    added = 0
    removed = 0
    changed = 0
    matcher = SequenceMatcher(
        a=from_version.markdown_body.splitlines(),
        b=to_version.markdown_body.splitlines(),
    )

    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == 'insert':
            added += b_end - b_start
        elif tag == 'delete':
            removed += a_end - a_start
        elif tag == 'replace':
            changed += max(a_end - a_start, b_end - b_start)

    return {
        'added': added,
        'removed': removed,
        'changed': changed,
    }


def _resolve_diff_versions(document, selected_version_id=None, target_version_id=None):
    versions = document.versions
    current_version = _get_current_version(document)
    if current_version is None:
        abort(404)

    version_by_id = {version.id: version for version in versions}
    compare_version = version_by_id.get(selected_version_id) if selected_version_id else None
    if compare_version is None:
        compare_version = next((version for version in versions if version.id != current_version.id), None)
    target_version = version_by_id.get(target_version_id) if target_version_id else current_version

    if compare_version is None or target_version is None:
        abort(404)
    if compare_version.document_id != document.id or target_version.document_id != document.id:
        abort(404)
    if compare_version.id == target_version.id:
        flash('Choose two different versions to compare.', 'info')
        return None, None, versions

    return compare_version, target_version, versions


def _render_inline(text_value):
    rendered = html.escape(text_value)
    rendered = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', rendered)
    rendered = re.sub(r'\[(.+?)\]\((https?://[^\s)]+)\)', r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', rendered)
    return rendered


def render_markdown_with_toc(markdown_body):
    if not markdown_body:
        return {
            'html': '<p class="text-muted">No content available.</p>',
            'toc': [],
        }
    normalized_markdown = _prepare_reader_markdown(markdown_body)
    md = markdown_lib.Markdown(
        extensions=['extra', 'sane_lists', 'toc', 'tables', 'fenced_code'],
        extension_configs={
            'toc': {
                'permalink': False,
            }
        },
        output_format='html5',
    )
    html_output = md.convert(normalized_markdown)
    html_output = re.sub(r'<h([1-6]) id="([^"]+)"', r'<h\1 id="\2" class="isms-heading-anchor"', html_output)
    return {
        'html': html_output,
        'toc': _flatten_toc_tokens(getattr(md, 'toc_tokens', [])),
    }


def render_markdown(markdown_body):
    return render_markdown_with_toc(markdown_body)['html']


@bp.route('/isms')
@login_required
@admin_required
def documents():
    documents = ISMSDocument.query.order_by(ISMSDocument.title.asc()).all()
    manual_document = next((document for document in documents if document.slug == 'isms-manual'), None)
    return render_template('isms_documents.html', documents=documents, manual_document=manual_document)


@bp.route('/isms/<int:document_id>')
@login_required
@admin_required
def document_detail(document_id):
    document = ISMSDocument.query.get_or_404(document_id)
    version = _get_current_version(document)
    if version is None:
        abort(404)

    rendered_document = render_markdown_with_toc(version.markdown_body)
    return render_template(
        'isms_document_detail.html',
        document=document,
        version=version,
        rendered_html=rendered_document['html'],
        toc_items=rendered_document['toc'],
        current_actor=_current_actor(),
    )


@bp.route('/isms/<int:document_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_document(document_id):
    document = ISMSDocument.query.get_or_404(document_id)
    version = _get_current_version(document)
    if version is None:
        abort(404)

    if request.method == 'POST':
        base_version_id = request.form.get('base_version_id', type=int)
        markdown_body = request.form.get('markdown_body', '')
        change_summary = (request.form.get('change_summary') or '').strip()

        if not change_summary:
            flash('Change summary is required.', 'warning')
            return render_template('isms_edit_document.html', document=document, version=version, rendered_html=render_markdown(markdown_body))

        if base_version_id != document.current_version_id:
            flash('This document changed after you opened it. Reload the editor and try again.', 'danger')
            return redirect(url_for('isms.edit_document', document_id=document.id))

        if markdown_body == version.markdown_body:
            flash('No content changes detected. Nothing was saved.', 'info')
            return redirect(url_for('isms.document_detail', document_id=document.id))

        new_version = _create_version(document, markdown_body, change_summary)
        _log_action(document, 'isms_edit', f'Saved version {new_version.version_number}: {change_summary}')
        db.session.commit()
        flash(f'Saved {document.title} as version {new_version.version_number}.', 'success')
        return redirect(url_for('isms.document_detail', document_id=document.id))

    return render_template('isms_edit_document.html', document=document, version=version, rendered_html=version.rendered_html or render_markdown(version.markdown_body))


@bp.route('/isms/<int:document_id>/history')
@login_required
@admin_required
def document_history(document_id):
    document = ISMSDocument.query.get_or_404(document_id)
    version = _get_current_version(document)
    if version is None:
        abort(404)
    return render_template('isms_document_history.html', document=document, current_version=version, versions=document.versions)


@bp.route('/isms/<int:document_id>/diff')
@bp.route('/isms/<int:document_id>/diff/<int:version_id>')
@login_required
@admin_required
def document_diff(document_id, version_id=None):
    document = ISMSDocument.query.get_or_404(document_id)
    selected_version_id = version_id or request.args.get('from_version_id', type=int)
    target_version_id = request.args.get('to_version_id', type=int)

    compare_version, target_version, versions = _resolve_diff_versions(document, selected_version_id, target_version_id)
    if compare_version is None or target_version is None:
        return redirect(url_for('isms.document_history', document_id=document.id))

    diff_table = _build_diff_table(compare_version, target_version)
    diff_summary = _build_diff_summary(compare_version, target_version)
    return render_template(
        'isms_document_diff.html',
        document=document,
        current_version=_get_current_version(document),
        compare_version=compare_version,
        target_version=target_version,
        versions=versions,
        diff_summary=diff_summary,
        diff_table=diff_table,
    )


@bp.route('/isms/<int:document_id>/restore/<int:version_id>', methods=['POST'])
@login_required
@admin_required
def restore_document_version(document_id, version_id):
    document = ISMSDocument.query.get_or_404(document_id)
    restore_source = ISMSDocumentVersion.query.filter_by(document_id=document.id, id=version_id).first_or_404()
    current_version = _get_current_version(document)
    if current_version and current_version.id == restore_source.id:
        flash('That version is already current.', 'info')
        return redirect(url_for('isms.document_history', document_id=document.id))

    restored_version = _create_version(
        document,
        restore_source.markdown_body,
        f'Restored from version {restore_source.version_number}',
        is_restore=True,
        restored_from=restore_source,
    )
    _log_action(document, 'isms_restore', f'Restored version {restore_source.version_number} into new version {restored_version.version_number}')
    db.session.commit()
    flash(f'Restored version {restore_source.version_number} as new current version {restored_version.version_number}.', 'success')
    return redirect(url_for('isms.document_detail', document_id=document.id))


@bp.route('/isms/<int:document_id>/export/<string:export_format>')
@login_required
@admin_required
def export_document(document_id, export_format):
    document = ISMSDocument.query.get_or_404(document_id)
    version = _get_current_version(document)
    if version is None:
        abort(404)

    export_format = (export_format or '').lower()
    if export_format not in {'md', 'docx', 'pdf'}:
        abort(404)

    _create_export_run(document, version, export_format)

    if export_format == 'md':
        return _build_markdown_export(document, version)
    if export_format == 'docx':
        return _build_docx_export(document, version)
    return _build_pdf_export(document, version)


@bp.route('/isms/<int:document_id>/export-glocalization-survey')
@login_required
@admin_required
def export_glocalization_survey(document_id):
    document = ISMSDocument.query.get_or_404(document_id)
    if document.slug != 'isms-manual':
        abort(404)

    version = _get_current_version(document)
    if version is None:
        abort(404)

    _create_export_run(document, version, 'glocalization-xlsx')
    _log_action(document, 'isms_export', f'Exported glocalization survey workbook from version {version.version_number}')

    output = _build_glocalization_survey_export(document, version)
    export_timestamp = datetime.utcnow().strftime('%Y-%m-%d-%H%M%S')
    download_name = f'Glocalization-Survey-Cirque-Filled-V7-{export_timestamp}.xlsx'
    response = send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=download_name,
    )
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ---------------------------------------------------------------------------
# ISMS Management Ledgers (ALAP IS-APM02-F02..F09 workbook)
# ---------------------------------------------------------------------------

def _log_ledger_action(action, details):
    try:
        db.session.add(
            AuditTrail(
                asset_id=None,
                action=action,
                table_name='isms_ledger',
                record_id=None,
                old_values=None,
                new_values=details,
                changed_by=_current_actor_username(),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


@bp.route('/isms/ledgers')
@login_required
@admin_required
def ledgers():
    template = isms_ledger_service.template_info()
    coverage = isms_ledger_service.ledger_coverage() if template else None
    readiness = None
    if template and request.args.get('readiness') == '1':
        # Generating the workbook to measure it is not free, so it's opt-in.
        try:
            readiness = isms_ledger_service.ledger_readiness()
        except Exception as exc:
            flash(f'Could not compute readiness: {exc}', 'warning')
    return render_template(
        'isms_ledgers.html',
        template=template,
        coverage=coverage,
        template_dir=str(isms_ledger_service.LEDGER_TEMPLATE_DIR),
        exclusions=isms_ledger_service.ledger_exclusions(),
        exclusion_setting=isms_ledger_service.LEDGER_EXCLUSION_SETTING,
        readiness=readiness,
    )


@bp.route('/isms/ledgers/template', methods=['POST'])
@login_required
@admin_required
def upload_ledger_template():
    upload = request.files.get('template')
    if upload is None or not upload.filename:
        flash('Choose a .xlsx ledger template to upload.', 'warning')
        return redirect(url_for('isms.ledgers'))

    filename = secure_filename(upload.filename)
    if not filename.lower().endswith('.xlsx'):
        flash('The ledger template must be an .xlsx workbook.', 'danger')
        return redirect(url_for('isms.ledgers'))

    isms_ledger_service.LEDGER_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    destination = isms_ledger_service.LEDGER_TEMPLATE_DIR / filename
    upload.save(destination)

    try:
        load_workbook(destination)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        flash(f'That file could not be read as an Excel workbook: {exc}', 'danger')
        return redirect(url_for('isms.ledgers'))

    _log_ledger_action('isms_ledger_template_upload', f'Uploaded ledger template {filename}')
    flash(f'Ledger template {filename} uploaded. It is now the active template.', 'success')
    return redirect(url_for('isms.ledgers'))


@bp.route('/isms/ledgers/export')
@login_required
@admin_required
def export_ledgers():
    try:
        output, summary = isms_ledger_service.build_ledger_workbook()
    except FileNotFoundError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('isms.ledgers'))

    filled = ', '.join(
        f"{name} {stats.get('total', 0)}" for name, stats in summary['sheets'].items()
    )
    _log_ledger_action(
        'isms_ledger_export',
        f"Generated ISMS management ledgers from template {summary['template']} ({filled})",
    )

    response = send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=isms_ledger_service.export_filename(),
    )
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
