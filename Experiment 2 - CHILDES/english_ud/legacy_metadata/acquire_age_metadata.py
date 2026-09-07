from pathlib import Path
import zipfile,re,csv,json,hashlib,collections,urllib.request
root=Path(__import__('sys').argv[1]).resolve()
archive=root/'0-Eng-NA-MOR.zip'
rows=[]; participant_rows=[]
with zipfile.ZipFile(archive) as z:
 for member in sorted(z.namelist()):
  if not member.lower().endswith('.cha') or '__MACOSX' in member:continue
  raw=z.read(member);text=raw.decode('utf-8-sig',errors='replace');head=text.split('\n*',1)[0]
  head=re.sub(r'\n[ \t]+',' ',head)
  ids=[]
  for line in re.findall(r'^@ID:\s*(.*)$',head,re.M):
   cols=line.split('|')
   if len(cols)>=8:ids.append({'language':cols[0],'corpus':cols[1],'code':cols[2],'age_raw':cols[3],'sex':cols[4],'role':cols[7]})
  pid=re.search(r'^@PID:\s*(.*)$',head,re.M)
  base={'collection':'Eng-NA','transcript':member.removeprefix('Eng-NA/'),'archive_member':member,'pid':pid[1].strip() if pid else '', 'chat_sha256':hashlib.sha256(raw).hexdigest(),'header_decode_replacements':head.count('\ufffd')}
  for p in ids:participant_rows.append({**base,**p})
  children=[p for p in ids if p['role']=='Target_Child']
  chi=[p for p in ids if p['code']=='CHI']
  chosen=chi[0] if len(chi)==1 else children[0] if len(children)==1 else None
  rawage=chosen['age_raw'] if chosen else ''
  age=re.fullmatch(r'(\d+);(\d*)\.(\d*)',rawage)
  years,months,days=(int(x or 0) for x in age.groups()) if age else ('','','')
  rows.append({**base,'target_code':chosen['code'] if chosen else '', 'target_role':chosen['role'] if chosen else '', 'age_raw':rawage,'years':years,'months':months,'days':days,'age_months_chat30':years*12+months+days/30 if age else '', 'selection':'CHI' if len(chi)==1 else 'unique_target' if len(children)==1 else 'unresolved','all_target_codes':';'.join(p['code'] for p in children),'n_main_tier_utterances':len(re.findall(r'^\*',text,re.M))})
for name,data in [('eng_na_mor_transcript_age_metadata.csv',rows),('eng_na_mor_participant_metadata.csv',participant_rows)]:
 with (root/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
summary={'source_archive':str(archive),'source_url':'https://talkbank.org/childes/access/Eng-NA/0-Eng-NA-MOR.zip','n_transcripts':len(rows),'n_with_raw_age':sum(bool(r['age_raw']) for r in rows),'n_with_parsed_age':sum(r['years']!='' for r in rows),'age_units_note':'Raw CHAT age preserved. age_months_chat30 is years*12+months+days/30 for convenient comparison only; use original local CHILDES-db ages to reproduce its conversion exactly.','selection_note':'Prefer CHI code; otherwise unique Target_Child; all ID rows preserved in participant metadata.','unparsed_ages':dict(collections.Counter(r['age_raw'] for r in rows if r['age_raw'] and r['years']==''))}
(root/'age_metadata_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
