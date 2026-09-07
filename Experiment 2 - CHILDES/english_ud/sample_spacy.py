"""Reparse a reproducible transcript sample for same-text extraction diagnostics."""
import argparse
from collections import Counter
import csv
import importlib.metadata
import json
import math
from pathlib import Path
import random
import re
import warnings
import zipfile
from bisect import bisect_right


def surface_groups(tokens):
    """Map expanded morphology indices to shared source-word positions."""
    words, groups = [], {}
    current = None
    for token in tokens:
        if token.word:
            words.append(token.word)
            current = (len(words), token.word)
        if token.gra and current is not None:
            groups[token.gra.dep] = current
    return " ".join(words), groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transcripts-per-bin", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--model", default="en_core_web_sm")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; select a new directory")
    if args.transcripts_per_bin < 1:
        parser.error("--transcripts-per-bin must be positive")
    import spacy
    import pylangacq
    metadata = json.loads((args.extraction / "metadata.json").read_text())
    rng = random.Random(args.seed)
    selected = []
    for lo in range(0, 96, 12):
        candidates = [f for f in metadata["files"] if f.get("status") == "parsed"
                      and isinstance(f.get("target_child_age_months"), (int, float))
                      and lo <= f["target_child_age_months"] < lo + 12]
        candidates.sort(key=lambda f: (f["collection"], f["transcript"]))
        selected.extend(rng.sample(candidates, min(args.transcripts_per_bin, len(candidates))))
    selected_ids = {(f["collection"], f["transcript"]) for f in selected}
    with (args.extraction / "english_ud_utterances.csv").open() as stream:
        utterances = [row for row in csv.DictReader(stream)
                      if (row["collection"], row["transcript"]) in selected_ids
                      and row["included_in_cds"] == "True"]
    # The comparison needs two analyses of the same usable text. Missing or
    # misaligned UD tiers are coverage exclusions, not parser disagreements.
    excluded = Counter(row["annotation_status"] for row in utterances if row["annotation_status"] != "annotated")
    utterances = [row for row in utterances if row["annotation_status"] == "annotated" and row["text"].strip()]
    usable = {(u["collection"], u["transcript"], u["utterance_id"]) for u in utterances}
    with (args.extraction / "english_ud_subject_verb_pairs.csv").open() as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        ud_pairs = [row for row in reader if (row["collection"], row["transcript"], row["utterance_id"]) in usable]
    by_utterance = {(u["collection"], u["transcript"], u["utterance_id"]): u for u in utterances}
    group_maps = {}
    sources = {s["archive_id"]: s for s in metadata["sources"]}
    for file in selected:
        source = sources[file["archive_id"]]
        with zipfile.ZipFile(source["path"]) as archive:
            raw = archive.read(file["member"]).decode("utf-8-sig")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader = pylangacq.CHAT.from_strs([raw], parallel=False, strict=False,
                                             mor_tier=metadata["morphology_tier"], gra_tier=metadata["grammar_tier"])
        uid = 0
        for utterance in reader.utterances():
            if utterance.changeable_header is not None:
                continue
            uid += 1
            key = (file["collection"], file["transcript"], str(uid))
            if key in by_utterance:
                text, groups = surface_groups(utterance.tokens)
                if text != by_utterance[key]["text"]:
                    raise ValueError(f"Surface reconstruction changed: {key}")
                group_maps[key] = groups
    fields = fields + ["subject_parser_surface", "verb_parser_surface", "index_space"]
    for pair in ud_pairs:
        key = (pair["collection"], pair["transcript"], pair["utterance_id"])
        for kind in ["subject", "verb"]:
            pair[kind + "_parser_surface"] = pair[kind + "_surface"]
            index, surface = group_maps[key][int(pair[kind + "_index"])]
            pair[kind + "_index"] = index
            pair[kind + "_surface"] = surface
        pair["index_space"] = "source_word"
    nlp = spacy.load(args.model)
    spacy_pairs = []
    for u, doc in zip(utterances, nlp.pipe((u["text"] for u in utterances), disable=["ner"], batch_size=128)):
        source_words = list(re.finditer(r"\S+", u["text"]))
        starts = [word.start() for word in source_words]
        for token in doc:
            if token.dep_ == "nsubj" and token.head.pos_ in {"VERB", "AUX"}:
                base = {field: u.get(field, "") for field in fields}
                base.update(subject_surface=token.text, subject_lemma=token.lemma_.lower(), subject_pos=token.pos_,
                            verb_surface=token.head.text, verb_lemma=token.head.lemma_.lower(), verb_pos=token.head.pos_,
                            dependency=token.dep_)
                for kind, parsed in [("subject", token), ("verb", token.head)]:
                    group = bisect_right(starts, parsed.idx)
                    base[kind + "_index"] = group
                    base[kind + "_parser_surface"] = parsed.text
                    base[kind + "_surface"] = source_words[group - 1].group()
                base["index_space"] = "source_word"
                spacy_pairs.append(base)
    args.output.mkdir(parents=True)
    for name, rows in [("spacy_pairs.csv", spacy_pairs), ("ud_pairs.csv", ud_pairs)]:
        with (args.output / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    with (args.output / "utterances.csv").open("w", newline="") as stream:
        if utterances:
            writer = csv.DictWriter(stream, fieldnames=list(utterances[0]))
            writer.writeheader()
            writer.writerows(utterances)
    report = {"scope": metadata["scope"], "collection": metadata["collection"],
              "seed": args.seed, "transcripts_per_bin": args.transcripts_per_bin,
              "selected_transcripts": selected, "n_utterances": len(utterances),
              "excluded_utterances_by_annotation_status": dict(excluded),
              "n_ud_pairs": len(ud_pairs), "n_spacy_pairs": len(spacy_pairs),
              "spacy_version": spacy.__version__, "model": args.model,
              "model_version": importlib.metadata.version(args.model),
              "text_source": "PyLangAcq normalized surface words; same utterances as UD; no reannotation of CHAT files",
              "index_note": "Indices are shared one-based source-word positions, not native parser indices. Clitic components map to their containing source word; original parser surfaces are retained separately.",
              "historical_note": "New same-transcript diagnostic. Historical pair CSVs lack utterance-level provenance."}
    (args.output / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ["n_utterances", "n_ud_pairs", "n_spacy_pairs"]}, indent=2))


if __name__ == "__main__":
    main()
