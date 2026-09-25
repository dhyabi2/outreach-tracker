# outreach-tracker — audit, 2026-09-25

A clone of `main` at `38fc83a`, Python 3.11. Second pass over this repository; the 2026-09-24 note
covered every function in `track.py`, so this one started from what has happened in production
since — the Action's run history, and the published files against what the code claims about them.

## What was checked

- `python3 -m unittest discover -s tests -t .` — **10 tests, OK**, before any change.
- The Action's real run history and the log of the last failure.
- `data.json` and `README.md` as published, against what `main()` actually writes.
- `render`, `summarise`, `payload`, and the carried-over-rows branch of `main`.
- A secret sweep of the tree: token shapes, seed-shaped hex, `nano_` addresses, key blocks.
  **Nothing found.** `test_no_token_ever_reaches_the_published_files` still holds.

## Found and fixed

**The table presented remembered state as live state.** When GitHub's search API answers 422 for an
author, `collect` reports it and `main` keeps that author's rows from the previous run rather than
let them vanish — deliberate, and right. But the skip was announced only on **stderr**. Everything
`main` then wrote said the opposite: `README.md` opens with *"with the state GitHub reports right
now"*, carries a fresh `_Generated <time>._`, and folds the carried-over rows into the headline
counts; `data.json` had no field for it either.

This is not hypothetical. The table published at `c4702c0` — whose own commit message reads
`PANDeveloper001 kept-unverified` — counts **881** submissions, **343** of them
`PANDeveloper001` rows that were not fetched that run. A reader of the published table had no way
to tell those 343 from the 538 that were.

Fixed: `render` and the new `payload` take the skipped authors, so the README carries one line
naming them and counting their rows, and `data.json` carries `unverified_authors`. A clean run
prints neither — a standing disclaimer nobody can act on is worse than none.

Rendered against the live `data.json` with `PANDeveloper001` skipped, the new line reads:

> **343 of these rows were not checked in this run.** GitHub's search API answered 422 for
> `PANDeveloper001`, so their rows are the ones last successfully fetched and their state may have
> changed since. Every other row is live.

The suite could not have caught it. `test_collect_skips_an_author_github_will_not_index` proves
`collect` *returns* `skipped`; nothing asserted anything about what reaches the published files.

## After

`python3 -m unittest discover -s tests -t .` → **11 tests, OK**. The new law,
`test_a_carried_over_row_is_never_published_as_current_state`, fails against the old code with
`TypeError: render() takes 2 positional arguments but 3 were given`.

## Found, not fixed

- **The Action is switched off.** `.github/workflows/track.yml` is `disabled_manually` (since
  2026-09-24T16:17Z). Its last three runs all failed at *Rebuild the table* with
  `urllib.error.HTTPError: HTTP Error 422` — at `ee3cd43d`, before the 422 handling existed, so the
  handling has never actually run in CI. The table only moves when someone runs `track.py` by hand,
  which is why every rebuild since is an agent's commit rather than the scheduler's. Re-enabling a
  workflow is an operator action and a release-path change, so this audit reports it rather than
  doing it. Worth knowing before it is switched back on: nothing here has yet demonstrated whether
  the 422 is specific to one account or systemic to the query.
- **A corrupt `data.json` crashes the rebuild.** The carried-over branch guards the reload with
  `except OSError` (`track.py:230`), but a truncated or malformed file raises
  `json.JSONDecodeError`, a `ValueError`, which is not caught — so the one path meant to degrade
  gracefully aborts instead. One concern per change; left for its own.
- **The 1000-result ceiling**, carried over from the 2026-09-24 note and still unchanged: `collect`
  stops at page 10 per query and says nothing. At 755 issues and 126 pull requests nothing is near
  it, but the honest fix is to read `total_count` and report the truncation.

## Not verified

- **`track.py` end to end.** This audit ran in a sandbox whose proxy refuses every
  non-repository-scoped GitHub path; `/search/issues` answers 403 there, so neither the 422 nor a
  clean rebuild could be reproduced against the live API. `render`, `payload` and `summarise` were
  exercised directly against the published `data.json`, which is where the change lives.
- **`fetch`'s rate-limit backoff**, for the same reason as last time: provoking a 403 from the
  search API on purpose is not something an audit should do.
