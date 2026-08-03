#!/usr/bin/env python3
"""YouTube → structured English study post, organized into a Hextra sidebar category.

Given a YouTube URL (and optional note), this script:
  1. Downloads the video's English captions (manual if available, else auto-generated)
     via yt-dlp — no video/audio download.
  2. Sends the cleaned transcript to Claude, which analyzes it AND picks the best-fit
     study category (reusing an existing sidebar category when one fits).
  3. Writes a Hextra content page under content/docs/<category-slug>/<post-slug>.md with:
       - a video embed
       - 💬 Idioms (meaning + 2 examples)
       - 📚 Vocabulary to Remember
       - 🔧 Say It Naturally (a phrase actually used in the video vs. a common learner
         mistake for the same idea)
       - ✅ Check Yourself (fill-in-the-blank toggle quiz)
       - ✍️ Mini Diary (one short story using the studied expressions)
  4. Always writes pipeline/.run-result.json describing the outcome (success/failed +
     reason) so the calling GitHub Actions workflow can comment on the source issue
     without scraping logs.

This script is invoked per-approved-issue by .github/workflows/process.yml — there is
no daily cron and no fallback content; every post traces back to an approved YouTube
request.

Usage:
    python pipeline/generate.py --url "https://youtu.be/XXXX" [--note "..."] [--issue 12] [--dry-run]

Env:
    JUDGE_BACKEND            "claude-code" | "api" (default: auto — claude-code if the
                             claude CLI is on PATH, else api)
    CLAUDE_CODE_OAUTH_TOKEN  claude-code backend CI auth (from `claude setup-token`;
                             locally the logged-in claude session is used instead)
    ANTHROPIC_API_KEY        required for the api backend
    CLAUDE_MODEL             generation model (default claude-sonnet-4-6)
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "pipeline" / "state.json"
CONTENT_DIR = ROOT / "content" / "docs"
RESULT_FILE = ROOT / "pipeline" / ".run-result.json"
SITE_BASE_URL = "https://playlist.metacog.co.kr"

KST = timezone(timedelta(hours=9))

# ============================== 도메인 설정 =================================
# 이 블록만 새 프로젝트 주제에 맞게 교체한다. 아래 엔진 코드는 건드릴 필요 없다.

SYSTEM_PROMPT = """You are an experienced ESL content coach. You analyze authentic \
spoken-English YouTube video transcripts (talks, interviews, vlogs, tutorials — auto \
or manual captions, so fillers, mishearings, and transcription noise are expected) and \
turn them into concise, encouraging study notes for intermediate learners. All output \
is in natural English. When quoting the video, quote what was actually said in the \
transcript and keep the speaker's intended meaning. Never invent quotes that are not \
grounded in the transcript."""

# {transcript} / {note} / {existing_categories} 세 자리를 반드시 유지. JSON 스키마의
# 이중 중괄호는 str.format() 이스케이프이므로 스키마를 고칠 때도 그대로 유지한다.
GENERATE_PROMPT = """Below is a transcript from a YouTube video (captions — it may \
contain fillers, transcription errors, and multiple speakers).{note} Analyze it and \
produce study notes AND classify the video into a study category. Respond ONLY with \
JSON in exactly this format, no other text:

{{"title": "Short English title capturing the video's main topic",
 "category_title": "2-4 word Title Case category name this video belongs under, e.g. \
'Business English' or 'Daily Conversation'",
 "summary": "2-3 sentence English overview of what the video is about",
 "idioms": [
   {{"idiom": "idiom or expression used or worth learning from this video",
     "meaning": "plain-English explanation",
     "examples": ["example sentence 1", "example sentence 2"]}}
 ],
 "vocabulary": [
   {{"word": "word or phrase worth remembering", "meaning": "plain-English definition",
     "example": "one natural example sentence"}}
 ],
 "say_it_naturally": [
   {{"natural": "a sentence or phrase actually said in the video, quoted closely from \
the transcript",
     "learner_version": "an awkward or incorrect way an English learner might try to \
express the same idea",
     "note": "one short line on why the natural version works better"}}
 ],
 "quiz": [
   {{"question": "natural sentence containing ____ (a blank) where one studied word or idiom fits",
     "options": ["studied word/idiom A", "studied word/idiom B", "studied word/idiom C"],
     "answer": "studied word/idiom B",
     "explanation": "one short line on why it fits the blank and the others don't"}}
 ],
 "diary": "one short first-person diary entry (4-6 sentences) told as a single connected story",
 "tags": ["kebab-case-tag", "max 3"]}}

Existing study categories on the site (reuse one of these EXACTLY as category_title \
when the video genuinely fits it; only propose a new category_title when none of these \
fit — keep new categories broad enough that other, similar videos could fit under them \
too, not a one-off title tied to this single video):
{existing_categories}

Requirements: 2-4 idioms (each with exactly 2 examples), 4-8 vocabulary items,
4-8 say_it_naturally entries grounded in things actually said in the video.
Diary rules: the diary is ONE coherent entry about ONE small everyday event
inspired by the video's topic — not a list of disconnected sentences. Tell it as
a natural mini-story with a beginning and end, weaving in 2-4 of the studied
idioms/vocabulary only where they genuinely fit; wrap each studied expression in
**double asterisks** so it stands out. Never force in more expressions at the
cost of natural flow.
Quiz rules (4-6 questions): every question is FILL-IN-THE-BLANK — a natural
sentence with "____" marking the blank. Every option (3-4 per question) MUST be
taken verbatim from this lesson's "idioms" or "vocabulary" entries — never invent
outside words, so all distractors are plausible items the learner just studied.
Exactly one option fits the blank; the sentence must be fully grammatical when
the correct option is inserted (adjust the option's form — tense, plural,
agreement — inside the option text if needed). The same option set should not
repeat across questions. Do not copy an example sentence from the
idioms/vocabulary sections as a quiz sentence — write a fresh sentence.

Transcript:
{transcript}"""

# 포스트 본문 섹션 제목
HEADING_OVERVIEW = "Video Overview"
HEADING_IDIOMS = "💬 Idioms"
HEADING_VOCAB = "📚 Vocabulary to Remember"
HEADING_SAY_IT = "🔧 Say It Naturally"
HEADING_QUIZ = "✅ Check Yourself"
HEADING_DIARY = "✍️ Mini Diary"

# ============================ 도메인 설정 끝 =================================


def log(msg: str) -> None:
    print(msg, flush=True)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return (slug or "study")[:60].rstrip("-")


def extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/shorts\/|\/live\/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


class TranscriptError(Exception):
    """캡션을 구할 수 없거나 너무 짧을 때 — 재시도가 아니라 사용자에게 사유를 보고한다."""


TAG_RE = re.compile(r"<[^>]+>")
TIME_RANGE_RE = re.compile(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->")
CUE_SETTINGS_RE = re.compile(r"\s+(align|position|size|line):\S+")


def vtt_to_text(vtt_content: str) -> str:
    """VTT 자막을 평문으로 변환하고, YouTube 자동 자막 특유의 롤링(겹침) 캡션을 병합한다."""
    lines: list[str] = []
    for raw in vtt_content.splitlines():
        line = CUE_SETTINGS_RE.sub("", raw.strip())
        if not line or line == "WEBVTT":
            continue
        if line.startswith(("Kind:", "Language:", "NOTE", "STYLE", "::cue")):
            continue
        if TIME_RANGE_RE.search(line):
            continue
        if re.match(r"^\d+$", line):
            continue
        clean = TAG_RE.sub("", line).strip()
        if clean:
            lines.append(clean)

    merged = ""
    for line in lines:
        if not merged:
            merged = line
            continue
        if line in merged[-max(len(line) * 2, 40):]:
            continue  # already captured by the previous rolling cue
        overlap = 0
        max_check = min(len(merged), len(line))
        for k in range(max_check, 0, -1):
            if not merged.endswith(line[:k]):
                continue
            before_ok = len(merged) == k or merged[-k - 1] == " "
            after_ok = k == len(line) or line[k] == " "
            if before_ok and after_ok:
                overlap = k
                break
        merged += (" " if overlap == 0 else "") + line[overlap:].lstrip()
    return re.sub(r"\s+", " ", merged).strip()


def fetch_transcript(url: str, workdir: Path) -> tuple[str, str, str]:
    """yt-dlp로 캡션만 받는다(영상/오디오 다운로드 없음). (video_id, title, transcript) 반환."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extractor-args", "youtube:player_client=android,web_safari",
        "--skip-download", "--print", "%(id)s\t%(title)s", "--no-warnings", url,
    ]
    try:
        meta_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptError("timed out reading video info") from exc
    if meta_result.returncode != 0:
        err = (meta_result.stderr or meta_result.stdout).strip()
        raise TranscriptError(f"yt-dlp could not read this video — {err[-400:]}")
    meta_line = next((l for l in meta_result.stdout.strip().splitlines() if "\t" in l), "")
    if not meta_line:
        raise TranscriptError("yt-dlp did not return video metadata")
    video_id, _, title = meta_line.partition("\t")
    video_id = video_id.strip() or extract_video_id(url)
    title = title.strip() or "Untitled video"
    if not video_id:
        raise TranscriptError("could not determine the video ID")

    # NOTE: --dump-json implies simulate mode and silently skips writing subtitle
    # files even with --write-sub — so subtitles are fetched in a separate, non-simulated call.
    sub_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extractor-args", "youtube:player_client=android,web_safari",
        "--skip-download", "--write-sub", "--write-auto-sub",
        "--sub-langs", "en,en-US,en-GB,en-orig",
        "--sub-format", "vtt", "--no-warnings",
        "-o", str(workdir / "%(id)s.%(ext)s"), url,
    ]
    try:
        sub_result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptError("timed out downloading captions") from exc
    if sub_result.returncode != 0:
        err = (sub_result.stderr or sub_result.stdout).strip()
        raise TranscriptError(f"yt-dlp could not fetch captions — {err[-400:]}")

    vtt_path = None
    for lang in ("en", "en-orig", "en-US", "en-GB"):
        candidate = workdir / f"{video_id}.{lang}.vtt"
        if candidate.exists():
            vtt_path = candidate
            break
    if vtt_path is None:
        remaining = sorted(workdir.glob(f"{video_id}.*.vtt"))
        vtt_path = remaining[0] if remaining else None
    if vtt_path is None:
        raise TranscriptError(
            "no English captions (manual or auto-generated) are available for this video"
        )
    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")
    text = vtt_to_text(raw)
    if len(text) < 40:
        raise TranscriptError("captions were found but the transcript text is too short")
    return video_id, title, text


class FatalAPIError(Exception):
    """재시도가 무의미한 오류(크레딧 부족, 인증 실패) — 실행 전체 중단."""


def is_fatal_api_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in (
        "credit balance", "authenticat", "invalid x-api-key",
        "invalid api key", "invalid bearer token", "oauth token", "/login",
        "401",
    ))


def parse_result(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    required = ("title", "category_title", "summary")
    if not all(isinstance(data.get(k), str) and data.get(k) for k in required):
        return None
    for key in ("idioms", "vocabulary", "say_it_naturally", "quiz"):
        value = data.get(key) or []
        data[key] = value if isinstance(value, list) else []
    diary = data.get("diary") or ""
    if isinstance(diary, list):  # 모델이 옛 형식(문장 목록)으로 답한 경우 이어붙임
        diary = " ".join(str(d).strip() for d in diary)
    data["diary"] = str(diary).strip()
    if not data["idioms"]:
        return None
    tags = data.get("tags") or []
    data["tags"] = [slugify(str(t)) for t in tags[:3] if str(t).strip()] or ["study-notes"]
    data["category_slug"] = slugify(data["category_title"])
    return data


def build_prompt(transcript: str, note: str, existing_categories: list[dict]) -> str:
    note_str = f" Notes from the person who requested this video: {note.strip()}" if note else ""
    if existing_categories:
        cat_str = "\n".join(f"- {c['title']}" for c in existing_categories)
    else:
        cat_str = "(none yet — this is the first post on the site)"
    return GENERATE_PROMPT.format(transcript=transcript, note=note_str, existing_categories=cat_str)


def generate_api(client, model: str, transcript: str, note: str, existing_categories: list[dict]) -> dict | None:
    prompt = build_prompt(transcript, note, existing_categories)
    for attempt in (1, 2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001
            if is_fatal_api_error(exc):
                raise FatalAPIError(str(exc)) from exc
            log(f"  API 오류 (시도 {attempt}): {exc}")
            if attempt == 2:
                return None
            continue
        text = next((b.text for b in response.content if b.type == "text"), "")
        result = parse_result(text)
        if result:
            return result
        log(f"  JSON 파싱 실패 (시도 {attempt}): {text[:120]!r}")
    return None


def generate_cli(model: str, transcript: str, note: str, existing_categories: list[dict]) -> dict | None:
    prompt = build_prompt(transcript, note, existing_categories)
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = ["claude", "-p", "--model", model, "--tools", "",
           "--output-format", "text", "--append-system-prompt", SYSTEM_PROMPT]
    for attempt in (1, 2):
        try:
            result = subprocess.run(cmd, input=prompt, env=env, timeout=360,
                                     capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            log(f"  CLI 타임아웃 (시도 {attempt})")
            continue
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            if is_fatal_api_error(RuntimeError(err)):
                raise FatalAPIError(err[:300])
            log(f"  CLI 오류 (시도 {attempt}): {err[:200]}")
            if attempt == 2:
                return None
            continue
        parsed = parse_result(result.stdout)
        if parsed:
            return parsed
        log(f"  JSON 파싱 실패 (시도 {attempt}): {result.stdout[:120]!r}")
    return None


def yaml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"processed": {}, "categories": {}}


def existing_categories_for_prompt(state: dict) -> list[dict]:
    cats = state.get("categories", {})
    return [{"slug": slug, "title": info["title"]} for slug, info in cats.items()]


def ensure_category_index(category_slug: str, category_title: str, state: dict) -> Path:
    category_dir = CONTENT_DIR / category_slug
    category_dir.mkdir(parents=True, exist_ok=True)
    index_path = category_dir / "_index.md"
    cats = state.setdefault("categories", {})
    if category_slug not in cats:
        weight = (max((c["weight"] for c in cats.values()), default=0)) + 1
        cats[category_slug] = {"title": category_title, "weight": weight}
    if not index_path.exists():
        front = f"""---
title: {yaml_quote(category_title)}
weight: {cats[category_slug]['weight']}
---
"""
        index_path.write_text(front, encoding="utf-8")
    return category_dir


def write_post(video_id: str, video_url: str, video_title: str, result: dict, date: datetime) -> Path:
    category_slug = result["category_slug"]
    category_dir = CONTENT_DIR / category_slug
    base = slugify(result["title"])
    path = category_dir / f"{base}.md"
    n = 2
    while path.exists():
        path = category_dir / f"{base}-{n}.md"
        n += 1

    existing_pages = [p for p in category_dir.glob("*.md") if p.name != "_index.md"]
    weight = len(existing_pages) + 1

    tags_str = ", ".join(yaml_quote(t) for t in result["tags"])

    embed = (
        '<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;'
        'max-width:100%;margin-bottom:1rem;">'
        f'<iframe src="https://www.youtube.com/embed/{video_id}" '
        'style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; '
        f'picture-in-picture; web-share" allowfullscreen title="{html_escape(video_title)}">'
        "</iframe></div>"
    )

    sections = [
        f"""{embed}

## {HEADING_OVERVIEW}

{result['summary']}

[Watch on YouTube ↗]({video_url})
"""
    ]

    if result["idioms"]:
        lines = [f"## {HEADING_IDIOMS}\n"]
        for item in result["idioms"]:
            lines.append(f"### “{item.get('idiom', '')}”\n")
            lines.append(f"{item.get('meaning', '')}\n")
            examples = item.get("examples") or []
            if examples:
                lines.append("\n".join(f"- *{ex}*" for ex in examples) + "\n")
        sections.append("\n".join(lines))

    if result["vocabulary"]:
        lines = [f"## {HEADING_VOCAB}\n"]
        for item in result["vocabulary"]:
            word = item.get("word", "")
            meaning = item.get("meaning", "")
            example = item.get("example", "")
            entry = f"- **{word}** — {meaning}"
            if example:
                entry += f"\n  - *{example}*"
            lines.append(entry)
        sections.append("\n".join(lines) + "\n")

    if result["say_it_naturally"]:
        lines = [f"## {HEADING_SAY_IT}\n"]
        for i, item in enumerate(result["say_it_naturally"], 1):
            lines.append(f"{i}. ❌ *{item.get('learner_version', '')}*")
            lines.append(f"   ✅ **{item.get('natural', '')}**")
            note = item.get("note", "")
            if note:
                lines.append(f"   💡 {note}")
        sections.append("\n".join(lines) + "\n")

    if result["quiz"]:
        lines = [f"## {HEADING_QUIZ}\n"]
        for i, item in enumerate(result["quiz"], 1):
            lines.append(f"**Q{i}.** {item.get('question', '')}\n")
            options = item.get("options") or []
            if options:
                lines.append("\n".join(f"- {opt}" for opt in options) + "\n")
            answer = html_escape(str(item.get("answer", "")))
            explanation = html_escape(str(item.get("explanation", "")))
            detail = f"<strong>{answer}</strong>"
            if explanation:
                detail += f" — {explanation}"
            lines.append(
                "<details><summary>Show answer</summary>"
                f"<p>{detail}</p></details>\n"
            )
        sections.append("\n".join(lines))

    if result["diary"]:
        entry = result["diary"].replace("\n", "\n> ")
        sections.append(f"## {HEADING_DIARY}\n\n> {entry}\n")

    post = f"""---
title: {yaml_quote(result['title'])}
weight: {weight}
date: {date.isoformat()}
tags: [{tags_str}]
params:
  video_url: {yaml_quote(video_url)}
  video_title: {yaml_quote(video_title)}
---
""" + "\n".join(sections)
    path.write_text(post, encoding="utf-8")
    return path


def write_result(status: str, **fields) -> None:
    payload = {"status": status, **fields}
    RESULT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube transcript study pipeline")
    parser.add_argument("--url", default=os.environ.get("YOUTUBE_URL", ""),
                         help="YouTube video URL")
    parser.add_argument("--note", default=os.environ.get("REQUEST_NOTE", ""),
                         help="optional context from the requester")
    parser.add_argument("--issue", type=int, default=int(os.environ.get("ISSUE_NUMBER", "0") or 0),
                         help="source GitHub issue number, for state tracking")
    parser.add_argument("--dry-run", action="store_true",
                         help="파일 생성/state.json 갱신 없이 결과만 출력")
    args = parser.parse_args()

    url = args.url.strip()
    if not url:
        log("오류: --url (또는 YOUTUBE_URL 환경변수)이 필요합니다")
        write_result("failed", reason="no YouTube URL was provided")
        return 1

    backend = os.environ.get("JUDGE_BACKEND", "").strip() or (
        "claude-code" if shutil.which("claude") else "api"
    )
    client = None
    if backend == "api":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log("오류: api 백엔드에는 ANTHROPIC_API_KEY 환경변수가 필요합니다")
            write_result("failed", reason="server misconfiguration (missing ANTHROPIC_API_KEY)")
            return 1
        import anthropic  # 지연 임포트

        client = anthropic.Anthropic()
    elif backend == "claude-code":
        if not shutil.which("claude"):
            log("오류: claude-code 백엔드에는 claude CLI가 PATH에 있어야 합니다")
            write_result("failed", reason="server misconfiguration (claude CLI not found)")
            return 1
    else:
        log(f"오류: 알 수 없는 JUDGE_BACKEND={backend!r} (claude-code | api)")
        write_result("failed", reason=f"unknown JUDGE_BACKEND={backend!r}")
        return 1

    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    state = load_state()
    processed: dict = state.get("processed", {})

    early_id = extract_video_id(url)
    if early_id and early_id in processed:
        prior = processed[early_id]
        log(f"이미 처리된 영상입니다 ({early_id}) — {prior.get('path')}")
        write_result("skipped_duplicate", video_id=early_id, **prior)
        return 0

    log(f"=== 생성 시작 (backend={backend}, model={model}, dry_run={args.dry_run}) ===")
    log(f"URL: {url}")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            video_id, video_title, transcript = fetch_transcript(url, Path(tmp))
        except TranscriptError as exc:
            log(f"  자막 확보 실패: {exc}")
            write_result("failed", reason=str(exc))
            return 1

    if video_id in processed:
        prior = processed[video_id]
        log(f"이미 처리된 영상입니다 ({video_id}) — {prior.get('path')}")
        write_result("skipped_duplicate", video_id=video_id, **prior)
        return 0

    log(f"영상: {video_title} ({video_id}), 자막 {len(transcript)}자")

    existing_categories = existing_categories_for_prompt(state)
    try:
        if backend == "claude-code":
            result = generate_cli(model, transcript, args.note, existing_categories)
        else:
            result = generate_api(client, model, transcript, args.note, existing_categories)
    except FatalAPIError as exc:
        log(f"\n중단: 복구 불가능한 API 오류 — {exc}")
        write_result("failed", reason=f"Claude API error: {exc}")
        return 1

    if result is None:
        log("  생성 실패")
        write_result("failed", reason="Claude did not return a usable study-notes JSON response")
        return 1

    log(f"  → {result['title']} [{result['category_title']}]")

    if args.dry_run:
        log(json.dumps(result, ensure_ascii=False, indent=2))
        log("(dry-run — 파일 생성/기록 갱신 없음)")
        return 0

    now = datetime.now(KST)
    ensure_category_index(result["category_slug"], result["category_title"], state)
    path = write_post(video_id, url, video_title, result, now)
    rel_path = path.relative_to(ROOT).as_posix()
    log(f"  생성 파일: {rel_path}")

    slug_parts = rel_path.removeprefix("content/").removesuffix(".md")
    page_url = f"{SITE_BASE_URL}/{slug_parts}/"

    processed[video_id] = {
        "issue": args.issue,
        "path": rel_path,
        "title": result["title"],
        "category": result["category_title"],
        "url": page_url,
        "date": now.date().isoformat(),
    }
    state["processed"] = processed
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    write_result(
        "success",
        video_id=video_id,
        path=rel_path,
        title=result["title"],
        category=result["category_title"],
        url=page_url,
        date=now.date().isoformat(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
