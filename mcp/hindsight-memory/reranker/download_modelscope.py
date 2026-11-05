"""Download the pinned BGE reranker only from ModelScope; preserve partial files."""

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

MODEL = "BAAI/bge-reranker-v2-m3"
REVISION = "e099d4b9cdbd291b1569d416f19aaa6523570bc3"
FILES = (
    "README.md", "config.json", "configuration.json", "special_tokens_map.json",
    "tokenizer_config.json", "tokenizer.json", "sentencepiece.bpe.model", "model.safetensors",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    hosts = set()

    class ModelScopeRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            host = urllib.parse.urlparse(newurl).hostname or ""
            if host != "modelscope.cn" and not host.endswith(".modelscope.cn"):
                raise RuntimeError(f"Unexpected model download redirect host: {host}")
            hosts.add(host)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    # Same vendor URL contract as the existing BGE-M3 deployment:
    # https://github.com/modelscope/modelscope/blob/v1.40.2/modelscope/hub/file_download.py
    opener = urllib.request.build_opener(ModelScopeRedirects())

    def fetch(url):
        hosts.add(urllib.parse.urlparse(url).hostname)
        return opener.open(urllib.request.Request(url, headers={"User-Agent": "hindsight-bge-reranker/1"}), timeout=60)

    query = urllib.parse.urlencode({"Revision": REVISION, "Recursive": "true"})
    with fetch(f"https://modelscope.cn/api/v1/models/{MODEL}/repo/files?{query}") as response:
        listing = json.load(response)
    if listing.get("Code") != 200 or not listing.get("Success"):
        raise RuntimeError("ModelScope did not return a successful file listing")
    source = {f["Path"]: f for f in listing["Data"]["Files"] if f["Type"] == "blob"}
    receipt = {"source": "ModelScope", "model": MODEL, "revision": REVISION, "files": [], "complete": False}
    (args.destination / "source-listing.json").write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")

    for name in FILES:
        meta = source[name]
        destination = args.destination / name
        partial = destination.with_name(name + ".part")
        if not destination.exists():
            if partial.exists():
                raise RuntimeError(f"Reconcile the existing partial download before retrying: {partial}")
            query = urllib.parse.urlencode({"Revision": REVISION, "FilePath": name})
            print(json.dumps({"event": "download_start", "file": name, "size": meta["Size"]}), flush=True)
            last = time.monotonic()
            with fetch(f"https://modelscope.cn/api/v1/models/{MODEL}/repo?{query}") as response, partial.open("xb") as output:
                size = 0
                for chunk in iter(lambda: response.read(8 * 1024 * 1024), b""):
                    output.write(chunk)
                    size += len(chunk)
                    if time.monotonic() - last >= 30:
                        print(json.dumps({"event": "download_progress", "file": name, "bytes": size}), flush=True)
                        last = time.monotonic()
            candidate = partial
        else:
            candidate = destination
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        if candidate.stat().st_size != meta["Size"] or digest.hexdigest() != meta["Sha256"]:
            raise RuntimeError(f"ModelScope file integrity mismatch, preserving the file: {candidate}")
        if candidate == partial:
            partial.rename(destination)
        receipt["files"].append({"file": name, "size_bytes": meta["Size"], "sha256": meta["Sha256"], "source_file_revision": meta["Revision"]})
        receipt.update(download_hosts=sorted(hosts), complete=len(receipt["files"]) == len(FILES))
        (args.destination / "download-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps({"event": "verified", "file": name, "bytes": meta["Size"]}), flush=True)


if __name__ == "__main__":
    main()
