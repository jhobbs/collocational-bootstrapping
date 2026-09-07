# Reproducing the English CHILDES Zipf Analysis with TalkBank UD and PyLangAcq

## Goal

Reproduce the existing English CHILDES result using **TalkBank's
annotated CHAT transcripts and PyLangAcq**, replacing the current
raw-text + spaCy subject--verb extraction stage while keeping the
downstream Zipf analysis as unchanged as possible.

The quantity to reproduce is the fitted Zipf exponent

\[ `\hat{\alpha}`{=tex}(a) \]

for the distribution of **subjects conditioned on verbs** in
child-directed speech, as a function of the target child's age (a).

The existing analysis reports an overall fitted value of approximately

\[ `\hat{\alpha}`{=tex}=1.43 \]

and the following values by 12-month age bin:

  Target-child age     Existing (`\hat{\alpha}`{=tex})
  ------------------ ---------------------------------
  0--12 months                                    1.46
  12--24 months                                   1.40
  24--36 months                                   1.44
  36--48 months                                   1.38
  48--60 months                                   1.37
  60--72 months                                   1.28
  72--84 months                                   1.23
  84--96 months                                   1.25

The main validation question is:

> **Does the TalkBank + PyLangAcq extraction pipeline recover
> approximately the same fitted Zipf exponents and the same age-related
> decline as the existing English analysis?**

If it does, we can use the same extraction and analysis framework for
other CHILDES languages with much greater confidence.

------------------------------------------------------------------------

## Why do this before the multilingual analysis?

The current English pipeline extracts raw CHILDES utterances and
reparses them with spaCy. For a multilingual study, TalkBank's Universal
Dependencies annotations are preferable because they provide a common
dependency representation across many CHILDES languages.

Before changing both the parser **and** the language at the same time,
we should change only the parser/data-access path and test it on
English.

Conceptually, the comparison is:

### Existing pipeline

\[ `\text{CHILDES}`{=tex} `\rightarrow`{=tex}
`\text{raw utterances}`{=tex} `\rightarrow`{=tex} `\text{spaCy}`{=tex}
`\rightarrow`{=tex}
(`\text{subject lemma}`{=tex},`\text{verb lemma}`{=tex})
`\rightarrow`{=tex} `\hat{\alpha}`{=tex}(a) \]

### Proposed validation pipeline

\[ `\text{TalkBank annotated CHAT}`{=tex} `\rightarrow`{=tex}
`\text{PyLangAcq}`{=tex} `\rightarrow`{=tex}
`\text{existing dependency annotations}`{=tex} `\rightarrow`{=tex}
(`\text{subject lemma}`{=tex},`\text{verb lemma}`{=tex})
`\rightarrow`{=tex} `\hat{\alpha}`{=tex}(a) \]

The downstream statistical analysis should initially be held fixed. This
makes differences in the results attributable primarily to corpus scope
and subject--verb extraction rather than to a simultaneous rewrite of
the fitting procedure.

------------------------------------------------------------------------

## Important scope issue to resolve explicitly

There is a discrepancy between the thesis description and the current
repository documentation.

The thesis describes the CHILDES experiment as using the **English-North
America** component of CHILDES. The current repository README describes
the present extraction pipeline as spanning English-language CHILDES
collections more broadly, including North American, UK, and clinical
English.

We should therefore treat these as two related reproduction targets:

1.  **Repository reproduction:** reproduce the current repository's
    corpus scope as closely as possible.
2.  **Thesis reproduction:** restrict the analysis to CHILDES **Eng-NA**
    and reproduce the published/thesis result.

The second is the scientifically important baseline for subsequent
cross-linguistic comparisons, but the first may be useful for debugging
because we have executable code on both sides.

Do not silently mix these scopes. Every output should record which
corpus selection produced it.

------------------------------------------------------------------------

## Proposed implementation

### 1. Preserve the existing analysis as the reference implementation

Do not modify the current spaCy analysis initially.

Run or retain its existing outputs, including at least:

-   overall (`\alpha`{=tex});
-   (`\alpha`{=tex}) for each 12-month age bin;
-   number of utterances;
-   number of extracted subject--verb pairs;
-   number of unique subjects;
-   number of unique verbs;
-   top verbs used in each age bin;
-   rank-frequency averages;
-   MSE search results.

These become the baseline against which the PyLangAcq pipeline is
compared.

Where practical, save the baseline outputs under a clearly named
directory such as:

``` text
output/english_spacy_baseline/
```

rather than overwriting existing experiment outputs.

------------------------------------------------------------------------

### 2. Acquire the corresponding TalkBank CHAT transcripts

For the thesis reproduction, use the CHILDES **Eng-NA** transcripts from
TalkBank.

TalkBank currently requires free registration/login for transcript
access. Keep the downloaded transcript archive outside Git if it is
large or if redistribution is not appropriate.

Record enough provenance to reproduce the dataset later:

``` text
source: TalkBank CHILDES
collection: Eng-NA
download date: YYYY-MM-DD
archive/file identifier: ...
```

Also preserve the corpus names represented in the downloaded data. This
will matter if we later discover that one corpus is responsible for a
discrepancy.

------------------------------------------------------------------------

### 3. Determine which annotation tiers contain the desired dependency analysis

TalkBank supports both older MOR analyses and Universal Dependencies for
English. Before writing the extractor, inspect representative English
CHAT files and determine exactly which tiers contain the dependency
representation we want to use.

Do **not** hard-code assumptions about tier names until this is verified
against the downloaded data.

PyLangAcq parses `%mor` and `%gra` by default and also supports custom
morphology and grammatical-relation tier names through `mor_tier` and
`gra_tier`. This means that if the desired English UD annotations use
alternate tier names, the same library can still parse them.

Record the annotation choice in the experiment metadata, for example:

``` text
annotation_scheme: Universal Dependencies
morphology_tier: ...
grammar_tier: ...
```

For the eventual multilingual experiment, the objective is to use the
same UD interpretation across languages.

------------------------------------------------------------------------

### 4. Read CHAT files with PyLangAcq

Use PyLangAcq to load the downloaded CHAT archive/files.

For each transcript we need:

-   corpus/file identity;
-   target-child identity;
-   target-child age;
-   speaker identity and role;
-   utterance tokens;
-   token lemmas;
-   token POS;
-   grammatical dependency relations.

PyLangAcq exposes target-child ages from CHAT metadata and converts the
CHILDES `years;months.days` representation to age in months. Preserve
the original file/corpus identifiers alongside the numeric age so that
extracted observations remain traceable to their source transcript.

------------------------------------------------------------------------

### 5. Match the original definition of child-directed speech

The original analysis did not simply take every non-child utterance. It
selected adult/caregiver speaker roles and excluded the target child's
own speech.

For the strict reproduction, match the current experiment's speaker-role
selection as closely as possible rather than using only:

``` text
speaker != CHI
```

The current repository describes 25 selected adult/caregiver roles,
including roles such as Mother, Father, Caretaker, Investigator,
Teacher, Relative, Sibling, and others.

Create one explicit function or configuration defining which speaker
roles count as child-directed speech. Save counts by speaker role so
that differences between the old and new pipelines can be diagnosed.

At minimum, record:

``` text
speaker_role
utterance_count
included_in_cds
```

------------------------------------------------------------------------

### 6. Extract subject--verb pairs from the existing dependency annotations

For each included utterance, identify dependency edges corresponding to
a nominal subject and its governing verb.

The intended UD relationship is approximately:

\[ `\text{subject}`{=tex} `\xrightarrow{\texttt{nsubj}}`{=tex}
`\text{verb}`{=tex} \]

with the head constrained to a verbal category such as `VERB` or `AUX`.

For each accepted dependency, emit a row such as:

  field                     meaning
  ------------------------- --------------------------------------
  language                  `eng`
  collection                e.g. `Eng-NA`
  corpus                    CHILDES corpus name
  transcript                source CHAT file
  target_child              target-child identifier if available
  target_child_age_months   numeric age
  speaker                   speaker code
  speaker_role              TalkBank participant role
  subject_surface           observed subject token
  subject_lemma             subject lemma
  subject_pos               subject POS
  verb_surface              observed verb token
  verb_lemma                verb lemma
  verb_pos                  verb POS
  dependency                dependency relation used

This table should become the stable interface between linguistic
extraction and statistical analysis.

For example:

``` text
data/derived/english_ud_subject_verb_pairs.csv
```

Everything downstream should operate on this table rather than directly
on PyLangAcq objects.

------------------------------------------------------------------------

### 7. Use the same age bins as the existing experiment

Use:

``` text
0–12
12–24
24–36
36–48
48–60
60–72
72–84
84–96 months
```

Match the existing boundary convention exactly. The current analysis
uses lower-inclusive, upper-exclusive bins:

\[ \[a,b). \]

Exclude observations without a usable target-child age from the
age-specific analysis, but report how many observations/files were
excluded for this reason.

------------------------------------------------------------------------

### 8. Reuse the existing Zipf-fitting procedure

For the first reproduction, avoid "improving" the statistical method. We
want to isolate the effect of changing the extraction pipeline.

Within each age bin:

1.  Count subject--verb pairs.
2.  Select the 100 most frequent verbs using the same rule as the
    current analysis.
3.  For each verb (v), count its subjects.
4.  Normalize subject counts within the verb:

\[ p_v(s)= `\frac{c_v(s)}`{=tex} {`\sum`{=tex}\_{s'} c_v(s')}. \]

5.  Rank the subjects of each verb from most to least frequent.
6.  Let (p_v(r)) be the proportion associated with the (r)-th most
    frequent subject of verb (v).
7.  Average across verbs at each rank to obtain the empirical
    rank-frequency curve:

\[ `\bar `{=tex}p(r). \]

8.  Fit the normalized Zipf distribution

\[ q\_`\alpha`{=tex}(r) = `\frac{r^{-\alpha}}`{=tex} {`\sum`{=tex}\_j
j\^{-`\alpha`{=tex}}}. \]

9.  Search the same (`\alpha`{=tex}) range and resolution as the
    existing implementation and select the value minimizing MSE:

\[ `\hat{\alpha}`{=tex} = `\arg`{=tex}`\min`{=tex}*`\alpha`{=tex}
`\operatorname{MSE}`{=tex} `\left`{=tex}(
`\bar `{=tex}p(r),q*`\alpha`{=tex}(r) `\right`{=tex}). \]

Produce both the overall-corpus fit and the age-specific fits.

------------------------------------------------------------------------

## Validation: compare more than (`\alpha`{=tex})

The final (`\alpha`{=tex}) values are the headline result, but comparing
only them would make discrepancies difficult to diagnose.

For each age bin, produce a comparison table containing at least:

  Metric                      Existing spaCy pipeline   TalkBank/PyLangAcq pipeline
  ------------------------- ------------------------- -----------------------------
  utterances
  subject--verb pairs
  unique subjects
  unique verbs
  top-100 verb overlap
  fitted (`\alpha`{=tex})
  best-fit MSE

Also compare:

-   the actual top verbs;
-   subject counts for a sample of high-frequency verbs;
-   the empirical (`\bar `{=tex}p(r)) curves;
-   the fitted Zipf curves;
-   the complete (`\alpha`{=tex})-by-age trajectories.

A useful diagnostic is the overlap between the two extracted pair sets
when both pipelines operate on the same underlying transcripts.

For a sample of transcripts, classify disagreements such as:

``` text
pair extracted by both
pair extracted only by spaCy
pair extracted only by TalkBank UD
same dependency, different lemma
same dependency, different verb/AUX treatment
```

This will tell us *why* the fitted distributions differ if they do.

------------------------------------------------------------------------

## What counts as successful reproduction?

We should not require byte-for-byte equality.

The TalkBank annotations and spaCy parser are different analyses, so
some difference in extracted pairs is expected.

A successful reproduction should show:

1.  comparable subject--verb rank-frequency distributions;
2.  fitted (`\alpha`{=tex}) values reasonably close to the existing
    values;
3.  the same broad developmental trajectory;
4.  in particular, evidence that fitted (`\alpha`{=tex}) is lower for
    speech directed to older children than for speech directed to
    younger children.

The strongest outcome would be recovery of both the approximate level

\[ `\alpha `{=tex}`\approx 1.4`{=tex} \]

in the younger age ranges and the decline toward roughly

\[ `\alpha `{=tex}`\approx 1.2`{=tex}`\text{–}`{=tex}1.3 \]

in the older age ranges.

If the new pipeline does **not** reproduce the result, that is still
informative. We should determine whether the difference is caused by
corpus selection, speaker filtering, dependency analysis, lemmatization,
verb/AUX handling, or the statistical fitting stage before attempting
other languages.

------------------------------------------------------------------------

## Recommended outputs

Add a new experiment area rather than replacing the existing English
analysis. For example:

``` text
Experiment 2 - CHILDES/
    ...
    english_ud/
        README.md
        extract_pairs.py
        fit_zipf.py
        compare_with_spacy.py
        data/
        output/
```

Suggested generated outputs:

``` text
english_ud_subject_verb_pairs.csv
english_ud_summary.csv
english_ud_alpha_by_age.csv
english_ud_rank_averages_<age>.csv
english_ud_actual_vs_predicted_<age>.csv
english_ud_alpha_by_age.png
english_ud_vs_spacy.csv
english_ud_vs_spacy.png
```

The README should document the TalkBank source, corpus scope, download
date, PyLangAcq version, annotation tiers used, speaker-role filter,
age-bin definitions, and exact fitting procedure.

------------------------------------------------------------------------

## Follow-up analysis after the strict reproduction

Once the strict reproduction works, run a few robustness checks
**without replacing the primary replication result**.

### Fixed verb vocabulary across age

The current procedure selects the top 100 verbs independently in each
age bin. This means changes in (`\alpha`{=tex}) can reflect both:

1.  changes in subject distributions for verbs; and
2.  changes in which verbs enter the top 100.

Run a secondary analysis using a fixed set of verbs that have sufficient
support across all age bins.

This asks more directly whether:

\[ P(`\text{subject}`{=tex}`\mid`{=tex}`\text{same verb}`{=tex}) \]

becomes flatter as target-child age increases.

### Bootstrap uncertainty

The existing plot reports point estimates of (`\alpha`{=tex}). Add
bootstrap confidence intervals in a secondary analysis, ideally
resampling at an appropriate unit such as transcripts, children, or
corpora rather than treating every extracted pair as independent.

This would let us distinguish a visually descending sequence from
statistically well-supported developmental change.

### Corpus sensitivity

Because CHILDES aggregates heterogeneous studies, repeat the fit while
leaving out one corpus at a time, or examine large corpora separately.

This will tell us whether the age trend is broadly distributed across
the data or dominated by a few studies whose recordings happen to occupy
particular age ranges.

These are improvements to pursue **after** the TalkBank/PyLangAcq
reproduction has been established.

------------------------------------------------------------------------

## Decision point for the multilingual study

Proceed to other languages only after we understand the English
comparison.

If the TalkBank/PyLangAcq English result agrees reasonably well with the
existing analysis, use the extracted-pair table as the common
multilingual interface:

``` text
language
corpus
transcript
target_child_age_months
speaker_role
subject_lemma
verb_lemma
```

Then the exact same Zipf-fitting code can estimate

\[ `\hat{\alpha}`{=tex}\_L(a) \]

for each language (L).

At that point the main cross-linguistic question becomes:

> **Does the fitted Zipf exponent of verb-conditioned subject
> distributions decrease with target-child age across languages, or is
> the English pattern language- or corpus-specific?**

The English reproduction is therefore not just a code migration. It is
the validation experiment that establishes whether TalkBank's
standardized dependency annotations can support the proposed
multilingual analysis.

------------------------------------------------------------------------

## Primary references and implementation sources

-   Claire Hobbs, *What Does Language Bring to the Learner? Zipfian
    Distributions as Statistical Signals for Learning Subject-Verb
    Agreement* (2026), especially Experiment 2 and Appendix F.
-   `ClaireHobbs/collocational-bootstrapping`, especially
    `Experiment 2 - CHILDES/README.md` and
    `analyze_age_groups_96mos.py`.
-   TalkBank CHILDES documentation for Eng-NA, CHAT, and Universal
    Dependencies.
-   PyLangAcq documentation for reading CHAT archives, target-child
    ages, participant metadata, `%mor`/`%gra` parsing, and custom
    morphology/grammar tiers.
