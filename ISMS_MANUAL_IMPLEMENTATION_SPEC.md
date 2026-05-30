# ISMS Manual Implementation Spec

## Purpose

This spec turns the roadmap into the first implementation contract for the Tracker codebase.

## Deliverables

1. Add a dedicated `isms` blueprint with a document library view and document detail view.
2. Add core SQLAlchemy models for managed documents, immutable versions, and export provenance.
3. Add a standalone migration script for the new ISMS tables.
4. Add an importer that loads the new ISMS source library into the new tables.
5. Add a managed content directory outside `templates/` for the later cutover.

## Data Model

### `isms_document`

- `id`
- `slug`
- `title`
- `doc_type`
- `category`
- `status`
- `source_path`
- `current_version_id`
- `created_by`
- `updated_by`
- `created_at`
- `updated_at`

### `isms_document_version`

- `id`
- `document_id`
- `version_number`
- `markdown_body`
- `rendered_html`
- `change_summary`
- `is_restore`
- `restored_from_version_id`
- `created_by`
- `created_at`

### `isms_export_run`

- `id`
- `document_id`
- `document_version_id`
- `export_format`
- `status`
- `output_path`
- `generated_by`
- `created_at`

## Blueprint Scope

### `/isms`

- list documents
- show status, current version, last update

### `/isms/<id>`

- show metadata
- show current rendered body
- show raw Markdown

Phase 1 is intentionally read-only. Editing, diff, restore, and exports remain in the next slice.

## Import Rules

- import from `content/isms/incoming`
- import all Markdown files except `README.md`
- use source path as the idempotent lookup key
- create version `1` on first import
- create a new version only when the Markdown body changes

## Markdown Rendering

The repo does not currently include a Markdown package. Phase 1 therefore uses a minimal internal renderer that supports:

- `#`, `##`, `###` headings
- `**bold**`
- `-` list items
- paragraph grouping

This keeps the first slice dependency-free and low-risk.

## Validation

- migration runs successfully against Postgres
- importer is idempotent
- ISMS nav item appears under Compliance
- library page loads
- detail page loads and renders imported content

## Immediate Follow-on Slice

1. editor UI
2. save new immutable version on edit
3. concurrency check
4. version history page
5. restore flow
6. audit logging for publish, restore, and export