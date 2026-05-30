#!/usr/bin/env python3
"""Rename an asset in the tracker database.

Usage:
    .venv/bin/python rename_asset.py <old_name> <new_name>

Examples:
    .venv/bin/python rename_asset.py KEN-DELL KEN-LENOVO
    .venv/bin/python rename_asset.py "OLD NAME" "NEW NAME"
"""
import sys
from pg_db import pg_connect

def main():
    if len(sys.argv) != 3:
        print("Usage: rename_asset.py <old_name> <new_name>")
        sys.exit(1)

    old_name = sys.argv[1]
    new_name = sys.argv[2]

    db = pg_connect()

    row = db.execute("SELECT id, name FROM asset WHERE name ILIKE %s", (old_name,)).fetchone()
    if not row:
        print(f"ERROR: No asset found matching '{old_name}'")
        db.close()
        sys.exit(1)

    conflict = db.execute("SELECT id FROM asset WHERE name ILIKE %s AND id != %s", (new_name, row['id'])).fetchone()
    if conflict:
        print(f"ERROR: An asset named '{new_name}' already exists (id={conflict['id']})")
        db.close()
        sys.exit(1)

    db.execute("UPDATE asset SET name = %s WHERE id = %s", (new_name, row['id']))
    db.commit()
    print(f"Renamed: '{row['name']}' -> '{new_name}'  (id={row['id']})")
    db.close()

if __name__ == "__main__":
    main()
