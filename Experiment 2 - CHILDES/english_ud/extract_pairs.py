"""Extract traceable subject–verb observations from explicitly selected CHAT UD tiers."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import warnings
import zipfile

import pylangacq

# Exactly the 25 roles in ../childes_filter_speakers.R; some are not adults.
CDS_ROLES = frozenset("Adult Caretaker Father Friend Grandfather Grandmother Investigator Mother Narrator Playmate Relative Sibling Sister Brother Teacher Unidentified Visitor Teenager Participant Girl Male Student Environment Doctor Target_Adult".split())
UD_POS = frozenset("ADJ ADP ADV AUX CCONJ DET INTJ NOUN NUM PART PRON PROPN PUNCT SCONJ SYM VERB X".split())
BASE_FIELDS = ["language", "collection", "corpus", "transcript", "target_child",
               "target_child_code", "target_child_age", "target_child_age_months",
               "age_status", "target_selection", "speaker", "speaker_role", "utterance_id"]
PAIR_FIELDS = BASE_FIELDS + ["subject_surface", "subject_lemma", "subject_pos", "subject_mor",
                            "verb_surface", "verb_lemma", "verb_pos", "verb_mor",
                            "dependency", "subject_index", "verb_index"]
UTTERANCE_FIELDS = BASE_FIELDS + ["included_in_cds", "exclusion_reason", "annotation_status", "n_pairs", "text", "chat_main_tier"]
# TalkBank UD feature suffixes start with an uppercase feature value (or S/P+person).
# Strip only that suffix, retaining lexical hyphens; keep raw MOR for later audits.
FEATURE_START = re.compile(r"-(?=(?:[A-Z][A-Za-z0-9]*)(?:-|$))")


def lemma_from_mor(mor):
    return FEATURE_START.split(mor, maxsplit=1)[0].lower()


def load_manifest(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    if config.get("scope") not in {"thesis_eng_na", "repository_english", "pilot_eng_na"}:
        raise ValueError("Manifest scope must be thesis_eng_na, pilot_eng_na, or repository_english")
    if not config.get("sources"):
        raise ValueError("Manifest contains no sources")
    seen = set()
    for item in config["sources"]:
        for field in ["path", "collection", "source_url", "download_date", "archive_id"]:
            if field not in item:
                raise ValueError(f"Source is missing {field}")
        source = Path(item["path"])
        if not source.is_absolute():
            source = path.parent / source
        item["path"] = str(source.resolve())
        if not source.exists():
            raise FileNotFoundError(source)
        if item["path"] in seen:
            raise ValueError(f"Duplicate source {source}")
        seen.add(item["path"])
        if config["scope"] in {"thesis_eng_na", "pilot_eng_na"} and item["collection"] != "Eng-NA":
            raise ValueError("Eng-NA scope cannot contain another collection")
    return config


def source_files(source):
    """Yield ZIP members without unpacking archives; preserve canonical member paths."""
    path = Path(source["path"])
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name.lower().endswith(".cha") and not name.startswith("__MACOSX/"):
                    yield name, archive.read(name)
    elif path.is_dir():
        for file in sorted(path.rglob("*")):
            if file.is_file() and file.suffix.lower() == ".cha":
                yield file.relative_to(path).as_posix(), file.read_bytes()
    elif path.suffix.lower() == ".cha":
        yield path.name, path.read_bytes()
    else:
        raise ValueError(f"Expected a CHAT file, directory, or ZIP: {path}")


def inspect_sources(manifest_path):
    config = load_manifest(manifest_path)
    counts = Counter()
    files = []
    for source in config["sources"]:
        for name, raw in source_files(source):
            text = raw.decode("utf-8-sig")
            tiers = Counter(re.findall(r"^(%[^:\s]+):", text, re.MULTILINE))
            counts.update(tiers)
            samples = {}
            # Only annotation tiers are sampled, not participants or utterance text.
            for match in re.finditer(r"^(%[^:\s]+):\s*([^\n]*)", text, re.MULTILINE):
                if "mor" in match[1] or "gra" in match[1]:
                    samples.setdefault(match[1], match[2][:500])
            files.append({"collection": source["collection"], "archive_id": source["archive_id"],
                          "member": name, "sha256": hashlib.sha256(raw).hexdigest(),
                          "tier_counts": dict(tiers), "annotation_samples": samples,
                          "annotation_comments": re.findall(r"^@Comment:\s*(.*(?:morphotag|stanza|batchalign|asr).*)", text, re.MULTILINE | re.IGNORECASE)})
    if not files:
        raise ValueError("No CHAT files found")
    return {"scope": config["scope"], "sources": config["sources"],
            "n_files": len(files), "tier_counts": dict(counts), "files": files}


def target_metadata(participants):
    children = [p for p in participants.values() if p.role == "Target_Child"]
    if "CHI" in participants:
        children = [participants["CHI"]]
    if len(children) != 1:
        return {"target_child": ";".join(p.name or p.code for p in children),
                "target_child_code": ";".join(p.code for p in children),
                "target_child_age": "", "target_child_age_months": None,
                "age_status": "ambiguous_target" if children else "missing_target",
                "target_selection": "unresolved"}
    child = children[0]
    age = child.age.in_months() if child.age else None
    usable = age is not None and math.isfinite(age) and age >= 0
    return {"target_child": child.name or child.code, "target_child_code": child.code,
            "target_child_age": str(child.age) if child.age else "",
            "target_child_age_months": age if usable else None,
            "age_status": "valid" if usable else "missing_age",
            "target_selection": "CHI_code" if child.code == "CHI" else "unique_target_role"}


def pair_edges(utterance, diagnostics):
    tokens = utterance.tokens
    by_dep = {}
    for token in tokens:
        if not token.gra:
            continue
        if token.gra.dep in by_dep:
            raise ValueError("Duplicate grammatical dependent index")
        by_dep[token.gra.dep] = token
    for token in tokens:
        edge = token.gra
        if not edge or edge.rel.lower() != "nsubj":
            continue
        head = by_dep.get(edge.head)
        if head is None:
            diagnostics["nsubj_missing_head"] += 1
            continue
        if head.pos.upper() not in {"VERB", "AUX"}:
            diagnostics["nsubj_nonverbal_head"] += 1
            continue
        subject, verb = lemma_from_mor(token.mor), lemma_from_mor(head.mor)
        if not subject or not verb or subject in {"_", "xxx", "yyy", "www"} or verb in {"_", "xxx", "yyy", "www"}:
            diagnostics["nsubj_missing_lemma"] += 1
            continue
        yield {"subject_surface": token.word, "subject_lemma": subject,
               "subject_pos": token.pos.upper(), "subject_mor": token.mor,
               "verb_surface": head.word, "verb_lemma": verb, "verb_pos": head.pos.upper(),
               "verb_mor": head.mor, "dependency": edge.rel,
               "subject_index": edge.dep, "verb_index": edge.head}


def dependencies_aligned(utterance, gra_tier):
    tokens = utterance.tokens
    # Rustling assigns %gra entries to tokens positionally. A missing entry
    # can otherwise silently attach a valid-looking index to the wrong token.
    if len(utterance.tiers[gra_tier].split()) != len(tokens):
        return False
    return all(t.gra is not None and t.gra.dep == i and 0 <= t.gra.head <= len(tokens)
               for i, t in enumerate(tokens, 1))


def extract(manifest_path, output, mor_tier, gra_tier, allow_parse_errors=False):
    config = load_manifest(manifest_path)
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if not mor_tier.startswith("%") or not gra_tier.startswith("%") or mor_tier == gra_tier:
        raise ValueError("Select distinct % morphology and grammar tiers after inspection")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".extract-", dir=output.parent))
    counts, diagnostics, roles, relations, pos_counts = Counter(), Counter(), Counter(), Counter(), Counter()
    file_records, errors = [], []
    seen = set()
    try:
        with (stage / "english_ud_subject_verb_pairs.csv").open("w", newline="") as pf, (stage / "english_ud_utterances.csv").open("w", newline="") as uf:
            pair_writer = csv.DictWriter(pf, fieldnames=PAIR_FIELDS)
            utterance_writer = csv.DictWriter(uf, fieldnames=UTTERANCE_FIELDS)
            pair_writer.writeheader()
            utterance_writer.writeheader()
            for source in config["sources"]:
                source_count = 0
                for name, raw in source_files(source):
                    counts["n_files"] += 1
                    source_count += 1
                    text = raw.decode("utf-8-sig")
                    record = {"collection": source["collection"], "archive_id": source["archive_id"],
                              "member": name, "sha256": hashlib.sha256(raw).hexdigest(),
                              "annotation_comments": re.findall(r"^@Comment:\s*(.*(?:morphotag|stanza|batchalign|asr).*)", text, re.MULTILINE | re.IGNORECASE)}
                    file_records.append(record)
                    try:
                        reader = pylangacq.CHAT.from_strs([text], ids=[name], parallel=False,
                                                         strict=True, mor_tier=mor_tier, gra_tier=gra_tier)
                    except ValueError as exc:
                        record["strict_validation_error"] = str(exc)
                        # PyLangAcq's non-strict mode keeps tiers but returns empty
                        # tokens for mor/word misalignment. Audit those below;
                        # never infer dependencies for the failed utterance.
                        try:
                            with warnings.catch_warnings(record=True) as caught:
                                warnings.simplefilter("always")
                                reader = pylangacq.CHAT.from_strs([text], ids=[name], parallel=False,
                                                                 strict=False, mor_tier=mor_tier, gra_tier=gra_tier)
                            record["parser_warnings"] = [str(w.message) for w in caught]
                            counts["n_files_relaxed_validation"] += 1
                        except ValueError as relaxed_exc:
                            if not allow_parse_errors:
                                raise ValueError(f"CHAT parse error {source['archive_id']}/{name}: {relaxed_exc}") from relaxed_exc
                            record["status"] = "parse_error"
                            errors.append({"archive_id": source["archive_id"], "member": name, "error": str(relaxed_exc)})
                            counts["n_files_parse_error"] += 1
                            continue
                    header = reader.headers()[0]
                    participants = {p.code: p for p in header.participants}
                    corpus_names = sorted({p.corpus for p in participants.values() if p.corpus})
                    corpus = source.get("corpus") or (corpus_names[0] if len(corpus_names) == 1 else PurePosixPath(name).parts[0])
                    parts = list(PurePosixPath(name).parts)
                    if parts[0] == source["collection"]:
                        parts.pop(0)
                    if "/".join(parts).startswith(corpus + "/"):
                        transcript = "/".join(parts)
                    else:
                        transcript = corpus + "/" + "/".join(parts)
                    identity = (source["collection"], transcript)
                    if identity in seen:
                        raise ValueError(f"Duplicate transcript identity: {identity}")
                    seen.add(identity)
                    target = target_metadata(participants)
                    record.update({"corpus": corpus, "transcript": transcript, **target, "status": "parsed"})
                    record["target_participants"] = [{"code": p.code, "name": p.name,
                                                       "age": str(p.age) if p.age else ""}
                                                      for p in participants.values() if p.role == "Target_Child" or p.code == "CHI"]
                    if target["target_child_age_months"] is None:
                        counts["n_files_missing_age"] += 1
                    bilingual = "biling" in corpus.lower()
                    record["excluded_biling_corpus"] = bilingual
                    uid = 0
                    for utterance in reader.utterances():
                        if utterance.changeable_header is not None:
                            counts["n_changeable_headers"] += 1
                            continue
                        uid += 1
                        person = participants.get(utterance.participant)
                        role = person.role if person else ""
                        language = person.language if person else ""
                        included = (role in CDS_ROLES and utterance.participant != "CHI"
                                    and role != "Target_Child" and language == "eng" and not bilingual)
                        reason = ("" if included else "biling_corpus" if bilingual else
                                  "speaker_language" if language != "eng" else "speaker_role")
                        roles[(role, included)] += 1
                        base = {"language": language, "collection": source["collection"], "corpus": corpus,
                                "transcript": transcript, **target, "speaker": utterance.participant,
                                "speaker_role": role, "utterance_id": uid}
                        counts["n_utterances"] += 1
                        if included:
                            counts["n_cds_utterances"] += 1
                            if target["target_child_age_months"] is None:
                                counts["n_cds_utterances_missing_age"] += 1
                        annotated = mor_tier in utterance.tiers and gra_tier in utterance.tiers
                        annotation_status = "annotated" if annotated else "missing_tiers"
                        if annotated and not utterance.tokens:
                            annotation_status = "misaligned_tokens"
                            counts["n_utterances_misaligned"] += 1
                            if included:
                                counts["n_cds_utterances_misaligned"] += 1
                            errors.append({"archive_id": source["archive_id"], "member": name,
                                           "utterance_id": uid, "error": "Annotated utterance has no aligned tokens"})
                            annotated = False
                        if annotated and not dependencies_aligned(utterance, gra_tier):
                            annotation_status = "invalid_dependencies"
                            counts["n_utterances_invalid_dependencies"] += 1
                            if included:
                                counts["n_cds_utterances_invalid_dependencies"] += 1
                            errors.append({"archive_id": source["archive_id"], "member": name,
                                           "utterance_id": uid, "error": "Dependency tier is not aligned to token positions"})
                            annotated = False
                        accepted = []
                        if annotated:
                            counts["n_annotated_utterances"] += 1
                            for token in utterance.tokens:
                                pos_counts[token.pos] += 1
                                if token.gra:
                                    relations[token.gra.rel] += 1
                                    if token.gra.rel.upper() in {"SUBJ", "PRED", "JCT"} or token.pos in {"v", "pro", "n:prop"}:
                                        raise ValueError(f"Legacy MOR annotation in selected tiers: {transcript}, utterance {uid}")
                            if included:
                                accepted = list(pair_edges(utterance, diagnostics))
                        elif included:
                            counts["n_cds_utterances_missing_tiers"] += 1
                        for pair in accepted:
                            pair_writer.writerow({**base, **pair})
                        counts["n_pairs"] += len(accepted)
                        if target["target_child_age_months"] is None:
                            counts["n_pairs_missing_age"] += len(accepted)
                        utterance_writer.writerow({**base, "included_in_cds": included, "exclusion_reason": reason,
                                                   "annotation_status": annotation_status,
                                                   "n_pairs": len(accepted), "text": " ".join(t.word for t in utterance.tokens if t.word),
                                                   "chat_main_tier": utterance.tiers.get(utterance.participant, "")})
                if source_count == 0:
                    raise ValueError(f"No CHAT files in {source['path']}")
                print(f"Extracted {source.get('corpus', source['archive_id'])}: {counts['n_files']:,} files, {counts['n_pairs']:,} pairs total", flush=True)
        if not counts["n_annotated_utterances"]:
            raise ValueError("No utterances with the selected annotation tiers")
        if not any(pos.upper() in {"VERB", "AUX"} for pos in pos_counts):
            raise ValueError("No UD verbal POS tags in selected annotations")
        with (stage / "speaker_roles.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["speaker_role", "utterance_count", "included_in_cds"])
            writer.writeheader()
            writer.writerows({"speaker_role": role, "utterance_count": n, "included_in_cds": yes}
                             for (role, yes), n in sorted(roles.items()))
        metadata = {"scope": config["scope"], "collection": ";".join(sorted({s["collection"] for s in config["sources"]})),
                    "annotation_scheme": "Universal Dependencies", "morphology_tier": mor_tier, "grammar_tier": gra_tier,
                    "pylangacq_version": pylangacq.__version__, "created_at": datetime.now(timezone.utc).isoformat(),
                    "sources": config["sources"], "files": file_records, "counts": dict(counts),
                    "diagnostics": dict(diagnostics), "dependency_counts": dict(relations), "pos_counts": dict(pos_counts),
                    "speaker_roles": sorted(CDS_ROLES), "speaker_language_filter": "eng (exact participant language)",
                    "corpus_exclusion": "case-insensitive Biling substring", "dependency_selection": "exact NSUBJ, case-insensitive; VERB or AUX head",
                    "lemma_rule": "lowercase MOR stem before uppercase feature suffix; raw morphology retained",
                    "surface_note": "PyLangAcq assigns empty surfaces to expanded MOR clitics; kept with dependency indices and raw morphology. Utterances retain CHAT main tiers.",
                    "parse_errors": errors, "complete_parse": not errors, "download_failures": config.get("download_failures", []),
                    "age_note": "CHI participant preferred, else unique Target_Child role; PyLangAcq in_months; missing or unresolved ages retained in extraction, excluded from fits"}
        (stage / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        stage.rename(output)
        return metadata
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspect", action="store_true", help="Write annotation inventory JSON; no extraction")
    parser.add_argument("--mor-tier")
    parser.add_argument("--gra-tier")
    parser.add_argument("--allow-parse-errors", action="store_true", help="Record and skip malformed files; output is marked incomplete")
    args = parser.parse_args()
    if args.inspect:
        if args.output.exists():
            parser.error("Inspection output already exists")
        report = inspect_sources(args.manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report[k] for k in ["scope", "n_files", "tier_counts"]}, indent=2))
    else:
        if not args.mor_tier or not args.gra_tier:
            parser.error("Inspect data first, then explicitly supply --mor-tier and --gra-tier")
        result = extract(args.manifest, args.output, args.mor_tier, args.gra_tier, args.allow_parse_errors)
        print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__":
    main()
