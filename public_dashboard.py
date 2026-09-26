"""Read-only, aggregate-only dashboard entry point for public hosting.

This module deliberately does not import pipeline.py or open the private database.
It serves exactly the HTML dashboard at / and an allow-listed aggregate JSON view at /api.
"""
from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
STATS_FILE = ROOT / "public_dashboard_stats.json"
TRACK_FIELDS = (
    "targeted", "success", "pending", "failed", "duplicates", "records",
    "captcha_events_total", "captcha_paused", "records_per_second_last_run",
    "last_activity_at",
)


def public_payload() -> dict:
    """Load only allow-listed aggregate fields; ignore any unexpected data keys."""
    source = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    tracks = {}
    for track in ("mock", "live"):
        item = source.get("tracks", {}).get(track, {})
        tracks[track] = {key: item.get(key) for key in TRACK_FIELDS}
    quota = source.get("live_quota", {})
    return {
        "snapshot_generated_at": source.get("snapshot_generated_at"),
        "tracks": tracks,
        "live_quota": {key: quota.get(key) for key in ("date", "used", "limit")},
    }


def render_page(payload: dict) -> bytes:
    def esc(value):
        return html.escape("—" if value is None else str(value))

    rows = []
    for track in ("mock", "live"):
        stats = payload["tracks"][track]
        rate = stats["records_per_second_last_run"]
        rate_text = "N/A" if rate is None else str(rate)
        paused = "Yes" if stats["captcha_paused"] else "No"
        rows.append(
            "<tr><th scope='row'>{}</th><td>{}</td><td>{}</td><td>{}</td>"
            "<td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                track.upper(), esc(stats["targeted"]), esc(stats["success"]),
                esc(stats["pending"]), esc(stats["failed"]), esc(stats["duplicates"]),
                esc(stats["captcha_events_total"]), paused, esc(rate_text),
            )
        )
    quota = payload["live_quota"]
    snapshot = esc(payload["snapshot_generated_at"])
    quota_line = "{} / {} live searches on {}".format(
        esc(quota["used"]), esc(quota["limit"]), esc(quota["date"])
    )
    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CoA Pipeline — Public Status</title><style>
body{{font:15px system-ui,sans-serif;max-width:1050px;margin:36px auto;padding:0 20px;color:#172033;background:#f7f8fa}}
h1{{margin:0 0 4px}}p{{color:#586579}}.card{{background:white;border:1px solid #e1e5eb;border-radius:10px;padding:16px 20px;margin:18px 0}}
.quota{{font-size:20px;font-weight:650;color:#174c3a}}table{{width:100%;border-collapse:collapse;background:white}}
th,td{{text-align:left;padding:12px 10px;border-bottom:1px solid #e7eaf0;white-space:nowrap}}
thead th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#596579;background:#f4f6f9}}
footer{{margin-top:16px;color:#697586;font-size:13px}}@media(max-width:850px){{.card{{overflow-x:auto}}}}
</style></head><body><h1>CoA Directory Pipeline</h1>
<p>Public, read-only pipeline status. Individual architect records and personal details are not displayed.</p>
<section class="card"><div class="quota">Live quota snapshot: {quota}</div></section>
<section class="card"><table><thead><tr><th>Track</th><th>Targeted</th><th>Success</th><th>Pending</th>
<th>Failed</th><th>Duplicates</th><th>CAPTCHA events</th><th>Paused now</th><th>Records/sec<br>last run</th></tr></thead>
<tbody>{rows}</tbody></table></section><footer>Aggregate snapshot generated {snapshot}. JSON: <a href="/api">/api</a></footer>
</body></html>""".format(quota=quota_line, rows="\n".join(rows), snapshot=snapshot)
    return document.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ("/", "/api"):
            self._send(404, b"Not found\n", "text/plain; charset=utf-8")
            return
        try:
            payload = public_payload()
            if path == "/api":
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            else:
                self._send(200, render_page(payload), "text/html; charset=utf-8")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self._send(500, ("Dashboard snapshot unavailable: " + str(exc) + "\n").encode(), "text/plain; charset=utf-8")

    def do_POST(self):
        self._send(405, b"Read-only dashboard: POST is not supported.\n", "text/plain; charset=utf-8")

    def do_PUT(self):
        self._send(405, b"Read-only dashboard: PUT is not supported.\n", "text/plain; charset=utf-8")

    def do_DELETE(self):
        self._send(405, b"Read-only dashboard: DELETE is not supported.\n", "text/plain; charset=utf-8")

    def log_message(self, *_args):
        pass


def main():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("Read-only public dashboard listening on 0.0.0.0:{}".format(port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
