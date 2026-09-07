# English CHILDES reproduction with TalkBank UD and PyLangAcq

This experiment implements `/home/jason/reproduce_english_childes_with_pylangacq.md`.
It preserves the original spaCy scripts and saved outputs, replaces linguistic
extraction with existing TalkBank UD annotations, and checks the numerical fitter
against all nine original saved pair datasets. The numerical checks pass.

The latest follow-up is in [COPULA_RESULTS.md](COPULA_RESULTS.md): adding UD
copulas and excluding `be` across both populations. Neither restores the
paper's age decline in the larger earlier TalkBank population; the matched
subset retains a descriptive decline. [MATCHED_RESULTS.md](MATCHED_RESULTS.md)
documents the same 2,728,212 matched utterances with historical ages held fixed.
[RESULTS.md](RESULTS.md) preserves the earlier live-download comparison and its
different corpus coverage. Numerical agreement of the fitter alone does not
establish reproduction of the scientific age trend.

## Environment

Tested with Python 3.14, PyLangAcq 0.23.0 (rustling 0.9.0), pandas 3.0.5,
NumPy 2.5.3 and matplotlib 3.11.1. The matched-text spaCy analyses use
spaCy 3.8.13 and `en_core_web_sm` 3.8.0. Exact installed dependencies are in
`requirements.lock.txt`.

Run these commands from this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pytest tests -q
```

## Data and scope

The 2026-09-07 acquisition is outside Git at
`/home/jason/talkbank-data/2026-09-07/`. Each archive has a SHA256, download date,
source URL and corpus identifier. Current downloads comprise 98 ZIP archives
across Eng-NA, Eng-UK, Eng-AAE and Clinical-Eng, containing 15,929 CHAT files.
The official `Eng-NA/Lego-protect/Lego` link returns 404 and is recorded as a
download failure. Individual downloaded archive CRCs were checked.

`thesis_eng_na.json` restricts sources to Eng-NA. `repository_english.json`
contains the four current English collections. This latter name describes the
reproduction target; it does **not** assert that its corpus membership equals
the original repository dataset. The original R code obtains corpus-role
combinations from English participants, then calls `get_utterances(corpus, role)`
without a language filter. Its actual saved CSV has 121 corpus names, including
corpora beyond the four current English collections. Scope comparisons retain
this limitation explicitly.

The original raw CSV was located at
`/home/jason/imagining-syntax/childes_full_utterances_20251211_120050.csv`.
It contains 5,147,586 utterance rows and 16,660 transcript IDs. A byte-identical
copy also exists at the repository root, and the original experiment's data
symlink resolves to that copy. An earlier note incorrectly called it missing.
The original scripts and symlink are unchanged. The original CSV carries child
ages but no CHAT filenames or PIDs. See [DATA_PROVENANCE.md](DATA_PROVENANCE.md)
for the export workflow and confirmed differences between historical and live
TalkBank transcripts.

To acquire the current archives again:

```sh
.venv/bin/python download_talkbank.py \
  --email "$TALKBANK_EMAIL" --password-file /path/to/talkbank-password \
  --collections Eng-NA Eng-UK Eng-AAE Clinical-Eng \
  --output /path/to/talkbank-data
```

The password and session cookies are kept in memory, never written to outputs.
An existing archive is reused only when its recorded checksum matches. Failed
downloads are listed explicitly; a partial acquisition returns a nonzero status.

## Preserve and verify the baseline

```sh
.venv/bin/python preserve_baseline.py \
  --source ../output --output ../output/english_spacy_baseline
```

The preservation manifest hashes the original output artifacts. The
baseline directory retains every pair, ranked-subject, rank-average, prediction
and complete MSE-search file. `baseline_summary.csv` standardizes the nine fits;
`baseline_verification.json` records comparisons of the new fitter against the
original saved pairs. Repeating the command checks existing hashes instead of
overwriting the baseline. No pickle is executed.

## Inspect and extract

Each source manifest records `scope` and a `sources` list. Each source requires
`path`, `collection`, `source_url`, `download_date` (null if genuinely unknown),
and `archive_id`; `corpus` and `sha256` are also recorded by the downloader.
Paths may point to ZIP archives, directories or individual CHAT files.

```sh
.venv/bin/python extract_pairs.py --inspect \
  --manifest /path/to/talkbank-data/repository_english.json \
  --output output/annotation_inventory.json

.venv/bin/python extract_pairs.py \
  --manifest /path/to/talkbank-data/repository_english.json \
  --mor-tier %mor --gra-tier %gra --allow-parse-errors \
  --output output/repository_extraction
```

The inspected current archives contain UD in `%mor`/`%gra`; representative
headers identify Stanza 1.11.1 morphotagging in May 2026. Tier arguments must be
explicit. Legacy `SUBJ`/`v` annotations are rejected. This experiment does not
label historical MOR analysis as UD.

Outputs include `english_ud_subject_verb_pairs.csv`, `english_ud_utterances.csv`,
`speaker_roles.csv`, and `metadata.json`. Pair rows retain source identity,
utterance ID, child/speaker metadata, surfaces, raw morphology, lemmas, POS and
dependency indices. Utterance IDs count main tiers only, excluding changeable
headers. Utterances without pairs remain available for denominator counts.

The role filter is exactly the 25 roles in `childes_filter_speakers.R`, with
`CHI` and `Target_Child` excluded; this includes historical choices such as
Sibling, Girl and Participant. Participant language must equal `eng`; corpus
names containing `Biling` are excluded. Roles are not inferred from speaker
code alone. The designated `CHI` supplies age, otherwise a unique Target_Child
participant is used. Unresolved ages are retained in extraction and excluded
from fits.

The strict pair rule is case-insensitive **exact `nsubj`**, with a `VERB` or
`AUX` head. Passive subtypes and copular links through a nonverbal predicate are
not added. Lemmas are lowercased MOR stems before uppercase feature suffixes;
lexical hyphens remain and raw morphology is retained. Expanded clitics can
have empty surfaces in PyLangAcq; they keep their morphology and dependency
indices and are not discarded.

The reader first validates strictly. If CHAT surface notation fails validation,
its documented non-strict mode is used and recorded. Misaligned utterances have
empty tokens and are explicitly excluded; dependency positions/counts are also
checked. `--allow-parse-errors` additionally permits recording and skipping
unparseable whole files. The actual run must be checked for such exclusions in
its metadata, rather than assuming this option lost data.

## Recover and corroborate ages

Age recovery uses metadata from the [official historical Eng-NA MOR archive](https://talkbank.org/childes/access/Eng-NA/0-Eng-NA-MOR.zip),
without using its dependency annotations. See [legacy_metadata/README.md](legacy_metadata/README.md)
for reproducible candidate generation.

Filename-only joins are unsafe. Current NewmanRatner filenames moved, while
some Hall files now contain different, unchecked ASR transcripts with anonymous
speakers. Recovery requires the same full PID, the downloaded member checksum,
preserved current speaker roles and speaker sequence, no ASR/anonymous flag,
and at least 90% normalized positional text agreement. The audit finds 95 PID
candidates, of which 82 satisfy all recovery checks.

The original CSV supplies exact day-based ages. `link_original_transcripts.py`
finds current transcript candidates using at least five distinct long utterances
unique to an original transcript, with a dominant winning match. These content
links alone do not authorize metadata replacement. Age enrichment additionally
requires a corroborating existing CHAT or verified legacy age within 0.1 months,
compatible child names and a unique original ID. Conflicts are reported.

```sh
.venv/bin/python link_original_transcripts.py \
  --original /path/to/childes_full_utterances_20251211_120050.csv \
  --extraction output/repository_extraction \
  --output output/original_transcript_links

.venv/bin/python recover_age_metadata.py \
  --extraction output/repository_extraction \
  --legacy-candidates /path/to/talkbank-data/legacy_mor/exact_pid_age_recovery_candidates.json \
  --original-links output/original_transcript_links \
  --collection Eng-NA --output output/eng_na_recovered_extraction
```

Omit `--collection Eng-NA` for the broader English run. Both pair and utterance
metadata are updated consistently; original CHAT fields, decisions, source
hashes and counts of recovered/adjusted observations are retained. Extraction
and fitting are separate, so changing a verified age does not reparse UD.

PyLangAcq months use `years*12 + months + days/30`. The original `childesr`
converts stored days using `365.2425/12` days per month. Corroborated original
ages therefore preserve the exact historical bin placement; their small
rounding differences matter at bin boundaries.

## Matched historical comparison

The matched follow-up uses the original CSV's exact ages and an occurrence-wise
intersection with independently corroborated CHAT transcripts. It retains
2,728,212 utterances from 6,866 contributing transcripts: 90.4% of usable original
utterances in the selected transcript cohort, and 57.6% of the full historical
analysis. Coverage varies by age; this is a subset of the historical data.

An audit of all 98 official corpus pages identified separately advertised
original Hall and McCune transcript archives. Their manifests are
`/home/jason/talkbank-data/2026-09-07/legacy_mor/Hall-original-source.json` and
`McCune-original-source.json`. Extract them with the same `extract_pairs.py`
command and link each with `link_original_transcripts.py`. The completed outputs
are `output/hall_original_extraction`, `output/hall_original_links`,
`output/mccune_original_extraction`, and `output/mccune_original_links`.
The later source arguments below replace earlier versions of the same corpus.

```sh
.venv/bin/python match_historical_data.py \
  --original /home/jason/imagining-syntax/childes_full_utterances_20251211_120050.csv \
  --extraction output/repository_recovered_extraction \
  --links output/original_transcript_links \
  --extraction output/hall_original_extraction \
  --links output/hall_original_links \
  --extraction output/mccune_original_extraction \
  --links output/mccune_original_links \
  --output output/matched_historical_data

.venv/bin/python parse_sharded_spacy.py \
  --utterances output/matched_historical_data/english_ud_utterances.csv \
  --metadata output/matched_historical_data/metadata.json \
  --text-column text --shards 8 --output output/matched_spacy_chat

.venv/bin/python parse_sharded_spacy.py \
  --utterances output/matched_historical_data/english_ud_utterances.csv \
  --metadata output/matched_historical_data/metadata.json \
  --text-column original_text --shards 8 --output output/matched_spacy_original

.venv/bin/python fit_matched_comparison.py \
  --matched output/matched_historical_data \
  --spacy-chat output/matched_spacy_chat \
  --spacy-original output/matched_spacy_original \
  --baseline ../output/english_spacy_baseline \
  --output output/matched_comparison
```

Use new output directories to repeat a run; existing results are protected from
overwriting. `parse_matched_spacy.py` also supports a direct single-process run
with `--n-process 1`. The sharded wrapper parses independent contiguous batches
with one process each and merges in input order. Both spaCy arms retain the same
matched utterance table, including zero-pair utterances.

The three arms are existing UD annotations, spaCy on the same CHAT surface text,
and spaCy on the matched original CSV text. The third arm measures the effect of
the historical text representation, which often lacks CHAT punctuation. Matching
normalization is used only to establish correspondence; each parser receives
its preserved source text. Each arm and age bin reselects its own top 100 verbs,
as the original analysis does. No token-index alignment between parsers is
implied by this comparison.

## Copula and `be` diagnostics

These commands preserve the exact earlier age-eligible population, parse its
CHAT text with the same spaCy model used on the matched subset, augment both
populations with explicit UD subject–copula links, and fit both methods with
and without verb lemma `be`. The original strict pairs remain unchanged.
The larger population retains missing-tier and zero-pair utterances; see the
annotation-availability counts and results in [COPULA_RESULTS.md](COPULA_RESULTS.md).

```sh
.venv/bin/python prepare_full_population.py \
  --source output/repository_recovered_extraction \
  --output output/copula_full_population

.venv/bin/python parse_sharded_spacy.py \
  --utterances output/copula_full_population/english_ud_utterances.csv \
  --metadata output/copula_full_population/metadata.json \
  --text-column text --shards 12 --output output/copula_full_spacy_chat

.venv/bin/python augment_ud_copulas.py \
  --population output/matched_historical_data \
  --output output/copula_matched_ud

.venv/bin/python augment_ud_copulas.py \
  --population output/copula_full_population \
  --output output/copula_full_ud

.venv/bin/python fit_copula_diagnostics.py \
  --population output/matched_historical_data \
  --augmented output/copula_matched_ud \
  --spacy output/matched_spacy_chat \
  --spacy-original output/matched_spacy_original \
  --dataset matched --workers 4 --output output/copula_matched_fits

.venv/bin/python fit_copula_diagnostics.py \
  --population output/copula_full_population \
  --augmented output/copula_full_ud \
  --spacy output/copula_full_spacy_chat \
  --dataset earlier_talkbank --workers 4 --output output/copula_full_fits

.venv/bin/python fit_saved_without_be.py \
  --baseline ../output/english_spacy_baseline \
  --output output/copula_paper_without_be
```

Use fresh output paths to repeat completed runs. Each diagnostic retains the
same utterance denominator and ages within a population/scope, and reselects
the original top 100 eligible verbs after filtering. The augmented outputs
include pair origins, the explicit predicate/copula paths, non-`be` copula
counts, and source/pair hashes. The fitter verifies that augmented and spaCy
inputs derive from the same utterance table, and that augmentation used the
exact strict-pair input. Both scope summaries include all nine overall/age fits.

### Rebuild the figures, tables and checks

The reusable analysis code lives in this directory alongside the extraction and
fitting scripts. Generated files live under `output/` and are ignored by Git.
The commands below use existing parsed/fitted data, so they do not download or
reparse the corpora. Use new output paths if these destinations already exist.

```sh
.venv/bin/python plot_copula_comparison.py \
  --matched-fits output/copula_matched_fits \
  --full-fits output/copula_full_fits \
  --baseline ../output/english_spacy_baseline \
  --paper-no-be output/copula_paper_without_be \
  --output output/copula_report_v2

.venv/bin/python verify_copula_results.py \
  --matched-fits output/copula_matched_fits \
  --full-fits output/copula_full_fits \
  --paper-no-be output/copula_paper_without_be \
  --output output/copula_verification_reusable_v2

.venv/bin/python audit_copula_pairs.py \
  --population output/matched_historical_data \
  --augmented output/copula_matched_ud \
  --spacy output/matched_spacy_chat \
  --output output/copula_matched_integrity_reusable.json

.venv/bin/python audit_copula_pairs.py \
  --population output/copula_full_population \
  --augmented output/copula_full_ud \
  --spacy output/copula_full_spacy_chat \
  --output output/copula_full_integrity_reusable.json
```

`plot_copula_comparison.py` writes PNG/PDF figures, all four alpha tables as
Markdown, endpoint comparisons as CSV, and input paths/hashes. Its titles,
counts and plotted values come from the input summaries; it does not assume
that future runs will have the same scientific outcome.
The figure and four tables compare the three CHAT-based methods. The endpoint
CSV also includes the optional matched original-CSV-text spaCy control, when
present; the metadata records which methods appear in each output.

`verify_copula_results.py` independently checks rank proportions, all candidate
MSE values, predictions, fitted minima, exact age-bin coverage, denominators
recounted from input CSVs, `be` filters and hashes.
It also regenerates the top-verb overlap and pair-effect CSVs.
`audit_copula_pairs.py` checks the full pair files against their utterance
population and verifies that augmentation preserved the strict pairs. These
checks read large CSVs but reuse the completed linguistic analyses.

The prose in [COPULA_RESULTS.md](COPULA_RESULTS.md) records the interpretation
of this completed run. Generated tables are available separately so future
results can be reviewed without editing the numerical values by hand.

## First live-download fit and comparison

```sh
.venv/bin/python fit_zipf.py \
  --pairs output/eng_na_recovered_extraction/english_ud_subject_verb_pairs.csv \
  --utterances output/eng_na_recovered_extraction/english_ud_utterances.csv \
  --metadata output/eng_na_recovered_extraction/metadata.json \
  --output output/eng_na_recovered_fit

.venv/bin/python compare_with_spacy.py \
  --baseline ../output/english_spacy_baseline \
  --ud output/eng_na_recovered_fit --output output/eng_na_comparison
```

The fitter preserves the original top-100 ASCII-verb filter and first-seen tie
order, subject normalization within each verb, averaging over only verbs present
at each rank, then a second normalization of the average curve. It searches
`np.arange(0.1, 3.0 + 0.01, 0.01)` and takes the first MSE minimum. Age bins are
`[0,12), …, [84,96)`; the original overall filter includes exactly 96 months.
Original per-bin minimum support checks remain. Literal lemmas such as `nan`
(the name Nan) are preserved rather than treated as CSV missing values.

Fit outputs include all nine summaries, top verbs, full subject-rank counts,
rank averages, all 291 MSE candidates, predictions, and the age plot. Comparisons
include counts, exponent/MSE differences, top-100 overlap, top-verb lists,
baseline-top-ten subject counts, aligned empirical/predicted curves and two plots.
Missing bins/artifacts stay null and scopes are always explicit.

## Same-transcript parser diagnostic

```sh
.venv/bin/python sample_spacy.py \
  --extraction output/repository_extraction \
  --output output/same_transcript_sample_aligned

.venv/bin/python compare_with_spacy.py \
  --baseline ../output/english_spacy_baseline \
  --ud output/eng_na_recovered_fit --output output/sample_comparison \
  --spacy-pairs output/same_transcript_sample_aligned/spacy_pairs.csv \
  --ud-pairs output/same_transcript_sample_aligned/ud_pairs.csv
```

The sample selects two transcripts per age bin with seed 20260907. Only included
utterances with usable UD annotations enter both parses. The same normalized
surface text is used for spaCy, with the original `nsubj`/VERB-or-AUX rule.
Native parser indices are converted to shared source-word positions, preserving
repeated-word identity and mapping clitics to their containing source word.
Parser-specific surfaces remain separately available. Pair multiplicities are
retained. Index alignment requires a shared declared coordinate system; positions
containing multiple distinct pairs are flagged as ambiguous and do not receive
inferred lemma/POS alignments. This is a newly run diagnostic, not a claim that historical pair CSVs
contain utterance-level provenance.

The original corpus and age coverage audit is reproducible with
`audit_original_scope.py --help`; its run-specific findings are in
`output/original_scope_audit/REPORT.md`. The authoritative final run directories
are listed in [RESULTS.md](RESULTS.md); earlier pilot and header-only outputs are
retained for the audit trail.

## References

- [PyLangAcq reading CHAT and custom tiers](https://docs.pylangacq.org/stable/read.html)
- [PyLangAcq transcription and annotation representation](https://docs.pylangacq.org/stable/transcriptions.html)
- [TalkBank UD processing](https://talkbank.org/0info/mor/)
- [CHILDES Eng-NA corpus index](https://talkbank.org/childes/access/Eng-NA/)
- [UD copula convention](https://universaldependencies.org/u/dep/cop.html)
- [childesr implementation and data access](https://github.com/langcog/childesr)
- Claire Hobbs (2026), *What Does Language Bring to the Learner?*, Experiment 2
  and Appendix F; original code in the parent experiment directory.

Raw transcripts and generated outputs are ignored by Git. Use a new output
directory for each run; the CLIs protect existing results. Bootstrap, fixed-verb
and corpus-sensitivity analyses remain subsequent work after the English
comparison is understood, as specified in the reproduction guide.
