# Council of Architecture directory investigation

This file consolidates the supplied `INVESTIGATION (1).md` and `CODEX_PROMPT.md`. The observations below were supplied as prior browser inspection on 26 Sep 2026; they are not independently reconfirmed by this implementation.

## Access and request constraints

Anonymous access is described as three searches per day per IP, with up to three visible result rows per search (approximately nine rows/day). CAPTCHA is required on each submission and is session-bound. No CAPTCHA solving is automated. Live POSTs are opt-in, concurrency is one, and the app persists a maximum of three daily attempts before transmission; a failed/timeout attempt still consumes its local allowance. This local guard cannot coordinate separate machines sharing an IP, so operators must not run independent copies against the site on the same day.

State-search endpoint: `POST https://coa.gov.in/search_architectResult.php?lang=1&level=1&linkid=&lid=289`; form page: `search_arch2.php?...&searCat=N`. Common fields are `val_sub=1`, `searCat`, a mode-specific input, matching `cap_code` and `T3`, and `submit=Search`. Keep form page, CAPTCHA image, and POST in the same PHP session.

## Search modes

| searCat | Mode | Field | Type |
|---|---|---|---|
| 1 | Name | `arc_name`, `m_name`, `l_name` | text; middle and last are optional |
| 2 | Registration year | `arc_reg_date` | select, internal code |
| 3 | Registration number | `arc_reg` | text |
| 4 | State | `state_id` | numeric select value |
| 5 | City/district | `district_id` | numeric select value |
| 6 | Pincode | `pin_code` | six digits |

Names and types were confirmed by GET-only inspection of all six forms. `arc_name` is required (1–200 chars); `m_name` and `l_name` are optional (1–255 chars). `arc_reg` is required (1–200 chars); `pin_code` is exactly six digits. State and district/year selects are required. Current select values and validation classes are captured in `form_lookups.json`: 39 state entries, 2,030 district entries, and 52 year entries, excluding the `-1` placeholder for each relevant list. The city control may be state-filtered; behavior remains unconfirmed.

Confirmed live lookup values used in this run: Maharashtra `state_id=27`, Delhi `state_id=7`, and Bengaluru `district_id=1655`.

Year-select code map (code → year): 44→1975, 52→1976, 55→1977, 57→1978, 58→1979, 59→1980, 60→1981, 61→1982, 62→1983, 63→1984, 64→1985, 65→1986, 66→1987, 67→1988, 69→1989, 70→1990, 71→1991, 72→1992, 73→1993, 75→1994, 76→1995, 77→1996, 78→1997, 68→1998, 79→1999, 80→2000, 81→2001, 82→2002, 83→2003, 84→2004, 85→2005, 86→2006, 87→2007, 88→2008, 89→2009, 1→2010, 2→2011, 3→2012, 4→2013, 5→2014, 90→2015, 93→2016, 94→2017, 96→2018, 98→2019, 446→2020, 936→2021, 992→2022, 1586→2023, 1587→2024, 1588→2025, 1589→2026.

## Response and field derivation

Expected response is HTML with `table.table-bordered.custm-table`, columns for S.No, name, registration number, disciplinary action, address, mobile, and email. Anonymous views show up to three rows then a login/purchase prompt. Registration year is extracted from `CA/YYYY/NNNNN`. State, city, and pincode are parsed from the address tail when it matches `CITY, STATE - PINCODE`; original address text is preserved. Registration status is unavailable anonymously and remains null. Disciplinary action is captured.

## Live search results observed

Three operator-solved Track B searches were completed. Each returned three architect rows followed by the expected login/purchase marker:

| Search | Mode / value | Rows | Outcome |
|---|---|---:|---|
| Maharashtra | State, `state_id=27` | 3 | Success |
| Delhi | State, `state_id=7` | 3 | Success |
| Bengaluru | City/District, `district_id=1655` | 3 | Success |

These three searches yielded nine rows total, consistent with the described approximate anonymous ceiling of three submissions × three visible rows per IP/day. The district-based City search worked with the looked-up Bengaluru district code. This is evidence from these searches, not a guarantee that each search returns three rows or that all rows are unique. The local quota ledger records 3/3 for 2026-09-26.

All three live POST log entries recorded `count_cookie=null`; no `count` cookie value was present in the saved session-cookie string. The implementation logs the value if present and does not infer quota state from it.

The current bundled SQLite/JSON dataset contains the nine live rows from these three searches plus mock-only demo rows. The live job table records all three searches as successful and the local quota ledger shows 3/3 for the captured date. See `RESUME_DEMO.md` for the separate mock interruption and recovery evidence.

## Remaining open questions

- Whether district options are state-filtered.
- `count` cookie semantics (no value appeared in these three live logs; it is logged when present and never decoded).
- Wrong-CAPTCHA response markup.
- Address formatting edge cases and dedup false positives.

## Source observations and verification provenance

The attached investigation notes report `count=0` in an earlier cookie capture and identify the session cookie as `PHPSESSID` and CAPTCHA image at `/app/admin/captcha/php_captcha.php`. The forms use client validation metadata in input classes with the pattern `field|type|required|min|max|pattern|message`. All six form pages and dropdown values were subsequently fetched using GET-only requests and saved in `form_lookups.json`; no live search submissions were used to inspect fields. The three live result searches above were submitted only after an operator solved each CAPTCHA.
