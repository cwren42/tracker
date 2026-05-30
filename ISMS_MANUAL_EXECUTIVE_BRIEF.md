# ISMS Manual Executive Brief

## What We Are Building

We are moving the ISMS manual from loose Markdown files into a managed application module inside Tracker.

## Why This Matters

- every change becomes versioned and recoverable
- the manual becomes exportable in controlled formats
- future audit packets can reuse approved content instead of being rebuilt manually
- selected forms can pull live Tracker data instead of requiring duplicate entry

## Phase 1 Outcome

The first implementation slice delivers:

- a dedicated ISMS section in the app
- managed document and version records in the database
- import of the current Markdown library
- a read-only library view and document detail view
- the schema needed for later restore and export workflows

## What Phase 1 Does Not Yet Do

- edit documents in the UI
- compare versions side by side
- restore an older version
- export PDF or DOCX
- generate audit forms or packets

Those are the next steps, but Phase 1 gives us the correct foundation so later work is built on versioned content instead of template files.

## Success Measure

The ISMS manual is now represented as managed application data with a path to restoreable history, controlled publishing, and reusable audit outputs.