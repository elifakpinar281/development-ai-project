from __future__ import annotations
 
import argparse
import glob
import logging
import sys
from pathlib import Path
 
from src.services.ai_service import format_result_as_text
from src.services.service_factory import build_analysis_service
 
 
def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if not matches:
            print(f"No file matches: {pattern}", file=sys.stderr)
        for match in matches:
            paths.append(Path(match))
    return paths
 
 
def main() -> None:
    parser = argparse.ArgumentParser(description="Run SpotThePhish on .eml files.")
    parser.add_argument("eml_files", nargs="+", help="files or patterns such as evaluation/cases/*.eml")
    parser.add_argument("--json", action="store_true", help="print the full AnalysisResult as JSON")
    parser.add_argument("--no-domain-server", action="store_true", help="do not start the MCP domain tool server")
    parser.add_argument("--verbose", action="store_true", help="show log messages")
    arguments = parser.parse_args()
 
    paths = expand_paths(arguments.eml_files)
    if not paths:
        sys.exit(1)
 
    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING)
    service = build_analysis_service(start_domain_server=not arguments.no_domain_server)
 
    for path in paths:
        result = service.analyze(path.read_text(encoding="utf-8", errors="replace"))
        print(f"\n===== {path}")
        if arguments.json:
            print(result.model_dump_json(indent=2))
        else:
            print(format_result_as_text(result))
            print(f"\n[{result.status.value}, {result.model_calls} model calls, {result.duration_ms} ms]")
 
 
if __name__ == "__main__":
    main()
