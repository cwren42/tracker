# ISMS Import Dropzone

Put the new ISMS manual source files here before running:

```bash
./.venv/bin/python import_isms_documents.py
```

Current importer behavior:

- imports every `.md` file in this folder except `README.md`
- uses front-matter-like metadata when present
- otherwise derives title from the first `# Heading` or the filename