"""
Auto-translate Prey English XML localization → Thai using Gemini.

Usage:
    1. pip install -r requirements.txt
    2. echo "GEMINI_API_KEY=YOUR_KEY" > .env
    3. python translate_all.py --dry-run        # preview
       python translate_all.py                  # full run
       python translate_all.py --only voices    # filter
       python translate_all.py --limit 20       # smoke test

State is persisted to _translation_state.json so re-running resumes.
Rows already translated (Thai chars detected) are skipped automatically.
"""

import argparse
import fnmatch
import html
import json
import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from tqdm import tqdm

# ---------- paths ----------

WORK_DIR = Path(__file__).resolve().parent
LOC_SRC = WORK_DIR / "loc_src"
GLOSSARY_PATH = WORK_DIR / "glossary.json"
STYLE_PATH = WORK_DIR / "style_prompt.md"
STATE_PATH = WORK_DIR / "_translation_state.json"
LOG_PATH = WORK_DIR / "translate.log"

# ---------- regex ----------

ROW_RE = re.compile(r"<Row\b[^>]*>(.*?)</Row>", re.DOTALL)
DATA_RE = re.compile(r'<Data ss:Type="String">(.*?)</Data>', re.DOTALL)
THAI_CHAR_RE = re.compile(r"[฀-๿]")
PLACEHOLDER_RE = re.compile(r"(\{\d+\}|%\w+%|%[sd]|%\d+%|<br>|<i>|</i>|<b>|</b>|<color=[^>]+>|</color>)")

# ---------- logging ----------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("translate")

# ---------- data classes ----------


@dataclass
class TodoRow:
    file_path: Path
    row_start: int
    row_end: int
    row_text: str
    key: str
    english_raw: str  # XML-escaped form, as it appears in file
    english_decoded: str  # html.unescape'd, what we send to Gemini
    char_limit: Optional[int]


# ---------- helpers ----------


def has_thai(s: str) -> bool:
    return THAI_CHAR_RE.search(s) is not None


def xml_text_escape(s: str) -> str:
    """Match the escape style used by existing batch scripts: only quote chars.
    Inline tags like <br> are preserved verbatim (they're raw in the source files)."""
    return s.replace("'", "&apos;").replace('"', "&quot;")


def find_xml_files(only_pattern: Optional[str]) -> list[Path]:
    files = sorted(LOC_SRC.rglob("*.xml"))
    if only_pattern:
        # Match against POSIX-style relative path so user can pass "voices/*" or "text_ui_*"
        kept = []
        for f in files:
            rel = f.relative_to(LOC_SRC).as_posix()
            if fnmatch.fnmatch(rel, only_pattern) or only_pattern in rel:
                kept.append(f)
        files = kept
    return files


def parse_file(path: Path) -> tuple[str, list[TodoRow]]:
    content = path.read_text(encoding="utf-8")
    todos: list[TodoRow] = []
    for m in ROW_RE.finditer(content):
        row_text = m.group(0)
        cells = DATA_RE.findall(m.group(1))
        if len(cells) < 3:
            continue  # malformed or non-translation row
        key, original, translated = cells[0], cells[1], cells[2]
        # Skip header
        if key == "KEY" or original == "ORIGINAL TEXT":
            continue
        # Skip already translated (in either col 2 or col 3 — handles old script that overwrote both)
        if has_thai(original) or has_thai(translated):
            continue
        # Skip empty / whitespace-only English
        if not original.strip():
            continue
        # Note: col 4 "LOCKED" in Prey's source files marks the English as "final/approved" by the
        # original loc team — it does NOT mean "don't translate". Audiologs, books, emails, lore are
        # all LOCKED but contain real story prose that needs Thai translation. We translate them.

        char_limit: Optional[int] = None
        # 5th column "CHARACTER LIMIT" might be a number cell; try string cells beyond 3
        # (we don't reliably parse Number cells, so only honor it if present as a string digit)
        if len(cells) >= 5 and cells[4].strip().isdigit():
            char_limit = int(cells[4].strip())

        todos.append(
            TodoRow(
                file_path=path,
                row_start=m.start(),
                row_end=m.end(),
                row_text=row_text,
                key=key,
                english_raw=original,
                english_decoded=html.unescape(original),
                char_limit=char_limit,
            )
        )
    return content, todos


def apply_translation(content: str, row: TodoRow, thai: str) -> str:
    """Replace ONLY the 3rd <Data> tag in the row with the Thai translation.
    Leaves cells 1 (KEY) and 2 (ORIGINAL) untouched."""
    encoded = xml_text_escape(thai)
    # Locate row's current span in content (it may have shifted from earlier replacements)
    # We search for the unmodified row_text starting at a hint position.
    idx = content.find(row.row_text)
    if idx == -1:
        log.warning(
            "Row not found in file (already replaced or shifted): %s key=%s",
            row.file_path.name,
            row.key,
        )
        return content
    row_text = row.row_text
    matches = list(DATA_RE.finditer(row_text))
    if len(matches) < 3:
        return content
    third = matches[2]
    new_row = (
        row_text[: third.start()]
        + f'<Data ss:Type="String">{encoded}</Data>'
        + row_text[third.end():]
    )
    return content[:idx] + new_row + content[idx + len(row_text):]


# ---------- gemini ----------


def build_system_prompt(style: str, glossary: dict) -> str:
    keep = ", ".join(glossary["keep_english"])
    trans_lines = "\n".join(f'  "{k}" → "{v}"' for k, v in glossary["translations"].items())
    return (
        style
        + "\n\n## glossary.keep_english (NEVER translate)\n"
        + keep
        + "\n\n## glossary.translations (USE EXACT THAI)\n"
        + trans_lines
    )


RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "key": {"type": "STRING"},
            "translation": {"type": "STRING"},
        },
        "required": ["key", "translation"],
    },
}


def parse_retry_delay(err: Exception) -> Optional[float]:
    """Extract retryDelay (seconds) from a Gemini 429 ClientError if present."""
    msg = str(err)
    m = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", msg)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
    if m:
        return float(m.group(1))
    return None


def call_gemini(client, model: str, system_prompt: str, batch: list[TodoRow]) -> dict[str, str]:
    """Send a batch to Gemini, return {key: thai}. Raises on hard failure."""
    from google.genai import types

    user_payload = [
        {
            "key": r.key,
            "text": r.english_decoded,
            **({"char_limit": r.char_limit} if r.char_limit else {}),
        }
        for r in batch
    ]

    last_err: Optional[Exception] = None
    for attempt in range(8):
        try:
            response = client.models.generate_content(
                model=model,
                contents=json.dumps(user_payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.3,
                ),
            )
            text = response.text
            if not text:
                raise RuntimeError("empty response")
            data = json.loads(text)
            if not isinstance(data, list):
                raise RuntimeError(f"expected list, got {type(data).__name__}")
            return {item["key"]: item["translation"] for item in data if "key" in item}
        except Exception as e:  # noqa: BLE001
            last_err = e
            # Respect server-suggested retry delay if 429
            server_delay = parse_retry_delay(e)
            if server_delay is not None:
                wait = server_delay + 2  # small safety margin
            else:
                wait = min(2 ** attempt, 60)
            log.warning(
                "Gemini call failed (attempt %d): %s — retrying in %.1fs",
                attempt + 1,
                str(e)[:200],
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"Gemini failed after 8 attempts: {last_err}")


# ---------- validation ----------


def validate(row: TodoRow, thai: str) -> Optional[str]:
    """Return None if OK, else a reason string."""
    if not thai or not thai.strip():
        return "empty translation"
    # Placeholders: every placeholder in source must appear in translation
    src_placeholders = PLACEHOLDER_RE.findall(row.english_decoded)
    out_placeholders = PLACEHOLDER_RE.findall(thai)
    for p in src_placeholders:
        # Tolerate count differences for repeating tags like <br>; just require presence
        if p not in out_placeholders:
            return f"missing placeholder {p!r}"
    # Char limit
    if row.char_limit and len(thai) > row.char_limit:
        return f"exceeds char_limit ({len(thai)} > {row.char_limit})"
    return None


# ---------- state ----------


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"completed_keys": {}, "failed": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- main ----------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="count only, no API calls")
    p.add_argument("--limit", type=int, default=0, help="cap total rows translated this run")
    p.add_argument("--only", default="", help="filename glob or substring to filter (e.g. 'voices/*' or 'text_ui_')")
    p.add_argument("--model", default="gemini-2.5-flash")
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--rpm", type=int, default=4, help="client-side rate limit (requests/min). Free tier=4 (under 5 limit). Paid tier: pass 60+ to go fast.")
    p.add_argument("--concurrency", type=int, default=1, help="number of batches to run in parallel. Free tier=1; paid tier safe up to 10.")
    p.add_argument("--retry-failed", action="store_true", help="re-attempt rows logged as failed in state")
    args = p.parse_args()

    load_dotenv(WORK_DIR / ".env")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key and not args.dry_run:
        log.error("GEMINI_API_KEY not set. Put it in .env or environment.")
        return 2

    if not LOC_SRC.exists():
        log.error("loc_src not found: %s", LOC_SRC)
        return 2

    glossary = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    style = STYLE_PATH.read_text(encoding="utf-8")
    system_prompt = build_system_prompt(style, glossary)

    state = load_state()

    files = find_xml_files(args.only or None)
    log.info("Scanning %d XML files...", len(files))

    # Collect all todos
    file_todos: list[tuple[Path, list[TodoRow]]] = []
    total_rows = 0
    for f in files:
        _, todos = parse_file(f)
        if args.retry_failed:
            failed_keys = {item["key"] for item in state["failed"] if item["file"] == str(f)}
            todos = [t for t in todos if t.key in failed_keys]
        if todos:
            file_todos.append((f, todos))
            total_rows += len(todos)

    log.info(
        "%d files have untranslated rows, %d total rows pending",
        len(file_todos),
        total_rows,
    )

    if args.dry_run:
        log.info("--dry-run: not calling API")
        for f, todos in file_todos[:10]:
            log.info("  %s: %d rows", f.relative_to(LOC_SRC), len(todos))
        if len(file_todos) > 10:
            log.info("  ... and %d more files", len(file_todos) - 10)
        return 0

    if total_rows == 0:
        log.info("Nothing to do. All rows already translated.")
        return 0

    # Init Gemini client lazily (only after dry-run check)
    from google import genai
    client = genai.Client(api_key=api_key)

    if args.limit:
        # Trim total work to limit
        capped: list[tuple[Path, list[TodoRow]]] = []
        budget = args.limit
        for f, todos in file_todos:
            if budget <= 0:
                break
            take = todos[:budget]
            capped.append((f, take))
            budget -= len(take)
        file_todos = capped
        total_rows = sum(len(t) for _, t in file_todos)
        log.info("--limit: capped to %d rows", total_rows)

    pbar = tqdm(total=total_rows, desc="translating", unit="row")

    min_interval = 60.0 / max(args.rpm, 1)
    last_call = [0.0]
    rate_lock = threading.Lock()
    state_lock = threading.Lock()
    file_locks: dict[Path, threading.Lock] = defaultdict(threading.Lock)
    file_locks_guard = threading.Lock()

    def get_file_lock(p: Path) -> threading.Lock:
        with file_locks_guard:
            return file_locks[p]

    # Flatten into list of (file_path, batch) tuples
    all_batches: list[tuple[Path, list[TodoRow]]] = []
    for file_path, todos in file_todos:
        for i in range(0, len(todos), args.batch_size):
            all_batches.append((file_path, todos[i : i + args.batch_size]))

    log.info(
        "Dispatching %d batches across %d workers (batch=%d, rpm=%d)",
        len(all_batches),
        args.concurrency,
        args.batch_size,
        args.rpm,
    )

    def worker(fpath: Path, batch: list[TodoRow]) -> tuple[Path, list[TodoRow], Optional[dict[str, str]], Optional[Exception]]:
        # Throttle: serialize the *start time* of API calls to honor --rpm
        with rate_lock:
            elapsed = time.time() - last_call[0]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            last_call[0] = time.time()
        try:
            return fpath, batch, call_gemini(client, args.model, system_prompt, batch), None
        except Exception as e:  # noqa: BLE001
            return fpath, batch, None, e

    def apply_result(fpath: Path, batch: list[TodoRow], translations: Optional[dict[str, str]], err: Optional[Exception]) -> None:
        if err is not None:
            log.error("Hard failure on batch %s: %s", fpath.name, str(err)[:200])
            with state_lock:
                for r in batch:
                    state["failed"].append({"file": str(r.file_path), "key": r.key, "reason": f"api: {err}"})
                save_state(state)
            pbar.update(len(batch))
            return

        with get_file_lock(fpath):
            content = fpath.read_text(encoding="utf-8")
            for r in batch:
                thai = translations.get(r.key) if translations else None
                if thai is None:
                    log.warning("Missing key in response: %s [%s]", r.key, fpath.name)
                    with state_lock:
                        state["failed"].append({"file": str(r.file_path), "key": r.key, "reason": "missing in response"})
                    pbar.update(1)
                    continue
                reason = validate(r, thai)
                if reason:
                    log.warning("Invalid translation [%s key=%s]: %s — text=%r", fpath.name, r.key, reason, thai)
                    with state_lock:
                        state["failed"].append({"file": str(r.file_path), "key": r.key, "reason": reason, "text": thai})
                    pbar.update(1)
                    continue
                content = apply_translation(content, r, thai)
                with state_lock:
                    state["completed_keys"].setdefault(str(r.file_path), []).append(r.key)
                pbar.update(1)
            fpath.write_text(content, encoding="utf-8")
            with state_lock:
                save_state(state)

    if args.concurrency <= 1:
        for fpath, batch in all_batches:
            apply_result(*worker(fpath, batch))
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as exe:
            futures = [exe.submit(worker, fp, b) for fp, b in all_batches]
            for fut in as_completed(futures):
                apply_result(*fut.result())

    pbar.close()
    log.info("Done. Failed rows logged to %s", LOG_PATH)
    if state["failed"]:
        log.info("To retry failed rows: python translate_all.py --retry-failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
