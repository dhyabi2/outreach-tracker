# outreach-tracker — audit, 2026-09-26

A clone of `main` at `df99f74`, Python 3.11. Third pass. The 2026-09-24 note read every function in
`track.py` and the 2026-09-25 note took the Action's run history, so this one went after the defect
the 09-25 note named and deliberately left for its own change — and found that the same branch lies
as well as crashes.

## What was checked

- `python3 -m unittest discover -s tests -t .` — **11 tests, OK**, before any change.
- The carried-over branch of `main()` (`track.py:230`) and `render`'s skip banner, exercised
  directly against the published 881-row `data.json`.
- `.github/workflows/track.yml` — unchanged by this audit and still `disabled_manually`.
- A secret sweep of the tree: token shapes, seed-shaped hex, `nano_` addresses, key blocks.
  **Nothing found.** `test_no_token_ever_reaches_the_published_files` still holds.

## Found and fixed — one concern, two symptoms, both in the degrade-gracefully branch

**1. A `data.json` that cannot be parsed aborted the whole rebuild.** The reload is guarded with
`except OSError`, but a truncated or malformed file raises `json.JSONDecodeError` — a `ValueError`,
which that clause does not catch. Reproduced against the real published table by cutting it in half,
which is exactly what a killed or cancelled run leaves behind, because `main()` opens `data.json`
with `"w"` (truncate) before dumping:

```
intact data.json: 371055 bytes, 881 rows
CRASH: json.decoder.JSONDecodeError -> Expecting property name enclosed in double quotes: line 5305
       is it an OSError (what line 230 catches)? False
```

So the one path written to degrade instead of losing rows was the path that aborted, and it aborts
the same way on every later run — a single interrupted write poisons the tracker until someone
notices by hand. Two neighbouring shapes crash too: a file that parses to a list raises
`AttributeError` on `prev.get`, and a row without a `url` raises `KeyError`.

**2. With nothing carried over, the banner described rows that are not in the table.** `render`
emitted the disclaimer whenever an author was skipped, regardless of how many rows survived:

> **0 of these rows were not checked in this run.** GitHub's search API answered 422 for
> `PANDeveloper001`, so their rows are the ones last successfully fetched and their state may have
> changed since. Every other row is live.

"Their rows are the ones last successfully fetched" is false when there are none — on a first run
with a 422, and on exactly the corrupt-file case above once it stops crashing. This is the same
defect the 09-25 change was made to cure (remembered state presented as live state), one branch
further along.

Fixed together because they are one behaviour: what the tracker does when the previous table cannot
be reloaded. The reload now catches `FileNotFoundError` silently (a first run is not a fault) and
`(OSError, ValueError)` with a named warning on stderr, validates that the file holds an object, and
skips rows without a `url`; the banner says *"could not check … and has no earlier rows for them …
missing from the table below rather than stale in it"* when nothing was carried, and keeps its
existing wording when rows were.

## After

`python3 -m unittest discover -s tests -t .` → **14 tests, OK**. The three new laws fail against
`track.py` as published, each for its own reason:

```
ERROR [truncated mid-write]   json.decoder.JSONDecodeError: Unterminated string ...
ERROR [not an object at all]  AttributeError: 'list' object has no attribute 'get'
ERROR [empty file]            json.decoder.JSONDecodeError: Expecting value ...
ERROR test_a_previous_row_without_a_url_is_skipped_not_fatal   KeyError: 'url'
FAIL  test_the_skip_banner_never_claims_rows_that_are_not_in_the_table
FAILED (failures=1, errors=4)
```

They also pin the behaviour that must not change: the live rows are still published, the skipped
author still reaches `unverified_authors`, a url-less previous row is skipped *without* a warning
because that is not a reload failure, and the real carried-over banner still reads as it did.

## Carried forward, still not fixed

- **The Action is still switched off** (`disabled_manually` since 2026-09-24T16:17Z), its last three
  runs having failed at *Rebuild the table* with HTTP 422 at `ee3cd43d`, before the 422 handling
  existed. Re-enabling a workflow is an operator action and a release-path change, so this audit
  reports it rather than doing it. It still has not been demonstrated whether that 422 is specific
  to one account or systemic to the query.
- **The 1000-result ceiling**, carried from both earlier notes: `collect` stops at page 10 per query
  and says nothing. At 755 issues and 126 pull requests nothing is near it; the honest fix is to
  read `total_count` and report the truncation.

## Not verified

- **`track.py` end to end.** This sandbox's egress policy refuses non-repository-scoped GitHub paths,
  so `/search/issues` cannot be reached and neither the 422 nor a clean rebuild was reproduced
  against the live API. Everything changed here was exercised directly instead: `main()` runs in a
  temporary directory against a hand-made previous table, and `render` against the published one.
- **`fetch`'s rate-limit backoff**, for the third time and the same reason: provoking a 403 from the
  search API on purpose is not something an audit should do.
