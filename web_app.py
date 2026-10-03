#!/usr/bin/env python3
"""Public Track A demo plus administrator-gated Track B controls."""
from __future__ import annotations

import hmac
import html
import json
import math
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pipeline

PAGE_SIZE, SESSION_SECONDS = 12, 900
DEMO_LOCK, LIVE_LOCK, SESSION_LOCK = threading.Lock(), threading.Lock(), threading.Lock()
SESSIONS: dict[str, float] = {}
ROOT = Path(__file__).resolve().parent

def load_dotenv() -> None:
    """Load local settings without adding a python-dotenv dependency.

    Existing process variables win, so Render's secret environment variables
    always override values in a developer's local .env file.
    """
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)

load_dotenv()

def esc(value: object) -> str: return html.escape("—" if value is None or value == "" else str(value))

def load_lookups() -> dict[int, list[tuple[str, str]]]:
    data = json.loads(Path(__file__).with_name("form_lookups.json").read_text(encoding="utf-8"))
    return {mode: [(str(v), str(label)) for v, label in data["forms"][str(mode)]["selects"][field] if str(v) not in ("", "-1")]
            for mode, field in ((4, "state_id"), (5, "district_id"))}
LOOKUPS = load_lookups()

def quota(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT posts_used FROM live_quota WHERE quota_date=?", (date.today().isoformat(),)).fetchone()
    return row[0] if row else 0

def stats(connection: sqlite3.Connection) -> dict:
    result = {}
    for track in ("mock", "live"):
        counts = {}
        for name, sql in {"targeted":"count(*) FROM extraction_jobs WHERE track=?", "success":"count(*) FROM extraction_jobs WHERE track=? AND status='success'", "pending":"count(*) FROM extraction_jobs WHERE track=? AND status IN ('pending','in_progress')", "failed":"count(*) FROM extraction_jobs WHERE track=? AND status='failed'", "duplicates":"count(*) FROM architects WHERE extraction_track=? AND is_duplicate=1", "records":"count(*) FROM architects WHERE extraction_track=?", "captcha":"coalesce(sum(captcha_events),0) FROM extraction_runs WHERE track=?"}.items():
            counts[name] = connection.execute("SELECT " + sql, (track,)).fetchone()[0]
        last = connection.execute("SELECT started_at, ended_at, records_success FROM extraction_runs WHERE track=? ORDER BY id DESC LIMIT 1", (track,)).fetchone()
        rate = None
        if last and last["ended_at"]:
            seconds = (datetime.fromisoformat(last["ended_at"]) - datetime.fromisoformat(last["started_at"])).total_seconds()
            rate = round(last["records_success"] / seconds, 2) if seconds else None
        counts["rate"] = rate
        result[track] = counts
    return {"tracks": result, "quota": quota(connection)}

def records(connection: sqlite3.Connection, track: str, term: str = "", page: int = 1) -> tuple[list[sqlite3.Row], int]:
    where, params = "extraction_track=?", [track]
    if term:
        where += " AND (name LIKE ? OR registration_number LIKE ? OR state LIKE ? OR city LIKE ? OR pincode LIKE ? OR email LIKE ?)"
        params += ["%" + term + "%"] * 6
    total = connection.execute("SELECT count(*) FROM architects WHERE " + where, params).fetchone()[0]
    rows = connection.execute("SELECT name,registration_number,year_of_registration,state,city,pincode,phone_normalized,email,is_duplicate FROM architects WHERE " + where + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    return rows, total

def run_records(connection: sqlite3.Connection, run_id: int, track: str) -> list[sqlite3.Row]:
    run = connection.execute("SELECT started_at FROM extraction_runs WHERE id=? AND track=?", (run_id, track)).fetchone()
    if not run: return []
    # Live runs finish their CAPTCHA-preparation phase before the operator submits
    # the answer, so their rows can be timestamped after extraction_runs.ended_at.
    # Use the next run's start as the boundary; this includes those later rows and
    # still keeps them out of older run views once a newer run has begun.
    next_run = connection.execute("SELECT min(started_at) FROM extraction_runs WHERE track=? AND id>?", (track, run_id)).fetchone()[0]
    sql = "SELECT name,registration_number,year_of_registration,state,city,pincode,phone_normalized,email,is_duplicate FROM architects WHERE extraction_track=? AND extraction_timestamp>=?"
    params: list[object] = [track, run["started_at"]]
    if next_run:
        sql += " AND extraction_timestamp<?"
        params.append(next_run)
    return connection.execute(sql + " ORDER BY id DESC", params).fetchall()

def table(rows: list[sqlite3.Row], empty: str) -> str:
    heads = ("Name","Registration number","Year","State","City","Pincode","Phone","Email","Duplicate status")
    body = "".join("<tr>" + "".join("<td>{}</td>".format(esc(v)) for v in (r["name"],r["registration_number"],r["year_of_registration"],r["state"],r["city"],r["pincode"],r["phone_normalized"],r["email"],"Duplicate" if r["is_duplicate"] else "New")) + "</tr>" for r in rows)
    if not body: body = "<tr><td class='empty' colspan='9'>{}</td></tr>".format(esc(empty))
    return "<div class='table-wrap'><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>".format("".join("<th>{}</th>".format(h) for h in heads), body)

def run_demo() -> int:
    token = uuid.uuid4().hex[:10]
    with DEMO_LOCK:
        with pipeline.db() as c: before = c.execute("SELECT coalesce(max(id),0) FROM extraction_runs").fetchone()[0]
        pipeline.add_jobs(SimpleNamespace(targets=["grader-demo-{}-{}".format(token, x) for x in ("alpha","bravo","charlie")], mode=1, track="mock"))
        pipeline.run_jobs("mock", workers=4)
        with pipeline.db() as c: run = c.execute("SELECT id FROM extraction_runs WHERE id>? AND track='mock' ORDER BY id DESC LIMIT 1", (before,)).fetchone()
    return run["id"]

def start_live(mode: int, target: str) -> int | None:
    if mode not in LOOKUPS or target not in {v for v, _ in LOOKUPS[mode]}: return None
    with LIVE_LOCK:
        with pipeline.db() as c:
            if quota(c) >= 3: return -1
            before = c.execute("SELECT coalesce(max(id),0) FROM extraction_jobs").fetchone()[0]
        pipeline.add_jobs(SimpleNamespace(targets=[target], mode=mode, track="live"))
        pipeline.run_jobs("live", workers=1)  # Existing GET + CAPTCHA preparation; no search POST.
        with pipeline.db() as c: job = c.execute("SELECT id FROM extraction_jobs WHERE id>? AND track='live' ORDER BY id DESC LIMIT 1", (before,)).fetchone()
    return job["id"] if job else None

def submit_captcha(job_id: int, code: str) -> str:
    if not code.strip(): return "missing"
    with LIVE_LOCK:
        try: pipeline.solve(SimpleNamespace(job_id=job_id, code=code.strip()))
        except SystemExit as error: return str(error)
        with pipeline.db() as c: job = c.execute("SELECT status FROM extraction_jobs WHERE id=? AND track='live'", (job_id,)).fetchone()
    return job["status"] if job else "missing"

def fresh_captcha(job_id: int) -> int | None:
    with pipeline.db() as c: job = c.execute("SELECT search_mode,target_identifier FROM extraction_jobs WHERE id=? AND track='live'", (job_id,)).fetchone()
    return start_live(job["search_mode"], job["target_identifier"]) if job else None

def session(cookie: str | None) -> bool:
    item = SimpleCookie(cookie).get("coa_admin")
    if not item: return False
    with SESSION_LOCK:
        expiry = SESSIONS.get(item.value, 0)
        if expiry > time.monotonic(): return True
        SESSIONS.pop(item.value, None)
    return False

def login(code: str) -> str | None:
    expected = os.environ.get("ADMIN_ACCESS_CODE", "")
    if not expected or not hmac.compare_digest(code, expected): return None
    token = secrets.token_urlsafe(32)
    with SESSION_LOCK: SESSIONS[token] = time.monotonic() + SESSION_SECONDS
    return token

def options(mode: int) -> str: return "".join("<option value='{}'>{}</option>".format(esc(v),esc(label)) for v,label in LOOKUPS[mode])

STYLE = """<style>:root{--ink:#182334;--muted:#64748b;--paper:#fff;--bg:#f4f7fb;--line:#dce4ef;--brand:#176b59;--warn:#fff4d6}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}header{background:#102c3d;color:#fff;padding:28px max(24px,calc((100% - 1180px)/2))}.brand{font-size:1.4rem;font-weight:750}.subtitle{color:#c8d6df;margin:4px 0}nav{display:flex;gap:20px;flex-wrap:wrap}nav a{color:#e5f5f0;font-weight:650;text-decoration:none}main{max-width:1180px;margin:28px auto;padding:0 24px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin:20px 0}.hero{display:flex;align-items:center;justify-content:space-between;gap:20px;border-left:5px solid var(--brand)}h1,h2,h3,p{margin-top:0}.eyebrow{text-transform:uppercase;font-size:.73rem;font-weight:800;letter-spacing:.09em;color:var(--brand)}.muted,.pager{color:var(--muted)}button{background:var(--brand);color:white;border:0;border-radius:8px;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer}.secondary{background:#e8eef5;color:#26394f}.admin{border-left:5px solid #875c0b}.notice,.error{padding:13px;border-radius:9px}.notice{background:var(--warn);border:1px solid #f0d580}.error{background:#fde8e8;border:1px solid #efb2b2;color:#8d2525}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.stat{padding:14px;border:1px solid var(--line);border-radius:9px;background:#f7fafc}.stat b{display:block;font-size:1.4rem}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:9px}table{width:100%;min-width:880px;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left}thead{background:#f1f5f9}.empty{text-align:center;color:var(--muted)}.form{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:16px 0}.form label{display:grid;gap:4px;font-weight:650;flex:1;min-width:180px}input,select{padding:10px;border:1px solid #b9c7d8;border-radius:7px;font:inherit}.captcha{max-width:260px;border:1px solid var(--line);margin:14px 0;display:block}.pager{display:flex;justify-content:space-between;margin-top:14px}@media(max-width:700px){.hero{align-items:start;flex-direction:column}.stats{grid-template-columns:1fr}}</style>"""

def admin_panel(is_admin: bool, query: dict[str,list[str]], c: sqlite3.Connection) -> str:
    if not is_admin:
        error = "<p class='error'>Incorrect code.</p>" if query.get("admin_error") else ""
        return "<section id='live-admin' class='panel admin'><p class='eyebrow'>Track B · administrator only</p><h2>Live search access</h2><p class='muted'>Public visitors can view totals but cannot start searches or submit CAPTCHA answers.</p>{}<form class='form' method='post' action='/admin/login'><label>Admin access code<input type='password' name='code' required></label><button>Unlock live controls</button></form></section>".format(error)
    job_id = int(query.get("live_job",["0"])[0]) if query.get("live_job",["0"])[0].isdigit() else 0
    job = c.execute("SELECT * FROM extraction_jobs WHERE id=? AND track='live'", (job_id,)).fetchone() if job_id else None
    used = quota(c)
    controls = "<p class='notice'>Today's live search quota is used, resets tomorrow.</p>" if used >= 3 else "<form class='form' method='post' action='/admin/start-live'><label>Search mode<select name='mode' id='mode'><option value='4'>State</option><option value='5'>City / District</option></select></label><label id='state'>State<select name='state_target'>{}</select></label><label id='city' hidden>City / District<select name='city_target'>{}</select></label><button>Start Live Search</button></form><script>mode.onchange=()=>{{state.hidden=mode.value!='4';city.hidden=mode.value!='5'}}</script>".format(options(4),options(5))
    detail = ""
    if job and job["status"] == "captcha_blocked":
        error = "<p class='error'>{}</p>".format(esc(job["error_message"])) if job["error_message"] else ""
        detail = "<p class='notice'>Live job #{} is ready for your CAPTCHA answer. This preparation did not consume quota.</p>{}<img class='captcha' src='/admin/captcha?job={}' alt='Live CAPTCHA'><form class='form' method='post' action='/admin/submit-captcha'><input type='hidden' name='job_id' value='{}'><label>CAPTCHA text<input name='code' required autocomplete='off'></label><button>Submit CAPTCHA</button></form><form method='post' action='/admin/fresh-captcha'><input type='hidden' name='job_id' value='{}'><button class='secondary'>Get a fresh CAPTCHA image</button></form>".format(job["id"],error,job["id"],job["id"],job["id"])
    elif job and job["status"] == "success": detail = "<h3>Live extraction completed</h3>" + table(run_records(c,job["run_id"],"live") if job["run_id"] else [], "No records were returned.")
    elif job: detail = "<p class='error'>Live job #{}: {}</p>".format(job["id"],esc(job["error_message"] or job["status"]))
    return "<section id='live-admin' class='panel admin'><p class='eyebrow'>Track B · administrator only</p><h2>Live Search Controls</h2><p class='muted'>Signed in for 15 minutes. CAPTCHA submission uses the existing quota-protected pipeline flow.</p>{}{}</section>".format(controls,detail)

def render(query: dict[str,list[str]], is_admin: bool) -> bytes:
    term=query.get("q",[""])[0][:100]
    try: requested=max(1,int(query.get("page",["1"])[0]))
    except ValueError: requested=1
    demo=int(query.get("run",["0"])[0]) if query.get("run",["0"])[0].isdigit() else 0
    with pipeline.db() as c:
        data=stats(c); rows,total=records(c,"mock",term,requested); pages=max(1,math.ceil(total/PAGE_SIZE)); page=min(requested,pages)
        if page!=requested: rows,total=records(c,"mock",term,page)
        latest=table(run_records(c,demo,"mock"),"This run produced no records.") if demo else ""
        admin=admin_panel(is_admin,query,c)
    prev="?"+urlencode({"q":term,"page":page-1}) if page>1 else ""; nxt="?"+urlencode({"q":term,"page":page+1}) if page<pages else ""
    statrows="".join("<tr><th>{}</th>{}</tr>".format(t.upper(),"".join("<td>{}</td>".format(esc(x)) for x in (d["targeted"],d["success"],d["pending"],d["failed"],d["duplicates"],d["records"],d["captcha"],"N/A" if d["rate"] is None else d["rate"]))) for t,d in data["tracks"].items())
    doc="<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>CoA Directory Extraction Demo</title>{}</head><body><header><div class='brand'>CoA Directory Extraction Demo</div><p class='subtitle'>Public Track A extraction with protected Track B controls.</p><nav><a href='#run-demo'>Run a Demo Extraction</a><a href='#records'>Extracted Records</a><a href='#stats'>Pipeline Stats</a><a href='#live-admin'>Live Search</a></nav></header><main><section id='run-demo' class='panel hero'><div><p class='eyebrow'>Track A · safe mock data</p><h1>Run a Demo Extraction</h1><p class='muted'>Runs the existing mock parser, deduplication, and database writer.</p></div><form method='post' action='/run-demo'><button>Run Demo Extraction</button></form></section>{}<section class='panel' id='records'><h2>Browse Extracted Records</h2><form class='form'><label>Filter demo records<input name='q' value='{}' placeholder='Name, registration number, state, city, pincode, or email'></label><button class='secondary'>Filter</button></form>{}<div class='pager'><span>Page {} of {}</span><span>{} {}</span></div></section><section class='panel' id='stats'><h2>Pipeline Stats</h2><div class='stats'><div class='stat'>Mock records<b>{}</b></div><div class='stat'>Live quota used today<b>{} / 3</b></div><div class='stat'>Live successes<b>{}</b></div></div><div class='table-wrap'><table><thead><tr><th>Track</th><th>Jobs</th><th>Success</th><th>Pending</th><th>Failed</th><th>Duplicates</th><th>Records</th><th>CAPTCHA events</th><th>Records/sec last run</th></tr></thead><tbody>{}</tbody></table></div></section>{}</main></body></html>".format(STYLE,"<section class='panel'><h2>Newly extracted demo records</h2>{}</section>".format(latest) if demo else "",esc(term),table(rows,"No matching mock records."),page,pages,"<a href='{}'>← Previous</a>".format(prev) if prev else "","<a href='{}'>Next →</a>".format(nxt) if nxt else "",data["tracks"]["mock"]["records"],data["quota"],data["tracks"]["live"]["success"],statrows,admin)
    return doc.encode()

class Handler(BaseHTTPRequestHandler):
    def form(self):
        size=min(int(self.headers.get("Content-Length","0")),10_000); return {k:v[0] for k,v in parse_qs(self.rfile.read(size).decode("utf-8","replace")).items()}
    def admin(self): return session(self.headers.get("Cookie"))
    def body(self,status,body,kind):
        self.send_response(status); self.send_header("Content-Type",kind); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(body)
    def redirect(self,url,cookie=None):
        self.send_response(303); self.send_header("Location",url)
        if cookie: self.send_header("Set-Cookie",cookie)
        self.end_headers()
    def require(self):
        if self.admin(): return True
        self.body(403,b"Incorrect code.\n","text/plain; charset=utf-8"); return False
    def do_GET(self):
        req=urlsplit(self.path)
        if req.path=="/":
            try: self.body(200,render(parse_qs(req.query),self.admin()),"text/html; charset=utf-8")
            except (OSError,sqlite3.Error,ValueError,KeyError) as e: self.body(500,("Application error: {}\n".format(e)).encode(),"text/plain; charset=utf-8")
        elif req.path=="/admin/captcha":
            if not self.require(): return
            raw=parse_qs(req.query).get("job",["0"])[0]
            with pipeline.db() as c: job=c.execute("SELECT captcha_path FROM extraction_jobs WHERE id=? AND track='live'",(int(raw) if raw.isdigit() else 0,)).fetchone()
            path=Path(job["captcha_path"]) if job and job["captcha_path"] else None
            safe=path and path.is_file() and path.parent.resolve()==(pipeline.ROOT/"captchas").resolve()
            self.body(200,path.read_bytes(),"image/jpeg") if safe else self.body(404,b"Not found\n","text/plain; charset=utf-8")
        else: self.body(404,b"Not found\n","text/plain; charset=utf-8")
    def do_POST(self):
        path=urlsplit(self.path).path; form=self.form()
        if path=="/run-demo": self.redirect("/?"+urlencode({"run":run_demo()})+"#run-demo"); return
        if path=="/admin/login":
            token=login(form.get("code",""))
            if not token: self.redirect("/?admin_error=1#live-admin"); return
            secure="; Secure" if self.headers.get("X-Forwarded-Proto","").lower()=="https" else ""
            self.redirect("/#live-admin","coa_admin={}; Max-Age={}; HttpOnly; SameSite=Strict; Path=/{}".format(token,SESSION_SECONDS,secure)); return
        if not self.require(): return
        if path=="/admin/start-live":
            try: mode=int(form.get("mode","0")); job=start_live(mode,form.get("state_target" if mode==4 else "city_target",""))
            except (OSError,sqlite3.Error,ValueError) as e: self.body(500,("Live setup failed: {}\n".format(e)).encode(),"text/plain; charset=utf-8"); return
            self.redirect("/?live_quota=1#live-admin" if job==-1 else "/?"+urlencode({"live_job":job or 0})+"#live-admin"); return
        if path=="/admin/submit-captcha":
            raw=form.get("job_id","0"); job=int(raw) if raw.isdigit() else 0; result=submit_captcha(job,form.get("code","")); self.redirect("/?"+urlencode({"live_job":job,"live_result":result})+"#live-admin"); return
        if path=="/admin/fresh-captcha":
            raw=form.get("job_id","0"); job=fresh_captcha(int(raw) if raw.isdigit() else 0); self.redirect("/?"+urlencode({"live_job":job or 0})+"#live-admin"); return
        self.body(404,b"Not found\n","text/plain; charset=utf-8")
    def log_message(self,*args): pass

def main():
    port=int(os.environ.get("PORT","10000")); server=ThreadingHTTPServer(("0.0.0.0",port),Handler); print("Interactive demo listening on 0.0.0.0:{}".format(port),flush=True); server.serve_forever()
if __name__=="__main__": main()
