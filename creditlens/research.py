"""Read-only research views over registered public disclosure examples."""
from collections import Counter
from datetime import date

from . import __version__
from .temporal import TemporalPack, quarterly_snapshot


def research_view(pack: TemporalPack, as_of: date) -> dict:
    result = quarterly_snapshot(pack, as_of)
    sources = result["evidence"]["sources"]
    counts = Counter(row["status"] for row in result["quarters"])
    return {
        "view_version": 1,
        "application_version": __version__,
        "company": pack.company,
        "fiscal_year_start": pack.fiscal_year_start.isoformat(),
        "dataset_scope": "registered_public_historical_example",
        "is_live": False,
        "quality": {key: counts[key] for key in ("reported", "derived_requires_review", "missing", "blocked")},
        "freshness": {
            "latest_visible_publication": max((s["published_at"] for s in sources), default=None),
            "visible_source_captures": [{"source_id": s["id"], "retrieved_at": s["retrieved_at"]} for s in sources],
            "historical_vintage_verified": False,
        },
        "snapshot": result,
    }
