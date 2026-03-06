#!/usr/bin/env python3
"""
Load SOC2 risks from CSV file into database
"""
import sqlite3
import csv
import os

def load_risks():
    db_path = '/var/www/tracker/assets.db'
    csv_path = '/var/www/tracker/cirque_corporation-risks.csv'
    
    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found at {csv_path}")
        print("Please upload the risks CSV file to /var/www/tracker/")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Clear existing risks
        cursor.execute("DELETE FROM risk")
        
        # Read and load risks from CSV
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            risks_loaded = 0
            for row in reader:
                risk_name = row.get('Risk Name', '').strip()
                if not risk_name:
                    continue
                
                risk_description = row.get('Risk Description', '').strip()
                risk_treatment = row.get('Risk Treatment', '').strip()
                risk_progress = row.get('Risk Progress', '').strip()
                risk_category = row.get('Risk Category', '').strip()
                risk_status = row.get('Risk Status', 'TRUE').strip().upper() == 'TRUE'
                risk_impact = row.get('Risk Impact', '').strip()
                risk_likelihood = row.get('Risk Likelihood', '').strip()
                risk_combined_score = row.get('Risk Combined Score', '').strip()
                risk_owner = row.get('Risk Owner', '').strip()
                active_controls = row.get('Active Controls', '').strip()
                
                cursor.execute("""
                    INSERT INTO risk (
                        risk_name, risk_description, risk_treatment, risk_progress,
                        risk_category, risk_status, risk_impact, risk_likelihood,
                        risk_combined_score, risk_owner, active_controls
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    risk_name, risk_description, risk_treatment, risk_progress,
                    risk_category, risk_status, risk_impact, risk_likelihood,
                    risk_combined_score, risk_owner, active_controls
                ))
                
                risks_loaded += 1
        
        conn.commit()
        print(f"✓ Loaded {risks_loaded} risks from CSV")
        
        # Show summary by category
        cursor.execute("""
            SELECT risk_category, COUNT(*) 
            FROM risk 
            GROUP BY risk_category
            ORDER BY COUNT(*) DESC
        """)
        print("\nRisks by Category:")
        for category, count in cursor.fetchall():
            print(f"  - {category}: {count}")
        
        # Show summary by score
        cursor.execute("""
            SELECT risk_combined_score, COUNT(*) 
            FROM risk 
            WHERE risk_combined_score != 'NA'
            GROUP BY risk_combined_score
        """)
        print("\nRisks by Score:")
        for score, count in cursor.fetchall():
            print(f"  - {score}: {count}")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to load risks: {e}")
        conn.rollback()
        return False
        
    finally:
        conn.close()

if __name__ == '__main__':
    success = load_risks()
    exit(0 if success else 1)
