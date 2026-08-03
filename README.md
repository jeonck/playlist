# Playlist — YouTube English Study

Submit a YouTube link as a GitHub issue → a maintainer approves it → Claude turns
the video's captions into a structured English study post (idioms, vocabulary,
natural phrasing, a fill-in-the-blank quiz, and a mini diary), automatically
classified into a sidebar category. Built with [Hugo](https://gohugo.io) +
[Hextra](https://github.com/imfing/hextra), deployed to GitHub Pages.

## How it works

```
Anyone opens an issue with a YouTube link (.github/ISSUE_TEMPLATE/youtube-request.yml)
        │
        ▼  triage.yml comments explaining the approval flow
A maintainer reviews it and adds the `approved` label
        │
        ▼  process.yml (.github/workflows/process.yml)
  1. Only fires for label "approved"; only proceeds if the label was added by an
     account listed in MAINTAINER_LOGINS (see below) — everyone else gets the
     label removed and a comment explaining why.
  2. Downloads the video's English captions with yt-dlp (no video/audio download).
  3. Sends the transcript to Claude, which writes the study notes AND picks the
     best-fit sidebar category (reusing an existing one when appropriate).
  4. Commits content/docs/<category>/<post>.md and builds + deploys the site,
     all in the same workflow run.
  5. Comments the published URL on the issue and closes it (or comments the
     failure reason — e.g. "no English captions" — and reopens it for retry).
```

There is no daily cron and no fallback content — every post traces back to an
approved YouTube request. Re-approving the same video is a no-op (deduped by
video ID in `pipeline/state.json`, no extra Claude call).

## Who can approve

`.github/workflows/process.yml` checks the label-adder's GitHub username against
a hardcoded allowlist:

```yaml
contains(fromJson('["jeonck"]'), github.event.sender.login)
```

Adding labels already requires write access to the repo, so a random issue author
can't self-approve — but GitHub's "Triage" role can add labels without full write
access, so this allowlist is a second, explicit gate. To add another maintainer,
add their GitHub username to that JSON array in both jobs of `process.yml`.

## One-time setup

1. **Claude Code OAuth token** (subscription auth, not per-token API billing):
   ```
   claude setup-token
   ```
   This opens a browser for authentication. **After** pasting the browser code
   into the terminal, copy the `sk-ant-oat01-...` token it prints (not the
   browser code itself), then:
   ```
   gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo jeonck/playlist
   ```
   Without this secret, `process.yml`'s generate step fails but the rest of the
   site still deploys — register the secret and re-approve the issue to retry.

2. **GitHub Pages**: already enabled to build from GitHub Actions (see deploy
   steps below). No further action needed unless you recreate the repo.

3. **Custom domain** (`playlist.metacog.co.kr`): point a CNAME record for that
   subdomain at `jeonck.github.io`, then confirm with:
   ```
   dig +short playlist.metacog.co.kr CNAME
   ```
   `static/CNAME` and `hugo.toml`'s `baseURL` are already set for this domain.

## Local development

```bash
hugo server -D                        # preview at http://localhost:1313
python3 pipeline/generate.py --url "https://youtu.be/XXXX" --dry-run   # test the pipeline without writing files
```

`pipeline/generate.py` env vars:

| Var | Purpose |
|---|---|
| `JUDGE_BACKEND` | `claude-code` (default if `claude` CLI is on PATH) or `api` |
| `CLAUDE_CODE_OAUTH_TOKEN` | claude-code backend auth in CI (local uses your logged-in session) |
| `ANTHROPIC_API_KEY` | required only for the `api` backend |
| `CLAUDE_MODEL` | default `claude-sonnet-4-6` |

## Repo layout

```
content/
  _index.md           # home page
  docs/
    _index.md         # sidebar root
    <category>/       # created automatically by generate.py
      _index.md
      <post>.md
pipeline/
  generate.py          # transcript → Claude → Hextra content page
  parse_issue.py        # extracts YouTube URL + note from the issue body
  report_issue_outcome.py  # comments/labels/closes the issue based on the run result
  state.json            # dedup (by video ID) + category sidebar weights
.github/
  ISSUE_TEMPLATE/youtube-request.yml
  workflows/
    triage.yml           # comments on newly opened requests
    process.yml           # generate + commit + deploy, gated by the approved label
    deploy.yml             # rebuild on manual content/theme edits
```
