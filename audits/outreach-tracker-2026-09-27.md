# outreach-tracker — audit 2026-09-27

Fourth pass. The 1000-result ceiling has been carried forward as "found, not fixed" by both earlier
notes, with the fix already named there: read `total_count` and report the truncation. It is done here.

## What was checked

- `python3 -m pytest tests -q` on a clean clone: **14 passed, 3 subtests** before any change.
- `collect`'s loop nesting, read against what it actually does rather than what it looks like. Worth
  recording because it looks wrong: the `if author in skipped: break` sits at the `for kind` body's
  indentation, so it breaks the **kind** loop, not the author loop. Checked with a fake API that 422s
  one author — the later author is still queried in full. **Not a defect; the earlier reading was mine
  and it was wrong.**
- The published `data.json` against the published `README.md`: **they agree** — 881 submissions, 598
  repositories, 881 table rows, and the same by-author split (dhyabi2 538, PANDeveloper001 343).
- `state_of`, `repo_of`, `is_ours`, `summarise`, the dedup, and the pipe-escaping in `render`.

## Fixed

- **`track.py:128` — a table that was missing rows read exactly like a complete one.** `collect` stops
  at `page <= 10`, because GitHub's search API returns at most 1000 results per query, and said
  nothing about it. The README's first sentence promises "**Every** issue and pull request the Nano
  swarm has opened on someone else's repository, with the state GitHub reports right now" — a claim
  the loop cannot keep past page 10, and the shortfall leaves no gap a reader could notice.

  In a tracker whose entire reason for existing is that outreach numbers were once overstated — "35
  issues were once opened on our own forks and reported as outreach", as the README itself says — a
  silent undercount is a defect in the purpose, not a rough edge.

  Fix: `collect` compares GitHub's own `total_count` against what it was actually handed and appends
  `"author/kind"` to an optional `truncated` list per cut-off query. `payload` publishes
  `truncated_queries`; `render` prints a banner saying the table is incomplete and that the total is a
  floor; `main` warns on stderr. An optional list rather than a third return value, so every existing
  caller keeps working.

  Shown end to end, not just in units: with a fake API claiming 1500 matches and handing over 1000,
  `main()` writes `truncated_queries: ['PANDeveloper001/issue', 'PANDeveloper001/pr', 'dhyabi2/issue',
  'dhyabi2/pr']` into `data.json` and "**This table is incomplete.** … cut off … the total is a floor,
  not the whole of it" into the README, and warns on stderr.

  Laws: 7 added to `tests/test_track.py`. They pin both directions — a cut-off query is named, a
  complete one is not (a banner that cried wolf every run would be worse than none), an API that omits
  `total_count` is **not guessed at**, the argument stays optional, and both the README banner and the
  `data.json` field reach a reader. **Before: 5 failed, 16 passed. After: 21 passed, 3 subtests.**

  One existing law was touched: the `collect` stub at `tests/test_track.py:206` was
  `lambda tok: …`, pinning an exact signature it had no stake in, which made this change look like four
  broken laws. Widened to `lambda *a, **k: …`. Nothing it asserts changed.

  **This is latent, not live.** The largest single query is 412 issues in the current table against a
  ceiling of 1000, so nothing is near it today. It is fixed now because the banner has to already be
  there on the run that first crosses the line — by definition nobody will be watching that run.

## Found, NOT fixed

- **The Action is still off, and the published table is stale.** `.github/workflows/track.yml` is
  `disabled_manually` — confirmed live against the API this run — and `data.json` is stamped
  **2026-09-25 05:24 UTC**, two days old. So the README's "the state GitHub reports right now" is
  currently false, and will stay false until someone re-enables the workflow. Nothing in this change
  reaches the published table before then, including the new banner. Re-enabling a workflow is an
  operator action on the release path and this audit does not do it.
- **`data.json` has no `unverified_authors` key**, which `payload` has emitted since 2026-09-26 —
  independent confirmation that no rebuild has run since that change landed.
- **It is still unestablished whether the 422 that disabled the Action is specific to one account or
  systemic to the query.** Carried from 2026-09-26. `PANDeveloper001` is the first author and is under
  GitHub review, which is the obvious suspect, but nothing here proves it.
- **`summarise` counts `answered` from `comments > 0`**, which includes our own follow-up comments and
  bots, not only outside replies. The README says "have at least one reply", which is literally what is
  measured, so this is not a false claim — but 205 of 881 is not 205 people. Narrowing it needs a
  per-issue comment fetch, which is a design decision and 881 extra API calls.

## Not verified

- **`track.py` against the live API, for the fourth time and the same reason.** This sandbox's egress
  policy refuses non-repository-scoped GitHub paths, so `/search/issues` cannot be reached: neither the
  422 nor a real truncation was reproduced against GitHub. Everything above was exercised against fakes
  and, for `main()`, end to end in a temporary directory.
- **`fetch`'s rate-limit backoff**, for the fourth time: provoking a real 403 from the search API on
  purpose is not something an audit should do.
