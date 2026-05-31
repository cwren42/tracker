"""IT System Registry — the structured catalog of systems/services the org runs.

Layer 1 of the knowledge brain (see docs/AGENTIC_IT_OS_GAMEPLAN.md): human-browsable AND
agent-queryable. Markdown docs attach to a system and flow into the Knowledge Agent (RAG);
structured `facts` hold the queryable truth; systems surface as nodes in the IT Graph.
"""
import re
import threading
import urllib.parse

import requests
from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app)
from flask_login import login_required, current_user

from extensions import db
from models import ITSystem, Asset, SystemDoc, SystemDocVersion, Setting
from secret_store import encrypt_secret, decrypt_secret
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
    # doc count per system (so the catalog shows where the knowledge lives)
    from sqlalchemy import func
    doc_counts = dict(db.session.query(SystemDoc.system_id, func.count(SystemDoc.id))
                      .group_by(SystemDoc.system_id).all())
    grouped = {}
    for s in rows:
        grouped.setdefault(s.category or 'Other', []).append(s)
    return render_template('systems.html', grouped=grouped, total=len(rows),
                           doc_counts=doc_counts, total_docs=sum(doc_counts.values()))


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


def _setting(key, default=''):
    r = Setting.query.filter_by(key=key).first()
    return r.value if (r and r.value) else default


def _set_setting(key, value):
    r = Setting.query.filter_by(key=key).first()
    if not r:
        r = Setting(key=key); db.session.add(r)
    r.value = value


def _normalize_repo(base, project):
    """If a full repo URL was pasted into `project`, split it into (base, project-path).
    Strips scheme/host and a trailing .git so '.../cjwren/it' -> base + 'cjwren/it'."""
    project = (project or '').strip()
    if project.startswith('http://') or project.startswith('https://'):
        u = urllib.parse.urlparse(project)
        base = f"{u.scheme}://{u.netloc}"
        project = u.path
    project = project.strip('/')
    if project.endswith('.git'):
        project = project[:-4]
    return base.rstrip('/'), project


def _ca_bundle(ca_pem):
    """Write a pasted internal CA cert (PEM) to a file for requests' `verify=`. Returns the
    path, or True (system trust) when no custom CA is configured. TLS is always verified."""
    ca_pem = (ca_pem or '').strip()
    if not ca_pem:
        return True
    path = '/tmp/tracker_gitlab_ca.pem'
    with open(path, 'w') as f:
        f.write(ca_pem + '\n')
    return path


# folder keyword -> a keyword expected in the target system's name
_ROUTE_ALIASES = [
    ('applocker', 'active directory'), ('gpo', 'active directory'), ('domain', 'active directory'),
    ('azure', 'active directory'), ('ad ', 'active directory'), ('network', 'active directory'),
    ('prox', 'proxmox'), ('backup', 'backup'), ('vpn', 'vpn'),
    ('k8s', 'kubernetes'), ('kube', 'kubernetes'), ('cert', 'certificate'), ('unifi', 'unifi'),
]


def _route_system(fpath, sys_all, default_sid):
    """Pick the best system for a repo file by its top folder: alias keywords first, then a
    direct name-substring match, else the default."""
    top = (fpath.split('/')[0].lower() if '/' in fpath else '')
    if top:
        for kw, name_kw in _ROUTE_ALIASES:
            if kw in top:
                for s in sys_all:
                    if name_kw in (s.name or '').lower():
                        return s.id
        for s in sys_all:           # direct name match
            nm = (s.name or '').lower()
            if nm and (top in nm or nm in top or top.replace('-', ' ') in nm):
                return s.id
    return default_sid


def _gitlab_import(base, project, branch, path, token, default_system_id, user, verify=True):
    """Pull .md files from a GitLab repo and save each as a versioned system doc (RAG too).
    Files are routed to a system by top-folder name match, else the chosen default system.
    `verify` is True (system trust) or a path to a CA bundle — TLS is always verified."""
    proj = urllib.parse.quote(project, safe='')
    headers = {'PRIVATE-TOKEN': token}
    files, page = [], 1
    while page and page <= 20:
        r = requests.get(f"{base}/api/v4/projects/{proj}/repository/tree", headers=headers,
                         params={'ref': branch, 'recursive': 'true', 'path': path, 'per_page': 100, 'page': page},
                         timeout=30, verify=verify)
        r.raise_for_status()
        files += [f for f in r.json() if f.get('type') == 'blob' and f.get('path', '').lower().endswith('.md')]
        nxt = r.headers.get('X-Next-Page')
        page = int(nxt) if nxt else 0

    sys_all = ITSystem.query.all()
    default_sid = int(default_system_id) if default_system_id else (sys_all[0].id if sys_all else None)
    imported = updated = 0
    for f in files:
        fpath = f['path']
        raw = requests.get(f"{base}/api/v4/projects/{proj}/repository/files/{urllib.parse.quote(fpath, safe='')}/raw",
                           headers=headers, params={'ref': branch}, timeout=30, verify=verify)
        if raw.status_code != 200:
            continue
        body = raw.text
        title = None
        for line in body.splitlines():
            if line.strip().startswith('#'):
                title = line.lstrip('#').strip(); break
        title = title or fpath.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        target = _route_system(fpath, sys_all, default_sid)
        if not target:
            continue
        doc_key = _slugify(fpath.rsplit('.', 1)[0])[:180]
        existed = SystemDoc.query.filter_by(system_id=target, doc_key=doc_key).first() is not None
        save_doc(target, title, body, source='gitlab', source_ref=fpath, user=user, doc_key=doc_key,
                 change_summary='Updated from GitLab' if existed else 'Imported from GitLab')
        updated += 1 if existed else 0
        imported += 0 if existed else 1
    return {'scanned': len(files), 'imported': imported, 'updated': updated}


@bp.route('/systems/import', methods=['GET', 'POST'])
@login_required
@admin_required
def systems_import():
    if request.method == 'POST':
        base = (request.form.get('base') or '').strip().rstrip('/') or 'https://gitlab.com'
        project = (request.form.get('project') or '').strip()
        base, project = _normalize_repo(base, project)   # tolerate a pasted full URL
        branch = (request.form.get('branch') or 'main').strip()
        path = (request.form.get('path') or '').strip()
        token = (request.form.get('token') or '').strip()
        default_system = request.form.get('default_system')
        ca_pem = (request.form.get('ca_pem') or '').strip()   # internal CA cert (PEM), not secret
        fresh_token = bool(token)
        if not token:
            saved = _setting('gitlab_token')
            token = decrypt_secret(saved) if saved else ''
        if not ca_pem:
            ca_pem = _setting('gitlab_ca_pem')                # fall back to saved CA
        if not project or not token:
            flash('GitLab project path and a token are required.', 'warning')
            return redirect(url_for('systems.systems_import'))

        # Persist config FIRST (so a failed import still saves your settings + PAT for retry).
        if request.form.get('save_config'):
            _set_setting('gitlab_base', base); _set_setting('gitlab_project', project)
            _set_setting('gitlab_branch', branch); _set_setting('gitlab_path', path)
            _set_setting('gitlab_default_system', default_system or '')
            _set_setting('gitlab_ca_pem', ca_pem or '')
            if fresh_token:
                _set_setting('gitlab_token', encrypt_secret(token))   # encrypted at rest
            db.session.commit()

        verify = _ca_bundle(ca_pem)
        # Validate connection synchronously (auth/TLS/URL) so errors surface immediately…
        try:
            proj = urllib.parse.quote(project, safe='')
            t = requests.get(f"{base}/api/v4/projects/{proj}/repository/tree",
                             headers={'PRIVATE-TOKEN': token}, params={'ref': branch, 'per_page': 1},
                             timeout=15, verify=verify)
            t.raise_for_status()
        except Exception as e:
            flash(f'GitLab connection failed: {e}', 'danger')
            return redirect(url_for('systems.systems_import'))
        # …then run the (slower) per-file fetch+embed in the background so the page returns now.
        flask_app = current_app._get_current_object()
        user = current_user.username
        def _bg():
            with flask_app.app_context():
                try:
                    _gitlab_import(base, project, branch, path, token, default_system, user, verify=verify)
                except Exception:
                    flask_app.logger.exception('GitLab background import failed')
        threading.Thread(target=_bg, daemon=True, name='gitlab-import').start()
        flash('Connected ✓ — importing your docs in the background. Refresh this page in a moment '
              'to watch them appear under each system.', 'info')
        return redirect(url_for('systems.systems'))
    cfg = {k: _setting('gitlab_' + k) for k in ('base', 'project', 'branch', 'path', 'default_system')}
    cfg['has_token'] = bool(_setting('gitlab_token'))
    cfg['has_ca'] = bool(_setting('gitlab_ca_pem'))
    return render_template('systems_import.html', systems=ITSystem.query.order_by(ITSystem.name).all(), cfg=cfg)


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
