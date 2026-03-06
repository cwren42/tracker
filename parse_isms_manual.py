"""
Parse ISMS Manual and extract policies for SOC2 controls
"""
from docx import Document
import re
from app import app, db
from soc2_models import SOC2Control

def parse_isms_manual(file_path='/home/webuser/ISMS-Manual2025v1.docx'):
    """Parse the ISMS manual and extract policy structure"""
    
    doc = Document(file_path)
    
    print("=" * 80)
    print("ISMS Manual Structure Analysis")
    print("=" * 80)
    
    # Extract all headings and their levels
    policies = []
    current_section = None
    current_content = []
    
    for para in doc.paragraphs:
        # Check if it's a heading
        if para.style.name.startswith('Heading'):
            # Save previous section
            if current_section:
                policies.append({
                    'title': current_section,
                    'content': '\n'.join(current_content),
                    'level': current_section_level
                })
            
            current_section = para.text.strip()
            current_section_level = int(para.style.name.replace('Heading', '').strip() or '1')
            current_content = []
            
            print(f"\n{'  ' * (current_section_level - 1)}📄 {current_section}")
        elif para.text.strip():
            current_content.append(para.text.strip())
    
    # Save last section
    if current_section:
        policies.append({
            'title': current_section,
            'content': '\n'.join(current_content),
            'level': current_section_level
        })
    
    print(f"\n{'=' * 80}")
    print(f"Total sections found: {len(policies)}")
    print(f"{'=' * 80}")
    
    # Try to map policies to SOC2 controls
    print("\n" + "=" * 80)
    print("Mapping Policies to SOC2 Controls")
    print("=" * 80)
    
    with app.app_context():
        controls = SOC2Control.query.all()
        
        # Keywords to match controls to policies
        control_keywords = {
            'Administrator Access': ['admin', 'administrator', 'privileged', 'access control', 'user access'],
            'Antivirus': ['antivirus', 'malware', 'endpoint protection', 'virus'],
            'Asset Inventory': ['asset', 'inventory', 'hardware', 'software', 'equipment'],
            'Automatic Patching': ['patch', 'update', 'vulnerability', 'security updates'],
            'Change Management: Infrastructure': ['change management', 'change control', 'infrastructure'],
            'Change Management Policy': ['change management', 'change policy', 'change procedure'],
            'Configuration Standards': ['configuration', 'baseline', 'hardening', 'security configuration'],
            'Data Classification Policy': ['data classification', 'information classification', 'data handling'],
            'Provisioning': ['provisioning', 'user creation', 'account creation', 'onboarding'],
            'User Access Review': ['access review', 'user review', 'access recertification', 'periodic review'],
            'Termination of Access': ['termination', 'offboarding', 'access removal', 'deprovisioning'],
            'User Authentication': ['authentication', 'password', 'credential', 'login'],
            'Vulnerability Scan': ['vulnerability', 'scan', 'penetration test', 'security assessment']
        }
        
        mappings = {}
        
        for control in controls:
            print(f"\n🔍 {control.control_name}")
            keywords = control_keywords.get(control.control_name, [])
            
            matched_policies = []
            for policy in policies:
                # Check if any keywords match in policy title or content
                policy_text = (policy['title'] + ' ' + policy['content']).lower()
                for keyword in keywords:
                    if keyword.lower() in policy_text:
                        matched_policies.append(policy['title'])
                        print(f"   ✓ {policy['title']}")
                        break
            
            if not matched_policies:
                print(f"   ⚠️  No matching policies found")
            
            mappings[control.control_name] = matched_policies
    
    return policies, mappings

def display_policy_summary(policies):
    """Display summary of policies found"""
    print("\n" + "=" * 80)
    print("Policy Summary")
    print("=" * 80)
    
    # Group by main sections (Heading 1)
    main_sections = [p for p in policies if p['level'] == 1]
    
    for section in main_sections:
        print(f"\n📋 {section['title']}")
        subsections = [p for p in policies if p['level'] > 1 and p['content'].startswith(section['title'][:20])]
        for sub in subsections[:5]:  # Show first 5 subsections
            print(f"   • {sub['title']}")

if __name__ == '__main__':
    try:
        policies, mappings = parse_isms_manual()
        display_policy_summary(policies)
        
        print("\n" + "=" * 80)
        print("✅ ISMS Manual parsed successfully!")
        print("=" * 80)
        print("\nNext steps:")
        print("1. Review the policy mappings above")
        print("2. Add PolicyDocument model to soc2_models.py")
        print("3. Create policy upload interface in dashboard")
        print("4. Link policies to controls in database")
        
    except Exception as e:
        print(f"\n❌ Error parsing ISMS manual: {e}")
        import traceback
        traceback.print_exc()
