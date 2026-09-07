"""Link original transcript IDs to current CHAT using multiple unique text matches.

This emits auditable candidate links, not automatic permission to import ages or
speaker roles. PID/content validation against legacy CHAT is a separate check.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re


def corpus_key(name):
    return re.sub(r"[^a-z0-9]", "", name.rsplit("/", 1)[-1].lower())


def signature(text):
    # Retain long utterances only. Strip whitespace/punctuation for childes-db
    # contraction tokenization, without lemmatizing or translating words.
    if len(re.findall(r"[a-zA-Z]+", text)) < 6:
        return None
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    if len(normalized) < 25:
        return None
    return hashlib.sha256(normalized.encode()).digest()[:16]


def choose_link(votes, min_matches=5):
    if not votes:
        return None, "no_unique_text_matches"
    ordered = votes.most_common()
    best, count = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0
    if count < min_matches:
        return None, "insufficient_text_matches"
    if runner_up and (count < 5 * runner_up or count / sum(votes.values()) < 0.8):
        return None, "ambiguous_text_matches"
    return best, "high_confidence_text_match"


def link(original, extraction, output, min_matches=5):
    if output.exists():
        raise FileExistsError(output)
    original_meta, original_counts = {}, Counter()
    index = {}
    with original.open() as stream:
        for n, row in enumerate(csv.DictReader(stream), 1):
            tid = row["transcript_id"]
            meta = {k: row[k] for k in ["target_child_name", "target_child_age", "target_child_sex", "corpus_name"]}
            if tid in original_meta and original_meta[tid] != meta:
                raise ValueError(f"Conflicting original transcript metadata for {tid}")
            original_meta[tid] = meta
            original_counts[tid] += 1
            sig = signature(row["full_utterance"])
            if sig is not None:
                key = (corpus_key(row["corpus_name"]), sig)
                if key not in index:
                    index[key] = tid
                elif index[key] != tid:
                    index[key] = None  # discard phrases shared by original transcripts
            if n % 1000000 == 0:
                print(f"Indexed {n:,} original utterances", flush=True)
    index = {key: tid for key, tid in index.items() if tid is not None}
    print(f"{len(index):,} signatures unique to an original transcript", flush=True)
    votes = defaultdict(Counter)
    seen = defaultdict(set)
    current_meta = {}
    with (extraction / "english_ud_utterances.csv").open() as stream:
        for n, row in enumerate(csv.DictReader(stream), 1):
            identity = (row["collection"], row["corpus"], row["transcript"])
            current_meta.setdefault(identity, {k: row[k] for k in ["target_child", "target_child_age_months", "age_status"]})
            sig = signature(row["text"])
            if sig is not None and sig not in seen[identity]:
                key = (corpus_key(row["corpus"]), sig)
                if key in index:
                    votes[identity][index[key]] += 1
                    seen[identity].add(sig)
            if n % 1000000 == 0:
                print(f"Matched {n:,} current utterances", flush=True)
    output.mkdir(parents=True)
    rows = []
    for identity, current in sorted(current_meta.items()):
        counts = votes[identity]
        match, status = choose_link(counts, min_matches)
        best_two = counts.most_common(2)
        meta = original_meta[match] if match else {}
        rows.append({"collection": identity[0], "corpus": identity[1], "transcript": identity[2],
                     "original_transcript_id": match or "", "status": status,
                     "unique_matching_utterances": best_two[0][1] if best_two else 0,
                     "runner_up_matches": best_two[1][1] if len(best_two) > 1 else 0,
                     "total_matching_utterances": sum(counts.values()),
                     "original_target_child": meta.get("target_child_name", ""),
                     "original_age_months": meta.get("target_child_age", ""),
                     "original_n_utterances": original_counts[match] if match else "",
                     "chat_target_child": current["target_child"], "chat_age_months": current["target_child_age_months"]})
    # One old transcript may recur in new releases. Mark duplicate links for
    # review rather than implicitly double counting a copied recording.
    target_counts = Counter(r["original_transcript_id"] for r in rows if r["original_transcript_id"])
    for row in rows:
        row["original_id_used_by_multiple_current_transcripts"] = target_counts[row["original_transcript_id"]] > 1
    with (output / "transcript_links.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with original.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"original": str(original.resolve()), "original_sha256": digest,
              "transcript_links_sha256": hashlib.sha256((output / "transcript_links.csv").read_bytes()).hexdigest(),
              "extraction": str(extraction.resolve()), "minimum_unique_matches": min_matches,
              "rule": "at least 6 alphabetic words, 25 alphanumeric characters; >=5 distinct signatures unique to one original transcript; winner>=5x runner-up and >=80% of votes",
              "n_original_transcripts": len(original_meta), "n_current_transcripts": len(rows),
              "statuses": dict(Counter(r["status"] for r in rows)),
              "note": "These content-based links must be checked against PID/source identity before enriching metadata; no filename-only or age-based inference."}
    (output / "metadata.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["statuses"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--extraction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    link(args.original, args.extraction, args.output)


if __name__ == "__main__":
    main()
