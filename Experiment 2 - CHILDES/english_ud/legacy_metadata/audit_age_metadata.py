from pathlib import Path
import zipfile,re,csv,json,collections
root=Path(__import__('sys').argv[1]).resolve();legacy=root/'legacy_mor'
rows=list(csv.DictReader((legacy/'eng_na_mor_transcript_age_metadata.csv').open()))
meta={r['transcript']:r for r in rows}
def header_info(raw):
 text=raw.decode('utf-8-sig',errors='replace');head=text.split('\n*',1)[0];head=re.sub(r'\n[ \t]+',' ',head)
 ids=[]
 for line in re.findall(r'^@ID:\s*(.*)$',head,re.M):
  cols=line.split('|')
  if len(cols)>=8:ids.append({'code':cols[2],'language':cols[0],'corpus':cols[1],'age_raw':cols[3],'sex':cols[4],'role':cols[7],'raw_id':line})
 def tier(key):
  m=re.search(r'^@'+key+r':\s*(.*)$',head,re.M);return m[1].strip() if m else ''
 return {'pid':tier('PID'),'participants_tier':tier('Participants'),'ids':ids,'comments':re.findall(r'^@Comment:\s*(.*)$',head,re.M),'n_utterances':len(re.findall(r'^\*',text,re.M))}
with zipfile.ZipFile(legacy/'0-Eng-NA-MOR.zip') as z:
 for member in z.namelist():
  key=member.removeprefix('Eng-NA/')
  if key in meta:meta[key].update(header_info(z.read(member)))
(legacy/'eng_na_mor_header_metadata.json').write_text(json.dumps({'source_url':'https://talkbank.org/childes/access/Eng-NA/0-Eng-NA-MOR.zip','transcripts':meta},indent=2)+'\n')
comparisons=[]
for path in sorted((root/'Eng-NA').glob('*.zip')):
 corpus=path.stem
 with zipfile.ZipFile(path) as z:
  for member in z.namelist():
   if not member.endswith('.cha') or member.startswith('__MACOSX/'):continue
   key=member.removeprefix('Eng-NA/')
   if not key.startswith(corpus+'/'):key=corpus+'/'+key
   h=header_info(z.read(member));old=meta.get(key)
   oldroles={p['code']:p['role'] for p in old['ids']} if old else {}
   roles={p['code']:p['role'] for p in h['ids']}
   chi=[p for p in h['ids'] if p['code']=='CHI'];targets=[p for p in h['ids'] if p['role']=='Target_Child']
   target=chi[0] if len(chi)==1 else targets[0] if len(targets)==1 else None
   anonymous=bool(roles) and all(role=='Participant' for role in roles.values())
   row={'transcript':key,'current_pid':h['pid'],'legacy_available':old is not None,'legacy_pid':old['pid'] if old else '', 'pid_same':bool(old and old['pid']==h['pid']),'current_age_raw':target['age_raw'] if target else '', 'legacy_age_raw':old['age_raw'] if old else '', 'current_n_utterances':h['n_utterances'],'legacy_n_utterances':old['n_utterances'] if old else '', 'anonymous_participants_only':anonymous,'asr_comment':any('asr' in c.lower() for c in h['comments']), 'roles_identical':bool(old and roles==oldroles),'current_roles':json.dumps(roles,sort_keys=True),'legacy_roles':json.dumps(oldroles,sort_keys=True)}
   comparisons.append(row)
with (legacy/'legacy_current_metadata_availability.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(comparisons[0]));w.writeheader();w.writerows(comparisons)
missing=[r for r in comparisons if not r['current_age_raw']]
recoverable=[r for r in missing if r['legacy_age_raw']]
summary={'n_current_transcripts':len(comparisons),'n_legacy_path_matches':sum(r['legacy_available'] for r in comparisons),'n_same_pid':sum(r['pid_same'] for r in comparisons),'n_current_age_missing':len(missing),'n_current_age_missing_with_legacy_age':len(recoverable),'legacy_age_candidates_by_corpus':dict(collections.Counter(r['transcript'].split('/')[0] for r in recoverable)),'n_age_candidates_anonymous_participants':sum(r['anonymous_participants_only'] for r in recoverable),'n_age_candidates_asr_comment':sum(r['asr_comment'] for r in recoverable),'n_age_candidates_identical_roles':sum(r['roles_identical'] for r in recoverable),'warning':'Path match and historical age do not establish stable speaker identity in replacement ASR transcripts. Do not add anonymous Participant speech to CDS merely by restoring an age.'}
(legacy/'legacy_current_metadata_audit.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
