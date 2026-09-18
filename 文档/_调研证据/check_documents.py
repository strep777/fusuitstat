import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
files=sorted(ROOT.glob('*.md'))
issues=[]
texts={p.name:p.read_text(encoding='utf-8') for p in files}
for p in files:
    b=p.read_bytes();t=texts[p.name]
    if b.startswith(b'\xef\xbb\xbf'):issues.append(f'{p.name}: BOM')
    if '\ufffd' in t:issues.append(f'{p.name}: replacement character')
    if not t.startswith('# '):issues.append(f'{p.name}: missing H1')
    if len(re.findall(r'^```',t,re.M))%2:issues.append(f'{p.name}: unmatched code fence')
    for target in re.findall(r'\]\(([^)]+)\)',t):
        if target.startswith(('http://','https://','#','mailto:')):continue
        target=target.split('#')[0]
        if not (ROOT/target).exists():issues.append(f'{p.name}: broken link {target}')
    for token in ['首先','综上所述','总而言之','赋能','深入探讨']:
        if token in t:issues.append(f'{p.name}: wording review {token}')
alltext='\n'.join(texts.values())
for prefix,source,regex in [('C','功能确认清单-已确认.md',r'\bC\d{2}\b'),('FR','02_软件需求规格说明书.md',r'\bFR-\d{3}\b'),('TC','13_测试计划与验收用例.md',r'\bTC-[A-Z]\d{2}\b')]:
    defs=set(re.findall(regex,texts[source])); refs=set(re.findall(regex,alltext))
    for x in refs-defs:issues.append(f'undefined {prefix} reference {x}')
assets=json.loads((ROOT/'_调研证据/assets.json').read_text(encoding='utf-8'))
for a in assets:
    p=Path(a['path'])
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=a['sha256']:issues.append(f'asset changed/missing: {p}')
report={'date':'2026-09-18','markdown_files':len(files),'characters':sum(map(len,texts.values())),'bytes':sum(p.stat().st_size for p in files),'confirmed_item_definitions':len(set(re.findall(r'\bC\d{2}\b',texts['功能确认清单-已确认.md']))),'requirement_definitions':len(set(re.findall(r'\bFR-\d{3}\b',texts['02_软件需求规格说明书.md']))),'test_case_definitions':len(set(re.findall(r'\bTC-[A-Z]\d{2}\b',texts['13_测试计划与验收用例.md']))),'assets_unchanged':not any(x.startswith('asset') for x in issues),'issues':issues,'status':'PASS' if not issues else 'FAIL','scope':'document structure and references only; no website implementation tests executed'}
(ROOT/'_调研证据/document-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(bool(issues))
