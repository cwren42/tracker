"""
Migration: Add card_number column to employee table.
Generates a unique 6-digit card number for all existing employees.
"""
import random
import sys
import os

sys.path.insert(0, '/var/www/tracker')
os.chdir('/var/www/tracker')

from app import app
from extensions import db

def run():
    with app.app_context():
        # Add column if it doesn't exist
        try:
            db.session.execute(db.text("ALTER TABLE employee ADD COLUMN card_number VARCHAR(20)"))
            db.session.commit()
            print("Added card_number column.")
        except Exception as e:
            db.session.rollback()
            if 'already exists' in str(e).lower() or 'duplicate column' in str(e).lower():
                print("card_number column already exists, skipping ALTER.")
            else:
                raise

        # Generate unique card numbers for all employees that don't have one
        rows = db.session.execute(
            db.text("SELECT id FROM employee WHERE card_number IS NULL OR card_number = ''")
        ).fetchall()

        used = set(
            r[0] for r in
            db.session.execute(db.text("SELECT card_number FROM employee WHERE card_number IS NOT NULL AND card_number != ''")).fetchall()
        )

        for (emp_id,) in rows:
            while True:
                num = str(random.randint(100000, 999999))
                if num not in used:
                    used.add(num)
                    break
            db.session.execute(
                db.text("UPDATE employee SET card_number = :num WHERE id = :id"),
                {"num": num, "id": emp_id}
            )

        db.session.commit()
        print(f"Generated card numbers for {len(rows)} employees.")

if __name__ == '__main__':
    run()
