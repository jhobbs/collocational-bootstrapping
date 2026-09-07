# Experiment 2 — CHILDES subject–verb distributions

This pipeline extracts English CHILDES utterances, finds subject–verb pairs with
spaCy, and estimates the Zipf parameter α overall and in eight 12-month age bins.

The [verified cleanup results](output/english_metadata_cleanup/README.md) compare
the original 2021.1 analysis, removal of the German collection alone, and the full
metadata cleanup on both 2021.1 and 2026.1. They include fit artifacts, code
snapshots, and verification records.

## Corrected data selection

The extraction now defaults to the pinned **2026.1** release and requires:

- Collection `Eng-NA` or `Eng-UK` for participants, transcripts, and utterances.
- Participant language exactly `eng`, with the original 25 allowed speaker roles.
- Transcript and utterance language exactly `eng`; multilingual labels are excluded.
- An exact match to a selected speaker ID, corpus ID, and collection ID, and to
  an eligible transcript ID in that same corpus and collection.

This excludes clinical and bilingual collections as well as German collections.
It is a conservative metadata selection: it cannot guarantee that an English
conversation contains no foreign words. Clinical English is outside this run's
explicit collection allowlist.

The historical scripts lost the selected speaker identities by expanding to all
speakers sharing a corpus name and role. That admitted substantial non-English
material, including German corpora. Testing only for “Biling” in a corpus name did
not address that problem. The historical results (overall α = 1.43; 4,739,189
age-eligible utterances) describe that earlier selection, and are **not expected
results for the corrected run**. Existing historical CSVs and analysis artifacts
are preserved; use a new run directory for corrected results.

## Dependencies

Use R 4.x, `childesr` 0.3.0 or newer, and `dplyr` 1.1.0 or newer. The old CRAN
`childesr` 0.2.3 uses the retired MySQL connection route. The verified Redivis
package revisions are:

```r
install.packages(c("remotes", "dplyr"))
remotes::install_github("redivis/redivis-r@3e060333faca9ee39618dd706c99dcf1a32175a7")
remotes::install_github("langcog/childesr@fce77ca62482677165cd3efaabe4f3eac57e6798")
```

The extraction runner reads a Redivis token from `REDIVIS_API_TOKEN`, or from
`~/redivis.token` if that variable is unset. `REDIVIS_TOKEN_FILE` can specify a
different token-file path. Tokens are not written into the run directory.
`R_LIBS_USER` may point to an existing library containing the required packages.

For Python, install the packages in `requirements.txt` and `en_core_web_sm`:

```sh
python -m pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

The verified environment uses spaCy 3.8.13 and English model 3.8.0.

## Run the complete pipeline

From this directory, choose a run directory that does not already exist:

```sh
Rscript run_childes_extraction.R /absolute/path/to/new_run
Rscript audit_childes_selection.R /absolute/path/to/new_run
python verify_childes_export.py /absolute/path/to/new_run
python run_childes_analysis.py /absolute/path/to/new_run --shards 16
python verify_childes_analysis.py /absolute/path/to/new_run
```

The R runner snapshots its source files and executes the three preparation
scripts in order. Every query specifies the database version. Retrieval errors
stop the build instead of silently skipping a corpus. The independent audit uses
server-side metadata joins to count the eligible population, and the verifier
checks every exported row against the saved participant/transcript metadata and
compares every corpus count with that independent query.
The analysis runner requires a passed audit whose CSV hash still matches the
input. The final analysis verifier checks all nine fits and their population
counts, and compares a reproducible sample with the original serial parser.

The Python runner uses the existing `english_ud/parse_sharded_spacy.py` utility
to parse independent, contiguous batches once, then supplies those ordered pairs
to the original two analysis scripts. It preserves zero-pair utterances in the
denominator. It keeps the original spaCy `nsubj` rule, lemmatization, top-100 verb
selection, rank averaging, and α grid search. Tests compare the parallel output
with the original parser, including age boundaries. `--shards 1` uses one worker.

For a literal serial rerun, after extraction change into the new run directory
and execute its snapshots instead:

```sh
python code/analyze_complete_dataset_96mos.py
python code/analyze_age_groups_96mos.py
```

Run the overall analysis first: the age plot reads that run's overall α rather
than labelling every result with the historical value 1.43.

The original three R scripts also remain directly executable from this folder.
Their timestamped speaker files include `db_version`; old files without version
metadata are rejected. The runner is preferred because it starts with an empty
output directory. `CHILDES_DB_VERSION` can explicitly select another supported
release for a separate sensitivity run; it defaults to `2026.1`.

## Output files

Within the chosen run directory:

- `data/childes_utterances.csv`: full export, including the original 13 fields
  plus collection identity, participant/transcript/utterance language, release,
  and source utterance ID.
- `rdata/speakers/` and `rdata/utterances/`: timestamped intermediate exports.
- `audit/`: selection policy, source metadata, retrieval counts, independent SQL
  and counts, full-export verification, and analysis provenance.
- `code/`: executable source snapshots.
- `parser_input/`: age-filtered utterances with stable source CSV row indices.
- `parsed/`: extracted pairs with utterance identity and parser metadata. The
  reused utility names its pair file `english_ud_subject_verb_pairs.csv`, but
  these rows are spaCy parses, as its metadata explicitly records.
- `output/complete_dataset_96mos/`: overall pairs, ranked distributions, MSE
  search, observed/predicted distributions, and summary.
- `output/age_groups_complete_96mos/`: the same outputs for each age bin and
  the α-by-age plot.

## Analysis conventions retained

The overall loader retains non-missing text and age with age ≤ 96 months. Each
age bin uses a lower-inclusive, upper-exclusive interval, so an utterance at
exactly 96 months contributes to the overall fit but not the final age bin.
The existing ASCII verb filter is retained for comparability; it is not a
language detector. Comparing the corrected 2026.1 run to the historical 2021.1
results combines a database-version change with the selection fix.

## Verification

```sh
Rscript tests/test_childes_selection.R
python -m unittest discover -s tests -p 'test_*.py'
```

The R integration test substitutes only the remote data calls and runs the real
export script. It catches the historical unselected-speaker leak, English-labelled
German metadata, multilingual/missing language metadata, and 64-bit ID handling.
