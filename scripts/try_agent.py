from __future__ import annotations

import argparse
import email
import email.policy
import importlib
import logging
import re
import sys
from pathlib import Path

from src.exceptions import InvalidEmailInputError
from src.schemas.responses import EmailAddress, LinkInfo, ParsedEmail
from src.services.ai_service import format_result_as_markdown
from src.services.service_factory import build_analysis_service

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


def stand_in_parser(text: str) -> ParsedEmail:
    message = email.message_from_string(text, policy=email.policy.default)
    if not message.keys():
        raise InvalidEmailInputError("no headers", "This stand-in parser needs an email with headers.")

    def address(header: str):
        value = message.get(header)
        if not value:
            return None
        name, addr = email.utils.parseaddr(str(value))
        return EmailAddress(display_name=name, address=addr, domain=addr.rsplit("@", 1)[-1].lower() if "@" in addr else "")

    body_part = message.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""

    links = []
    for url in dict.fromkeys(_URL_RE.findall(body)):
        host = url.split("//", 1)[1].split("/", 1)[0].lower()
        links.append(LinkInfo(url=url, display_text=url, domain=host))

    return ParsedEmail(
        raw_text=text,
        subject=str(message.get("Subject", "")),
        sender=address("From") or EmailAddress(),
        reply_to=address("Reply-To"),
        return_path=address("Return-Path"),
        date=str(message.get("Date", "")) or None,
        body_plain=body,
        links=links,
        has_headers=True,
    )

def pick_parser():
    try:
        module = importlib.import_module("src.email_parser")
        if hasattr(module, "parse_email"):
            return module.parse_email
    except ImportError:
        pass
    print("(Person 1's parser not found. Using the stand-in parser.)\n", file=sys.stderr)
    return stand_in_parser


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SpotThePhish on one .eml file.")
    parser.add_argument("eml_file", type=Path)
    parser.add_argument("--json", action="store_true", help="print the full AnalysisResult as JSON")
    parser.add_argument("--verbose", action="store_true", help="show log messages")
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING)

    service = build_analysis_service(parse_email=pick_parser())
    result = service.analyze(arguments.eml_file.read_text(encoding="utf-8", errors="replace"))

    if arguments.json:
        print(result.model_dump_json(indent=2))
    else:
        print(format_result_as_markdown(result))
        print(f"\n[{result.status.value}, {result.model_calls} model calls, {result.duration_ms} ms]")


if __name__ == "__main__":
    main()
