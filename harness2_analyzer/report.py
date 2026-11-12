"""`h2a report`: one self-contained HTML page (offline, inline SVG, no scripts).

The page compares the versions a run used (change id, else config fingerprint), lists the
failure signatures, counts the root causes, and lists the order breaks of the atelier events.
Everything on it is an identifier or a count, so it holds no body text either.
"""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .signature import version_of

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--line:#e2ddd3;--card:#fff;
--ok:#2f7d4f;--bad:#b4442c;--bar:#d9d3c7}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--fg:#ebe7df;--muted:#a19b90;
--line:#34312c;--card:#1f1d1a;--ok:#5fb383;--bad:#e07a5f;--bar:#3b3833}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
p.note{color:var(--muted);margin:4px 0 12px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px}
tr:last-child td{border-bottom:0}
td.n{text-align:right}code{font:12px ui-monospace,Consolas,monospace}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}
.kpi{border:1px solid var(--line);border-radius:6px;padding:10px 14px;background:var(--card);
min-width:140px}
.kpi b{display:block;font-size:20px}.kpi span{color:var(--muted);font-size:12px}
.bad{color:var(--bad)}.ok{color:var(--ok)}
svg text{fill:var(--fg);font-size:12px}
"""


def _e(value: Any) -> str:
    """HTML-escaped text; None shows as an em dash."""
    return "—" if value is None else html.escape(str(value))


def _pct(part: int, whole: int) -> str:
    """A share as a percentage with one decimal."""
    return "—" if not whole else f"{100 * part / whole:.1f}%"


def _mean(values: Sequence[float]) -> str:
    """The mean of the values with two decimals, or a dash."""
    return "—" if not values else f"{sum(values) / len(values):.2f}"


def _table(head: Sequence[str], rows: Iterable[Sequence[str]], numeric: set[int]) -> str:
    """A table whose cells are already escaped HTML."""
    out = ["<div class='wrap'><table><thead><tr>"]
    out += [f"<th>{_e(h)}</th>" for h in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        cells = (
            f"<td class='n'>{cell}</td>" if i in numeric else f"<td>{cell}</td>"
            for i, cell in enumerate(row)
        )
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _bars(items: Sequence[tuple[str, float]]) -> str:
    """Horizontal bars of shares from 0 to 1, as inline SVG."""
    if not items:
        return ""
    height = 26 * len(items) + 8
    parts = [
        f"<svg viewBox='0 0 640 {height}' width='100%' role='img' aria-label='版ごとの合格率'>"
    ]
    for i, (label, share) in enumerate(items):
        y = 6 + 26 * i
        width = max(0.0, min(1.0, share)) * 360
        parts.append(f"<text x='0' y='{y + 14}'>{_e(label[:34])}</text>")
        parts.append(f"<rect x='250' y='{y}' width='360' height='18' fill='var(--bar)' rx='3'/>")
        parts.append(
            f"<rect x='250' y='{y}' width='{width:.1f}' height='18' fill='var(--ok)' rx='3'/>"
        )
        parts.append(f"<text x='{256 + width:.1f}' y='{y + 14}'>{share * 100:.0f}%</text>")
    parts.append("</svg>")
    return "".join(parts)


def _parts_text(parts: Any) -> str:
    """Signature parts as compact `key=value` text, without the empty ones."""
    if not isinstance(parts, Mapping):
        return ""
    shown = []
    for key in sorted(parts):
        value = parts[key]
        if value in (None, "", [], False, "0") or value == {
            "critical": "0",
            "major": "0",
            "minor": "0",
        }:
            continue
        if isinstance(value, (list, dict)):
            value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        shown.append(f"{key}={value}")
    return " ".join(shown)


def render(
    units: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any] | None,
    breaks: Sequence[Mapping[str, Any]],
    meta: Mapping[str, Any],
) -> str:
    """The whole HTML page."""
    total = len(units)
    passed = sum(1 for u in units if (u.get("outcome") or {}).get("passed"))
    systems = Counter(u["system"] for u in units)
    body = [
        "<h1>harness2-analyzer レポート (1 段目)</h1>",
        f"<p class='note'>作成 {_e(meta.get('generated_at'))}。入力 {len(meta.get('sources', []))} "
        "ファイル。値は識別子と数だけで、本文は含まない。</p>",
        "<div class='kpis'>",
        f"<div class='kpi'><b>{total}</b><span>unit (試行)</span></div>",
    ]
    for system, count in sorted(systems.items()):
        body.append(f"<div class='kpi'><b>{count}</b><span>{_e(system)}</span></div>")
    body.append(f"<div class='kpi'><b>{_pct(passed, total)}</b><span>合格率</span></div>")
    body.append(f"<div class='kpi'><b>{len(breaks)}</b><span>順番の破れ</span></div></div>")

    body.append("<h2>版ごとの比較</h2>")
    body.append(
        "<p class='note'>版は change_id、無ければ config_fp (cfg:)・harness_fp (fp:)・"
        "worker.md の sha (prompt:) の順に取る。リファクタの前後の run をここで並べて比べる。</p>"
    )
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for unit in units:
        groups[(unit["system"], version_of(unit))].append(unit)
    rows, bars = [], []
    for (system, version), members in sorted(groups.items()):
        ok = sum(1 for u in members if (u.get("outcome") or {}).get("passed"))
        usd = [
            u["outcome"]["usd"] for u in members if (u.get("outcome") or {}).get("usd") is not None
        ]
        sec = [
            u["outcome"]["seconds"]
            for u in members
            if (u.get("outcome") or {}).get("seconds") is not None
        ]
        failing = Counter(
            u["signature"]["sig_id"]
            for u in members
            if u.get("signature") and not (u.get("outcome") or {}).get("passed")
        )
        top = ", ".join(f"<code>{_e(s)}</code>×{n}" for s, n in failing.most_common(3))
        rows.append(
            [
                _e(system),
                f"<code>{_e(version)}</code>",
                str(len(members)),
                str(len({u["run_id"] for u in members})),
                _pct(ok, len(members)),
                _mean(usd),
                _mean(sec),
                str(len(failing)),
                top or "—",
            ]
        )
        bars.append((f"{system} {version}", ok / len(members) if members else 0.0))
    body.append(_bars(bars))
    body.append(
        _table(
            [
                "system",
                "版",
                "unit",
                "run",
                "合格率",
                "USD 平均",
                "秒 平均",
                "失敗 sig 数",
                "多い失敗 sig",
            ],
            rows,
            {2, 3, 4, 5, 6, 7},
        )
    )

    body.append("<h2>失敗のシグネチャ</h2>")
    sigs = [s for s in (summary or {}).get("sigs", []) if s.get("failing")]
    if sigs:
        body.append(
            _table(
                ["sig_id", "system", "失敗", "全体", "割合", "課題数", "最初の版", "parts"],
                (
                    [
                        f"<code>{_e(s['sig_id'])}</code>",
                        _e(s["system"]),
                        str(s["failing"]),
                        str(s["count"]),
                        f"{100 * s['rate']:.1f}%",
                        str(s.get("tasks", 0)),
                        f"<code>{_e(s.get('first_version'))}</code>",
                        f"<code>{_e(_parts_text(s.get('parts')))}</code>",
                    ]
                    for s in sigs[:40]
                ),
                {2, 3, 4, 5},
            )
        )
    else:
        body.append("<p class='note'>失敗した unit はない (または h2a signature を未実行)。</p>")

    body.append("<h2>根因</h2>")
    causes: dict[str, Counter[str]] = defaultdict(Counter)
    for unit in units:
        if unit.get("root_cause"):
            causes[version_of(unit)][unit["root_cause"]] += 1
    if causes:
        names = sorted({c for counter in causes.values() for c in counter})
        body.append(
            _table(
                ["版", *names],
                (
                    [f"<code>{_e(v)}</code>", *(str(c[n]) for n in names)]
                    for v, c in sorted(causes.items())
                ),
                set(range(1, len(names) + 1)),
            )
        )
    else:
        body.append("<p class='note'>根因の付いた unit はない。</p>")

    body.append("<h2>順番の破れ (atelier のイベント)</h2>")
    skipped = meta.get("order_skipped") or []
    if skipped:
        body.append(
            "<p class='note'>確かめられなかった規則 (要るイベントが入力に無い): "
            + ", ".join(f"<code>{_e(name)}</code>" for name in skipped)
            + "</p>"
        )
    if breaks:
        per_rule = Counter(b["rule"] for b in breaks)
        body.append(
            _table(
                ["規則", "件数"],
                ([f"<code>{_e(r)}</code>", str(n)] for r, n in sorted(per_rule.items())),
                {1},
            )
        )
        body.append("<p></p>")
        body.append(
            _table(
                ["時刻", "規則", "重さ", "run", "type", "seq", "pbi", "行"],
                (
                    [
                        _e(b["ts"]),
                        f"<code>{_e(b['rule'])}</code>",
                        f"<span class='{'bad' if b['severity'] in ('critical', 'major') else ''}'>"
                        f"{_e(b['severity'])}</span>",
                        f"<code>{_e(str(b['run_id'])[:12])}</code>",
                        f"<code>{_e(b['type'])}</code>",
                        _e(b.get("seq")),
                        _e(b.get("pbi")),
                        _e(b.get("line")),
                    ]
                    for b in breaks
                ),
                {5, 7},
            )
        )
    else:
        body.append("<p class='note'>破れはない。</p>")

    warnings = list(meta.get("warnings") or [])
    if warnings:
        body.append("<h2>注意</h2><ul>")
        body += [f"<li>{_e(w)}</li>" for w in warnings[:50]]
        body.append("</ul>")

    return (
        "<!doctype html><html lang='ja'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Analyzer Report</title><style>{_CSS}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )
