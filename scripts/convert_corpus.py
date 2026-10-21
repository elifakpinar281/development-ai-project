"""Corpus conversion utility for SpotThePhish.

Reads raw email corpora (e.g. Nazario phishing corpus or SpamAssassin public corpus)
from a directory or file, runs src.email_parser over each email, and saves a structured benchmark dataset for the evaluation runner.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from src.email_parser import parse_email


def convert_raw_directory(input_dir: Path, label: str) -> List[Dict[str, Any]]:
    """Parses all raw .eml or text files in a directory into structured records."""
    records = []
    files = sorted([p for p in input_dir.iterdir() if p.is_file()])

    for idx, file_path in enumerate(files):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_email(content)
            records.append({
                "id": f"{label}-{idx:04d}",
                "filename": file_path.name,
                "label": label,
                "has_headers": parsed.has_headers,
                "subject": parsed.subject,
                "sender": parsed.sender.model_dump(),
                "body_plain": parsed.body_plain[:2000],
                "links_count": len(parsed.links),
                "attachments_count": len(parsed.attachments),
                "links": [l.model_dump() for l in parsed.links],
                "attachments": [a.model_dump() for a in parsed.attachments],
            })
        except Exception as err:
            print(f"Warning: Failed to parse {file_path.name}: {err}")

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert raw email corpora into structured evaluation datasets.")
    parser.add_argument("--input", type=Path, default=Path("data/raw"), help="Path to raw email files or directory.")
    parser.add_argument("--output", type=Path, default=Path("data/evaluation_corpus.json"), help="Output JSON path.")
    parser.add_argument("--label", type=str, default="phishing", choices=["phishing", "legitimate"], help="Corpus label.")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    if args.input.is_dir():
        records = convert_raw_directory(args.input, args.label)
    else:
        print(f"Input path '{args.input}' does not exist or is not a directory.")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"Successfully converted {len(records)} emails -> {args.output}")


if __name__ == "__main__":
    main()