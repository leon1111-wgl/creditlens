"""Export a research snapshot with original operands and disclosure cutoffs."""
from datetime import date
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.temporal import TemporalPack, quarterly_snapshot


def render_snapshot(pack: TemporalPack, result: dict) -> str:
    lines = [f"# {pack.company} 季度数据快照", "", f"研究截止日：{result['as_of']}。财年开始：{pack.fiscal_year_start}。",
             "", "公告日次日才视为可用；网页是当前抓取，不能证明历史版本。推导值需要确认更正与合并范围后才能用于研究。",
             "", "| 财季 | 指标 | 数值 | 单位 | 状态 |", "|---|---|---:|---|---|"]
    labels = {"cfo": "经营现金流", "capex": "固定资产现金支出", "net_income": "净利润"}
    units = {"USD_million": "百万美元", "USD": "美元", "CNY_million": "百万元人民币"}
    status = {"reported": "披露值", "derived_requires_review": "累计相减，待复核", "blocked": "口径冲突，已阻断", "missing": "缺少可用数据"}
    for row in result["quarters"]:
        lines.append(f"| Q{row['quarter']} | {labels[row['metric']]} | {row['value'] if row['value'] is not None else '—'} | {units.get(row['unit'], '—')} | {status[row['status']]} |")
    lines.extend(["", "来源、原始值、期间、公式和具体检查见同名 JSON。当前没有训练标签，也没有预测或投资收益结果。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "data/examples/msft-fy2025-temporal.json")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2025, 7, 31))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/quarterly-snapshot.json")
    args = parser.parse_args()
    pack = TemporalPack.model_validate_json(args.pack.read_text(encoding="utf-8"))
    result = quarterly_snapshot(pack, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_snapshot(pack, result), encoding="utf-8")
    print(f"Exported {len(result['quarters'])} quarterly slots; training readiness: false.")


if __name__ == "__main__":
    main()
