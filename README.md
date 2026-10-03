# CoA Directory Extraction Pipeline

This Python 3.10+ project demonstrates a Council of Architecture (CoA) directory extraction pipeline. Track A runs against local fixtures. Track B makes real requests only when explicitly selected, pauses for an operator to read each CAPTCHA, and enforces a persistent daily POST cap. No third-party Python packages are required.

## Start here

Open PowerShell in the project directory:

```powershell
cd 'C:\Users\Shreyas\CoA Directory Extraction Pipeline'
python --version
```

The SQLite database and tables are created automatically on the first CLI command. The main entry point is `pipeline.py`.

## Track A: local mock

Add one or more jobs, then run them:

```powershell
python pipeline.py add demo-state --mode 4
python pipeline.py add demo-district --mode 5
python pipeline.py run --track mock --workers 4
```

Track A is the default for both `run` and `resume`; it never contacts the CoA site. `--workers N` controls mock concurrency and defaults to 4. Track A returns three fixture rows per successful job, with HTML parsing, address and registration-year derivation, deduplication, and the anonymous-results marker applied.

To reproduce the mock challenge controls, a target containing `captcha` pauses as a mock CAPTCHA event; continue it with `python pipeline.py solve-mock --job-id N`. A target containing `fail` injects a failed job. Failed jobs are retried by `resume`.

The standalone local HTTP fixture server can be run with `python mock_server.py` and opened at `http://127.0.0.1:8765/result?target=demo`. Targets containing `fail` return a simulated 503; targets containing `captcha` return a simulated verification response. This server is local and does not contact coa.gov.in.

## Track B: real, operator-supervised searches

First inspect the current mode lookup values. This only GETs search-form pages; it does not consume a search POST:

```powershell
python investigate.py
```

Use the form field's actual value when adding a live job. State mode uses numeric `state_id`, City/District uses numeric `district_id`, Registration Year uses the internal `arc_reg_date` code, and Pincode uses exactly six digits. For example, Maharashtra's `state_id` is `27`:

```powershell
python pipeline.py add 27 --mode 4 --track live
python pipeline.py run --track live
```

The live run fetches the form and its CAPTCHA image in the same session, prints the image path, and pauses. Read that image, then submit its exact case-sensitive text using the job ID printed by the command:

```powershell
python pipeline.py solve-captcha --job-id 1 --code 'CAPTCHA_TEXT'
```

`solve-captcha` is the operation that submits one real search POST. It records the attempt in the quota database before sending; even timeouts count. A successful anonymous response normally contains up to three rows and the expected login/purchase end marker. Failed or ambiguous responses need operator review before another POST.

**Live safety limits:** choose `--track live` explicitly for both `run` and `resume`; these commands otherwise default to mock. Live processing always forces one worker, regardless of `--workers`. The hard cap is **3 live POSTs per local calendar day**. It is persisted in `live_quota` in SQLite and cannot be raised through a CLI option. It is a per-local-database guard, not a cross-machine IP coordinator; do not run separate copies against the site to evade or exceed the site's anonymous limit. Respect the site's robots.txt and terms. Never automate CAPTCHA solving.

## Interactive public demo on Render

`web_app.py` is a separate interactive entry point for grading. It imports the existing Track A functions from `pipeline.py`, adds fresh mock jobs, and runs them from the page. It displays the resulting synthetic mock records with filtering and pagination. Public visitors see Track B aggregate status only. Live controls require a short-lived administrator session created with the `ADMIN_ACCESS_CODE` environment variable; they reuse the existing live job, CAPTCHA, quota, and submit functions in `pipeline.py`.

The service uses only the Python standard library; `requirements.txt` intentionally contains no third-party dependencies. Configure the Render Web Service with:

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python web_app.py`

Render supplies `PORT`; the app reads it from the environment and binds to `0.0.0.0`. The interactive demo uses the pipeline database for mock rows and current aggregate counts. Keep the database persistent if you want demo history to survive redeploys.

For local use, `web_app.py` loads `.env` without an extra dependency; `.env` is Git-ignored and `.env.example` documents the required values. Process environment values override `.env`. For Render, add `ADMIN_ACCESS_CODE` in Environment settings as a secret value. Use a long random string (for example, 32 or more random characters); never commit it to the repository. The administrator session expires after 15 minutes. The live site still enforces its independent three-search daily limit.

## Resume, status, dashboard, and export

`resume` processes pending, failed, or interrupted (`in_progress`) jobs and skips successful jobs:

```powershell
python pipeline.py resume --track mock --workers 4
python pipeline.py resume --track live
```

The recorded crash/skip/recovery demonstration is in `RESUME_DEMO.md`.

Start the local dashboard with `python pipeline.py dashboard --port 8000`, then open [http://127.0.0.1:8000](http://127.0.0.1:8000). The human-readable table displays job totals, success, pending, failure, duplicate and CAPTCHA counts, current pause state, live quota, processing rate, and last activity. Programmatic JSON is available at `/api`. Stop the foreground dashboard with Ctrl+C.

Export the current architect rows to JSON with:

```powershell
python pipeline.py export
```

## Configuration and file locations

The CLI options and fixed limits are:

- `add TARGET... --mode 1..6 --track mock|live`: mode is required; `--track` defaults to `mock`.
- `run` / `resume --track mock|live --workers N`: workers defaults to 4 for mock. Live is always 1.
- `dashboard --port N`: port defaults to 8000.
- Daily live cap: fixed at 3 POSTs/day; enforced and stored by the database, not configurable.
- `solve-captcha --job-id N --code TEXT`: submits one operator-solved CAPTCHA challenge.

All project data is relative to this directory, `C:\Users\Shreyas\CoA Directory Extraction Pipeline`:

- SQLite database: `coa_pipeline.sqlite3`
- Private, unredacted JSON export (gitignored): `architects_export.json`
- Public, redacted JSON dataset: `architects_export.redacted.json`
- Structured event log: `pipeline.jsonl`
- Session-bound CAPTCHA images: `captchas\job-N.jpg`
- Current form field/dropdown lookup: `form_lookups.json`
- Schema reference: `schema.sql`
- Public dashboard aggregate snapshot: `public_dashboard_stats.json`

## Data fields and limits

The parser stores architect name, registration number and derived year, raw and normalized address, derived state/city/pincode, raw and normalized phone, email, and disciplinary action. It preserves the raw address. Registration status is NULL because it is not exposed in anonymous results. Address derivation is best-effort for the source's free-text tail format. The row-level login/purchase prompt is treated as the normal end of anonymous results.

### Public dataset privacy

Use `architects_export.redacted.json` for public sharing. In live-track rows it replaces names with `Architect #<id>`, blanks phone and email fields, and redacts street-level address text while retaining the city/state/pincode tail. Registration number, derived year, location fields, disciplinary action, and pipeline metadata remain unchanged so the extraction and parsing work can still be reviewed. Mock rows remain unchanged because they are synthetic. The unredacted `architects_export.json`, `coa_pipeline.sqlite3`, and `captchas/` directory are excluded by `.gitignore`; the database is a private local working artifact and is not part of the public dataset.

The site is described as allowing three anonymous searches per IP per day and showing at most three rows per search. In the three live searches recorded in the included dataset, we received nine rows total (three each), consistent with an approximate ceiling of nine visible records per day. This project does not attempt to retrieve data beyond that limit.

## Project files

- `pipeline.py`: CLI, database layer, parser, deduplication, mock and live adapters, quota ledger, and dashboard.
- `public_dashboard.py`: previous aggregate-only status page/API.
- `web_app.py`: interactive Track A demonstration interface and admin-gated Track B controls.
- `public_dashboard_stats.json`: sanitized counters and timestamps used by the public dashboard.
- `build_public_stats.py`: local-only aggregate snapshot generator; never included in the deployed request path.
- `requirements.txt`: documents that the public dashboard requires no external packages.
- `mock_server.py`: isolated local fixture HTTP server.
- `investigate.py`: GET-only form inspection and dropdown export.
- `schema.sql`: SQLite schema reference.
- `form_lookups.json`: captured field metadata and select lookup values.
- `INVESTIGATION.md`: source observations, lookup findings, and live-search evidence.
- `RESUME_DEMO.md`: before/after evidence for interruption, successful-job skipping, and recovery of an in-progress mock job.
