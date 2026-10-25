"""Explicit, bounded downloads of registered upstream assets; no overwrites."""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
MAX_ASSET_BYTES = 60_000_000


class RemoteAssetError(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteAssetError("Duplicate registry JSON key")
        result[key] = value
    return result


def validate_url(url: str, allowed_hosts: list[str]) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment):
            raise RemoteAssetError("URL is outside the registered HTTPS host allowlist")
    except (TypeError, ValueError) as exc:
        raise RemoteAssetError("Invalid registered or redirected URL") from exc
    return url


def load_registry(root: Path = ROOT) -> dict:
    path = Path(root) / "data/remote-assets.json"
    try:
        if path.stat().st_size > 100_000:
            raise RemoteAssetError("Registry is unexpectedly large")
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
        if data.get("schema_version") != 1 or type(data.get("assets")) is not list:
            raise RemoteAssetError("Unsupported remote asset registry")
        assets = {}
        for asset in data["assets"]:
            if type(asset) is not dict:
                raise RemoteAssetError("Invalid asset registration")
            asset_id, filename = asset["id"], asset["filename"]
            if (type(asset_id) is not str or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", asset_id)
                    or asset_id in assets or type(filename) is not str
                    or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\.(?:pdf|json)", filename)
                    or asset["kind"] not in {"pdf", "finqa_json"}
                    or (asset["kind"] == "pdf") != filename.endswith(".pdf")
                    or type(asset["max_bytes"]) is not int or not 1 <= asset["max_bytes"] <= MAX_ASSET_BYTES
                    or type(asset["sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])):
                raise RemoteAssetError("Invalid asset identity, size or hash")
            hosts = asset["allowed_hosts"]
            if (type(hosts) is not list or not hosts or len(hosts) != len(set(hosts))
                    or any(type(host) is not str or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) for host in hosts)):
                raise RemoteAssetError("Invalid registered hosts")
            validate_url(asset["url"], hosts)
            if asset["kind"] == "finqa_json" and (type(asset["records"]) is not int or asset["records"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{40}", asset["revision"])
                    or not re.fullmatch(r"[0-9a-f]{40}", asset["git_blob_sha1"])):
                raise RemoteAssetError("Invalid pinned JSON registration")
            assets[asset_id] = asset
        return assets
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RemoteAssetError):
            raise
        raise RemoteAssetError("Cannot read or validate remote asset registry") from exc


class RegisteredRedirects(HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = validate_url(urljoin(req.full_url, newurl), self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, target)


def destination_path(root: Path, asset: dict, output: Path | None = None) -> Path:
    root = Path(root).resolve()
    base = root / ".run/downloads"
    target = Path(output) if output is not None else base / asset["id"] / asset["filename"]
    if not target.is_absolute():
        target = root / target
    # Reject aliases rather than trusting a .run directory that points into data.
    lexical = Path(os.path.abspath(target))
    if not lexical.is_relative_to(base) or lexical == base or ".." in target.parts:
        raise RemoteAssetError("Downloads must create a file inside this repository's .run/downloads")
    for candidate in (target, *target.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise RemoteAssetError("Download destinations cannot contain filesystem aliases")
        if candidate != target and candidate.exists() and not candidate.is_dir():
            raise RemoteAssetError("Download parent path is not a directory")
    if target.resolve() != lexical or not target.resolve().is_relative_to(base):
        raise RemoteAssetError("Download destination escapes its registered cache")
    if target.exists():
        raise RemoteAssetError("Destination already exists; refusing to overwrite it")
    if target.suffix.lower() != Path(asset["filename"]).suffix:
        raise RemoteAssetError("Destination extension must match the registered format")
    return lexical


def _check_content(raw: bytes, asset: dict):
    if sha256(raw).hexdigest() != asset["sha256"]:
        raise RemoteAssetError("SHA256 mismatch; downloaded bytes were not accepted")
    if asset["kind"] == "pdf":
        if not raw.startswith(b"%PDF-"):
            raise RemoteAssetError("Registered PDF signature is missing")
    else:
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != asset["git_blob_sha1"]:
            raise RemoteAssetError("Pinned Git blob does not match")
        try:
            records = json.loads(raw)
            if (type(records) is not list or len(records) != asset["records"]
                    or any(type(r) is not dict or not {"id", "table", "pre_text", "post_text", "qa"} <= r.keys() for r in records)):
                raise RemoteAssetError("Registered FinQA split shape does not match")
        except (ValueError, UnicodeError) as exc:
            raise RemoteAssetError("Registered JSON content is invalid") from exc


def fetch_asset(root: Path, asset_id: str, *, output: Path | None = None) -> dict:
    asset = load_registry(root).get(asset_id)
    if asset is None:
        raise RemoteAssetError("Unknown asset; use --list to inspect the registry")
    target = destination_path(root, asset, output)
    request = Request(asset["url"], headers={"User-Agent": "CreditLens-registered-source/1", "Accept-Encoding": "identity"})
    opener = build_opener(RegisteredRedirects(asset["allowed_hosts"]))
    buffer = BytesIO()
    try:
        with opener.open(request, timeout=60) as response:
            resolved_url = validate_url(response.geturl(), asset["allowed_hosts"])
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) > asset["max_bytes"]):
                raise RemoteAssetError("Declared asset size exceeds the registered limit")
            while True:
                chunk = response.read(min(65536, asset["max_bytes"] + 1 - buffer.tell()))
                if not chunk:
                    break
                buffer.write(chunk)
                if buffer.tell() > asset["max_bytes"]:
                    raise RemoteAssetError("Asset exceeds the registered byte limit")
    except OSError as exc:
        raise RemoteAssetError("Registered source download failed; no file was accepted") from exc
    raw = buffer.getvalue()
    _check_content(raw, asset)
    # No persistent download is created before byte and format validation.
    target = destination_path(root, asset, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = destination_path(root, asset, target)
    created = False
    try:
        with target.open("xb") as stream:
            created = True
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        if created:
            target.unlink(missing_ok=True)
        raise RemoteAssetError("Could not create a new verified cache file") from exc
    return {"asset_id": asset_id, "path": str(target), "sha256": asset["sha256"], "bytes": len(raw),
            "requested_url": asset["url"], "resolved_url": resolved_url, "verified": True,
            "license_note": asset.get("license_note", "Check upstream rights before reuse")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", help="Exact registered asset ID; one explicit asset per download")
    parser.add_argument("--list", action="store_true", help="List registered assets without downloading")
    parser.add_argument("--output", type=Path, help="New file inside .run/downloads; no overwriting")
    args = parser.parse_args(argv)
    try:
        if args.list:
            if args.asset or args.output:
                parser.error("--list cannot be combined with download options")
            print(json.dumps([{k: a[k] for k in ("id", "title", "kind", "max_bytes", "url")} for a in load_registry().values()], indent=2))
        elif args.asset:
            print(json.dumps(fetch_asset(ROOT, args.asset, output=args.output), ensure_ascii=False, indent=2))
        else:
            parser.error("Select one --asset explicitly, or use --list; no download starts by default")
    except RemoteAssetError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
