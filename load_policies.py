"""
Parse ISMS policies and procedures from markdown files
"""
import sys
import os
import re
sys.path.insert(0, '/var/www/tracker')

from app import app, db
from sqlalchemy import text
from datetime import datetime

ISMS_DIR = "/var/www/tracker/templates/ISMS-MANUAL"

def parse_policy_file(filepath):
    """Parse a single policy markdown file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Extract metadata from the header
    doc_id_match = re.search(r'\*\*Document:\s*([^\*]+)\*\*', content)
    title_match = re.search(r'\*\*Standards Name:\s*([^\*]+)\*\*', content)
    category_match = re.search(r'\*\*Category:\s*([^\*]+)\*\*', content)
    division_match = re.search(r'\*\*Division:\s*([^\*]+)\*\*', content)
    type_match = re.search(r'\*\*Standard Type:\s*([^\*]+)\*\*', content)
    version_match = re.search(r'\*\*Version:\*\*\s*([^\*]+?)(?:\*\*|$)', content)
    effective_match = re.search(r'\*\*Effective Date:\*\*\s*([^\*]+?)(?:\*\*|$)', content)
    review_match = re.search(r'\*\*Review Date:\*\*\s*([^\*]+?)(?:\*\*|$)', content)
    approved_match = re.search(r'\*\*Approved By:\*\*\s*([^\n]+)', content)
    
    return {
        'document_id': doc_id_match.group(1).strip() if doc_id_match else None,
        'title': title_match.group(1).strip() if title_match else os.path.basename(filepath),
        'category': category_match.group(1).strip() if category_match else 'General',
        'division': division_match.group(1).strip() if division_match else 'Unknown',
        'standard_type': type_match.group(1).strip() if type_match else 'Global',
        'version': version_match.group(1).strip() if version_match else '1.0',
        'effective_date': effective_match.group(1).strip() if effective_match else None,
        'review_date': review_match.group(1).strip() if review_match else None,
        'approved_by': approved_match.group(1).strip() if approved_match else None,
        'content': content,
        'file_path': filepath
    }

def parse_sections(content):
    """Parse sections from policy content"""
    sections = []
    # Match numbered sections like "1. Introduction", "2. Purpose", etc.
    section_pattern = r'\*\*(\d+)\.\s+([^\*]+)\*\*'
    matches = re.finditer(section_pattern, content)
    
    section_positions = []
    for match in matches:
        section_positions.append({
            'number': match.group(1),
            'title': match.group(2).strip(),
            'start': match.end()
        })
    
    # Extract content between sections
    for i, section in enumerate(section_positions):
        end_pos = section_positions[i+1]['start'] if i+1 < len(section_positions) else len(content)
        section_content = content[section['start']:end_pos].strip()
        
        sections.append({
            'section_number': section['number'],
            'section_title': section['title'],
            'section_content': section_content,
            'section_order': i
        })
    
    return sections

def load_policies():
    """Load all policies and procedures from the ISMS directory"""
    with app.app_context():
        # Clear existing policies
        db.session.execute(text("DELETE FROM policy_section"))
        db.session.execute(text("DELETE FROM policy"))
        db.session.commit()
        
        policy_count = 0
        procedure_count = 0
        
        # Get all markdown files
        for filename in sorted(os.listdir(ISMS_DIR)):
            if not filename.endswith('.md'):
                continue
            
            # Skip non-policy/procedure files
            if not (filename.startswith('IS-CIRQ-P-') or filename.startswith('IS-CIRQ-PR-')):
                continue
            
            filepath = os.path.join(ISMS_DIR, filename)
            
            try:
                # Parse the policy
                policy_data = parse_policy_file(filepath)
                
                if not policy_data['document_id']:
                    print(f"⚠ Skipping {filename} - no document ID found")
                    continue
                
                # Insert policy
                result = db.session.execute(text("""
                    INSERT INTO policy 
                    (document_id, title, category, division, standard_type, version, 
                     effective_date, review_date, approved_by, content, file_path, updated_by)
                    VALUES 
                    (:doc_id, :title, :category, :division, :std_type, :version,
                     :effective, :review, :approved, :content, :filepath, 'system')
                """), {
                    'doc_id': policy_data['document_id'],
                    'title': policy_data['title'],
                    'category': policy_data['category'],
                    'division': policy_data['division'],
                    'std_type':policy_data['standard_type'],
                    'version': policy_data['version'],
                    'effective': policy_data['effective_date'],
                    'review': policy_data['review_date'],
                    'approved': policy_data['approved_by'],
                    'content': policy_data['content'],
                    'filepath': policy_data['file_path']
                })
                
                policy_id = result.lastrowid
                
                # Parse and insert sections
                sections = parse_sections(policy_data['content'])
                for section in sections:
                    db.session.execute(text("""
                        INSERT INTO policy_section
                        (policy_id, section_number, section_title, section_content, section_order)
                        VALUES (:policy_id, :number, :title, :content, :order)
                    """), {
                        'policy_id': policy_id,
                        'number': section['section_number'],
                        'title': section['section_title'],
                        'content': section['section_content'],
                        'order': section['section_order']
                    })
                
                if filename.startswith('IS-CIRQ-PR-'):
                    procedure_count += 1
                    print(f"✓ Loaded procedure: {policy_data['title']}")
                else:
                    policy_count += 1
                    print(f"✓ Loaded policy: {policy_data['title']}")
                
            except Exception as e:
                print(f"✗ Error loading {filename}: {e}")
                continue
        
        db.session.commit()
        print(f"\n✓ Loaded {policy_count} policies and {procedure_count} procedures")

if __name__ == '__main__':
    load_policies()
