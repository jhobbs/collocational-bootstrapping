"""Reparse every included utterance in a selected CHILDES CSV with spaCy."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile


PAIR_FIELDS = [
    "subject_surface", "subject_lemma", "subject_pos",
    "verb_surface", "verb_lemma", "verb_pos", "dependency",
    "subject_index", "verb_index", "index_space",
]
IDENTITY_FIELDS = [
    "collection", "corpus", "transcript", "utterance_id",
    "target_child_age_months",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_included_utterances(path: Path, text_column: str):
    """Yield ``(text, row)`` tuples lazily in source order."""
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = ["included_in_cds", text_column]
        missing = [name for name in required if name not in fields]
        if missing:
            raise ValueError(f"Utterance CSV is missing required columns: {', '.join(missing)}")
        for row in reader:
            if row["included_in_cds"].strip().lower() == "true":
                yield row[text_column], row


def extract_doc_pairs(doc):
    """Yield pairs using the historical spaCy extraction rule."""
    for token in doc:
        if token.dep_ == "nsubj" and token.head.pos_ in {"VERB", "AUX"}:
            yield {
                "subject_surface": token.text,
                "subject_lemma": token.lemma_.lower(),
                "subject_pos": token.pos_,
                "verb_surface": token.head.text,
                "verb_lemma": token.head.lemma_.lower(),
                "verb_pos": token.head.pos_,
                "dependency": token.dep_,
                "subject_index": token.i,
                "verb_index": token.head.i,
                "index_space": "spacy_token",
            }


def _csv_fields(path: Path, text_column: str) -> list[str]:
    with path.open(newline="") as stream:
        fields = csv.DictReader(stream).fieldnames or []
    required = ["included_in_cds", *IDENTITY_FIELDS, text_column]
    missing = [name for name in required if name not in fields]
    if missing:
        raise ValueError(f"Utterance CSV is missing required columns: {', '.join(missing)}")
    return fields


def _expected_count(metadata: dict):
    counts = metadata.get("counts", {})
    for key in ("n_included_utterances", "n_cds_utterances"):
        if isinstance(counts.get(key), int):
            return counts[key], key
    return None, None


def parse_matched(utterances, metadata_path, output, text_column="text",
                  n_process=4, model="en_core_web_sm", *, nlp=None):
    utterances = Path(utterances).resolve()
    metadata_path = Path(metadata_path).resolve()
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if n_process < 1:
        raise ValueError("n_process must be positive")
    input_fields = _csv_fields(utterances, text_column)
    input_metadata = json.loads(metadata_path.read_text())
    if nlp is None:
        import spacy
        nlp = spacy.load(model)

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".parse-matched-spacy-", dir=output.parent))
    counts = {"n_input_rows": 0, "n_included_utterances": 0, "n_pairs": 0}
    if "n_pairs" in input_fields and "source_ud_n_pairs" in input_fields:
        raise ValueError("Input cannot contain both n_pairs and source_ud_n_pairs")
    pair_fields = ["source_ud_n_pairs" if name == "n_pairs" else name
                   for name in input_fields]
    pair_fields += [name for name in PAIR_FIELDS if name not in pair_fields]

    def records():
        with utterances.open(newline="") as stream:
            reader = csv.DictReader(stream)
            for row in reader:
                counts["n_input_rows"] += 1
                if row["included_in_cds"].strip().lower() == "true":
                    counts["n_included_utterances"] += 1
                    yield row[text_column], row

    try:
        parsed = 0
        with (stage / "english_ud_subject_verb_pairs.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=pair_fields)
            writer.writeheader()
            docs = nlp.pipe(records(), as_tuples=True, batch_size=128,
                            n_process=n_process, disable=["ner"])
            for doc, row in docs:
                parsed += 1
                for pair in extract_doc_pairs(doc):
                    source = dict(row)
                    if "n_pairs" in source:
                        source["source_ud_n_pairs"] = source.pop("n_pairs")
                    writer.writerow({**source, **pair})
                    counts["n_pairs"] += 1
                if parsed % 50_000 == 0:
                    print(f"Parsed {parsed:,} included utterances", flush=True)
        if parsed != counts["n_included_utterances"]:
            raise RuntimeError(
                f"Parser returned {parsed} documents for "
                f"{counts['n_included_utterances']} included utterances"
            )
        expected, expected_key = _expected_count(input_metadata)
        if expected is not None and expected != counts["n_included_utterances"]:
            raise ValueError(
                f"Input metadata {expected_key}={expected} differs from supplied "
                f"included utterance count {counts['n_included_utterances']}"
            )
        try:
            spacy_version = importlib.metadata.version("spacy")
        except importlib.metadata.PackageNotFoundError:
            spacy_version = None
        model_version = getattr(nlp, "meta", {}).get("version")
        if model_version is None:
            try:
                model_version = importlib.metadata.version(model)
            except importlib.metadata.PackageNotFoundError:
                model_version = None
        preserved = {
            key: input_metadata[key] for key in (
                "matching_provenance", "selection_provenance", "matched_scope",
            ) if key in input_metadata
        }
        report = {
            "scope": input_metadata.get("scope"),
            "collection": input_metadata.get("collection"),
            "annotation_scheme": "spaCy",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "input": {
                "utterances_path": str(utterances),
                "utterances_sha256": sha256_file(utterances),
                "metadata_path": str(metadata_path),
                "metadata_sha256": sha256_file(metadata_path),
                "text_column": text_column,
                "renamed_columns": ({
                    "n_pairs": "source_ud_n_pairs (pair count from the source UD annotation)"
                } if "n_pairs" in input_fields else {}),
            },
            "parser": {
                "model": model, "model_version": model_version,
                "spacy_version": spacy_version, "n_process": n_process,
                "batch_size": 128, "disabled_pipes": ["ner"],
                "extraction_rule": "token.dep_ == 'nsubj' and token.head.pos_ in {'VERB', 'AUX'}; lowercase spaCy lemmas",
                "index_space": "spacy_token",
            },
            "denominator": {
                "utterance_csv": str(utterances),
                "included_column": "included_in_cds",
                "note": "All supplied included utterances are counted, including utterances with zero extracted pairs.",
            },
            **preserved,
        }
        (stage / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(json.dumps(counts, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--utterances", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text-column", default="text")
    parser.add_argument("--n-process", type=int, default=4)
    parser.add_argument("--model", default="en_core_web_sm")
    args = parser.parse_args()
    try:
        parse_matched(args.utterances, args.metadata, args.output,
                      args.text_column, args.n_process, args.model)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
