import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ENGLISH_UD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGLISH_UD_DIR))

from compare_with_spacy import compare_aligned_pairs, compare_outputs, main


SUMMARY_COLUMNS = [
    "age_group",
    "alpha",
    "mse",
    "n_utterances",
    "n_pairs",
    "n_unique_subjects",
    "n_unique_verbs",
    "scope",
    "collection",
    "annotation_scheme",
]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_inputs(tmp_path: Path) -> tuple[Path, Path]:
    baseline = tmp_path / "baseline"
    ud = tmp_path / "ud"
    baseline.mkdir()
    ud.mkdir()

    baseline_rows = [
        ["overall", 1.0, 0.01, None, 100, 4, 3,
         "repository_english", "mixed_english_unknown_manifest", "spaCy"],
        ["0-12mo", 0.8, 0.02, None, 20, 3, 3,
         "repository_english", "mixed_english_unknown_manifest", "spaCy"],
    ]
    pd.DataFrame(baseline_rows, columns=SUMMARY_COLUMNS).to_csv(
        baseline / "baseline_summary.csv", index=False
    )
    ud_rows = [[
        "overall", 1.2, 0.03, 90, 80, 5, 3,
        "eng_na", "Eng-NA", "Universal Dependencies", "ok",
    ]]
    pd.DataFrame(ud_rows, columns=SUMMARY_COLUMNS + ["status"]).to_csv(
        ud / "english_ud_summary.csv", index=False
    )

    baseline_ranked = [
        {"verb": "be", "subject": "it", "frequency": 7, "rank": 1, "total": 10, "proportion": .7},
        {"verb": "be", "subject": "you", "frequency": 3, "rank": 2, "total": 10, "proportion": .3},
        {"verb": "go", "subject": "I", "frequency": 6, "rank": 1, "total": 6, "proportion": 1.0},
        {"verb": "want", "subject": "I", "frequency": 4, "rank": 1, "total": 4, "proportion": 1.0},
    ]
    ud_ranked = [
        {"verb": "go", "subject": "I", "frequency": 5, "rank": 1, "total": 5, "proportion": 1.0},
        {"verb": "want", "subject": "you", "frequency": 2, "rank": 1, "total": 2, "proportion": 1.0},
        {"verb": "have", "subject": "I", "frequency": 1, "rank": 1, "total": 1, "proportion": 1.0},
    ]
    _write_csv(baseline / "complete_dataset_96mos" / "all_verbs_ranked_96mos.csv", baseline_ranked)
    _write_csv(
        baseline / "age_groups_complete_96mos" / "all_verbs_ranked_0-12mo.csv",
        baseline_ranked[:2],
    )
    _write_csv(ud / "english_ud_all_verbs_ranked_overall.csv", ud_ranked)

    baseline_curve = [
        {"rank": 1, "actual_frequency": .7, "predicted_frequency": .6,
         "squared_error": .01, "num_verbs": 3},
        {"rank": 2, "actual_frequency": .3, "predicted_frequency": .4,
         "squared_error": .01, "num_verbs": 1},
    ]
    ud_curve = [
        {"rank": 1, "actual_frequency": .6, "predicted_frequency": .55,
         "squared_error": .0025, "num_verbs": 3},
        {"rank": 2, "actual_frequency": .4, "predicted_frequency": .45,
         "squared_error": .0025, "num_verbs": 1},
    ]
    _write_csv(
        baseline / "complete_dataset_96mos" / "actual_vs_predicted_96mos.csv",
        baseline_curve,
    )
    _write_csv(
        baseline / "age_groups_complete_96mos" / "actual_vs_predicted_0-12mo.csv",
        baseline_curve,
    )
    _write_csv(ud / "english_ud_actual_vs_predicted_overall.csv", ud_curve)
    return baseline, ud


def test_comparison_preserves_missing_values_and_ranked_top_verb_order(tmp_path):
    """Catches treating missing UD bins as zero or alphabetizing top verbs."""
    baseline, ud = _make_inputs(tmp_path)
    output = tmp_path / "comparison"

    compare_outputs(baseline, ud, output)

    summary = pd.read_csv(output / "english_ud_vs_spacy.csv")
    overall = summary.loc[summary.age_group == "overall"].iloc[0]
    assert overall.spacy_alpha == pytest.approx(1.0)
    assert overall.ud_alpha == pytest.approx(1.2)
    assert overall.alpha_delta == pytest.approx(0.2)
    assert overall.top_100_overlap_count == 2
    assert overall.top_100_overlap_denominator == 3
    assert overall.top_100_overlap_fraction == pytest.approx(2 / 3)
    assert not bool(overall.scope_comparable)

    missing_ud = summary.loc[summary.age_group == "0-12mo"].iloc[0]
    assert pd.isna(missing_ud.ud_alpha)
    assert pd.isna(missing_ud.ud_n_pairs)
    assert pd.isna(missing_ud.top_100_overlap_count)

    top_verbs = pd.read_csv(output / "top_verbs_comparison.csv")
    baseline_overall = top_verbs[
        (top_verbs.age_group == "overall") & (top_verbs.source == "spacy")
    ]
    assert baseline_overall.verb.tolist() == ["be", "go", "want"]

    curves = pd.read_csv(output / "aligned_curves.csv")
    missing_curve = curves[(curves.age_group == "0-12mo") & (curves["rank"] == 1)].iloc[0]
    assert pd.isna(missing_curve.ud_actual_frequency)
    assert (output / "alpha_age_trajectory.png").stat().st_size > 0
    assert (output / "curve_overlay.png").stat().st_size > 0

    metadata = json.loads((output / "comparison_metadata.json").read_text())
    assert "no corpus/transcript manifest" in metadata["scope_note"]
    assert metadata["source_labels"]["spacy"] == {
        "scopes": ["repository_english"],
        "collections": ["mixed_english_unknown_manifest"],
        "annotation_schemes": ["spaCy"],
    }
    assert metadata["source_labels"]["ud"]["scopes"] == ["eng_na"]
    assert metadata["top_100_overlap_denominator"] == (
        "min(100, number of ordered verbs available in each ranked table)"
    )
    assert any("0-12mo" in item and "UD ranked" in item for item in metadata["diagnostics"])
    assert any("0-12mo" in item and "UD summary row missing" in item for item in metadata["diagnostics"])


def test_cli_refuses_nonempty_output_directory(tmp_path):
    """Catches silently mixing a new comparison with stale artifacts."""
    baseline, ud = _make_inputs(tmp_path)
    output = tmp_path / "comparison"
    output.mkdir()
    (output / "stale.txt").write_text("old")

    with pytest.raises(FileExistsError, match="not empty"):
        main(["--baseline", str(baseline), "--ud", str(ud), "--output", str(output)])


def test_aligned_pair_diagnostic_counts_multiplicity_and_lemma_disagreement():
    """Catches set-based comparison that loses duplicates and lemma differences."""
    common = {
        "collection": "Eng-NA",
        "corpus": "Brown",
        "transcript": "adam01",
        "subject_surface": "I",
        "verb_surface": "want",
        "subject_lemma": "I",
        "verb_lemma": "want",
        "verb_pos": "VERB",
    }
    spacy = pd.DataFrame([
        {**common, "utterance_id": "u1"},
        {**common, "utterance_id": "u1"},
        {**common, "utterance_id": "u2", "subject_surface": "Dogs", "subject_lemma": "dog"},
    ])
    ud = pd.DataFrame([
        {**common, "utterance_id": "u1"},
        {**common, "utterance_id": "u2", "subject_surface": "Dogs", "subject_lemma": "dogs"},
    ])

    diagnostics = compare_aligned_pairs(spacy, ud)

    assert diagnostics.groupby("classification")["multiplicity"].sum().to_dict() == {
        "both": 1,
        "same_dependency_different_lemma": 1,
        "spacy_only": 1,
    }


def test_aligned_pair_diagnostic_rejects_unalignable_aggregate_tables():
    """Catches presenting historical aggregate pair rows as transcript-aligned."""
    aggregate = pd.DataFrame({"subject_lemma": ["I"], "verb_lemma": ["want"]})

    with pytest.raises(ValueError, match="required columns"):
        compare_aligned_pairs(aggregate, aggregate)
def test_blank_indices_fall_back_to_surface_alignment():
    import pandas as pd
    from compare_with_spacy import compare_aligned_pairs
    base = dict(collection="Eng-NA", corpus="Brown", transcript="Brown/a.cha", utterance_id=1,
                subject_surface="you", verb_surface="go", subject_lemma="you", verb_lemma="go", verb_pos="VERB")
    spacy = pd.DataFrame([{**base, "subject_index": "", "verb_index": ""}])
    ud = pd.DataFrame([{**base, "subject_index": 1, "verb_index": 2}])
    result = compare_aligned_pairs(spacy, ud)
    assert result["classification"].tolist() == ["both"]
    assert result["alignment_basis"].tolist() == ["surface_forms"]


@pytest.mark.parametrize("spaces", [(None, None), ("spacy_token", "ud_token")])
def test_native_indices_require_a_shared_declared_coordinate_space(spaces):
    base = dict(collection="Eng-NA", corpus="Brown", transcript="a.cha", utterance_id=1,
                subject_surface="you", verb_surface="go", subject_lemma="you", verb_lemma="go",
                verb_pos="VERB", subject_index=1, verb_index=2)
    frames = []
    for space in spaces:
        row = dict(base)
        if space is not None:
            row["index_space"] = space
        frames.append(pd.DataFrame([row]))
    result = compare_aligned_pairs(*frames)
    assert result["alignment_basis"].tolist() == ["surface_forms"]


def test_multiple_subtokens_at_one_source_word_are_not_greedily_aligned():
    base = dict(collection="Eng-NA", corpus="Brown", transcript="a.cha", utterance_id=1,
                subject_surface="iiis", verb_surface="iiis", subject_lemma="ii", verb_lemma="be",
                verb_pos="AUX", subject_index=1, verb_index=1, index_space="source_word")
    spacy = pd.DataFrame([base, {**base, "subject_lemma": "i"}])
    ud = pd.DataFrame([{**base, "subject_lemma": "it"}])
    result = compare_aligned_pairs(spacy, ud)
    assert result.groupby("classification")["multiplicity"].sum().to_dict() == {
        "spacy_only": 2, "ud_only": 1}
    assert result["dependency_alignment_ambiguous"].all()
    assert set(result["alignment_basis"]) == {"token_indices"}
