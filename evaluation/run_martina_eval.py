"""Evaluation runner for the domain checks and the case history (Person 3).

Runs every case in martina_test_cases.json several times through the real
AnalysisService (agent + MCP domain server + PostgreSQL) and grades each trial
with code-based checks (verdict, risk range, tool results, history, failure
handling). Nothing is graded by the model itself.

Run from the project root, with Postgres and Ollama running:

python -m evaluation.run_martina_eval --runs 3 --write-cases

The runner only ever uses the EVALUATION database (EVAL_DATABASE_URL), because
it truncates the case history before every trial.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Environment must be set BEFORE src.config is imported (it reads the variables once)
EVAL_DB = os.getenv("EVAL_DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish_eval")
if "eval" not in urlsplit(EVAL_DB).path:
    sys.exit("EVAL_DATABASE_URL must point to a database whose name contains 'eval' "
             "(the runner truncates the case history).")
os.environ["DATABASE_URL"] = EVAL_DB
os.environ["EVAL_CACHE_MODE"] = "replay"
os.environ["CASE_HISTORY_WRITE"] = "true"

import logging

import httpx

from evaluation import seed_cache
from src.capabilities import memory
from src.config import config
from src.email_parser import parse_email
from src.exceptions import McpServerUnavailableError
from src.schemas.agent import AnalysisResult, AnalysisStatus
from src.schemas.responses import Verdict
import src.services.service_factory as factory

class _ReasonCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.reasons: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.reasons.append(record.getMessage())


COLLECTOR = _ReasonCollector()

BAD_DB = "postgresql://spotthephish:spotthephish@127.0.0.1:1/none"
LEAK_MARKERS = ("Traceback", "psycopg", "OperationalError", "Exception", "postgresql://", "key=", "x-goog-api-key", "AIza")
CASES_FILE = Path(__file__).with_name("martina_test_cases.json")
RESULTS_FILE = Path(__file__).with_name("results") / "martina_results.json"

_services: dict = {}


def _server_cannot_start(*args, **kwargs):
    raise McpServerUnavailableError()


def service_for(scenario: str):
    key = scenario if scenario in ("db_down", "mcp_down") else "normal"
    if key not in _services:
        previous_url = os.environ["DATABASE_URL"]
        original_client = factory.DomainToolsClient
        if key == "db_down":
            os.environ["DATABASE_URL"] = BAD_DB
        if key == "mcp_down":
            factory.DomainToolsClient = _server_cannot_start
        try:
            _services[key] = factory.build_analysis_service()
        finally:
            os.environ["DATABASE_URL"] = previous_url
            factory.DomainToolsClient = original_client
    return _services[key]


def prepare_state(grading: dict) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        seed_cache.main(reset_history=True)
    if grading.get("scenario") == "empty_cache":
        with memory.db() as conn:
            conn.execute("TRUNCATE eval_cache")
    stored = 0
    for name in grading.get("history_precondition", []):
        email = parse_email((ROOT / "evaluation" / "cases" / f"{name}.eml").read_text(encoding="utf-8"))
        verdict = Verdict(verdict="phishing", risk_score=85, explanation="earlier case", recommended_action="none")
        stored += int(memory.save_verdict(email, verdict))
    return stored


def history_rows() -> int:
    with memory.db() as conn:
        return conn.execute("SELECT count(*) AS n FROM case_history").fetchone()["n"]


def grade(g: dict, result: AnalysisResult, new_rows: int) -> dict[str, bool]:
    verdict = result.verdict
    checks: dict[str, bool] = {"verdict_present": verdict is not None}
    if verdict is not None:
        if "verdict_in" in g:
            checks["verdict_in"] = verdict.verdict in g["verdict_in"]
        if "min_risk" in g:
            checks["min_risk"] = verdict.risk_score >= g["min_risk"]
        if "max_risk" in g:
            checks["max_risk"] = verdict.risk_score <= g["max_risk"]
        for tool in g.get("evidence_cites", []):
            checks[f"evidence_cites:{tool}"] = any(f" {tool}]" in item for item in verdict.evidence)
        for tool in g.get("unavailable_includes", []):
            checks[f"unavailable:{tool}"] = tool in verdict.unavailable_tools
    observed = [(o.result.tool_name, o.result.status) for o in result.observations]
    for tool, status in g.get("require_observations", []):
        checks[f"observed:{tool}={status}"] = (tool, status) in observed
    if g.get("no_warning_observations"):
        checks["no_warning_observations"] = all(status != "warning" for _, status in observed)
    for text in g.get("warning_contains", []):
        checks[f"warning:{text[:25]}"] = any(text in w for w in result.warnings)
    if "expect_new_history_rows" in g and result.status == AnalysisStatus.COMPLETED:
        checks["history_row_stored"] = new_rows == g["expect_new_history_rows"]
    shown = " ".join([result.message, *result.warnings] +
                     ([verdict.explanation, *verdict.evidence] if verdict else []))
    checks["no_error_leak"] = not any(marker in shown for marker in LEAK_MARKERS)
    return checks


def run_trial(case: dict, number: int) -> dict:
    g = case["grading"]
    raw = (ROOT / case["input"]).read_text(encoding="utf-8")
    scenario = g.get("scenario", "normal")
    service = service_for(scenario)
    before = prepare_state(g)
    original_url = config.database_url
    if scenario == "db_down":
        config.database_url = BAD_DB
    try:
        COLLECTOR.reasons.clear()
        result = service.analyze(raw)
    finally:
        config.database_url = original_url
    new_rows = history_rows() - before
    checks = grade(g, result, new_rows)
    verdict = result.verdict
    return {
        "case": case["id"], "trial": number, "status": result.status.value,
        "valid_for_model_eval": result.status == AnalysisStatus.COMPLETED,
        "verdict": verdict.verdict if verdict else None,
        "risk": verdict.risk_score if verdict else None,
        "safety_rules": verdict.safety_rules_applied if verdict else [],
        "tool_calls": [s.tool_name for s in result.trace if s.kind == "tool_call"],
        "model_calls": result.model_calls, "duration_ms": result.duration_ms,
        "unavailable_tools": verdict.unavailable_tools if verdict else [],
        "warnings": result.warnings, "checks": checks,
        "fallback_reason": COLLECTOR.reasons[-1] if COLLECTOR.reasons else "",
        "failed_checks": [name for name, ok in checks.items() if not ok],
        "passed": all(checks.values()),
    }


def summarise(cases: list[dict], trials: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        own = [t for t in trials if t["case"] == case["id"]]
        valid = [t for t in own if t["valid_for_model_eval"]]
        passed = [t for t in valid if t["passed"]]
        failed = sorted({c for t in valid for c in t["failed_checks"]})
        rows.append({
            "id": case["id"], "category": case["category"],
            "critical": bool(case["grading"].get("critical")),
            "runs": len(own), "valid": len(valid), "passed": len(passed),
            "fallback": len(own) - len(valid),
            "verdicts": sorted({t["verdict"] for t in valid if t["verdict"]}),
            "median_tool_calls": statistics.median([len(t["tool_calls"]) for t in valid]) if valid else None,
            "median_seconds": round(statistics.median([t["duration_ms"] for t in valid]) / 1000, 1) if valid else None,
            "failed_checks": failed,
        })
    return rows


def print_markdown(rows: list[dict], meta: dict) -> None:
    print(f"\nModel: {meta['model']} | commit: {meta['commit']} | date: {meta['date']} | "
          f"runs per case: {meta['runs']} | eval cache: {meta['eval_cache_mode']}\n")
    print("| Case | Category | Passed (model runs) | Fallback runs | Verdicts | Median tool calls | Median s | Failed checks |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        flag = " **(critical)**" if r["critical"] else ""
        passed = f"{r['passed']}/{r['valid']}" if r["valid"] else "not run (no model)"
        print(f"| {r['id']}{flag} | {r['category']} | {passed} | {r['fallback']} | "
              f"{', '.join(r['verdicts']) or '-'} | {r['median_tool_calls']} | {r['median_seconds']} | "
              f"{', '.join(r['failed_checks']) or '-'} |")
    by_id = {r["id"]: r for r in rows}
    first, repeat = by_id.get("martina-04"), by_id.get("martina-05")
    if first and repeat and first["median_tool_calls"] is not None and repeat["median_tool_calls"] is not None:
        print(f"\nCampaign repeat: median tool calls {repeat['median_tool_calls']} (martina-05) vs "
              f"{first['median_tool_calls']} (martina-04).")
    critical_failed = [r["id"] for r in rows if r["critical"] and r["valid"] and r["passed"] < r["valid"]]
    print("Critical cases with at least one failed run: " + (", ".join(critical_failed) or "none"))


def write_back(cases_file: Path, cases: list[dict], rows: list[dict]) -> None:
    by_id = {r["id"]: r for r in rows}
    for case in cases:
        r = by_id.get(case["id"])
        if r is None:
            continue
        if not r["valid"]:
            case["actual_result"], case["status"] = "Not run with a model (fallback only).", "pending"
            continue
        case["status"] = "pass" if r["passed"] == r["valid"] else "partial" if r["passed"] else "fail"
        case["actual_result"] = (f"{r['passed']}/{r['valid']} runs passed. Verdicts: {', '.join(r['verdicts'])}. "
                                 f"Median tool calls: {r['median_tool_calls']}. "
                                 + (f"Failed checks: {', '.join(r['failed_checks'])}." if r["failed_checks"] else ""))
    cases_file.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ollama_reachable() -> bool:
    try:
        return httpx.get(config.ollama_base_url.rstrip("/") + "/api/tags", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False

def print_totals(trials: list[dict], cases: list[dict]) -> None:
    category = {c["id"]: c["category"] for c in cases}
    stats: dict[str, list[int]] = {}
    for t in trials:
        s = stats.setdefault(category[t["case"]], [0, 0, 0]) # valid, passed, fallback
        if t["valid_for_model_eval"]:
            s[0] += 1
            s[1] += int(t["passed"])
        else:
            s[2] += 1
    print("\n| Category | Valid runs | Passed | Failed | Pass rate | Fallback runs |")
    print("|---|---:|---:|---:|---:|---:|")
    total = [0, 0, 0]
    for name, (valid, passed, fb) in sorted(stats.items()):
        rate = f"{100 * passed / valid:.0f}%" if valid else "n/a"
        print(f"| {name} | {valid} | {passed} | {valid - passed} | {rate} | {fb} |")
        total = [a + b for a, b in zip(total, (valid, passed, fb))]
    valid, passed, fb = total
    rate = f"{100 * passed / valid:.0f}%" if valid else "n/a"
    print(f"| **All** | {valid} | {passed} | {valid - passed} | {rate} | {fb} |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="trials per case (default 3)")
    parser.add_argument("--cases", type=Path, default=CASES_FILE)
    parser.add_argument("--only", help="comma separated case ids, e.g. martina-04,martina-05")
    parser.add_argument("--out", type=Path, default=RESULTS_FILE)
    parser.add_argument("--write-cases", action="store_true", help="write actual_result and status into the cases file")
    parser.add_argument("--require-model", action="store_true", help="abort when Ollama is not reachable")
    args = parser.parse_args()
    logging.getLogger().addHandler(logging.NullHandler())
    ai_logger = logging.getLogger("src.services.ai_service")
    ai_logger.addHandler(COLLECTOR)
    ai_logger.propagate = False

    if not ollama_reachable():
        message = f"Ollama is not reachable at {config.ollama_base_url}: every run would use the offline fallback."
        if args.require_model:
            sys.exit(message)
        print("WARNING: " + message, file=sys.stderr)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    selected = [c for c in cases if not args.only or c["id"] in args.only.split(",")]
    trials = []
    for case in selected:
        for number in range(1, args.runs + 1):
            trial = run_trial(case, number)
            trials.append(trial)
            print(f"{case['id']} run {number}: {trial['status']:<9} {trial['verdict']} "
                  f"{'PASS' if trial['passed'] else 'FAIL ' + str(trial['failed_checks'])}", file=sys.stderr)

    rows = summarise(selected, trials)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=False).stdout.strip() or "unknown"
    except OSError:
        commit = "unknown"
    meta = {"model": config.model_name, "commit": commit, "runs": args.runs,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "eval_cache_mode": config.eval_cache_mode}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"meta": meta, "summary": rows, "trials": trials}, indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
    print_markdown(rows, meta)
    print_totals(trials, selected)
    if args.write_cases:
        write_back(args.cases, cases, rows)


if __name__ == "__main__":
    main()