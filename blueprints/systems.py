"""IT System Registry — the structured catalog of systems/services the org runs.

Layer 1 of the knowledge brain (see docs/AGENTIC_IT_OS_GAMEPLAN.md): human-browsable AND
agent-queryable. Markdown docs attach to a system and flow into the Knowledge Agent (RAG);
structured `facts` hold the queryable truth; systems surface as nodes in the IT Graph.
"""
import re

from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify)
from flask_login import login_required, current_user

from extensions import db
from models import ITSystem, Asset, SystemDoc, SystemDocVersion
from utils import admin_required

bp = Blueprint('systems', __name__)


def save_doc(system_id, title, body, *, source='manual', source_ref=None, user='system',
             doc_id=None, doc_key=None, change_summary=None):
    """Create or update a VERSIONED system doc + re-embed it for RAG. Snapshots the prior
    version into SystemDocVersion before overwriting. Returns the SystemDoc."""
    import knowledge_agent
    title = (title or '').strip()
    body = (body or '').strip()
    if not body:
        raise ValueError('Content is required.')
    if not title:
        title = (body.splitlines()[0].lstrip('# ').strip()[:120]) or 'Untitled'
    key = doc_key or _slugify(title)

    doc = SystemDoc.query.get(doc_id) if doc_id else None
    if not doc:
        doc = SystemDoc.query.filter_by(system_id=system_id, doc_key=key).first()
    if doc:
        # Snapshot the current version into history, then update + bump.
        db.session.add(SystemDocVersion(doc_id=doc.id, version_number=doc.version or 1,
                                        title=doc.title, body=doc.body,
                                        change_summary=change_summary, created_by=user))
        doc.title = title; doc.body = body
        doc.version = (doc.version or 1) + 1; doc.updated_by = user
        if source: doc.source = source
        if source_ref: doc.source_ref = source_ref
        key = doc.doc_key or key
    else:
        doc = SystemDoc(system_id=system_id, doc_key=key, title=title, body=body,
                        source=source, source_ref=source_ref, version=1, updated_by=user)
        db.session.add(doc)
    db.session.commit()
    # Re-embed the current text into the knowledge base (best-effort; needs OpenAI key).
    try:
        knowledge_agent.add_system_doc(system_id, title, body, doc_key=key)
    except Exception:
        pass
    return doc

CATEGORIES = ['Network', 'Identity', 'Virtualization', 'Compute', 'Backup', 'Security', 'Endpoint', 'Other']


def _slugify(name):
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    return s[:120] or 'system'


def _parse_facts(text):
    """key=value per line (or comma-separated) -> dict."""
    facts = {}
    for line in re.split(r'[\n,]', text or ''):
        if '=' in line:
            k, v = line.split('=', 1)
            k = k.strip()
            if k:
                facts[k] = v.strip()
    return facts


def _facts_to_text(facts):
    if not isinstance(facts, dict):
        return ''
    return '\n'.join(f'{k}={v}' for k, v in facts.items())


@bp.route('/systems')
@login_required
@admin_required
def systems():
    rows = ITSystem.query.order_by(ITSystem.category, ITSystem.name).all()
    grouped = {}
    for s in rows:
        grouped.setdefault(s.category or 'Other', []).append(s)
    return render_template('systems.html', grouped=grouped, total=len(rows))


@bp.route('/systems/new', methods=['GET', 'POST'])
@login_required
@admin_required
def system_new():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Name is required.', 'warning')
            return redirect(url_for('systems.system_new'))
        s = ITSystem(
            name=name, slug=_slugify(name),
            category=(request.form.get('category') or 'Other').strip(),
            role=(request.form.get('role') or '').strip() or None,
            vendor=(request.form.get('vendor') or '').strip() or None,
            summary=(request.form.get('summary') or '').strip() or None,
            facts=_parse_facts(request.form.get('facts')),
            asset_id=(int(request.form['asset_id']) if request.form.get('asset_id') else None),
            created_by=current_user.username,
        )
        db.session.add(s)
        db.session.commit()
        flash(f'System "{s.name}" created.', 'success')
        return redirect(url_for('systems.system_detail', system_id=s.id))
    assets = Asset.query.order_by(Asset.name).all()
    return render_template('system_edit.html', sys=None, categories=CATEGORIES, assets=assets, facts_text='')


@bp.route('/systems/<int:system_id>')
@login_required
@admin_required
def system_detail(system_id):
    s = ITSystem.query.get_or_404(system_id)
    docs = SystemDoc.query.filter_by(system_id=system_id).order_by(SystemDoc.title).all()
    asset = Asset.query.get(s.asset_id) if s.asset_id else None
    return render_template('system_detail.html', sys=s, docs=docs, asset=asset)


@bp.route('/systems/<int:system_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def system_edit(system_id):
    s = ITSystem.query.get_or_404(system_id)
    if request.method == 'POST':
        s.name = (request.form.get('name') or s.name).strip()
        s.category = (request.form.get('category') or s.category).strip()
        s.role = (request.form.get('role') or '').strip() or None
        s.vendor = (request.form.get('vendor') or '').strip() or None
        s.summary = (request.form.get('summary') or '').strip() or None
        s.facts = _parse_facts(request.form.get('facts'))
        s.asset_id = int(request.form['asset_id']) if request.form.get('asset_id') else None
        db.session.commit()
        flash('System updated.', 'success')
        return redirect(url_for('systems.system_detail', system_id=s.id))
    assets = Asset.query.order_by(Asset.name).all()
    return render_template('system_edit.html', sys=s, categories=CATEGORIES, assets=assets,
                           facts_text=_facts_to_text(s.facts))


@bp.route('/systems/<int:system_id>/add-doc', methods=['POST'])
@login_required
@admin_required
def system_add_doc(system_id):
    ITSystem.query.get_or_404(system_id)
    try:
        save_doc(system_id, request.form.get('title'), request.form.get('content'),
                 source='manual', user=current_user.username)
        flash('Doc saved and added to the knowledge base.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not save doc: {e}', 'danger')
    return redirect(url_for('systems.system_detail', system_id=system_id))


@bp.route('/systems/doc/<int:doc_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def system_doc_edit(doc_id):
    doc = SystemDoc.query.get_or_404(doc_id)
    if request.method == 'POST':
        try:
            save_doc(doc.system_id, request.form.get('title'), request.form.get('content'),
                     user=current_user.username, doc_id=doc.id,
                     change_summary=(request.form.get('change_summary') or '').strip() or None)
            flash(f'Doc updated — now v{doc.version}.', 'success')
        except ValueError as e:
            flash(str(e), 'warning')
        return redirect(url_for('systems.system_detail', system_id=doc.system_id))
    return render_template('system_doc_edit.html', doc=doc, sys=ITSystem.query.get(doc.system_id))


@bp.route('/systems/doc/<int:doc_id>/history')
@login_required
@admin_required
def system_doc_history(doc_id):
    doc = SystemDoc.query.get_or_404(doc_id)
    versions = (SystemDocVersion.query.filter_by(doc_id=doc.id)
                .order_by(SystemDocVersion.version_number.desc()).all())
    return render_template('system_doc_history.html', doc=doc,
                           sys=ITSystem.query.get(doc.system_id), versions=versions)


@bp.route('/systems/doc/<int:doc_id>/restore/<int:version_id>', methods=['POST'])
@login_required
@admin_required
def system_doc_restore(doc_id, version_id):
    doc = SystemDoc.query.get_or_404(doc_id)
    ver = SystemDocVersion.query.get_or_404(version_id)
    if ver.doc_id != doc.id:
        flash('Version does not belong to this doc.', 'warning')
        return redirect(url_for('systems.system_doc_history', doc_id=doc.id))
    save_doc(doc.system_id, ver.title or doc.title, ver.body, user=current_user.username,
             doc_id=doc.id, change_summary=f'Restored from v{ver.version_number}')
    flash(f'Restored v{ver.version_number} (saved as v{doc.version}).', 'success')
    return redirect(url_for('systems.system_detail', system_id=doc.system_id))


@bp.route('/systems/<int:system_id>/delete', methods=['POST'])
@login_required
@admin_required
def system_delete(system_id):
    s = ITSystem.query.get_or_404(system_id)
    name = s.name
    db.session.delete(s)
    db.session.commit()
    flash(f'System "{name}" deleted (its knowledge docs remain in the base).', 'info')
    return redirect(url_for('systems.systems'))
