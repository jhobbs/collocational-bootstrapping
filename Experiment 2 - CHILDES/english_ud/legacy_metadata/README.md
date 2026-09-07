# Historical metadata audit

These scripts preserve the exact audit used for the 2026-09-07 run. The historical
MOR annotations are never used to extract UD pairs. They supply metadata only.

First download the official archive to `<data-root>/legacy_mor/0-Eng-NA-MOR.zip`:
https://talkbank.org/childes/access/Eng-NA/0-Eng-NA-MOR.zip

With current Eng-NA per-corpus ZIPs already in `<data-root>/Eng-NA/`, run:

```sh
python legacy_metadata/acquire_age_metadata.py <data-root>/legacy_mor
python legacy_metadata/audit_age_metadata.py <data-root>
python legacy_metadata/match_pid_metadata.py <data-root>
```

The generated `exact_pid_age_recovery_candidates.json` is evidence for
`recover_age_metadata.py`. Recovery checks the exact PID, current member checksum,
unchanged speaker roles and sequence, and text agreement. Filename identity alone
does not establish recording/transcript identity. In particular, some current
Hall files replace the former human transcripts with unchecked ASR and anonymous
speaker codes.

The scripts read ZIP members without unpacking them. Historical non-UTF8 decoding
replacements are counted; original archive bytes and hashes remain authoritative.
The convenience `age_months_chat30` column is not the original CHILDES-db day-based
month conversion. Original ages are corroborated separately against the original
utterance CSV.
