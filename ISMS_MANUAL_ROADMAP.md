# ISMS Manual Manager Roadmap

## Objective

Build a dedicated ISMS section in Tracker that:

- manages the ISMS manual as editable Markdown content
- supports export to `.md`, `.pdf`, and `.docx`
- keeps full revision history with restore capability
- pulls Tracker asset and compliance data into ISMS and SOC 2 forms
- generates audit-ready SOC 2 and ISMS artifacts from approved content and live system data

## What Already Exists In Tracker

The current codebase already gives us several useful building blocks:

- `Policy`, `PolicySection`, and `SystemDescription` content models
- `Asset`, `Employee`, `AssetHistory`, and `AuditTrail` models
- SOC 2 evidence models such as `EvidenceSnapshot`, `M365User`, and `IntuneDevice`
- existing DOCX export logic for system description content
- existing PDF generation patterns using HTML to PDF and `reportlab`
- an existing Markdown policy import flow from the current ISMS manual files

These are useful references, but they are not enough by themselves for a real document-management module with versioning, publishing, restore, and form generation.

## Scope

### In scope

- dedicated ISMS manual area in the application
- Markdown-based document editing and preview
- document metadata and publishing status
- revision history with compare and restore
- export of single documents and assembled manual outputs
- forms that prefill from manual content and Tracker asset/compliance data
- SOC 2 / ISMS artifact generation from approved templates and live data

### Out of scope for phase 1

- rich collaborative editing
- simultaneous multi-user conflict resolution beyond basic optimistic locking
- external e-signature workflow
- broad document workflow automation outside ISMS/SOC 2

## Recommended Architecture

### Authoring model

- Keep Markdown as the canonical authoring format.
- Treat the ISMS manual as a document library, not a single monolithic file.
- Preserve the current source library, but move canonical editable content out of `templates/` into a dedicated content location.

### Storage model

- Store current document metadata and published state in the database.
- Store every saved revision as an immutable version record.
- Keep the current Markdown body, rendered HTML snapshot, and change summary per version.
- Optionally mirror published versions to disk for backup/export compatibility.

### Rendering and export model

- Markdown is the source of truth.
- Render Markdown to normalized HTML once.
- Use normalized HTML for preview and PDF export.
- Use a document conversion service for DOCX export.
- Support both single-document export and full-manual assembly export.

### Data binding model

- Form templates should bind fields to one of:
  - manual section content
  - Tracker asset/compliance data
  - calculated values
  - user-entered values
- Generated outputs should be stored as reusable artifacts with traceability back to source data and source document versions.

## Revision History And Restore Plan

Revision history is a core feature, not an afterthought.

### Requirements

- every save creates a new immutable version record
- show version number, author, timestamp, status, and change summary
- allow side-by-side diff between versions
- allow restore of any previous version
- restoring creates a new version rather than mutating old history
- exports must be traceable to the exact version used

### Recommended implementation

Add new ISMS-specific models instead of trying to reuse `AssetHistory` or `AuditTrail` as the primary document version store.

Suggested tables:

- `isms_document`
  - `id`, `slug`, `title`, `doc_type`, `parent_id`, `sort_order`, `status`, `source_path`, `current_version_id`, `created_by`, `updated_by`, timestamps
- `isms_document_version`
  - `id`, `document_id`, `version_number`, `markdown_body`, `rendered_html`, `change_summary`, `is_restore`, `restored_from_version_id`, `created_by`, timestamp
- `isms_export_run`
  - `id`, `document_id`, `document_version_id`, `format`, `status`, `output_path`, `generated_by`, timestamp

### What existing audit models can still do

- `AuditTrail` can log create, publish, export, and restore events for cross-app auditing.
- `AssetHistory` should stay focused on assets and should not be repurposed for document source-of-truth versioning.

## Asset Data Pulling Plan

Some SOC 2 and ISMS forms should pull data directly from Tracker instead of requiring manual re-entry.

### Primary source domains

- asset inventory from `Asset`
- ownership and department context from `Employee`
- asset change context from `AssetHistory`
- general operational audit context from `AuditTrail`
- user and admin access state from `M365User`
- device compliance state from `IntuneDevice`
- control evidence history from `EvidenceSnapshot`
- risks and control mappings from `Risk`, `Control`, and `ControlRiskMapping`

### Likely forms that should prefill from Tracker

- ISMS asset register
- endpoint inventory and ownership summary
- access review workbook
- privileged access/admin role review
- vulnerability and patching summary
- encryption/compliance posture summary
- backup and recovery evidence summary
- risk register supporting packet
- control-to-policy mapping packet
- system description appendix sections that reference current assets and tools

### Data strategy

- use stable query services instead of embedding raw SQL in templates
- define field-level bindings from form templates to source services
- capture the source timestamp and source version used for generated outputs
- permit manual override fields where auditor narrative or exception handling is required

## Product Modules

### 1. ISMS document library

- tree/list view of all ISMS documents
- filters by category, status, and document type
- document metadata and ownership

### 2. Editor and preview

- Markdown editor
- rendered preview
- metadata sidebar
- publish/unpublish controls

### 3. Revision history

- version list
- diff view
- restore action
- export by selected version

### 4. Manual assembly

- define full manual ordering
- include/exclude documents from assembled exports
- generate master table of contents

### 5. Forms and artifact generation

- form template definitions
- form submission UI
- prefill from assets/manual/compliance data
- generate output files for audits and internal review

## Delivery Roadmap

### Phase 1: foundation

Goal: create a usable ISMS module with managed documents.

- create new ISMS blueprint and navigation entry
- add ISMS document and version tables
- add import routine for the current Markdown manual library
- create read-only library browser and document detail page
- move canonical manual content out of `templates/ISMS-MANUAL` into a managed content area

### Phase 2: editor and revision history

Goal: make the content editable and safe.

- add Markdown editor page
- save immutable versions on every edit
- add change summaries
- add document locking or optimistic concurrency check
- add version history page
- add version diff page
- add restore workflow that creates a new version

### Phase 3: exports

Goal: produce usable outputs from managed documents.

- export single document as `.md`
- export single document as `.pdf`
- export single document as `.docx`
- add full-manual assembly pipeline
- export full manual as `.pdf`, `.docx`, and `.md`

### Phase 4: asset and compliance bindings

Goal: make manual outputs and forms data-aware.

- add source services for assets, employees, M365, Intune, risks, controls, and evidence
- add reusable data binding layer for forms
- add snapshot metadata so generated outputs can be traced to source data state

### Phase 5: forms engine

Goal: prefill and generate audit artifacts.

- add form template models
- add field binding definitions
- add form submission UI
- add reviewer workflow
- generate completed files as Markdown, PDF, and DOCX artifacts

### Phase 6: SOC 2 / ISMS packets

Goal: generate audit-ready deliverables.

- generate asset inventory packet
- generate access review packet
- generate control evidence support packet
- generate risk treatment packet
- generate system description support packet
- attach artifacts to controls/evidence records where applicable

## Phase 1 Build Checklist

- [ ] create `isms` blueprint and routes
- [ ] add `isms_document` table
- [ ] add `isms_document_version` table
- [ ] add `isms_export_run` table
- [ ] create migration for new ISMS tables
- [ ] build importer for current manual `.md` files
- [ ] create managed content directory
- [ ] build document list view
- [ ] build document detail view
- [ ] add role/permission checks for read/edit/approve/export

## Phase 2 Build Checklist

- [ ] build Markdown editor UI
- [ ] build rendered preview UI
- [ ] save a new immutable version on each edit
- [ ] require change summary on save
- [ ] add optimistic concurrency check
- [ ] add history page
- [ ] add diff page
- [ ] add restore workflow
- [ ] log restore/publish/export actions to `AuditTrail`

## Phase 3 Build Checklist

- [ ] add raw Markdown export
- [ ] add normalized HTML render service
- [ ] add PDF export from HTML pipeline
- [ ] add DOCX export service
- [ ] add full manual assembly service
- [ ] add title page and TOC generation
- [ ] stamp exports with source version metadata

## Phase 4 Build Checklist

- [ ] define asset/compliance query services
- [ ] add service for asset inventory payloads
- [ ] add service for employee ownership payloads
- [ ] add service for M365 access/admin payloads
- [ ] add service for Intune compliance payloads
- [ ] add service for control/risk/evidence payloads
- [ ] define field binding contract for forms

## Phase 5 Build Checklist

- [ ] add `form_template` table
- [ ] add `form_field` table
- [ ] add `form_submission` table
- [ ] add `generated_artifact` table
- [ ] build template editor for admin users
- [ ] build submission UI
- [ ] support prefilled + manual fields
- [ ] store provenance of source document versions and source data timestamps

## Phase 6 Build Checklist

- [ ] build asset inventory form/template
- [ ] build access review form/template
- [ ] build backup and recovery evidence form/template
- [ ] build patch/vulnerability summary form/template
- [ ] build risk packet template
- [ ] build system description appendix template
- [ ] link outputs to SOC 2 controls/evidence where appropriate

## Key Risks And Controls

### Risks

- continuing to use `templates/` as canonical content storage
- mixing published content with draft content
- treating audit logs as version storage
- exporting documents without version provenance
- pulling live asset data without recording snapshot timing
- over-hardcoding forms instead of building a reusable binding layer

### Controls

- use immutable document versions
- use explicit published version pointers
- log restore, publish, and export actions
- store export provenance per run
- snapshot source data metadata for generated artifacts
- separate template definitions from generated outputs

## Recommended First Slice

The smallest useful slice is:

1. import the current ISMS Markdown library into new ISMS document tables
2. view a single document in a managed UI
3. edit it and create version history
4. restore an earlier version
5. export that document as Markdown, PDF, and DOCX

That proves the core content-management problem before building forms and packets.

## Success Criteria

- ISMS content is no longer managed as loose editable template files
- every document edit produces a recoverable version
- any approved version can be restored without losing history
- manual and form exports are traceable to exact content versions
- selected SOC 2 / ISMS forms can prefill from Tracker asset and compliance data
- generated artifacts are reusable in audit preparation and internal review