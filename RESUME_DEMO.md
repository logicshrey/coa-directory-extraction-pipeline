# Track A crash-and-resume demonstration

This is the recorded interruption/resume evidence from the mock track. The fresh batch is jobs **39–68** (`resume-crash-01` through `resume-crash-30`). It used only local fixture data.

The preceding local mock batch (jobs 9–38) completed 30 jobs / 90 rows in 0.428 seconds, about **210 rows/second**. The deterministic fixture intentionally repeats its three sample architects; the database marks those repeated mock rows as duplicates (192 of the 195 mock rows currently stored). This demonstrates deduplication and mock throughput without contacting the live site.

## Interruption

Command: `python pipeline.py run --track mock --workers 1`

The process (PID 14252) was force-stopped while it was still running. Two jobs had committed successfully before termination; the next job had been marked in progress when the process was killed.

State immediately after the interruption:

```text
job 39: success, attempts=1
job 40: success, attempts=1
job 41: in_progress, attempts=1
jobs 42–68: pending, attempts=0
batch totals: 2 success, 1 in_progress, 27 pending
```

## Resume

Command: `python pipeline.py resume --track mock --workers 1`

Observed CLI output:

```text
Processed 29 mock job(s) with 1 worker(s); run 11.
```

The 29 selected jobs were the 28 unfinished jobs in this batch (job 41 plus jobs 42–68) and one earlier, unrelated injected-failure fixture job (job 4). Successful jobs 39 and 40 were excluded by the queue query and remained at one attempt each. Job 41 was recovered from `in_progress` and completed on its second attempt.

State after resume:

```text
batch jobs 39–68: 30 success, 0 pending, 0 in_progress, 0 failed
job 39: attempts=1 (skipped)
job 40: attempts=1 (skipped)
job 41: attempts=2 (recovered and completed)
jobs 42–68: attempts=1 (completed)
```

The pipeline log contains one `mock_job_success` event each for jobs 39 and 40; neither was emitted again by resume. Database job status and attempt counts provide the before/after evidence. The interrupted run remains without an end timestamp, as expected for a killed process.
