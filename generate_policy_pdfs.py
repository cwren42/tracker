#!/usr/bin/env python3
"""
Generate PDF files for all policies and update StrikeGraph evidence repository
"""
import sqlite3
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from datetime import datetime, timedelta

def generate_policy_pdf(policy_id, document_id, title, db_conn):
    """Generate PDF for a single policy"""
    cursor = db_conn.cursor()
    
    # Get policy details
    cursor.execute("""
        SELECT category, division, standard_type, version, 
               effective_date, review_date, approved_by
        FROM policy WHERE id = ?
    """, (policy_id,))
    
    policy_info = cursor.fetchone()
    if not policy_info:
        return None
    
    category, division, std_type, version, eff_date, rev_date, approved = policy_info
    
    # Get sections
    cursor.execute("""
        SELECT section_number, section_title, section_content
        FROM policy_section
        WHERE policy_id = ?
        ORDER BY section_order
    """, (policy_id,))
    
    sections = cursor.fetchall()
    
    # Create PDF file path
    safe_filename = title.replace('/', '-').replace('\\', '-')
    safe_filename = safe_filename[:100]  # Limit length
    evidence_dir = '/var/www/tracker/static/evidence/policies'
    os.makedirs(evidence_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d')
    filename = f"{document_id}_{safe_filename}_{timestamp}.pdf"
    file_path = os.path.join(evidence_dir, filename)
    
    # Generate PDF
    pdf = SimpleDocTemplate(file_path, pagesize=letter,
                           topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    
    # Styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor='#1a365d',
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=11,
        textColor='#4a5568',
        spaceAfter=6,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=13,
        textColor='#2d3748',
        spaceAfter=10,
        spaceBefore=14,
        fontName='Helvetica-Bold'
    )
    
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontSize=10,
        alignment=TA_JUSTIFY,
        spaceAfter=10,
        leftIndent=0,
        rightIndent=0
    )
    
    # Header
    story.append(Paragraph("Cirque Corporation", title_style))
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Metadata table
    story.append(Paragraph(f"<b>Document ID:</b> {document_id}", subtitle_style))
    if category:
        story.append(Paragraph(f"<b>Category:</b> {category}", subtitle_style))
    if division:
        story.append(Paragraph(f"<b>Division:</b> {division}", subtitle_style))
    if std_type:
        story.append(Paragraph(f"<b>Type:</b> {std_type}", subtitle_style))
    if version:
        story.append(Paragraph(f"<b>Version:</b> {version}", subtitle_style))
    if eff_date:
        story.append(Paragraph(f"<b>Effective Date:</b> {eff_date}", subtitle_style))
    if rev_date:
        story.append(Paragraph(f"<b>Review Date:</b> {rev_date}", subtitle_style))
    if approved:
        story.append(Paragraph(f"<b>Approved By:</b> {approved}", subtitle_style))
    
    story.append(Paragraph(f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y')}", subtitle_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Policy sections
    for section_num, section_title, section_content in sections:
        # Section heading
        section_header = f"{section_num}. {section_title}" if section_num else section_title
        story.append(Paragraph(section_header, heading_style))
        
        # Section content
        if section_content:
            # Split by newlines and handle bullets
            lines = section_content.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 0.1*inch))
                    continue
                
                # Escape XML characters
                line = (line.replace('&', '&amp;')
                           .replace('<', '&lt;')
                           .replace('>', '&gt;'))
                
                # Handle bullet points
                if line.startswith('- '):
                    line = '• ' + line[2:]
                    story.append(Paragraph(line, body_style))
                elif line.startswith('* '):
                    line = '• ' + line[2:]
                    story.append(Paragraph(line, body_style))
                else:
                    story.append(Paragraph(line, body_style))
    
    # Build PDF
    try:
        pdf.build(story)
        print(f"✅ Generated: {filename}")
        # Return relative path for web access
        relative_path = f"evidence/policies/{filename}"
        return relative_path
    except Exception as e:
        print(f"❌ Error building PDF for {document_id}: {e}")
        return None


def update_strikegraph_evidence(db_conn):
    """Update StrikeGraph evidence table with policy evidence"""
    cursor = db_conn.cursor()
    
    print(f"\n📋 UPDATING STRIKEGRAPH EVIDENCE")
    print(f"=" * 80)
    
    # Get all policies
    cursor.execute("""
        SELECT id, document_id, title
        FROM policy
        ORDER BY document_id
    """)
    
    policies = cursor.fetchall()
    print(f"Found {len(policies)} policies in database\n")
    
    created_count = 0
    updated_count = 0
    error_count = 0
    
    for policy_id, document_id, title in policies:
        try:
            # Generate PDF
            pdf_path = generate_policy_pdf(policy_id, document_id, title, db_conn)
            
            if not pdf_path:
                error_count += 1
                continue
            
            # Check if evidence entry exists
            evidence_name = title
            
            cursor.execute("""
                SELECT id, file_path FROM strikegraph_evidence
                WHERE evidence_name = ?
            """, (evidence_name,))
            
            existing = cursor.fetchone()
            
            # Calculate expiration date (1 year from now)
            expiration_date = (datetime.now() + timedelta(days=365)).date()
            
            if existing:
                # Update existing entry
                evidence_id, old_file = existing
                cursor.execute("""
                    UPDATE strikegraph_evidence
                    SET file_path = ?,
                        evidence_type = 'Policy',
                        automation_source = 'ISMS',
                        expiration_date = ?,
                        expiration_schedule = 365,
                        updated_at = ?
                    WHERE id = ?
                """, (pdf_path, expiration_date, datetime.now(), evidence_id))
                
                # Delete old file if different
                # Handle both absolute and relative paths
                if old_file and old_file != pdf_path:
                    # Convert relative path to absolute if needed
                    if not old_file.startswith('/'):
                        old_file_abs = f'/var/www/tracker/static/{old_file}'
                    else:
                        old_file_abs = old_file
                    
                    # Only delete if it's truly a different file
                    new_file_abs = f'/var/www/tracker/static/{pdf_path}'
                    if old_file_abs != new_file_abs and os.path.exists(old_file_abs):
                        try:
                            os.remove(old_file_abs)
                        except:
                            pass
                
                updated_count += 1
            else:
                # Create new evidence entry
                cursor.execute("""
                    INSERT INTO strikegraph_evidence (
                        evidence_name, evidence_description, evidence_type,
                        expiration_schedule, expiration_date, is_active,
                        owner, automation_source, file_path, submission_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    evidence_name,
                    f"{document_id} - {title}",
                    'Policy',
                    365,
                    expiration_date,
                    True,
                    'chris.wren@cirque.com',
                    'ISMS',
                    pdf_path,
                    'Not Submitted'
                ))
                created_count += 1
        
        except Exception as e:
            print(f"❌ Error processing {document_id}: {e}")
            error_count += 1
            continue
    
    db_conn.commit()
    
    print(f"\n📊 SUMMARY")
    print(f"=" * 80)
    print(f"PDFs Generated: {created_count + updated_count}")
    print(f"Evidence Created: {created_count}")
    print(f"Evidence Updated: {updated_count}")
    print(f"Errors: {error_count}")
    
    # Show total evidence count
    cursor.execute("SELECT COUNT(*) FROM strikegraph_evidence WHERE evidence_type = 'Policy'")
    total = cursor.fetchone()[0]
    print(f"\nTotal Policy Evidence Items: {total}")


def main():
    db_path = '/var/www/tracker/assets.db'
    
    if not os.path.exists(db_path):
        print(f"❌ Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    
    try:
        update_strikegraph_evidence(conn)
        print(f"\n✅ Policy PDFs generated and evidence updated successfully!")
        print(f"📂 PDFs saved to: /var/www/tracker/static/evidence/policies/")
        print(f"🌐 Access at: https://tracker.corp.cirque.com/soc2/strikegraph")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        conn.close()


if __name__ == '__main__':
    main()
