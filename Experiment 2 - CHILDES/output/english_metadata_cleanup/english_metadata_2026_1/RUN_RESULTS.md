# Corrected English CHILDES run — 2026.1

The extraction and both analyses completed successfully. All **4,427,473 exported
utterances** pass the collection, participant, transcript, and utterance metadata
checks, and every corpus count matches an independent server-side SQL query.

After the original missing-text and age filters, the analysis contains
**3,013,603 utterances** and **2,143,372 subject–verb pairs**. The overall fitted
Zipf parameter is **α = 1.45** (historical analysis: 1.43).

## Selection and implementation

- Database release: **2026.1**, official Redivis archive **v1.4**.
- Allowed collections: **Eng-NA and Eng-UK**. Clinical and bilingual collections
  are outside this run's scope.
- Participant, transcript, and utterance language must each be exactly `eng`.
- The original 25-role allowlist is retained. There are 3,979 selected speakers
  across 73 corpora, from 6,075 participant records in the two collections.
- Every exported utterance must match a selected speaker ID and an eligible
  transcript ID in the same corpus and collection. IDs remain exact across the
  Redivis 64-bit representation and CSV intermediate files.
- The corpus queries retrieved 4,428,070 English-labelled rows. Identity and
  transcript checks removed 597 rows from EHS, leaving 4,427,473.
- Export counts: Eng-NA **2,717,989**; Eng-UK **1,709,484**.

The corrected scripts are integrated into the existing checkout at
`/home/jason/collocational-bootstrapping/Experiment 2 - CHILDES/`.
The historical CSV's SHA-256 was rechecked and is unchanged. Its existing data
symlink and the historical analysis outputs were preserved.

The existing parallel spaCy utility parsed 16 contiguous batches and merged them
in source order. Its extraction rule is identical to the original `nsubj` rule
with VERB/AUX heads and lowercase lemmas. Both original analysis routines then
consumed those freshly parsed pairs, selected by original CSV row index. No
sampling was used for the fitted results. The age plot now reads this run's
overall α instead of displaying a hard-coded historical 1.43.

## Results

| Age, months | Historical α | Corrected α | Corrected utterances | Corrected pairs |
|---|---:|---:|---:|---:|
| 0-12 | 1.46 | 1.42 | 166,385 | 118,929 |
| 12-24 | 1.40 | 1.39 | 500,683 | 310,151 |
| 24-36 | 1.44 | 1.46 | 1,399,876 | 997,455 |
| 36-48 | 1.38 | 1.40 | 512,741 | 395,375 |
| 48-60 | 1.37 | 1.40 | 211,915 | 166,322 |
| 60-72 | 1.28 | 1.45 | 118,618 | 86,211 |
| 72-84 | 1.23 | 1.53 | 44,490 | 30,417 |
| 84-96 | 1.25 | 1.51 | 58,895 | 38,512 |

[Comparison plot](comparison_with_historical.png) ·
[Comparison CSV](comparison_with_historical.csv)

The historical decline in the oldest bins is absent in this corrected run.
However, this comparison changes **both the database release and the population
selection**. It does not isolate the effect of German removal. The subsequent
[same-release comparison](../english_metadata_2021_1/RUN_RESULTS.md) supplies
that control and separates it from the full metadata cleanup.

The 20 diagnostic terms checked in the two oldest `be` subject distributions
(`und`, `du`, `das`, `nicht`, `ich`, `wir`, `gucken`, `wörter`, `quatsch`,
`natürlich`, `machen`, `zeigen`, `mal`, `ja`, `dir`, `ist`, `auch`, `denn`, `wie`,
`bisschen`) have **zero occurrences** there in the corrected results. Their
historical combined frequencies were 573 at 72–84 months and 318 at 84–96 months.
These are term counts, not an automated classification of sentence language.
Metadata selection cannot guarantee the absence of every foreign word in an
English conversation.

## Age and text exclusions

- Missing text: 88,458 rows.
- Missing age after retaining text: 1,257,219 rows.
- Age greater than 96 months after retaining text: 68,193 rows.
- Remaining analysis population: 3,013,603 rows.
- No retained rows have negative ages or age exactly 96, so the eight bin counts
  sum to the overall population in this run.

The missing-age exclusions are those of the original loader applied to the
2026.1 database metadata; this run does not infer or recover missing ages.

## Verification

- Independent SQL and local export counts match for every corpus.
- Every export row was checked against the saved participant and transcript
  metadata; zero violations or duplicate/out-of-order source identities.
- All eight age-bin utterance and pair counts match the parser population.
- All nine fitted α values match the minima in the saved MSE searches; observed
  and predicted distributions normalize to one.
- A deterministic sample of **1,212 real utterances**, spanning all age bins,
  reproduces **859 pairs exactly** with the original serial parser.
- R metadata/export regression tests and all three Python regression tests pass.
  The existing 93-test suite also passed before the changes; its modules were
  reused unchanged.
- Extraction, independent audit, export verification, parallel analysis, and
  analysis verification all exited successfully. The initial ID-type failure is
  retained separately in `../english_metadata_2026_1_initial_id_failure/`.

## Files and rerun instructions

- Corrected CSV: `data/childes_utterances.csv`, about 841 MB, in the full local run
  at `/home/jason/talkbank-data/2026-09-07/english_metadata_2026_1/`.
- [Overall summary](output/complete_dataset_96mos/summary_96mos.txt).
- [Age-bin summary](output/age_groups_complete_96mos/summary_96mos.csv).
- [Export verification](audit/export_verification.json).
- [Analysis verification](audit/analysis_verification.json).
- [German-term comparison](audit/german_be_subject_comparison.csv).
- [Run provenance](audit/run_provenance.json), [analysis code hashes](audit/analysis_run.json),
  and [maintained-code integration hashes](audit/reviewed_code_integration.json).
- `code/`: sources used for the extraction and analysis, plus verification and
  comparison scripts. The maintained code includes additional review safeguards
  described in the provenance file; they do not change this run's results.
- `logs/`: extraction, audit, analysis, and test logs in the full local run.

Follow the updated repository README to rerun into a **new** directory:

```sh
Rscript run_childes_extraction.R /absolute/path/to/new_run
Rscript audit_childes_selection.R /absolute/path/to/new_run
python verify_childes_export.py /absolute/path/to/new_run
python run_childes_analysis.py /absolute/path/to/new_run --shards 16
python verify_childes_analysis.py /absolute/path/to/new_run
```

On this container, use the existing Python environment at
`/home/jason/collocational-bootstrapping/Experiment 2 - CHILDES/.venv/bin/python`
and set `R_LIBS_USER` to
`/home/jason/talkbank-data/2026-09-07/documented_csv_rebuild_211155/redivis_library`.
The extraction runner reads the existing Redivis token file without logging it.
