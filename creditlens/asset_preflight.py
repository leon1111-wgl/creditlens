"""Read-only identity preflight around the immutable transfer protocol.

PDF/page hashes are registrations, not proof that original bytes are present or
authentic. Same-issuer revisions are permitted when their content differs.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from .date_diagnostic import read_json
from .diligence import digest
from .document_transfer import TransferManifest, TransferPack, load_manifest

VERSION = "transfer-asset-preflight-1"


class AssetPreflightError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code, self.path = code, path
        super().__init__(f"{code} at {path}: {message}")


def _require(condition, code, path, message):
    if not condition:
        raise AssetPreflightError(code, path, message)


def _model(value, cls, path):
    data = value.model_dump(mode="json") if isinstance(value, cls) else value
    _require(isinstance(data, dict), "invalid_inventory", path, "Expected a parsed object")
    _require(type(data.get("schema_version")) is int, "invalid_inventory", path+".schema_version", "Schema version must be an integer")
    try:
        return cls.model_validate(data)
    except (ValueError, TypeError) as exc:
        raise AssetPreflightError("invalid_inventory", path, "Parsed registration violates the transfer contract") from exc


def _unique(seen, value, code, path, message):
    _require(value not in seen, code, path, message)
    seen.add(value)


def inspect_transfer_assets(
    manifest: TransferManifest | dict,
    packs: Mapping[str, TransferPack | dict],
    *, resolved_pack_paths: Mapping[str, str] | None = None,
) -> dict:
    """Pure inspection of parsed registrations; no files, network or models.

Optional resolved paths are caller-supplied identity claims. Only the wrapper
below can say it resolved filesystem aliases and verified registered bytes.
"""
    frozen = _model(manifest, TransferManifest, "manifest")
    ids = {d.id for d in frozen.documents}
    _require(isinstance(packs, Mapping) and set(packs) == ids, "pack_inventory_mismatch", "packs", "Every document needs exactly one pack")
    if resolved_pack_paths is not None:
        _require(isinstance(resolved_pack_paths, Mapping) and set(resolved_pack_paths) == ids
                 and all(isinstance(v, str) and v for v in resolved_pack_paths.values()),
                 "path_inventory_mismatch", "resolved_pack_paths", "Resolved path claims must cover all documents")
    pdfs, raw_packs, canonical_packs, paths, resolved, source_pages, cells, issuers = (set() for _ in range(8))
    documents, fact_count, image_groups = [], 0, {}
    for index, doc in enumerate(frozen.documents):
        base = f"documents[{index}]"
        # A changed label, company name or doc.id cannot create a second asset.
        _unique(pdfs, doc.pdf.sha256, "duplicate_pdf_content", base+".pdf.sha256", "Same registered PDF bytes cannot count as another document")
        _unique(paths, doc.pack_path, "duplicate_pack_path", base+".pack_path", "Reference path is already registered")
        _unique(raw_packs, doc.pack_sha256, "duplicate_pack_raw_content", base+".pack_sha256", "Identical reference bytes cannot count twice")
        if resolved_pack_paths is not None:
            identity = resolved_pack_paths[doc.id].replace("\\", "/").rstrip("/").casefold()
            _unique(resolved, identity, "duplicate_pack_path_alias", base+".pack_path", "Reference paths resolve to the same file")
        pack = _model(packs[doc.id], TransferPack, f"packs[{doc.id}]")
        canonical_hash = digest(pack.model_dump(mode="json"))
        _unique(canonical_packs, canonical_hash, "duplicate_pack_content", base+".pack_path", "Formatting changes do not create new reference content")
        source = pack.sources[0]
        _require(source.sha256 == doc.pdf.sha256 and str(source.url) == str(doc.pdf.url)
                 and source.underlying_filing_date == doc.pdf.underlying_filing_date
                 and source.wrapper_publication_date == doc.pdf.wrapper_published_at,
                 "source_binding_mismatch", base+".pdf", "Pack and PDF/source-date registration disagree")
        issuers.add(pack.entity_id)
        by_id = {f.id: f for f in pack.facts}
        page_numbers, registered_facts, pages = set(), set(), []
        for page_index, page in enumerate(doc.pages):
            page_path = base+f".pages[{page_index}]"
            _unique(page_numbers, page.page, "duplicate_page_registration", page_path+".page", "A physical page cannot be counted twice")
            page_key = (doc.pdf.sha256, page.page)
            _unique(source_pages, page_key, "duplicate_source_page", page_path+".page", "Source page is already registered")
            fact_items = []
            for fact_index, fid in enumerate(page.fact_ids):
                fact_path = page_path+f".fact_ids[{fact_index}]"
                _unique(registered_facts, fid, "duplicate_fact_registration", fact_path, "A fact must appear on exactly one page")
                _require(fid in by_id, "unknown_fact", fact_path, "Page references an unregistered fact")
                fact = by_id[fid]
                loc = fact.pdf_location
                _require(fact.source_id == source.id and loc is not None and
                         (loc.page, loc.page_width, loc.page_height) == (page.page, page.width, page.height),
                         "fact_binding_mismatch", fact_path, "Fact source, physical page or dimensions disagree")
                # Identity is spatial source evidence, never the financial value.
                cell = {"source_pdf_sha256": source.sha256, "page": page.page,
                        "amount_bbox": loc.amount_bbox.model_dump(mode="json")}
                cell_hash = digest(cell)
                _unique(cells, cell_hash, "duplicate_fact_evidence", fact_path,
                        "The same amount cell cannot be counted again under another fact name")
                fact_items.append({"fact_id": fid, "global_fact_id": f"{pack.entity_id}:{source.sha256}:{fid}",
                                   "cell_identity_sha256": cell_hash})
            pages.append({**page.model_dump(mode="json"), "source_page_id": f"{source.sha256}:page:{page.page}", "facts": fact_items})
            image_groups.setdefault(page.image_sha256, []).append({"document_id": doc.id,
                "source_pdf_sha256": source.sha256, "page": page.page,
                "source_page_id": f"{source.sha256}:page:{page.page}"})
        _require(registered_facts == set(by_id), "fact_inventory_mismatch", base+".pages", "All and only the pack facts must be counted")
        fact_count += len(registered_facts)
        documents.append({"id": doc.id, "entity_id": pack.entity_id, "source_id": source.id,
                          "source_pdf_sha256": source.sha256, "pack_path": doc.pack_path,
                          "pack_sha256": doc.pack_sha256, "pack_canonical_sha256": canonical_hash, "pages": pages})
    return {"schema_version": 1, "rule_version": VERSION, "status": "passed", "experiment_id": frozen.experiment_id,
            "manifest_sha256": None,
            "counts": {"documents": len(documents), "unique_pdf_assets": len(pdfs), "unique_reference_packs": len(canonical_packs),
                       "unique_source_pages": len(source_pages), "unique_page_images": len(image_groups),
                       "unique_fact_evidence": fact_count, "unique_issuers": len(issuers)},
            "documents": documents,
            "same_page_content_groups": [{"image_sha256": key, "registrations": value} for key, value in image_groups.items() if len(value) > 1],
            "validation": {"mode": "parsed_inventory", "registered_files_checked": False, "strict_json_checked": False,
                           "pack_bytes_checked": False, "path_aliases_checked": False, "pdf_bytes_checked": False},
            "limitations": ["PDF and page-image hashes are registered identities; original bytes and document authenticity were not checked.",
                            "Parsed-only inspection cannot verify disk bytes or caller-supplied resolved-path claims.",
                            "Distinct PDF versions of one issuer are separate assets, not independent issuers or evidence of generalization.",
                            "Unique issuers count registered entity_id values, not verified entity resolution. Shared registered image hashes do not establish independent page content.",
                            "Unique fact evidence means unique registered amount-cell locations within each PDF version, not completeness or independent corroboration of financial evidence.",
                            "Equal monetary values on distinct registered cells are not duplicates."]}


def _strict_read(path, limit, label):
    try:
        return read_json(path, limit=limit)
    except (ValueError, OSError, UnicodeError) as exc:
        raise AssetPreflightError("invalid_registered_json", label, "Registered JSON is unavailable, oversized, duplicated-key or non-finite") from exc


def preflight_transfer_assets(root: Path, manifest_path: Path | None = None) -> dict:
    """Validate frozen legacy bindings, then reject duplicate sample identities."""
    root = Path(root).resolve()
    selected = Path(manifest_path) if manifest_path is not None else Path("configs/document-transfer.json")
    path = selected if selected.is_absolute() else root/selected
    try:
        frozen, packs = load_manifest(root, path)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise AssetPreflightError("legacy_binding_validation_failed", "manifest", "Existing frozen source, code, profile or pack binding failed") from exc
    # Follow the old loader with strict reads of the exact snapshot it accepted;
    # never combine current bytes with a different parsed manifest or pack.
    raw, parsed = _strict_read(path, 1_000_000, "manifest")
    _require(sha256(raw).hexdigest() == frozen.snapshot_sha256, "source_changed_during_preflight", "manifest", "Manifest snapshot changed")
    strict_manifest = _model(parsed, TransferManifest, "manifest")
    _require(digest(strict_manifest.model_dump(mode="json")) == digest(frozen.model_dump(mode="json")),
             "source_changed_during_preflight", "manifest", "Parsed manifest is not the validated snapshot")
    resolved_paths = {}
    for index, doc in enumerate(frozen.documents):
        label = f"documents[{index}].pack_path"
        reference = (root/doc.pack_path).resolve()
        _require(reference.is_relative_to((root/"data/examples").resolve()), "pack_path_escape", label, "Reference path escaped examples")
        pack_raw, pack_json = _strict_read(reference, 5_000_000, label)
        _require(sha256(pack_raw).hexdigest() == doc.pack_sha256, "source_changed_during_preflight", label, "Reference bytes changed")
        strict_pack = _model(pack_json, TransferPack, label)
        _require(digest(strict_pack.model_dump(mode="json")) == digest(packs[doc.id].model_dump(mode="json")),
                 "source_changed_during_preflight", label, "Reference is not the validated snapshot")
        resolved_paths[doc.id] = str(reference)
    result = inspect_transfer_assets(frozen, packs, resolved_pack_paths=resolved_paths)
    result["manifest_sha256"] = frozen.snapshot_sha256
    result["validation"] = {"mode": "registered_manifest_and_packs", "registered_files_checked": True,
                            "strict_json_checked": True, "pack_bytes_checked": True,
                            "path_aliases_checked": True, "pdf_bytes_checked": False}
    return result
