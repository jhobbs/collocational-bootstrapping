# Original CHILDES scope and age audit

The historical utterance export contains 5,147,586 utterances across 121 corpus names. 4,818,027 have a numeric nonnegative target-child age at or below 96 months.

The current repository manifest contains 98 archives. Of the historical corpus names, 43 have no exact source-corpus or leaf match and 3 have ambiguous matches.

There are 8,371 high-confidence linked current transcripts. Among the 567 that lack a usable current age, 280 (49.4%) have a usable historical age; 18 have both ages and differ by more than 0.1 month.

Corpus-name coverage is not a language audit. The historical R workflow filtered the participant table to `language == "eng"`, then requested utterances by corpus and role without a language argument. No language was inferred from corpus names here.

See `original_corpus_coverage.csv`, `current_missing_with_original_age.csv`, `age_conflicts_gt_0_1mo.csv`, `matched_transcript_age_audit.csv`, and `audit_summary.json` for details.
