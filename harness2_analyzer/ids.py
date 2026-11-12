"""Short identifiers, hashes and JSON Lines helpers shared by every stage."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

# An identifier a unit may carry: letters, digits and `._:/@+-`. Longer than atelier's 64
# characters because a case id joins the repository, the work key and the PBI.
ID_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,200}$")


def hash12(text: str) -> str:
    """The first 12 hex digits of the sha256 of `text` in UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def is_id(value: Any) -> bool:
    """Whether `value` is a string that passes as a short identifier."""
    return isinstance(value, str) and bool(ID_RE.match(value))


def safe_id(value: Any) -> str | None:
    """`value` when it is an identifier, `h:<hash12>` for any other string, None for None."""
    if value is None:
        return None
    text = str(value)
    if is_id(text):
        return text
    return "h:" + hash12(text)


def sig_id_of(parts: Mapping[str, Any]) -> str:
    """The signature id of `parts`, the same rule as atelier's `record_run.sig_id_of`.

    The parts are dumped with sorted keys and compact separators, without ASCII escaping, and
    the first 12 hex digits of the sha256 of that UTF-8 text are the id.
    """
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hash12(text)


_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sha256=[0-9a-f]+(\s+chars=\d+)?"), "<H>"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<U>",
    ),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?"), "<T>"),
    (re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w.@+-]+){2,}[/\\]?"), "<P>"),
    (re.compile(r"\b(?:0x)?[0-9a-f]{7,}\b", re.I), "<X>"),
    (re.compile(r"\d+(\.\d+)?"), "<N>"),
    (re.compile(r"\s+"), " "),
)


def norm_error(value: Any) -> str:
    """An error message with its run-specific pieces replaced by symbols.

    A hashed body (`{"sha256": ..., "chars": ...}` or `sha256=<hex>`) becomes `<H>`; uuids,
    times, paths, long hex runs and numbers become `<U>`, `<T>`, `<P>`, `<X>` and `<N>`.
    An empty value gives the empty string.
    """
    if not value:
        return ""
    if isinstance(value, Mapping):
        return "<H>" if "sha256" in value else "<O>"
    text = str(value)
    for pattern, symbol in _NORMALIZERS:
        text = pattern.sub(symbol, text)
    return text.strip()


def read_jsonl(path: Path) -> Iterator[tuple[int, dict]]:
    """`(line number, object)` for each JSON object line of `path`; other lines are skipped."""
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield number, value


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Write `rows` as sorted-key JSON lines; returns the number of rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            count += 1
    return count


def write_json(path: Path, data: Any) -> None:
    """Write one indented, sorted-key JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
