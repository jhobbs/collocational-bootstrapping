from pathlib import Path
import zipfile,re,csv,json,collections,hashlib
root=Path(__import__('sys').argv[1]).resolve();d=root/'legacy_mor'
old=json.loads((d/'eng_na_mor_header_metadata.json').read_text())['transcripts']
current=list(csv.DictReader((d/'legacy_current_metadata_availability.csv').open()))
by_pid=collections.defaultdict(list);by_base=collections.defaultdict(list)
for path,h in old.items():
 if h['pid']:
  by_pid[h['pid']].append(path);by_base[h['pid'].rsplit('-',1)[0]].append(path)
def utterances(raw):
 s=raw.decode('utf-8-sig',errors='replace');s=re.sub(r'\n[ \t]+',' ',s);s=re.sub('\x15[^\x15]*\x15','',s)
 return [(m[1],' '.join(m[2].split())) for m in re.finditer(r'^\*([^:]+):\s*(.*)$',s,re.M)]
rows=[];detail={};counts=collections.Counter();candidate_corpora=collections.Counter()
with zipfile.ZipFile(d/'0-Eng-NA-MOR.zip') as oldzip:
 for corpus,group in __import__('itertools').groupby(current,key=lambda r:r['transcript'].split('/')[0]):
  with zipfile.ZipFile(root/'Eng-NA'/f'{corpus}.zip') as z:
   for r in group:
    matches=by_pid.get(r['current_pid'],[]);bases=by_base.get(r['current_pid'].rsplit('-',1)[0],[]) if r['current_pid'] else []
    chosen=matches[0] if len(matches)==1 else None;h=old[chosen] if chosen else None
    oldroles={p['code']:p['role'] for p in h['ids']} if h else {};roles=json.loads(r['current_roles'])
    roles_preserved=bool(h and roles and all(oldroles.get(k)==v for k,v in roles.items()))
    age_available=bool(h and h['age_raw']);missing=not r['current_age_raw'];is_asr=r['asr_comment']=='True';anon=r['anonymous_participants_only']=='True'
    row={'transcript':r['transcript'],'current_pid':r['current_pid'],'current_age_raw':r['current_age_raw'],'exact_pid_matches':len(matches),'pid_base_matches':len(bases),'legacy_transcript':chosen or '', 'legacy_pid':h['pid'] if h else '', 'legacy_age_raw':h['age_raw'] if h else '', 'same_path':chosen==r['transcript'],'roles_preserved_for_all_current_codes':roles_preserved,'asr_comment':is_asr,'anonymous_participants_only':anon,'legacy_n_utterances':h['n_utterances'] if h else '', 'current_n_utterances':r['current_n_utterances'],'normalized_text_position_agreement':'','underscore_normalized_position_agreement':'','same_speaker_sequence':'','age_recovery_candidate':bool(missing and age_available and roles_preserved and not is_asr and not anon)}
    counts['n_current']+=1;counts['n_unique_exact_pid_match']+=len(matches)==1;counts['n_unique_base_pid_match']+=len(bases)==1;counts['n_same_pid_same_path']+=bool(chosen==r['transcript']);counts['n_current_missing_age']+=missing
    if missing and age_available:
     counts['n_missing_age_exact_pid_legacy_age']+=1;counts['n_missing_age_exact_pid_role_preserved']+=roles_preserved
     counts['n_missing_age_exact_pid_asr']+=is_asr;counts['n_missing_age_exact_pid_anonymous']+=anon
     newmember=r['transcript'].removeprefix(corpus+'/')
     if newmember not in z.namelist():newmember=r['transcript']
     raw=z.read(newmember);a=utterances(oldzip.read(h['archive_member']));b=utterances(raw)
     denom=max(len(a),len(b));row['same_speaker_sequence']=[x[0] for x in a]==[x[0] for x in b]
     row['normalized_text_position_agreement']=sum(x==y for x,y in zip(a,b))/denom if denom else 1
     def norm(x):return (x[0],' '.join(x[1].replace('_',' ').split()))
     row['underscore_normalized_position_agreement']=sum(norm(x)==norm(y) for x,y in zip(a,b))/denom if denom else 1
     detail[r['transcript']]={**row,'legacy_header':h,'current_chat_sha256':hashlib.sha256(raw).hexdigest(),'current_raw_id_lines':re.findall(r'^@ID:\s*(.*)$',raw.decode('utf-8-sig',errors='replace').split('\n*',1)[0],re.M)}
    if row['age_recovery_candidate']:
     counts['n_age_recovery_candidates']+=1;candidate_corpora[corpus]+=1
     counts['n_candidates_same_speaker_sequence']+=row['same_speaker_sequence'] is True
     counts['n_candidates_body_agreement_ge_0_9']+=row['underscore_normalized_position_agreement']>=.9
    rows.append(row)
with (d/'current_legacy_pid_matches.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
summary={'counts':dict(counts),'age_recovery_candidate_corpora':dict(candidate_corpora),'pid_match_rule':'Exact full @PID, preserving c/a namespace and revision suffix. Base-only match counts provided for audit; never selected automatically.','candidate_rule':'Missing current target age; unique full PID legacy match with raw age; all current code-to-role mappings equal old mappings; no ASR comment; not anonymous-only participants. Text and speaker-sequence agreement are separate diagnostics, not presumed by PID.','source_url':'https://talkbank.org/childes/access/Eng-NA/0-Eng-NA-MOR.zip'}
(d/'current_legacy_pid_match_summary.json').write_text(json.dumps(summary,indent=2)+'\n');(d/'exact_pid_age_recovery_candidates.json').write_text(json.dumps({'summary':summary,'transcripts':detail},indent=2)+'\n')
print(json.dumps(summary,indent=2))
