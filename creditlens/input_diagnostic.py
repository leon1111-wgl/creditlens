"""Publish a reproducible development check of the new input boundary only."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .asset_preflight import AssetPreflightError, inspect_transfer_assets, preflight_transfer_assets
from .diligence import digest
from .document_input import input_error
from .document_transfer import load_manifest, file_hash
from .input_controls import raw_control_cases, printable
from .ocr_input import OcrInputError, inspect_raw_ocr
from scripts.evaluate_word_evidence import adapt_result as legacy_adapter

VERSION="document-input-diagnostic-1"
IMPLEMENTATION_FILES=("creditlens/ocr_input.py","creditlens/asset_preflight.py","creditlens/document_input.py",
 "creditlens/input_controls.py","creditlens/input_diagnostic.py","scripts/preflight_document_input.py",
 "scripts/evaluate_document_inputs.py")
SCOPE={"controls":"fixed_synthetic_development_inputs","independent_human_gold_count":0,
 "model_inference_performed":False,"original_raw_ocr_checked":False,"pdf_image_bytes_verified":False,
 "old_runners_changed":False,"financial_facts_approved":False}


def _attempt(function, *, legacy_errors=False):
    try:
        value=function()
        return {"accepted":True,"error":None,"summary":value.get("summary",value.get("counts"))}
    except (ValueError,TypeError,KeyError,AttributeError,ZeroDivisionError) as exc:
        if not legacy_errors and not isinstance(exc,(OcrInputError,AssetPreflightError)):
            raise  # An unexpected implementation error must fail the control run.
        return {"accepted":False,"error":input_error(exc),"summary":None}


def build_input_diagnostic(root:Path, *, generated_at=None):
    root=Path(root)
    inventory=preflight_transfer_assets(root)
    manifest,packs=load_manifest(root)
    if manifest.snapshot_sha256!=inventory["manifest_sha256"]:
        raise ValueError("Registration changed while capturing input checks")
    rows=[]
    for case in raw_control_cases():
        raw=case["raw"]
        def old():
            page=legacy_adapter(SimpleNamespace(**raw["result"]),**{k:raw[k] for k in (
                "page","width","height","image_width","image_height")})
            return {"summary":{"lines":len(page.lines),"words":sum(len(e.words) for e in page.lines)}}
        previous=_attempt(old,legacy_errors=True)
        current=_attempt(lambda:inspect_raw_ocr(raw))
        rows.append({"id":case["id"],"title":case["title"],"provenance":"synthetic",
                     "expected_accepted":case["expected_accepted"],"input":printable(raw),
                     "legacy":previous,"current":current,"passed":current["accepted"]==case["expected_accepted"]})
    # This source-identity mutation is explicitly synthetic; the registered
    # NVIDIA manifest itself is kept untouched and has no repeated source.
    duplicated=manifest.model_copy(deep=True)
    alias=duplicated.documents[0].model_copy(update={"id":"duplicate-source-alias"},deep=True)
    duplicated.documents.append(alias)
    duplicated_packs={**packs,alias.id:packs[manifest.documents[0].id]}
    assets=[{"id":"registered_inventory","title":"当前登记清单","provenance":"registered_metadata",
             "expected_accepted":True,"current":_attempt(lambda:inspect_transfer_assets(manifest,packs))},
            {"id":"duplicate_source_alias","title":"同一PDF换编号重复登记","provenance":"synthetic",
             "expected_accepted":False,"current":_attempt(lambda:inspect_transfer_assets(duplicated,duplicated_packs))}]
    for case in assets:
        case["passed"]=case["current"]["accepted"]==case["expected_accepted"]
    now=datetime.now(timezone.utc)
    stamp=datetime.fromisoformat(generated_at) if generated_at else now
    if stamp.tzinfo is None or stamp>now or stamp<manifest.frozen_at:
        raise ValueError("Invalid input diagnostic timestamp")
    return {"schema_version":1,"version":VERSION,"generated_at":stamp.isoformat(),"scope":deepcopy(SCOPE),
       "implementation":{p:file_hash(root/p) for p in IMPLEMENTATION_FILES},
       "inventory":inventory,"raw_controls":rows,"asset_controls":assets,
       "summary":{"raw_cases":len(rows),"raw_passed":sum(r["passed"] for r in rows),
                  "raw_expected_accept":sum(r["expected_accepted"] for r in rows),
                  "raw_expected_reject":sum(not r["expected_accepted"] for r in rows),
                  "legacy_accepted_negative_inputs":sum(not r["expected_accepted"] and r["legacy"]["accepted"] for r in rows),
                  "asset_cases":len(assets),"asset_passed":sum(r["passed"] for r in assets)}}


def input_diagnostic_view(root:Path,report:dict):
    if not isinstance(report,dict) or not isinstance(report.get("generated_at"),str):
        raise ValueError("Invalid input diagnostic")
    expected=build_input_diagnostic(root,generated_at=report["generated_at"])
    if report!=expected or digest(report)!=digest(expected):
        raise ValueError("Input diagnostic does not reproduce field by field")
    return {"read_only":True,"report":expected,"validation_scope":"Registered identities and fixed synthetic raw inputs; no source OCR run or financial correctness claim."}
