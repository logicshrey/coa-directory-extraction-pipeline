#!/usr/bin/env python3
"""CoA directory extraction demo. Mock is the safe default; live mode is opt-in."""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import re
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "coa_pipeline.sqlite3"
LOG_PATH = ROOT / "pipeline.jsonl"
BASE = "https://coa.gov.in"
YEAR_CODES = {44:1975,52:1976,55:1977,57:1978,58:1979,59:1980,60:1981,61:1982,62:1983,63:1984,64:1985,65:1986,66:1987,67:1988,69:1989,70:1990,71:1991,72:1992,73:1993,75:1994,76:1995,77:1996,78:1997,68:1998,79:1999,80:2000,81:2001,82:2002,83:2003,84:2004,85:2005,86:2006,87:2007,88:2008,89:2009,1:2010,2:2011,3:2012,4:2013,5:2014,90:2015,93:2016,94:2017,96:2018,98:2019,446:2020,936:2021,992:2022,1586:2023,1587:2024,1588:2025,1589:2026}
MODES = {1:("name","arc_name"),2:("year","arc_reg_date"),3:("registration","arc_reg"),4:("state","state_id"),5:("city","district_id"),6:("pincode","pin_code")}

def now(): return datetime.now(timezone.utc).isoformat(timespec="microseconds")
def log(event, **data):
    record={"time":now(),"event":event,**data}
    with LOG_PATH.open("a",encoding="utf-8") as f: f.write(json.dumps(record,ensure_ascii=False)+"\n")

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS architects (
 id INTEGER PRIMARY KEY, source_record_id TEXT, name TEXT, registration_number TEXT,
 year_of_registration INTEGER, address_raw TEXT, address_normalized TEXT, state TEXT, city TEXT, pincode TEXT,
 phone_raw TEXT, phone_normalized TEXT, email TEXT, disciplinary_action TEXT, registration_status TEXT,
 extra_fields TEXT NOT NULL DEFAULT '{}', source_url TEXT, search_params_used TEXT,
 extraction_timestamp TEXT, http_status INTEGER, is_duplicate INTEGER NOT NULL DEFAULT 0,
 duplicate_of_id INTEGER REFERENCES architects(id), processing_status TEXT NOT NULL DEFAULT 'success',
 error_message TEXT, retry_count INTEGER NOT NULL DEFAULT 0, extraction_track TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arch_reg ON architects(registration_number);
CREATE INDEX IF NOT EXISTS idx_arch_name_state ON architects(name,state);
CREATE INDEX IF NOT EXISTS idx_arch_status ON architects(processing_status);
CREATE TABLE IF NOT EXISTS extraction_jobs (
 id INTEGER PRIMARY KEY, target_identifier TEXT NOT NULL, search_mode INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, last_attempted_at TEXT,
 created_at TEXT NOT NULL, track TEXT NOT NULL, captcha_code TEXT, captcha_path TEXT,
 session_cookie TEXT, error_message TEXT, run_id INTEGER
);
CREATE TABLE IF NOT EXISTS extraction_runs (
 id INTEGER PRIMARY KEY, track TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
 records_found INTEGER NOT NULL DEFAULT 0, records_success INTEGER NOT NULL DEFAULT 0,
 records_failed INTEGER NOT NULL DEFAULT 0, captcha_events INTEGER NOT NULL DEFAULT 0,
 quota_used_today INTEGER NOT NULL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS live_quota (quota_date TEXT PRIMARY KEY, posts_used INTEGER NOT NULL DEFAULT 0);
"""
def db():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.executescript(SCHEMA)
    columns={r[1] for r in c.execute("PRAGMA table_info(extraction_jobs)")}
    if "run_id" not in columns: c.execute("ALTER TABLE extraction_jobs ADD COLUMN run_id INTEGER")
    c.commit(); return c

class TableParser(HTMLParser):
    def __init__(self): super().__init__(); self.in_grid=False; self.in_td=False; self.cell=""; self.row=[]; self.rows=[]; self.prompt=False; self.in_select=False; self.select_name=None; self.options={}
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=="tr" and "GridRow" in a.get("class",""): self.in_grid=True; self.row=[]
        if self.in_grid and tag=="td": self.in_td=True; self.cell=""
        if tag=="select": self.in_select=True; self.select_name=a.get("name")
        if tag=="option" and self.in_select: self.option_value=a.get("value",""); self.option_text=""
    def handle_data(self,data):
        if self.in_td: self.cell+=data
        if self.in_select and hasattr(self,"option_value"): self.option_text=getattr(self,"option_text","")+data
    def handle_endtag(self,tag):
        if tag=="td" and self.in_td: self.row.append(" ".join(self.cell.split())); self.in_td=False
        if tag=="tr" and self.in_grid:
            self.in_grid=False
            if self.row: self.rows.append(self.row)
        if tag=="option" and self.in_select and self.select_name:
            self.options.setdefault(self.select_name,[]).append((self.option_value," ".join(getattr(self,"option_text","").split())))
            del self.option_value
        if tag=="select": self.in_select=False; self.select_name=None

def derive_address(raw):
    # CoA address tail is usually CITY, STATE - PINCODE; preserve source verbatim.
    m=re.search(r"(?:,\s*)([^,]+),\s*([A-Z][A-Z .&()-]+?)\s*-\s*(\d{6})\s*$",raw)
    if not m: return None,None,None
    return m.group(2).strip(),m.group(1).strip(),m.group(3)

def parse_results(html):
    p=TableParser(); p.feed(html); records=[]
    for row in p.rows:
        if len(row)<7: continue
        name,reg,discipline,address,phone,email=row[1:7]
        state,city,pincode=derive_address(address)
        yr=re.search(r"CA/((?:19|20)\d{2})/",reg,re.I)
        records.append({"name":name,"registration_number":reg,"year_of_registration":int(yr.group(1)) if yr else None,"disciplinary_action":discipline,"address_raw":address,"state":state,"city":city,"pincode":pincode,"phone_raw":phone,"phone_normalized":re.sub(r"\D","",phone),"email":email,"registration_status":None,"extra_fields":{"s_no":row[0]}})
    return records, ("custm-table" in html and "purchase an Online Directory" in html)

def save_records(c, records, track, target, mode, status=200):
    for r in records:
        exists=c.execute("SELECT id FROM architects WHERE registration_number=? AND registration_number<>''",(r["registration_number"],)).fetchone()
        if not exists and r["name"] and r["state"]:
            exists=c.execute("SELECT id FROM architects WHERE lower(name)=lower(?) AND lower(state)=lower(?)",(r["name"],r["state"])).fetchone()
        dup=int(bool(exists)); payload=dict(r)
        c.execute("""INSERT INTO architects(source_record_id,name,registration_number,year_of_registration,address_raw,address_normalized,state,city,pincode,phone_raw,phone_normalized,email,disciplinary_action,registration_status,extra_fields,source_url,search_params_used,extraction_timestamp,http_status,is_duplicate,duplicate_of_id,processing_status,retry_count,extraction_track)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(r["registration_number"],r["name"],r["registration_number"],r["year_of_registration"],r["address_raw"]," ".join(r["address_raw"].split()),r["state"],r["city"],r["pincode"],r["phone_raw"],r["phone_normalized"],r["email"],r["disciplinary_action"],None,json.dumps(r["extra_fields"]),BASE,"{}",now(),status,dup,exists[0] if exists else None,"success",0,track))
    c.commit()

def mock_html(seed):
    names=[("Mira Shah","CA/2014/01234","No","12 Lake Road, South Mumbai, MAHARASHTRA -  400001","9876543210","mira@example.test"),("Arun Menon","CA/2002/29347","No","8 Park Street, Kolkata, WEST BENGAL -  700016","9833590162","arun@example.test"),("Leela Rao","CA/2020/04567","Yes","4 Residency Road, Bengaluru, KARNATAKA -  560025","9123456780","leela@example.test")]
    rows="".join("<tr class='GridRow'><td>%d</td>%s</tr>"%(i,"".join("<td>%s</td>"%v for v in (n,r,d,a,p,e))) for i,(n,r,d,a,p,e) in enumerate(names,1))
    return "<table class='table table-bordered custm-table'><tbody>"+rows+"<tr><td colspan='9'>To View further you are required to Login and purchase an Online Directory</td></tr></tbody></table>"

def add_jobs(args):
    c=db()
    for target in args.targets:
        c.execute("INSERT INTO extraction_jobs(target_identifier,search_mode,created_at,track) VALUES(?,?,?,?)",(target,args.mode,now(),args.track))
    c.commit(); print(f"Added {len(args.targets)} {args.track} job(s).")

def run_jobs(track, workers=4):
    c=db(); run=c.execute("INSERT INTO extraction_runs(track,started_at) VALUES(?,?)",(track,now())).lastrowid; c.commit()
    # A killed worker leaves its current job in_progress; resume it like any other unfinished job.
    jobs=c.execute("SELECT * FROM extraction_jobs WHERE track=? AND status IN ('pending','failed','in_progress') ORDER BY id",(track,)).fetchall()
    if track=="live": workers=1
    lock=threading.Lock()
    def process(job):
        conn=db(); conn.execute("UPDATE extraction_jobs SET status='in_progress',attempts=attempts+1,last_attempted_at=?,run_id=? WHERE id=?",(now(),run,job["id"])); conn.commit()
        if track=="mock":
            target=job["target_identifier"].lower()
            if "fail" in target:
                conn.execute("UPDATE extraction_jobs SET status='failed',error_message='Injected mock transient failure' WHERE id=?",(job["id"],)); conn.commit(); log("mock_injected_failure",job_id=job["id"]); conn.close(); return
            if "captcha" in target:
                conn.execute("UPDATE extraction_jobs SET status='captcha_blocked',error_message='Injected mock CAPTCHA pause' WHERE id=?",(job["id"],)); conn.execute("UPDATE extraction_runs SET captcha_events=captcha_events+1 WHERE id=?",(run,)); conn.commit(); log("mock_captcha_pause",job_id=job["id"]); conn.close(); return
            records,marker=parse_results(mock_html(job["target_identifier"]))
            with lock:
                save_records(conn,records,"mock",job["target_identifier"],job["search_mode"])
                conn.execute("UPDATE extraction_jobs SET status='success' WHERE id=?",(job["id"],)); conn.execute("UPDATE extraction_runs SET records_found=records_found+?,records_success=records_success+? WHERE id=?",(len(records),len(records),run)); conn.commit()
            log("mock_job_success",job_id=job["id"],records=len(records),end_marker=marker)
        else: live_prepare(conn,job,run)
        conn.close()
    if track=="mock" and workers>1:
        with ThreadPoolExecutor(max_workers=workers) as pool: list(pool.map(process,jobs))
    else:
        for job in jobs: process(job)
    c.execute("UPDATE extraction_runs SET ended_at=? WHERE id=?",(now(),run)); c.commit()
    print(f"Processed {len(jobs)} {track} job(s) with {workers} worker(s); run {run}.")

def solve_mock(args):
    c=db(); job=c.execute("SELECT * FROM extraction_jobs WHERE id=? AND track='mock' AND status='captcha_blocked'",(args.job_id,)).fetchone()
    if not job: raise SystemExit("No mock CAPTCHA-paused job with that ID.")
    records,marker=parse_results(mock_html(job["target_identifier"]))
    save_records(c,records,"mock",job["target_identifier"],job["search_mode"])
    if job["run_id"]:
        c.execute("UPDATE extraction_runs SET records_found=records_found+?,records_success=records_success+? WHERE id=?",(len(records),len(records),job["run_id"]))
    c.execute("UPDATE extraction_jobs SET status='success',error_message=NULL WHERE id=?",(job["id"],)); c.commit()
    log("mock_challenge_resolved",job_id=job["id"],records=len(records)); print(f"Mock challenge completed; stored {len(records)} rows.")

def live_prepare(c,job,run):
    # A GET only obtains form/CAPTCHA; POST remains gated on explicit operator input.
    mode=job["search_mode"]; url=f"{BASE}/search_arch2.php?lang=1&level=1&lid=289&searCat={mode}"
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        html=opener.open(url,timeout=30).read()
        jar=next(h.cookiejar for h in opener.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
        cookie="; ".join(f"{x.name}={x.value}" for x in jar)
        image_url=BASE+"/app/admin/captcha/php_captcha.php"
        req=urllib.request.Request(image_url,headers={"Cookie":cookie,"Referer":url})
        image=urllib.request.urlopen(req,timeout=30).read()
        capdir=ROOT/"captchas"; capdir.mkdir(exist_ok=True); path=capdir/f"job-{job['id']}.jpg"; path.write_bytes(image)
        c.execute("UPDATE extraction_jobs SET status='captcha_blocked',captcha_path=?,session_cookie=? WHERE id=?",(str(path),cookie,job["id"])); c.execute("UPDATE extraction_runs SET captcha_events=captcha_events+1 WHERE id=?",(run,)); c.commit()
        log("captcha_required",job_id=job["id"],image=str(path)); print(f"Job {job['id']} paused for CAPTCHA. Image: {path}. Solve with: python pipeline.py solve-captcha --job-id {job['id']} --code YOUR_TEXT")
    except Exception as e:
        c.execute("UPDATE extraction_jobs SET status='failed',error_message=? WHERE id=?",(str(e),job["id"])); c.commit(); log("live_form_error",job_id=job["id"],error=str(e)); print(f"Live GET failed for job {job['id']}: {e}")

def solve(args):
    c=db(); job=c.execute("SELECT * FROM extraction_jobs WHERE id=? AND status='captcha_blocked'",(args.job_id,)).fetchone()
    if not job: raise SystemExit("No CAPTCHA-paused job with that ID.")
    day=date.today().isoformat()
    c.execute("INSERT OR IGNORE INTO live_quota(quota_date,posts_used) VALUES(?,0)",(day,))
    used=c.execute("SELECT posts_used FROM live_quota WHERE quota_date=?",(day,)).fetchone()[0]
    if used>=3: raise SystemExit("Daily live POST cap reached (3/3). No request was sent.")
    mode=job["search_mode"]; field=MODES[mode][1]
    val=job["target_identifier"]
    if mode==2 and val.isdigit():
        if int(val) in YEAR_CODES: val=str(int(val))
        elif int(val) in YEAR_CODES.values(): val=str(next(k for k,v in YEAR_CODES.items() if v==int(val)))
        else: raise SystemExit("Year target must be a supported year or arc_reg_date code.")
    if mode==4 and not val.isdigit(): raise SystemExit("State target must be a numeric state_id.")
    if mode==6 and not re.fullmatch(r"\d{6}",val): raise SystemExit("Pincode must be exactly six digits.")
    data={"val_sub":"1","searCat":str(mode),field:val,"cap_code":args.code,"T3":args.code,"submit":"Search"}
    url=f"{BASE}/search_architectResult.php?lang=1&level=1&linkid=&lid=289"
    req=urllib.request.Request(url,data=urllib.parse.urlencode(data).encode(),headers={"Content-Type":"application/x-www-form-urlencoded","Cookie":job["session_cookie"] or "","Referer":f"{BASE}/search_arch2.php?lang=1&level=1&lid=289&searCat={mode}"})
    # Count the attempt before sending. Even a timeout consumes quota conservatively.
    c.execute("UPDATE live_quota SET posts_used=posts_used+1 WHERE quota_date=?",(day,))
    c.execute("UPDATE extraction_jobs SET status='in_progress' WHERE id=?",(job["id"],)); c.commit()
    try:
        with urllib.request.urlopen(req,timeout=30) as response: body=response.read().decode("utf-8","replace"); code=response.status; setcookie=response.headers.get("Set-Cookie","")
        count=re.search(r"(?:^|;\s*)count=([^;]+)",job["session_cookie"] or ""); log("live_post",job_id=job["id"],http_status=code,count_cookie=count.group(1) if count else None)
        if "custm-table" not in body:
            c.execute("UPDATE extraction_jobs SET status='captcha_blocked',error_message='Response did not contain results table; inspect CAPTCHA/session.' WHERE id=?",(job["id"],)); c.commit(); print("No results table; job returned to CAPTCHA pause for operator review."); return
        records,marker=parse_results(body); save_records(c,records,"live",val,mode,code)
        if job["run_id"]:
            c.execute("UPDATE extraction_runs SET records_found=records_found+?,records_success=records_success+? WHERE id=?",(len(records),len(records),job["run_id"]))
        c.execute("UPDATE extraction_jobs SET status='success',captcha_code=?,error_message=NULL WHERE id=?",(args.code,job["id"])); c.commit(); log("live_job_success",job_id=job["id"],records=len(records),end_marker=marker); print(f"Stored {len(records)} row(s); login marker={marker}.")
    except Exception as e:
        c.execute("UPDATE extraction_jobs SET status='failed',error_message=? WHERE id=?",(str(e),job["id"])); c.commit(); log("live_post_error",job_id=job["id"],error=str(e)); print(f"POST failed: {e}")

def dashboard(port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            c=db(); counts={}
            for track in ("mock","live"):
                counts[track]={"targeted":c.execute("SELECT count(*) FROM extraction_jobs WHERE track=?",(track,)).fetchone()[0],"success":c.execute("SELECT count(*) FROM extraction_jobs WHERE track=? AND status='success'",(track,)).fetchone()[0],"pending":c.execute("SELECT count(*) FROM extraction_jobs WHERE track=? AND status IN ('pending','in_progress')",(track,)).fetchone()[0],"failed":c.execute("SELECT count(*) FROM extraction_jobs WHERE track=? AND status='failed'",(track,)).fetchone()[0],"duplicates":c.execute("SELECT count(*) FROM architects WHERE extraction_track=? AND is_duplicate=1",(track,)).fetchone()[0],"records":c.execute("SELECT count(*) FROM architects WHERE extraction_track=?",(track,)).fetchone()[0],"captcha":c.execute("SELECT count(*) FROM extraction_jobs WHERE track=? AND status='captcha_blocked'",(track,)).fetchone()[0]}
                latest=c.execute("SELECT started_at,ended_at,records_success FROM extraction_runs WHERE track=? ORDER BY id DESC LIMIT 1",(track,)).fetchone()
                last=c.execute("SELECT max(last_attempted_at) FROM extraction_jobs WHERE track=?",(track,)).fetchone()[0]
                captcha_total=c.execute("SELECT coalesce(sum(captcha_events),0) FROM extraction_runs WHERE track=?",(track,)).fetchone()[0]
                speed=None
                if latest and latest[1]:
                    seconds=(datetime.fromisoformat(latest[1])-datetime.fromisoformat(latest[0])).total_seconds()
                    if seconds>0: speed=round(latest[2]/seconds,2)
                counts[track].update({"run_started_at":latest[0] if latest else None,"last_activity_at":last,"records_per_second_last_run":speed,"captcha_events_total":captcha_total})
            used_row=c.execute("SELECT posts_used FROM live_quota WHERE quota_date=?",(date.today().isoformat(),)).fetchone()
            today=used_row[0] if used_row else 0
            payload=json.dumps({"counts":counts,"live_searches_today":today,"live_daily_cap":3,"captcha_paused":counts['live']['captcha']>0,"updated_at":now()})
            page="""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CoA Pipeline Dashboard</title><style>
body{font:15px system-ui,sans-serif;max-width:1100px;margin:36px auto;padding:0 20px;color:#172033;background:#f7f8fa}h1{margin-bottom:4px}.sub{color:#596579;margin-top:0}.card{background:white;border:1px solid #e1e5eb;border-radius:10px;padding:16px 20px;margin:18px 0;box-shadow:0 2px 8px #1720330a}.quota{font-size:20px;font-weight:650}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px 10px;border-bottom:1px solid #e7eaf0;white-space:nowrap}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#596579;background:#f4f6f9}.badge{display:inline-block;border-radius:99px;padding:4px 9px;background:#e8f5ee;color:#17643a}.paused{background:#fff3d7;color:#825900}footer{color:#697586;font-size:13px;margin-top:16px}#error{color:#9f2525}
</style><h1>CoA Directory Pipeline</h1><p class="sub">Mock processing and quota-limited live extraction status</p><div class="card"><div class="quota">Live quota: <span id="quota">Loading…</span></div><div id="pause"></div></div><div class="card"><table><thead><tr><th>Track</th><th>Targeted</th><th>Success</th><th>Pending</th><th>Failed</th><th>Duplicates</th><th>CAPTCHA events</th><th>Paused now</th><th>Records/sec<br>last run</th><th>Last activity</th></tr></thead><tbody id="rows"><tr><td colspan="10">Loading…</td></tr></tbody></table></div><footer id="updated"></footer><p id="error"></p><script>
fetch('/api').then(r=>r.json()).then(d=>{document.querySelector('#quota').textContent=d.live_searches_today+'/'+d.live_daily_cap+' searches today';document.querySelector('#pause').innerHTML=d.captcha_paused?'<span class="badge paused">Live track paused for CAPTCHA</span>':'<span class="badge">No live CAPTCHA pause</span>';const body=document.querySelector('#rows');body.innerHTML='';for(const key of ['mock','live']){const x=d.counts[key],tr=document.createElement('tr'),values=[key.toUpperCase(),x.targeted,x.success,x.pending,x.failed,x.duplicates,x.captcha_events_total,x.captcha?'Yes':'No',x.records_per_second_last_run===null?'N/A':x.records_per_second_last_run,(x.last_activity_at||'—').replace('T',' ')];for(const v of values){const td=document.createElement('td');td.textContent=v;tr.appendChild(td)}body.appendChild(tr)}document.querySelector('#updated').textContent='Updated '+d.updated_at.replace('T',' ')+' UTC';}).catch(e=>document.querySelector('#error').textContent='Could not load dashboard data: '+e);
</script></html>"""
            if self.path=="/api": body=payload.encode(); typ="application/json"
            else: body=page.encode(); typ="text/html; charset=utf-8"
            self.send_response(200); self.send_header("Content-Type",typ); self.end_headers(); self.wfile.write(body)
        def log_message(self,*a): pass
    print(f"Dashboard at http://127.0.0.1:{port}"); http.server.ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()

def export():
    c=db(); rows=[dict(r) for r in c.execute("SELECT * FROM architects ORDER BY id")]; out=ROOT/"architects_export.json"; out.write_text(json.dumps(rows,indent=2,ensure_ascii=False),encoding="utf-8"); print(f"Exported {len(rows)} architect rows to {out}")

def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("add"); a.add_argument("targets",nargs="+"); a.add_argument("--mode",type=int,choices=MODES,required=True); a.add_argument("--track",choices=("mock","live"),default="mock"); a.set_defaults(fn=add_jobs)
    for cmd in ("run","resume"):
        a=sub.add_parser(cmd); a.add_argument("--track",choices=("mock","live"),default="mock"); a.add_argument("--workers",type=int,default=4); a.set_defaults(fn=lambda x:run_jobs(x.track,max(1,x.workers)))
    a=sub.add_parser("solve-captcha"); a.add_argument("--job-id",type=int,required=True); a.add_argument("--code",required=True); a.set_defaults(fn=solve)
    a=sub.add_parser("solve-mock"); a.add_argument("--job-id",type=int,required=True); a.set_defaults(fn=solve_mock)
    a=sub.add_parser("dashboard"); a.add_argument("--port",type=int,default=8000); a.set_defaults(fn=lambda x:dashboard(x.port))
    a=sub.add_parser("export"); a.set_defaults(fn=lambda x:export())
    args=p.parse_args(); logging.basicConfig(level=logging.INFO); args.fn(args)
if __name__=="__main__": main()
