"""Import Markdown ISMS source files into the managed ISMS tables."""
import os
import re
import sys
from html import escape

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import ISMSDocument, ISMSDocumentVersion


ISMS_DIR = os.environ.get('ISMS_IMPORT_DIR', '/var/www/tracker/content/isms/incoming')


def parse_front_matter(content):
    if not content.startswith('---\n'):
        return {}, content

    parts = content.split('\n---\n', 1)
    if len(parts) != 2:
        return {}, content

    metadata = {}
    for line in parts[0].splitlines()[1:]:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        metadata[key.strip().lower()] = value.strip().strip('"').strip("'")
    return metadata, parts[1]


def slugify(value):
    value = value.strip().lower()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-')


def render_markdown(markdown_body):
    if not markdown_body:
        return '<p class="text-muted">No content available.</p>'

    lines = markdown_body.replace('\r\n', '\n').split('\n')
    blocks = []
    paragraph = []
    in_list = False

    def inline(text_value):
        rendered = escape(text_value)
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', rendered)

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list():
        nonlocal in_list
        if in_list:
            blocks.append('</ul>')
            in_list = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue

        if line.startswith('### '):
            flush_paragraph()
            close_list()
            blocks.append(f"<h4>{inline(line[4:])}</h4>")
            continue
        if line.startswith('## '):
            flush_paragraph()
            close_list()
            blocks.append(f"<h3>{inline(line[3:])}</h3>")
            continue
        if line.startswith('# '):
            flush_paragraph()
            close_list()
            blocks.append(f"<h2>{inline(line[2:])}</h2>")
            continue
        if line.startswith('- '):
            flush_paragraph()
            if not in_list:
                blocks.append('<ul>')
                in_list = True
            blocks.append(f"<li>{inline(line[2:])}</li>")
            continue

        close_list()
        paragraph.append(line)

    flush_paragraph()
    close_list()
    return '\n'.join(blocks)


def parse_policy_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as handle:
        content = handle.read()

    filename = os.path.basename(filepath)
    base_name = os.path.splitext(filename)[0]

    front_matter, body_content = parse_front_matter(content)
    heading_match = re.search(r'^#\s+(.+)$', body_content, re.MULTILINE)

    # For consolidated manuals with front matter, do not let internal section metadata
    # override the document identity.
    if front_matter.get('title'):
        title = front_matter['title']
        category = front_matter.get('category', 'ISMS Manual')
        doc_type = front_matter.get('document_type', 'manual').lower()
        slug = slugify(front_matter.get('slug', base_name))
        version_label = front_matter.get('version', '1.0')
    else:
        doc_id_match = re.search(r'\*\*Document(?: ID)?:\s*([^\*]+)\*\*', content)
        title_match = re.search(r'\*\*(?:Standards Name|Document Title):\s*([^\*]+)\*\*', content)
        category_match = re.search(r'\*\*Category:\s*([^\*]+)\*\*', content)
        type_match = re.search(r'\*\*(?:Standard Type|Document Type):\s*([^\*]+)\*\*', content)
        version_match = re.search(r'\*\*Version:\*\*\s*([^\*]+?)(?:\*\*|$)', content)

        title = title_match.group(1).strip() if title_match else (heading_match.group(1).strip() if heading_match else base_name.replace('-', ' ').replace('_', ' ').title())
        category = category_match.group(1).strip() if category_match else 'General'
        doc_type = type_match.group(1).strip().lower() if type_match else ('procedure' if 'procedure' in filename.lower() else 'manual')
        slug = slugify(doc_id_match.group(1).strip() if doc_id_match else base_name)
        version_label = version_match.group(1).strip() if version_match else '1.0'

    return {
        'slug': slug,
        'title': title,
        'category': category,
        'doc_type': doc_type,
        'version_label': version_label,
        'markdown_body': body_content,
    }


def import_documents():
    with app.app_context():
        if not os.path.isdir(ISMS_DIR):
            raise FileNotFoundError(f'ISMS import directory not found: {ISMS_DIR}')

        count = 0
        for filename in sorted(os.listdir(ISMS_DIR)):
            if not filename.endswith('.md'):
                continue
            if filename.lower() == 'readme.md':
                continue

            filepath = os.path.join(ISMS_DIR, filename)
            data = parse_policy_file(filepath)
            document = ISMSDocument.query.filter_by(source_path=filepath).first()
            if document is None:
                document = ISMSDocument(
                    slug=data['slug'],
                    title=data['title'],
                    doc_type=data['doc_type'],
                    category=data['category'],
                    status='draft',
                    source_path=filepath,
                    created_by='import_isms_documents',
                    updated_by='import_isms_documents',
                )
                db.session.add(document)
                db.session.flush()
            else:
                document.slug = data['slug']
                document.title = data['title']
                document.doc_type = data['doc_type']
                document.category = data['category']
                document.updated_by = 'import_isms_documents'

            latest_version = ISMSDocumentVersion.query.filter_by(document_id=document.id).order_by(ISMSDocumentVersion.version_number.desc()).first()
            if latest_version and latest_version.markdown_body == data['markdown_body']:
                document.current_version = latest_version
                count += 1
                continue

            next_version = 1 if latest_version is None else latest_version.version_number + 1
            version = ISMSDocumentVersion(
                document_id=document.id,
                version_number=next_version,
                markdown_body=data['markdown_body'],
                rendered_html=render_markdown(data['markdown_body']),
                change_summary='Initial import from ISMS source library' if latest_version is None else 'Re-import from ISMS source library',
                created_by='import_isms_documents',
            )
            db.session.add(version)
            db.session.flush()
            document.current_version = version
            count += 1

        db.session.commit()
        print(f'Imported or refreshed {count} ISMS documents.')


if __name__ == '__main__':
    import_documents()