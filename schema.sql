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
