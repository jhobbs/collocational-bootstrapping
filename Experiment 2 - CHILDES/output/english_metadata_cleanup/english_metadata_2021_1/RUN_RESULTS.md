# CHILDES 2021.1: separate German removal, broader cleanup, and release changes

Both requested comparisons completed and passed verification:

1. The full metadata fixes were rerun on the original **2021.1** database release.
2. A separate control removed **only the German collection** from the original
   analysis population, with the same parser, age rules, and fitting method.

The control freshly reproduced **all 2,802,071 historical subject–verb pairs
exactly, in the same order**. This checks that the controlled contrast is not
being driven by a parser or model change.

## Results

| Age, months | Original 2021.1 α | Remove German collection, 2021.1 α | Full cleanup, 2021.1 α | Full cleanup, 2026.1 α |
|---|---:|---:|---:|---:|
| overall | 1.43 | 1.45 | 1.45 | 1.45 |
| 0-12 | 1.46 | 1.46 | 1.46 | 1.42 |
| 12-24 | 1.40 | 1.40 | 1.38 | 1.39 |
| 24-36 | 1.44 | 1.45 | 1.46 | 1.46 |
| 36-48 | 1.38 | 1.38 | 1.41 | 1.40 |
| 48-60 | 1.37 | 1.38 | 1.40 | 1.40 |
| 60-72 | 1.28 | 1.34 | 1.40 | 1.45 |
| 72-84 | 1.23 | 1.35 | 1.35 | 1.53 |
| 84-96 | 1.25 | 1.39 | 1.39 | 1.51 |

[Comparison plot](same_version_comparison.png) ·
[All counts and estimates](same_version_comparison.csv) ·
[Alpha comparison CSV](alpha_comparison.csv)

**German-corpus contamination materially lowers the historical older-bin
estimates.** Removing that collection raises α from 1.23 to 1.35 at 72–84 months
and from 1.25 to 1.39 at 84–96 months. The early-bin value remains 1.46 at 0–12
months, so this removal reduces the older-bin decline rather than eliminating
all age differences.

The full metadata cleanup on 2021.1 produces the same rounded α values in those
two oldest bins. That equality is at the original 0.01 fitting-grid resolution;
the populations and complete frequency distributions differ.

With the full cleanup held fixed, switching from 2021.1 to 2026.1 changes the
oldest-bin estimates from **1.35/1.39 to 1.53/1.51**. The larger increase observed
in the newer release therefore cannot be attributed to German removal alone.
The selected older-bin populations also change: 16,944/34,476 utterances on
2021.1 versus 44,490/58,895 on 2026.1. This comparison does not identify which
individual source or metadata updates within the newer release cause that shift.

## Population and scope

| Condition | Analysis utterances | Subject–verb pairs |
|---|---:|---:|
| Original 2021.1 | 4,739,189 | 2,802,071 |
| Remove German collection, 2021.1 | 4,590,557 | 2,757,541 |
| Full metadata cleanup, 2021.1 | 3,003,214 | 2,140,505 |
| Full metadata cleanup, 2026.1 | 3,013,603 | 2,143,372 |

The single-exclusion control removes 148,632 utterances whose archived collection
is `German`, including English-labelled German transcripts. Every other original
row is retained. This isolates the known bulk German-collection input, not every
possible German word. For example, 822 age-eligible utterances from English
Gleason transcripts labelled `eng deu` remain in this control. Those labels mark
multilingual transcripts, not 822 independently classified German sentences.

The full cleanup allows only `Eng-NA` and `Eng-UK`, requires exact `eng` labels
for participant/transcript/utterance metadata, preserves the original 25 roles,
and joins on the selected speaker, corpus, collection, and transcript identities.
It therefore also removes other non-English, multilingual, and clinical data.

The 2021.1 corrected raw CSV contains **3,320,394 utterances**: 1,902,548 from
Eng-NA and 1,417,846 from Eng-UK. The original loader excludes 61,741 rows with
missing text, 228,134 additional rows with missing age, and 27,305 additional
rows over 96 months, leaving 3,003,214 analysis utterances.

## Verification

- The independent 2021.1 server-side query and export agree for all 63 corpora.
- Every raw export row passes the saved participant/transcript metadata checks.
- All 3,320,394 corrected rows match rows in the original CSV. Every original
  non-age field matches exactly. There are 16,280 tiny age-rounding differences,
  with maximum 9.95e-14 months.
- The corrected analysis's eight age counts and all pair counts agree with its
  parser population. All nine fitted values match the saved MSE minima.
- A deterministic sample of 1,212 corrected utterances reproduces 841 pairs
  exactly with the original serial parser.
- The direct German-collection control's excluded age-bin counts match the
  independent historical database query. Every output pair matches its exact
  source-row selection in both control cohorts, and all 18 fits pass checks.
- The fresh control baseline matches every saved historical pair, not just the
  summary α values.
- Both runs use spaCy 3.8.13 and English model 3.8.0, with the original extraction
  and fitting code. Parallel parsing preserves source order.

## Files

This full-cleanup run is under:

`/home/jason/talkbank-data/2026-09-07/english_metadata_2021_1/`

- Corrected CSV: `data/childes_utterances.csv` in the local run directory above.
- [Overall summary](output/complete_dataset_96mos/summary_96mos.txt).
- [Age-bin summary](output/age_groups_complete_96mos/summary_96mos.csv).
- [Export verification](audit/export_verification.json).
- [Original-row subset verification](audit/historical_subset_verification.json).
- [Analysis verification](audit/analysis_verification.json).
- `audit/original_row_mapping.csv`: corrected-to-original CSV row identities.
- `code/`: extraction, parsing, fitting, verification, and comparison source.
- `logs/`: complete execution logs in the local run directory above.

The direct control is under:

`/home/jason/talkbank-data/2026-09-07/german_collection_control_2021_1/`

Its [report](../german_collection_control_2021_1/RUN_RESULTS.md),
[verification](../german_collection_control_2021_1/audit/control_verification.json),
`code/run_german_control.py`, and `cohorts/` outputs document the single-exclusion
experiment. Original data and prior results were preserved.

## Reproduce the full cleanup on 2021.1

From the repository's `Experiment 2 - CHILDES` directory, with the existing R and
Python environments configured, use a new output directory:

```sh
CHILDES_DB_VERSION=2021.1 Rscript run_childes_extraction.R /absolute/path/to/new_run
Rscript audit_childes_selection.R /absolute/path/to/new_run
python verify_childes_export.py /absolute/path/to/new_run
python run_childes_analysis.py /absolute/path/to/new_run --shards 16
python verify_childes_analysis.py /absolute/path/to/new_run
```

The database version is selected by the first command and saved in the run's
policy file; the independent audit reads it from that file. On this container,
`R_LIBS_USER` points to
`/home/jason/talkbank-data/2026-09-07/documented_csv_rebuild_211155/redivis_library`.
Use `/home/jason/collocational-bootstrapping/Experiment 2 - CHILDES/.venv/bin/python`
for the Python commands. The token remains in the existing private token file.
