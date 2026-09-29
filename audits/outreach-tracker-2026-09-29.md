# outreach-tracker — audit 2026-09-29

Sixth pass, on `main` at `62c225f`. Baseline on a clean clone, before any change:

```
$ python3 -m unittest discover -s tests -t .
Ran 27 tests ... OK
$ python3 -m py_compile track.py tests/test_track.py     # clean
```

The five previous notes read `track.py` as source. This run instead **rendered the published
`data.json` back through `render()` and checked the result against the file that is actually
served**, which is a property of the artefact rather than of the code. The two agree byte for byte —
and that is how the defect below came out: the ordering the table is sorted into is broken in the
published table itself, 54 times, and has been since 2026-09-25.

## Fixed

**`main()` appends carried-over rows to a list `collect()` had already sorted, so the published table
runs merged / open / closed and then starts over.** `collect` returns its rows sorted by
`(STATE_ORDER[state], repo, number)` (`track.py:210`). When an author answers 422, `main` reloads
`data.json` and appends that author's previously-published rows to the end of that sorted list
(`track.py:347`) — and nothing sorts again before `render` walks the list in order.

Measured on the published table, which is exactly this case:

```
README.md == render(data.json)               True       (881 rows, both)
rows   1..538   author dhyabi2           live-fetched, 0 state inversions
rows 539..881   author PANDeveloper001   carried over, 53 state inversions
first break at row 538: "closed | zuplo/echo-api #49" followed by "open | 0xSardius/solenrich #4"
54 positions in the published table where a row ranks above the row before it
```

`STATE_ORDER`'s own comment says why this matters: *"a merged PR is the only state in this table that
means someone else accepted our work"*, which is why merged sorts first. A carried-over merged pull
request is rendered **below every closed row** — position 600-odd in an 881-row table, under a heading
block a reader has already scrolled past. Today no merged row is stranded, because the carried author
has none (`Counter({'open': 248, 'closed': 95})`). `PANDeveloper001` is precisely the account the
search API refuses, so it is also the account whose rows are always the carried ones: the first merge
it ever gets is the first one to disappear into the tail.

**Fixed** by sorting once more after the carry-over, with the same key. The key is lifted into a named
`row_order()` so the two call sites cannot drift — the only reason to name it. Three lines of
behaviour; nothing else changed, and no row is added, dropped or altered.

**Laws: 3 added.** Against the unchanged `main()`, **2 fail**:

```
FAIL: test_a_carried_over_merged_pr_is_not_stranded_below_the_closed_rows
  the table is not in state order:
  ['merged', 'open', 'closed', 'closed', 'merged', 'open']
FAIL: test_the_whole_table_is_monotonic_in_state_after_a_carry_over
  [0, 1, 1, 2, 2, 1, 0, 2, 0] != [0, 0, 0, 1, 1, 1, 2, 2, 2]
  ['merged','open','open','closed','closed','open','merged','closed','merged']
```

They drive the real `main()` end to end — `collect` stubbed, a scratch directory, a previous
`data.json` written by hand — rather than the sort in isolation, because the defect is in how the two
halves are joined and not in either half. The third law pins that `collect` and `main` use the same
key; it passes either way by construction, and is said plainly because a law that could never have
failed is evidence the fix broke nothing, not evidence that it works.

**Before: 2 failed, 28 passed. After: 30 passed.** `py_compile` clean.

## Found, NOT fixed

- **The Action is still off and the table is still four days stale.** `.github/workflows/track.yml`
  is `disabled_manually`, confirmed against the API this run as on 09-26, 09-27 and 09-28, and
  `data.json` is stamped **2026-09-25 05:24 UTC**. Its keys are still `generated` / `summary` / `rows`
  — no `unverified_authors`, no `truncated_queries` — so no rebuild has run since either of those
  landed, and none since the 09-28 error-message fix either. The README's "the state GitHub reports
  right now" is false today and stays false until someone re-enables the workflow. **Re-enabling a
  workflow is an operator action on the release path and this audit does not do it.** Nothing in this
  change reaches the published table before then.
- **Carried-over rows never expire and carry no date of their own.** `main` rewrites `data.json` every
  run including the carried rows, so an author GitHub permanently refuses to index keeps its
  2026-09-25 rows indefinitely. The banner is honest that they "may have changed since" but never says
  *since when*, and the only timestamp on the page is the generation stamp, which moves every run
  while those rows do not. Stamping each carried row with the run that last fetched it is a schema
  change to `data.json` and a decision about what consumers are promised, so it is not made here.
- **Whether the 422 is `PANDeveloper001`-specific or systemic is still unestablished** — sixth run. The
  09-28 fix means the next real run will say which; there has been no real run.
- **A 403 that is not a rate limit costs 60 seconds before it is reported** (`fetch` treats every 403
  as a rate limit and sleeps 20s then 40s). Carried from 09-28: reading `x-ratelimit-remaining` to
  skip the sleeps is a behaviour change, not a diagnosis, and does not belong beside an unrelated fix.
- **`render`'s stale-row banner counts rows that WERE checked**, and **`summarise` counts `answered`
  from `comments > 0`**, which includes our own follow-ups and bots. Both carried from 09-28
  unchanged; both err toward claiming less than is known, which is the safe direction.

## Checked and clean

- **The published artefact matches its own generator.** `render(data.json)` reproduces `README.md`
  exactly, 881 rows both ways, so nothing has been hand-edited into the table.
- **`render` against hostile row content.** A title containing `|` is escaped to `\|` and cannot open
  a column; the 90-character truncation happens after escaping, so it can keep a lone `\` but never a
  lone `|`; an empty title, a 200-character title and a `None` issue number all render a well-formed
  row.
- **`is_ours`, `repo_of`, `state_of`, the url dedupe and the 422 skip** behave as the existing 27 laws
  describe; a `repository_url` that is missing yields `""` and the row is dropped rather than
  mis-attributed.
- **The workflow file** names no credential but `secrets.GITHUB_TOKEN`, the Action's own scoped token,
  and writes nothing outside this repository.
- **README links.** The only link outside the table is the relative `[track.py](track.py)`, which
  resolves. The table's links are GitHub URLs built from the search API's own `html_url`.

## Not verified

- **`track.py` against the live search API, for the sixth time and the same reason.** This session is
  bound to its own repositories, so `GET /search/issues` answers **403** before it reaches GitHub, with
  and without a token. Neither the real 422 nor a real truncation was reproduced. The change above
  never touches the network: it reorders a list that is already in memory.
- **`fetch`'s rate-limit backoff against a real 403 from the search API.** Provoking one on purpose is
  not something an audit should do.

## Secrets

Clean. No key, seed, token or `.env` in the tree or in this change.
