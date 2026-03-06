"""
Load System Description template into database and auto-populate sections
"""
import sys
sys.path.insert(0, '/var/www/tracker')

from app import app, db, Asset, Employee, Setting
from sqlalchemy import text
from docx import Document
from datetime import datetime

def load_template_sections():
    """Parse template and load sections into database"""
    doc = Document("/home/webuser/System Description Template - Provided by Strike Graph.docx")
    
    sections = []
    current_section = None
    current_content = []
    section_order = 0
    
    for para in doc.paragraphs:
        text_content = para.text.strip()
        style = para.style.name
        
        if style.startswith('Heading'):
            # Save previous section
            if current_section:
                sections.append({
                    'title': current_section['title'],
                    'level': current_section['level'],
                    'content': '\n'.join(current_content),
                    'order': section_order
                })
                section_order += 1
            
            # Start new section
            level = int(style.replace('Heading ', '')) if 'Heading ' in style else 1
            current_section = {
                'title': text_content,
                'level': level
            }
            current_content = []
        elif text_content:
            current_content.append(text_content)
    
    # Save last section
    if current_section:
        sections.append({
            'title': current_section['title'],
            'level': current_section['level'],
            'content': '\n'.join(current_content),
            'order': section_order
        })
    
    return sections

def categorize_section(title):
    """Determine section category"""
    title_lower = title.lower()
    
    if any(kw in title_lower for kw in ['company background', 'overview of operations']):
        return 'company_overview'
    elif any(kw in title_lower for kw in ['infrastructure', 'network']):
        return 'infrastructure'
    elif any(kw in title_lower for kw in ['software', 'application']):
        return 'software'
    elif any(kw in title_lower for kw in ['people', 'organizational structure']):
        return 'people'
    elif any(kw in title_lower for kw in ['data', 'data flow']):
        return 'data'
    elif any(kw in title_lower for kw in ['control', 'security', 'access', 'monitoring', 'backup', 'incident', 'vendor', 'change management']):
        return 'controls'
    else:
        return 'general'

def auto_populate_section(title, category):
    """Auto-populate sections where possible"""
    with app.app_context():
        title_lower = title.lower()
        
        # Infrastructure section
        if 'infrastructure' in title_lower:
            assets = Asset.query.filter_by(category='Server').all()
            if assets:
                content = f"Cirque Corporation's {title} system comprises:\n\n"
                content += "**Primary Infrastructure:**\n"
                for asset in assets:
                    content += f"- {asset.name}: {asset.model or 'Server'} ({asset.location or 'Cloud'})\n"
                return content
        
        # Software section
        if 'software' in title_lower:
            # Get unique software from assets
            software_list = set()
            for asset in Asset.query.all():
                if asset.os_version:
                    software_list.add(asset.os_version)
            
            if software_list:
                content = "**Primary Software Components:**\n\n"
                for sw in sorted(software_list):
                    content += f"- {sw}\n"
                return content
        
        # People/Organizational section
        if 'people' in title_lower or 'organizational' in title_lower:
            employees = Employee.query.all()
            depts = set(e.department for e in employees if e.department)
            
            content = f"Cirque Corporation employs {len(employees)} personnel across multiple departments:\n\n"
            for dept in sorted(depts):
                dept_count = len([e for e in employees if e.department == dept])
                content += f"- {dept}: {dept_count} employees\n"
            return content
        
        # Vendor Management
        if 'vendor' in title_lower:
            # Get third party services from settings
            content = "**Third-Party Service Providers:**\n\n"
            content += "- Microsoft 365 (Email, Authentication, Device Management)\n"
            content += "- TeamViewer (Remote Access)\n"
            content += "- AWS/Cloud Infrastructure\n"
            return content
    
    return None

def main():
    with app.app_context():
        # Clear existing sections
        db.session.execute(text("DELETE FROM system_description"))
        db.session.commit()
        
        # Load template sections
        sections = load_template_sections()
        print(f"Loaded {len(sections)} sections from template\n")
        
        auto_populated_count = 0
        
        for sec in sections:
            category = categorize_section(sec['title'])
            auto_content = auto_populate_section(sec['title'], category)
            
            db.session.execute(text("""
                INSERT INTO system_description 
                (section_title, section_level, section_order, category, template_content, content, auto_populated, updated_by)
                VALUES (:title, :level, :order, :category, :template, :content, :auto, 'system')
            """), {
                'title': sec['title'],
                'level': sec['level'],
                'order': sec['order'],
                'category': category,
                'template': sec['content'],
                'content': auto_content or sec['content'],
                'auto': 1 if auto_content else 0
            })
            
            if auto_content:
                auto_populated_count += 1
                print(f"✓ Auto-populated: {sec['title']}")
        
        db.session.commit()
        print(f"\n✓ Loaded {len(sections)} sections ({auto_populated_count} auto-populated)")

if __name__ == '__main__':
    main()
