"""Evaluation runner (Person 4).

Runs the same emails through three configurations and reports detection quality,
efficiency and reliability:

  agent           the application: the model chooses the checks
  fixed_pipeline  every check runs on every email, then the model decides once
  llm_only        the model decides from the email alone, without any checks

Run from the project root, with Ollama running:

    python -m evaluation.run_evaluation --configs agent --limit 2        # quick check
    python -m evaluation.run_evaluation                                   # all hand-crafted cases
    python -m evaluation.run_evaluation --zenodo 20                       # plus 20 Zenodo emails
    python -m evaluation.run_evaluation --model qwen3:8b --only 22-legit-mentions-ai,23-legit-password-reset

Every run writes evaluation/results/<date-time>/ with summary.md, metrics.json,
records.csv and records.jsonl. records.jsonl is written after every email, so an
interrupted run (Ctrl+C) still keeps what it finished.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVAL_DIR = Path(__file__).resolve().parent
CASE_FILES = ("eval_cases.json", "eval_cases_extra.json")
ZENODO_CSV = ROOT / "data" / "phishing_validation_emails.csv"
CONFIGS = ("agent", "fixed_pipeline", "llm_only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", nargs="+", choices=CONFIGS, default=list(CONFIGS))
    parser.add_argument("--limit", type=int, help="only the first N hand-crafted cases")
    parser.add_argument("--only", help="comma separated case ids")
    parser.add_argument("--zenodo", type=int, default=0, metavar="N",
                        help="add N emails from the Zenodo dataset (half phishing, half legitimate)")
    parser.add_argument("--seed", type=int, default=42, help="seed of the Zenodo sample")
    parser.add_argument("--no-hand-crafted", action="store_true", help="only the Zenodo sample")
    parser.add_argument("--model", help="Ollama model name (overrides MODEL_NAME)")
    parser.add_argument("--cache", choices=("off", "record", "replay"), default="off",
                        help="evaluation cache of the online checks (replay needs the eval database, see README)")
    parser.add_argument("--suspicious-as", choices=("phishing", "legitimate"), default="phishing",
                        help="how a 'suspicious' verdict is counted (default: phishing)")
    parser.add_argument("--write-history", action="store_true", help="save verdicts in the case history")
    parser.add_argument("--no-domain-server", action="store_true", help="do not start the MCP domain tool server")
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "results", help="results folder")
    return parser.parse_args()


# The environment must be set BEFORE src.config is imported (it reads it once).
ARGS = parse_args() if __name__ == "__main__" else None
if ARGS is not None:
    if ARGS.model:
        os.environ["MODEL_NAME"] = ARGS.model
    os.environ["EVAL_CACHE_MODE"] = ARGS.cache
    os.environ["CASE_HISTORY_WRITE"] = "true" if ARGS.write_history else "false"
    if ARGS.cache != "off":
        eval_db = os.getenv("EVAL_DATABASE_URL", "postgresql://spotthephish:spotthephish@localhost:5432/spotthephish_eval")
        if "eval" not in urlsplit(eval_db).path:
            sys.exit("EVAL_DATABASE_URL must point to a database whose name contains 'eval'.")
        os.environ["DATABASE_URL"] = eval_db

import httpx  # noqa: E402

from src.capabilities import memory  # noqa: E402
from src.capabilities.baselines import BASELINE_NAMES, build_baseline  # noqa: E402
from src.config import config  # noqa: E402
from src.email_parser import parse_email  # noqa: E402
from src.schemas.agent import AnalysisResult, AnalysisStatus  # noqa: E402
from src.services import service_factory as factory  # noqa: E402
from src.services.ai_service import AnalysisService  # noqa: E402


# ---------------------------------------------------------------- cases

def load_hand_crafted(only: str | None, limit: int | None) -> list[dict]:
    cases: list[dict] = []
    for name in CASE_FILES:
        path = EVAL_DIR / name
        if not path.exists():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")):
            file = EVAL_DIR / entry["file"]
            if not file.exists():
                print(f"WARNING: {file} is missing, case {entry['id']} skipped.", file=sys.stderr)
                continue
            cases.append({
                "id": entry["id"], "source": "hand-crafted", "label": entry["label"],
                "category": entry.get("category", ""), "text": file.read_text(encoding="utf-8", errors="replace"),
            })
    if only:
        wanted = {item.strip() for item in only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if limit:
        cases = cases[:limit]
    return cases


def load_zenodo(count: int, seed: int) -> list[dict]:
    if not ZENODO_CSV.exists():
        sys.exit(f"{ZENODO_CSV} not found.")
    csv.field_size_limit(10_000_000)
    rows: dict[str, list[str]] = {"phishing": [], "legitimate": []}
    with ZENODO_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            label = "phishing" if row["Email Type"].strip() == "Phishing Email" else "legitimate"
            rows[label].append(row["Email Text"])
    rng = random.Random(seed)
    per_class = max(1, count // 2)
    cases: list[dict] = []
    for label in ("phishing", "legitimate"):
        for number, text in enumerate(rng.sample(rows[label], per_class), start=1):
            cases.append({"id": f"zenodo-{label[:4]}-{number:02d}", "source": "zenodo",
                          "label": label, "category": "dataset", "text": text})
    rng.shuffle(cases)
    return cases


# ---------------------------------------------------------------- running

def build_services(args: argparse.Namespace) -> dict[str, AnalysisService]:
    components = factory.build_components(start_domain_server=not args.no_domain_server)
    services: dict[str, AnalysisService] = {}
    for name in args.configs:
        analyzer = factory.build_analyzer("agent", components) if name == "agent" else build_baseline(name, components)
        services[name] = AnalysisService(
            parse_email=parse_email,
            main_analyzer=analyzer,
            fallback_analyzer=components.fallback,
            case_recorder=memory.save_verdict if args.write_history else None,
            max_input_chars=components.settings.max_email_chars,
            startup_warnings=components.startup_warnings,
        )
    return services


def to_label(verdict: str, suspicious_as: str) -> str:
    if verdict == "phishing":
        return "phishing"
    if verdict == "suspicious":
        return suspicious_as
    return "legitimate"


def make_record(case: dict, config_name: str, result: AnalysisResult, suspicious_as: str) -> dict:
    verdict = result.verdict
    predicted = to_label(verdict.verdict, suspicious_as) if verdict else None
    return {
        "case": case["id"], "source": case["source"], "category": case["category"],
        "config": config_name, "label": case["label"],
        "status": result.status.value,
        "verdict": verdict.verdict if verdict else None,
        "risk_score": verdict.risk_score if verdict else None,
        "predicted": predicted,
        "correct": (predicted == case["label"]) if predicted else None,
        "fallback": result.status == AnalysisStatus.FALLBACK,
        "latency_s": round(result.duration_ms / 1000, 1),
        "model_calls": result.model_calls,
        "tool_calls": sum(1 for s in result.trace if s.kind == "tool_call"),
        "automatic_checks": sum(1 for s in result.trace if s.kind == "automatic_check"),
        "invalid_outputs": sum(1 for s in result.trace if s.kind == "invalid_output"),
        "safety_rules": ";".join(verdict.safety_rules_applied) if verdict else "",
        "unavailable_tools": ";".join(verdict.unavailable_tools) if verdict else "",
        "note": "" if verdict else result.message,
    }


# ---------------------------------------------------------------- metrics

def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def compute_metrics(records: list[dict]) -> dict:
    answered = [r for r in records if r["predicted"] is not None]
    tp = sum(1 for r in answered if r["label"] == "phishing" and r["predicted"] == "phishing")
    fn = sum(1 for r in answered if r["label"] == "phishing" and r["predicted"] == "legitimate")
    fp = sum(1 for r in answered if r["label"] == "legitimate" and r["predicted"] == "phishing")
    tn = sum(1 for r in answered if r["label"] == "legitimate" and r["predicted"] == "legitimate")
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else None
    latencies = [r["latency_s"] for r in records]
    return {
        "runs": len(records), "answered": len(answered),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": _ratio(tp + tn, len(answered)),
        "precision": precision, "recall": recall, "f1": f1,
        "false_positive_rate": _ratio(fp, fp + tn),
        "fallback_runs": sum(1 for r in records if r["fallback"]),
        "no_answer_runs": len(records) - len(answered),
        "median_latency_s": round(statistics.median(latencies), 1) if latencies else None,
        "mean_model_calls": round(statistics.mean(r["model_calls"] for r in records), 1) if records else None,
        "mean_tool_calls": round(statistics.mean(r["tool_calls"] for r in records), 1) if records else None,
        "runs_with_invalid_output": sum(1 for r in records if r["invalid_outputs"]),
    }


def metrics_by_config(records: list[dict], configs: list[str]) -> dict:
    out: dict = {}
    for name in configs:
        own = [r for r in records if r["config"] == name]
        model_only = [r for r in own if not r["fallback"]]
        out[name] = {"all_runs": compute_metrics(own), "model_runs_only": compute_metrics(model_only)}
    return out


def fmt(value) -> str:
    return "-" if value is None else str(value)


def metrics_table(metrics: dict, key: str) -> list[str]:
    lines = ["| Configuration | Runs | Answered | TP | FP | TN | FN | Accuracy | Precision | Recall | F1 | False-positive rate |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, groups in metrics.items():
        m = groups[key]
        lines.append(f"| {name} | {m['runs']} | {m['answered']} | {m['tp']} | {m['fp']} | {m['tn']} | {m['fn']} | "
                     f"{fmt(m['accuracy'])} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} | "
                     f"{fmt(m['false_positive_rate'])} |")
    return lines


def efficiency_table(metrics: dict) -> list[str]:
    lines = ["| Configuration | Median time per email (s) | Mean model calls | Mean tool calls | "
             "Offline fallbacks | Runs with invalid model output | No answer |",
             "|---|---|---|---|---|---|---|"]
    for name, groups in metrics.items():
        m = groups["all_runs"]
        lines.append(f"| {name} | {fmt(m['median_latency_s'])} | {fmt(m['mean_model_calls'])} | "
                     f"{fmt(m['mean_tool_calls'])} | {m['fallback_runs']} | {m['runs_with_invalid_output']} | "
                     f"{m['no_answer_runs']} |")
    return lines


def case_table(records: list[dict], configs: list[str]) -> list[str]:
    lines = ["| Case | Source | Label | " + " | ".join(configs) + " |",
             "|---|---|---|" + "---|" * len(configs)]
    case_ids: list[str] = []
    for r in records:
        if r["case"] not in case_ids:
            case_ids.append(r["case"])
    for case_id in case_ids:
        own = {r["config"]: r for r in records if r["case"] == case_id}
        first = next(iter(own.values()))
        cells = []
        for name in configs:
            r = own.get(name)
            if r is None:
                cells.append("-")
            elif r["verdict"] is None:
                cells.append("no answer")
            else:
                mark = "ok" if r["correct"] else "WRONG"
                cells.append(f"{r['verdict']} {r['risk_score']:.0f} ({mark}{', fallback' if r['fallback'] else ''})")
        lines.append(f"| {case_id} | {first['source']} | {first['label']} | " + " | ".join(cells) + " |")
    return lines


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def write_results(folder: Path, records: list[dict], meta: dict, configs: list[str]) -> dict:
    metrics = metrics_by_config(records, configs)
    sources = sorted({r["source"] for r in records})
    by_source = {s: metrics_by_config([r for r in records if r["source"] == s], configs) for s in sources}

    lines = ["# Evaluation summary", "",
             f"Model: `{meta['model']}` | Commit: `{meta['commit']}` | Date: {meta['date']} | "
             f"Cache: `{meta['cache']}` | Suspicious counts as: **{meta['suspicious_as']}** | "
             f"Emails: {meta['emails']} | Zenodo seed: {meta['seed']}", "",
             "## Detection quality (all emails)", ""]
    lines += metrics_table(metrics, "all_runs")
    lines += ["", "Offline fallback runs are included above because the user sees that verdict too. "
              "The table below counts only runs where the model really answered.", ""]
    lines += metrics_table(metrics, "model_runs_only")
    for source in sources:
        if len(sources) > 1:
            lines += ["", f"## Detection quality: {source}", ""]
            lines += metrics_table(by_source[source], "all_runs")
    lines += ["", "## Efficiency and reliability", ""]
    lines += efficiency_table(metrics)
    lines += ["", "## Result of every email", ""]
    lines += case_table(records, configs)
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {"meta": meta, "all": metrics, "by_source": by_source}
    (folder / "metrics.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if records:
        with (folder / "records.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    return metrics


def ollama_reachable() -> bool:
    try:
        return httpx.get(config.ollama_base_url.rstrip("/") + "/api/tags", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def main() -> None:
    args = ARGS
    logging.disable(logging.CRITICAL)
    if not ollama_reachable():
        print(f"WARNING: Ollama is not reachable at {config.ollama_base_url}. "
              "Every run will use the offline fallback.", file=sys.stderr)

    cases = [] if args.no_hand_crafted else load_hand_crafted(args.only, args.limit)
    if args.zenodo:
        cases += load_zenodo(args.zenodo, args.seed)
    if not cases:
        sys.exit("No cases selected.")

    folder = args.output / datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"model": config.model_name, "commit": git_commit(), "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "cache": config.eval_cache_mode, "suspicious_as": args.suspicious_as, "emails": len(cases),
            "seed": args.seed, "configs": args.configs}

    services = build_services(args)
    records: list[dict] = []
    total = len(cases) * len(args.configs)
    started = time.perf_counter()
    try:
        with (folder / "records.jsonl").open("a", encoding="utf-8") as log:
            for case in cases:
                for name in args.configs:
                    result = services[name].analyze(case["text"])
                    record = make_record(case, name, result, args.suspicious_as)
                    records.append(record)
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    mark = "ok" if record["correct"] else ("WRONG" if record["correct"] is False else "no answer")
                    print(f"[{len(records)}/{total}] {case['id']:<40} {name:<15} {fmt(record['verdict']):<11} "
                          f"{mark:<6} {record['latency_s']}s{' fallback' if record['fallback'] else ''}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted. Writing the results of the finished runs.", file=sys.stderr)

    if not records:
        sys.exit("No run finished.")
    meta["total_minutes"] = round((time.perf_counter() - started) / 60, 1)
    write_results(folder, records, meta, args.configs)
    print(f"\nResults written to {folder}")
    print((folder / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
