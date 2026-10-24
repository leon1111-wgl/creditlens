"""A reviewed source-cell calculation, not an automatically generated formula."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.service import ResearchService

service = ResearchService(ROOT / "data/raw/finqa-dev.json", ROOT / "data/audit.sqlite")
report = service.analyze(
    "V/2008/page_17.pdf-1",
    "what is the average payment volume per transaction for american express?",
    steps=[{"op": "divide", "left": "cell:table_3:1", "right": "cell:table_3:3"}],
)
report["demo_note"] = "Analyst-authored formula. Both source columns are in billions; arithmetic is 637 / 5.0."
out = ROOT / "artifacts/demo-report.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"report_id": report["id"], "value": report["calculation"]["value"],
                  "path": str(out), "status": report["status"]}, indent=2))
