#!/usr/bin/env python3
"""Extract youtube_url/note from a GitHub issue-form body and write step outputs.

Reads the raw body from the ISSUE_BODY env var (never interpolated into a shell
string — untrusted user input passed as a `run:` env var is safe; splicing it
into the script text would not be) and appends the two fields to $GITHUB_OUTPUT
using the multiline-safe heredoc format, since "Notes" can contain newlines.
"""
import os
import re
import uuid

FIELD_LABELS = {
    "youtube_url": "YouTube URL",
    "note": "Notes for the study post (optional)",
}


def extract_field(body: str, label: str) -> str:
    pattern = re.compile(
        rf"^###\s*{re.escape(label)}\s*\n+(.*?)(?=\n###|\Z)", re.MULTILINE | re.DOTALL
    )
    m = pattern.search(body)
    if not m:
        return ""
    value = m.group(1).strip()
    return "" if value.lower() == "_no response_" else value


def main() -> None:
    body = os.environ.get("ISSUE_BODY", "")
    out_path = os.environ["GITHUB_OUTPUT"]
    with open(out_path, "a", encoding="utf-8") as f:
        for key, label in FIELD_LABELS.items():
            value = extract_field(body, label)
            delim = f"EOF_{uuid.uuid4().hex}"
            f.write(f"{key}<<{delim}\n{value}\n{delim}\n")


if __name__ == "__main__":
    main()
