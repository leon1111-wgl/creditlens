/* Shapes of creditlens/ask-pack.json (built by scripts/build_browser_pack.py). */

export type Box = { x0: number; top: number; x1: number; bottom: number };
export type Reason = { code: string; message: string };

export interface Period {
  start: string | null; end: string; days: number | null;
  fiscal_calendar: string; kind: string;
}

export interface ScopeEvidence {
  field: string; value: string; page: number; quote: string; source_sha256: string; note?: string; verification?: string;
}

export interface Observation {
  id: string; fact_id: string; issuer_id: string; company: string; metric: string; label: string; row_label: string;
  year: number; kind: 'duration' | 'instant'; raw_value: string; raw_signed_value: string; normalized_value: string;
  unit: string; transformation: string; source_version_id: string; period: Period;
  basis: Record<string, unknown> & { scope_evidence: ScopeEvidence[] };
  source: { document: string; url: string; sha256: string; page: number; printed_page: number | null; locator: string;
            amount_bbox: Box | null; unit_header: string | null; unit_bbox: Box | null };
  evidence: { method: string; period_header: string; row_label_bbox: Box | null; year_bbox: Box | null; reference: string;
              parser_version?: string; bbox_role?: string };
  ocr: { method: string; outcome: string; reason: string; raw_value: string | null;
         span: { text: string; bbox: Box; confidence: number } | null; unit_text: string | null; unit_check: string | null };
}

export interface PercentObservation {
  id: string; issuer_id: string; company: string; metric: string; label: string; year: number; kind: 'percent';
  unit: 'percent'; exact_percent: string; percent_6dp: string; percent_2dp: string; period: Period;
  input_ids: string[]; rule_version: string; report_case: string;
}

export interface Pack {
  schema_version: number; rule_version: string; years: number[]; currency: string; registered_scale: string;
  scales: Record<string, number>;
  issuers: Record<string, { company: string; name_zh: string; document: string; fiscal_calendar: string; fiscal_year_end: string }>;
  metrics: Record<string, { label: string; kind: 'duration' | 'instant' | 'percent'; statement: string; note: string | null }>;
  observations: Observation[]; percents: PercentObservation[]; limitations: string[];
  pages: Record<string, { width: number; height: number; spans: [string, number, number, number, number][] }>;
  catalog_sha256: string;
}

export interface PlanItem { issuer_id: string; metric: string; year: number }
export interface Unit { currency: string; scale: string | null }
export interface Plan { kind: 'lookup' | 'change' | 'comparable_check' | 'cross_company'; items: PlanItem[]; unit: Unit | null; scope?: string }

export interface ChangeRow {
  [key: string]: unknown;
  status: string; reasons: Reason[]; warnings: Reason[];
}

export interface Value {
  observation_id: string; kind: string; raw_value?: string; normalized_value?: string; unit?: string;
  percent_2dp?: string; percent_6dp?: string; requested: { scale: string; value: string } | null;
}

export interface Answer {
  schema_version: number; rule_version: string; plan: Required<Plan>;
  selected: { selector: PlanItem; status: string }[]; values: Value[];
  comparison: ChangeRow | null; cross_company: { reasons: Reason[]; ranking: null; difference: null } | null;
  status: string; gaps: Reason[]; read_only: true; adopted_by_analyst: false; ranking: null;
}
