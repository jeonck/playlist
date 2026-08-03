#!/usr/bin/env python3
"""Comment on / label / close the source issue based on pipeline/.run-result.json.

Runs unconditionally after the generate step (success or failure — see process.yml's
`if: always()`) so every approved request gets a clear outcome comment, never silence.

Env:
    ISSUE_NUMBER       source issue number
    GITHUB_REPOSITORY  "owner/repo" (set automatically by GitHub Actions)
    GH_TOKEN           token for the gh CLI (set automatically by GitHub Actions)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_FILE = ROOT / "pipeline" / ".run-result.json"
STATUS_LABELS = {"pending-review", "approved", "published", "failed"}


def gh(*args: str, check: bool = True) -> None:
    subprocess.run(["gh", *args], check=check)


def main() -> int:
    number = os.environ["ISSUE_NUMBER"]
    repo = os.environ["GITHUB_REPOSITORY"]

    if RESULT_FILE.exists():
        data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    else:
        data = {
            "status": "failed",
            "reason": "the pipeline exited unexpectedly — check the workflow run log",
        }

    status = data.get("status")
    if status == "success":
        body = f"✅ Published under **{data['category']}**: {data['url']}"
        add, close = "published", True
    elif status == "skipped_duplicate":
        body = (
            f"ℹ️ This video was already published under **{data.get('category', '?')}**: "
            f"{data.get('url', '')}"
        )
        add, close = "published", True
    else:
        reason = data.get("reason", "unknown error")
        body = (
            f"❌ Could not process this video: {reason}\n\n"
            "The `approved` label has been removed. Fix the request (e.g. use a video "
            "that has English captions) and re-add the `approved` label to retry."
        )
        add, close = "failed", False

    gh("issue", "comment", number, "--repo", repo, "--body", body)
    for label in STATUS_LABELS - {add}:
        gh("issue", "edit", number, "--repo", repo, "--remove-label", label, check=False)
    gh("issue", "edit", number, "--repo", repo, "--add-label", add)
    if close:
        gh("issue", "close", number, "--repo", repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
