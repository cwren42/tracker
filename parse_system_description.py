#!/usr/bin/env python3
"""
Parse System Description Template and create database structure
"""
from docx import Document
import re

def parse_system_description_template(docx_path):
    """Parse the System Description template and extract sections"""
    doc = Document(docx_path)
    
    sections = []
    current_section = None
    current_content = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name
        
        # Check if this is a heading (new section)
        if style.startswith('Heading'):
            # Save previous section if exists
            if current_section:
                sections.append({
                    'title': current_section['title'],
                    'level': current_section['level'],
                    'content': '\n'.join(current_content),
                    'style': current_section['style']
                })
            
            # Start new section
            level = int(style.replace('Heading ', '')) if 'Heading ' in style else 0
            current_section = {
                'title': text,
                'level': level,
                'style': style
            }
            current_content = []
        else:
            # Add content to current section
            if text:
                current_content.append(text)
    
    # Save last section
    if current_section:
        sections.append({
            'title': current_section['title'],
            'level': current_section['level'],
            'content': '\n'.join(current_content),
            'style': current_section['style']
        })
    
    return sections

def categorize_sections(sections):
    """Categorize sections by type for automation"""
    categorized = {
        'company_overview': [],
        'system_components': [],
        'controls': [],
        'infrastructure': [],
        'manual': []
    }
    
    for section in sections:
        title_lower = section['title'].lower()
        
        if any(kw in title_lower for kw in ['company background', 'overview of operations']):
            categorized['company_overview'].append(section)
        elif any(kw in title_lower for kw in ['infrastructure', 'software', 'network']):
            categorized['infrastructure'].append(section)
        elif any(kw in title_lower for kw in ['people', 'data', 'organizational']):
            categorized['system_components'].append(section)
        elif any(kw in title_lower for kw in ['control', 'security', 'access', 'monitoring', 'backup', 'incident', 'vendor']):
            categorized['controls'].append(section)
        else:
            categorized['manual'].append(section)
    
    return categorized

if __name__ == '__main__':
    template_path = "/home/webuser/System Description Template - Provided by Strike Graph.docx"
    sections = parse_system_description_template(template_path)
    
    print(f"Parsed {len(sections)} sections")
    print("\nSections by category:")
    
    categorized = categorize_sections(sections)
    for category, secs in categorized.items():
        print(f"\n{category.upper()}: {len(secs)} sections")
        for sec in secs:
            print(f"  - {sec['title']}")
