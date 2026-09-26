"""Create an aggregate-only public dashboard snapshot from the local SQLite DB.

Run locally before deployment. This script writes counts/timestamps only; it never
selects or exports architect names, contact details, or address fields.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "coa_pipeline.sqlite3"
OUTPUT = ROOT / "public_dashboard_stats.json"


def main():
    connection = sqlite3.connect(DB_FILE)
    tracks = {}
    for track in ("mock", "live"):
        statuses = dict(connection.execute(
            "SELECT status, count(*) FROM extraction_jobs WHERE track=? GROUP BY status", (track,)
        ).fetchall())
        latest = connection.execute(
            "SELECT started_at, ended_at, records_success FROM extraction_runs "
            "WHERE track=? ORDER BY id DESC LIMIT 1", (track,)
        ).fetchone()
        rate = None
        # Manual CAPTCHA waits are outside the live worker's run timing; expose
        # processing throughput only for mock runs where that rate is meaningful.
        if track == "mock" and latest and latest[0] and latest[1]:
            seconds = (datetime.fromisoformat(latest[1]) - datetime.fromisoformat(latest[0])).total_seconds()
            if seconds > 0:
                rate = round(latest[2] / seconds, 2)
        tracks[track] = {
            "targeted": connection.execute("SELECT count(*) FROM extraction_jobs WHERE track=?", (track,)).fetchone()[0],
            "success": statuses.get("success", 0),
            "pending": statuses.get("pending", 0) + statuses.get("in_progress", 0),
            "failed": statuses.get("failed", 0),
            "duplicates": connection.execute("SELECT count(*) FROM architects WHERE extraction_track=? AND is_duplicate=1", (track,)).fetchone()[0],
            "records": connection.execute("SELECT count(*) FROM architects WHERE extraction_track=?", (track,)).fetchone()[0],
            "captcha_events_total": connection.execute("SELECT coalesce(sum(captcha_events),0) FROM extraction_runs WHERE track=?", (track,)).fetchone()[0],
            "captcha_paused": connection.execute("SELECT count(*) FROM extraction_jobs WHERE track=? AND status='captcha_blocked'", (track,)).fetchone()[0],
            "records_per_second_last_run": rate,
            "last_activity_at": connection.execute("SELECT max(last_attempted_at) FROM extraction_jobs WHERE track=?", (track,)).fetchone()[0],
        }
    quota = connection.execute("SELECT quota_date,posts_used FROM live_quota ORDER BY quota_date DESC LIMIT 1").fetchone()
    payload = {
        "snapshot_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tracks": tracks,
        "live_quota": {"date": quota[0] if quota else None, "used": quota[1] if quota else 0, "limit": 3},
    }
    connection.close()
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Wrote aggregate-only dashboard snapshot to {}".format(OUTPUT))


if __name__ == "__main__":
    main()
