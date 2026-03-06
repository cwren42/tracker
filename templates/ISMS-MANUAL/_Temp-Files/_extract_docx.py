import zipfile, xml.etree.ElementTree as ET, os, re
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
files=[
'ISMS-Manual2025v1.docx',
'IS-CIRQ-D-001-G-ISMS Scope Document.docx',
'IS-CIRQ-D-005-G-Risk Treatment Plan (RTP) (Template).docx',
'IS-CIRQ-D-006-G-Statement of Applicability (SoA) (Template).docx',
'IS-CIRQ-F-001-G- Risk Assessment Register (Template).docx',
'IS-CIRQ-P-001-G-Information Security Policy (Master Policy).docx',
'IS-CIRQ-P-003-G-Risk Management Policy.docx',
'IS-CIRQ-PR-002-G- Information Security Risk Assessment Procedure.docx',
'IS-CIRQ-PR-003-G- Information Security Risk Treatment Procedure.docx',
'IS-CIRQ-PR-023-G- Internal Audit Procedure.docx',
'IS-CIRQ-PR-024-G- Management Review Procedure.docx'
]
for f in files:
    print('\n===', f, '===')
    if not os.path.exists(f):
        print('MISSING')
        continue
    try:
        with zipfile.ZipFile(f) as z:
            data=z.read('word/document.xml')
        root=ET.fromstring(data)
        paras=[]
        for p in root.findall('.//w:body/w:p', ns):
            texts=[t.text for t in p.findall('.//w:t', ns) if t.text]
            if texts:
                s=''.join(texts).strip()
                if s:
                    paras.append(re.sub(r'\s+',' ',s))
        keys=('purpose','scope','risk','control','audit','review','soc 2','statement of applicability','owner','frequency','evidence','exception','vendor','incident','change management','access','backup','monitoring','trust services criteria')
        print('FIRST_PARAS:')
        for p in paras[:25]:
            print('-', p[:220])
        print('KEY_HITS:')
        hits=[p for p in paras if any(k in p.lower() for k in keys)]
        for p in hits[:35]:
            print('*', p[:240])
    except Exception as e:
        print('ERROR:', e)
