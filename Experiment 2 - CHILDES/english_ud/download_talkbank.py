"""Download authenticated transcript ZIPs; credentials and cookies stay in memory."""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


def links(url):
    parser = Links()
    with urllib.request.urlopen(url, timeout=45) as response:
        parser.feed(response.read().decode("utf-8"))
    return [urllib.parse.urljoin(url, href) for href in parser.links]


def discover(collection):
    index = f"https://talkbank.org/childes/access/{collection}/"
    pages = sorted({url for url in links(index)
                    if url.startswith(index) and url.endswith(".html")})
    archives = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for page_links in pool.map(links, pages):
            archives.update(url for url in page_links
                            if url.startswith(f"https://talkbank.org/data/childes/{collection}/")
                            and urllib.parse.urlparse(url).query == "f=zip")
    if not archives:
        raise ValueError(f"No transcript archives found for {collection}")
    return sorted(archives)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--collections", nargs="+", default=["Eng-NA", "Eng-UK", "Clinical-Eng"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"email": args.email,
                       "pswd": args.password_file.read_text().rstrip("\r\n")}).encode()
    request = urllib.request.Request("https://sla2.talkbank.org/logInUser", data=body,
                                     headers={"Content-Type": "application/json", "Origin": "https://talkbank.org"})
    with opener.open(request, timeout=45) as response:
        result = json.load(response)
    del body, request
    if not result.get("success"):
        raise SystemExit("TalkBank login failed: " + str(result.get("respMsg", "unknown")))
    print("TalkBank login succeeded.", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    failures = []
    sources = []
    for collection in args.collections:
        urls = discover(collection)
        print(f"{collection}: {len(urls)} transcript archives", flush=True)
        for number, url in enumerate(urls, 1):
            corpus = urllib.parse.urlparse(url).path.split(f"/{collection}/", 1)[1]
            destination = args.output / collection / (corpus + ".zip")
            provenance_path = destination.with_suffix(".provenance.json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if not provenance_path.exists():
                    raise SystemExit(f"Existing archive has no provenance: {destination}")
                record = json.loads(provenance_path.read_text())
                if record["sha256"] != sha256(destination):
                    raise SystemExit(f"Existing archive checksum mismatch: {destination}")
                sources.append(record)
                print(f"[{number}/{len(urls)}] Reused {collection}/{corpus}", flush=True)
                continue
            for attempt in range(3):
                try:
                    with opener.open(url, timeout=90) as response:
                        first = response.read(4)
                        if first != b"PK\x03\x04":
                            raise ValueError("response was not a transcript ZIP (login/access may be required)")
                        partial = destination.with_suffix(".zip.part")
                        with partial.open("wb") as stream:
                            stream.write(first)
                            while chunk := response.read(1024 * 1024):
                                stream.write(chunk)
                    with zipfile.ZipFile(partial) as archive:
                        bad_member = archive.testzip()
                        if bad_member or not any(n.lower().endswith(".cha") for n in archive.namelist()):
                            raise ValueError("invalid ZIP or no CHAT transcripts")
                    partial.rename(destination)
                    record = {"path": str(destination.resolve()), "collection": collection,
                              "corpus": corpus, "source_url": url,
                              "download_date": datetime.now(timezone.utc).date().isoformat(),
                              "archive_id": f"{collection}/{corpus}.zip",
                              "sha256": sha256(destination), "bytes": destination.stat().st_size}
                    provenance_path.write_text(json.dumps(record, indent=2) + "\n")
                    sources.append(record)
                    print(f"[{number}/{len(urls)}] Saved {collection}/{corpus}: {record['bytes'] / 1e6:.1f} MB", flush=True)
                    break
                except (urllib.error.URLError, TimeoutError, ValueError, zipfile.BadZipFile) as exc:
                    if attempt == 2:
                        failures.append({"url": url, "reason": str(exc)})
                        print(f"Failed {collection}/{corpus}: {exc}", flush=True)
                    else:
                        time.sleep(2)
            # Checkpoint nonsecret inventory after every archive.
            (args.output / "download_inventory.json").write_text(json.dumps(
                {"sources": sources, "failures": failures}, indent=2) + "\n")
    for scope, selected in [("thesis_eng_na", [s for s in sources if s["collection"] == "Eng-NA"]),
                            ("repository_english", sources)]:
        (args.output / f"{scope}.json").write_text(json.dumps({
            "scope": scope, "sources": selected, "download_failures": failures,
            "selection_note": "English collection indexes; original participant-level English query may additionally include other collections."}, indent=2) + "\n")
    if failures:
        raise SystemExit(f"Downloaded {len(sources)} archives; {len(failures)} failures recorded.")
    print(f"Finished: {len(sources)} archives", flush=True)


if __name__ == "__main__":
    main()
