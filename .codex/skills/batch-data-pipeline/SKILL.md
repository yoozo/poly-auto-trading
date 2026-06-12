---
name: batch-data-pipeline
description: Design resilient batch data pipelines for this project. Use when Codex changes batch downloads, external API synchronization, large report generation, database bulk writes, pagination, backfills, import/export jobs, or data processing that may involve many rows, many network requests, memory pressure, retries, or service availability risks.
---

# Batch Data Pipeline

Use bounded, resumable, memory-conscious data flow whenever a feature may process many rows or many external API pages.

## Default Workflow

1. Identify volume boundaries before coding:
   - expected rows or pages
   - max request size from the external API
   - database parameter or statement-size risk
   - whether results are needed all at once or can be processed incrementally

2. Prefer streaming pipelines:
   - expose async generators for downloads or scans
   - process `async for batch in ...` at the caller
   - write each batch before fetching too much more data
   - for enrichment jobs, fetch one bounded key chunk, persist it, then move to the next chunk
   - keep only current batch, cursors, counters, and small dedupe/index sets in memory

3. Use bounded concurrent IO:
   - use async IO for network-bound work
   - cap concurrency with a small constant or semaphore
   - coalesce duplicate in-flight requests by key when multiple endpoints can request the same resource
   - avoid unbounded `gather()` over all pages, all slugs, or all accounts

4. Write to the database in small batches:
   - bulk insert/upsert in batches with a named batch-size constant
   - commit per batch or per bounded transaction window
   - keep batch sizes conservative enough to avoid SQL parameter limits, oversized statements, lock pressure, and long rollback times
   - make writes idempotent with unique keys and upsert/conflict handling where possible

5. Preserve availability:
   - retry or degrade external metadata failures without failing the main report when exact metadata is optional
   - checkpoint progress in task records after meaningful stages
   - persist completed chunks so reruns skip them from cache instead of restarting from the beginning
   - derive resume cursors from persisted data, not only from in-memory task state
   - surface partial progress and counts in task results
   - avoid holding a DB transaction while performing external network calls

## Project Patterns

- Polymarket activity:
   - keep `limit` within documented endpoint bounds
   - fetch pages concurrently inside a bounded time/window
   - stream normalized batches to storage instead of accumulating all activity rows before writing
   - before a download, read local activity count and oldest timestamp for the account
   - if the requested target count is only partially present, resume with `end = oldest_timestamp - 1`
   - if the requested target count is already present, skip downloading and continue enrichment/report steps
   - use timestamp cursor windows when offset pagination reaches live API boundaries

- Polymarket market metadata:
   - collect unique slugs first
   - read cached metadata before calling Gamma
   - never refresh closed markets unless explicitly requested
   - use TTL for open markets
   - coalesce in-flight requests for the same slug
   - process stale slugs in bounded chunks
   - after each chunk is fetched, immediately batch upsert and commit it
   - never download all stale market metadata before the first write
   - design reruns so already persisted chunks are skipped by the cache check

- Reports:
  - use DB-level pagination or bounded loading for large detail tables
  - prefer summary/aggregate queries when full row materialization is not needed
  - keep report generation usable when optional enrichment is missing

## Review Checklist

Before finishing a batch-data change, verify:

- No unbounded list of all pages, tasks, or DB rows is required unless the requested limit is intentionally small.
- Network concurrency is capped and duplicate in-flight requests are avoided when likely.
- Database writes are chunked and idempotent.
- Long enrichment jobs interleave download and write; they do not perform all downloads first.
- Interrupted jobs can reuse completed chunks from persistent storage.
- Network calls are outside long DB transactions.
- The task can report progress and recover or fail clearly.
- Tests cover boundary sizes, duplicate data, partial failures, and batch behavior.
