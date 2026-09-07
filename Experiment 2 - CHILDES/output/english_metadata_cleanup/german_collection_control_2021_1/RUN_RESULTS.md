# German-collection removal control on CHILDES 2021.1

The original analysis population was reparsed using the same spaCy version and
model as the corrected runs. It reproduced **all 2,802,071 saved historical
subject–verb pairs exactly, including their order**. Both control conditions use
those same parses and the original fitting routines.

The only selection difference is excluding rows whose archived transcript
metadata says `collection_name == "German"`. This removes **148,632 of 4,739,189
analysis utterances** and leaves **4,590,557 utterances / 2,757,541 pairs**.
The excluded rows belong to the known German collection sources, including
English-labelled German transcripts. Text, age rules, speaker selection in the
remaining population, parser, and fitting method are held constant.

Overall α changes from **1.43 to 1.45**.

| Age, months | Original α | Without German collection α | Retained utterances |
|---|---:|---:|---:|
| 0-12 | 1.46 | 1.46 | 182,023 |
| 12-24 | 1.40 | 1.40 | 669,921 |
| 24-36 | 1.44 | 1.45 | 1,858,350 |
| 36-48 | 1.38 | 1.38 | 895,907 |
| 48-60 | 1.37 | 1.38 | 605,965 |
| 60-72 | 1.28 | 1.34 | 219,277 |
| 72-84 | 1.23 | 1.35 | 83,883 |
| 84-96 | 1.25 | 1.39 | 75,231 |

This control confirms that the known German-corpus contamination contributes to
the older-bin decline. It does not remove every possible German word or all
multilingual studies: English-collection transcripts labelled `eng deu` remain
in this control, as do all other original non-German collections. See
`audit/german_labels_outside_german_collection.json` for their metadata counts.

All excluded age-bin counts match the independent 2021.1 database query. Every
saved overall and age-bin pair output matches the exact retained source rows;
all 18 fits pass their MSE-minimum checks. See
[audit/control_verification.json](audit/control_verification.json).

The source is the original historical CSV, already reproduced from the official
2021.1 Redivis archive in the documented rebuild. Input/code hashes are retained
in [audit/run.json](audit/run.json). Original CSV row indices identify every
utterance in `audit/cohort_membership.csv` in the full local run at
`/home/jason/talkbank-data/2026-09-07/german_collection_control_2021_1/`.

- `cohorts/original/output/`: fresh baseline fits.
- `cohorts/without_german_collection/output/`: German-collection exclusion fits.
- `parsed/`: the shared original-population parses.
- `code/run_german_control.py`: runner, with parser and fit-source snapshots nearby.
- `code/verify_german_control.py`: independent control verification.

The separate full-cleanup run on the same release is at
`../english_metadata_2021_1/`; its combined comparison report distinguishes this
single exclusion from the complete metadata fixes.
