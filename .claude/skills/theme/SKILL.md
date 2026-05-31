---
name: theme
description: Bring a Tracker page (template and/or its CSS) onto the Refined Slate + Indigo design system. Use when a page looks off-theme, dark, capped-width, or has rainbow filter buttons. Converts hardcoded colors to tokens and verifies.
allowed-tools: Read, Edit, Write, Grep, Bash
argument-hint: "[template-or-css file]"
---
# Theme a page → Refined Slate + Indigo

The design system lives in `static/css/theme.css` (token `:root` + a "Design System v2"
component layer + global rules). Default theme = light "Refined Slate + Indigo".
Other themes (`[data-theme=...]`: deep-forest, modern-naturalist, heritage, dark)
override the same tokens, so **use tokens, never hardcoded colors**.

## Token map (what to convert TO)
- Surfaces: `var(--surface)` (card/white), `var(--surface-2)`, `var(--app-bg)` (page gray)
- Text: `var(--text)`, `var(--muted)`
- Lines: `var(--border)`
- Accent: `var(--accent)` (indigo #6366F1), `var(--accent-weak)` (pale indigo, hover/tint)
- Semantic: `var(--success|warning|danger|info)` + `var(--*-bg)` (light tints)
- Scales: `--space-1..7`, `--radius-sm|--radius|--radius-lg`, `--shadow-sm|--shadow|--shadow-lg`, `--fs-*`, `--ring`

## Off-theme checklist (find + fix)
1. **Dark hardcoded hex** (`#0d1117`, `#1a1a2e`, `#0a1420`, `#1a2233`, `#2a2f3a`, gradients, `text-white` on cards) → tokens. Grep: `grep -nE "#0d|#1a|#2a|linear-gradient|bg-dark|text-white|table-dark"`.
2. **Width caps**: `container-fluid` wrappers and `style="max-width:1500px"` → drop them; pages should sit directly in `.content` (full width, like assets/tickets/dashboard). Replace `<div class="container-fluid py-3" style="max-width:1500px;">` → `<div class="py-3">`.
3. **`thead.table-dark`** is neutralized globally, but `<table class="... table-dark ...">` (on the table itself) makes the whole table dark → remove `table-dark`.
4. **Rainbow filter bars** (each button a different color at rest) → uniform `btn-outline-secondary`, active = `btn-primary` (indigo). See assets/users/tickets for the pattern.
5. **Solid colored card headers** (`card-header bg-primary text-white`) are already softened to white-with-colored-icon by a global rule — leave them; they work.
6. **Cache-bust**: ensure per-page CSS is `?v={{ asset_version }}`.
7. **Per-page CSS files** (`static/css/<page>.css`) are the usual culprit when the markup looks clean but the page is dark — retokenize them.

## Keep dark on purpose
Code/console blocks stay dark — the AI search bar (`_ai_search_bar.html`), PowerShell
script panels (`.ps-panel`), `<pre>` command/output blocks, terminal views. Dark is the
right convention there.

## Then
Verify (Jinja parse / CSS brace balance) and deploy with the **ship** skill. Restart so
`asset_version` updates and the new CSS actually reaches browsers.
