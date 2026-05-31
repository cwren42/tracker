"""IT System Registry — the structured catalog of systems/services the org runs.

Layer 1 of the knowledge brain (see docs/AGENTIC_IT_OS_GAMEPLAN.md): human-browsable AND
agent-queryable. Markdown docs attach to a system and flow into the Knowledge Agent (RAG);
structured `facts` hold the queryable truth; systems surface as nodes in the IT Graph.
"""
import re

from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify)
from flask_login import login_required, current_user

from extensions import db
from models import ITSystem, Asset
from utils import admin_required

bp = Blueprint('systems', __name__)

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
    import knowledge_agent
    s = ITSystem.query.get_or_404(system_id)
    docs = knowledge_agent.system_docs(system_id)
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
    import knowledge_agent
    ITSystem.query.get_or_404(system_id)
    title = (request.form.get('title') or '').strip()
    content = (request.form.get('content') or '').strip()
    if not content:
        flash('Doc content is required.', 'warning')
        return redirect(url_for('systems.system_detail', system_id=system_id))
    try:
        knowledge_agent.add_system_doc(system_id, title, content, doc_key=_slugify(title) if title else None)
        flash('Doc added to the knowledge base.', 'success')
    except ValueError as e:
        flash(str(e), 'warning')
    except Exception as e:
        flash(f'Could not add doc: {e}', 'danger')
    return redirect(url_for('systems.system_detail', system_id=system_id))


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
