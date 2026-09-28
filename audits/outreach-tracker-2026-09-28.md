# outreach-tracker — audit 2026-09-28

Fifth pass, on `main` at `25d7c7c`. Four notes in a row have ended with the same open item —
*"it is still unestablished whether the 422 that disabled the Action is specific to one account or
systemic to the query"* — and this run found out **why** nobody could establish it. The failing run's
own log is a stack trace and a bare status code. It names no query, no author, and none of GitHub's
response body, which is the one place the answer is written down.

That is the defect fixed here. The 422 itself is still not reproduced: this sandbox cannot reach
`/search/issues` at all (below).

## What was checked

- `python3 -m unittest discover -s tests -t .` on a clean clone: **21 passed** before any change.
- `python3 -m py_compile track.py tests/test_track.py`: clean.
- The **live workflow state**: `.github/workflows/track.yml` is still `disabled_manually`. Confirmed
  against the API this run, as on 09-26 and 09-27.
- The **three runs the repository has ever had**, all `failure`: `2026-09-23T21:13Z` (push),
  `2026-09-24T04:49Z` and `2026-09-24T11:45Z` (schedule). Job 107617240339's log was read in full.
- `data.json` against the tree: still stamped **2026-09-25 05:24 UTC**, and its keys are
  `generated`/`summary`/`rows` — no `unverified_authors`, no `truncated_queries`, so no rebuild has run
  since either of those landed. Three days stale now.
- `fetch`'s retry arms, `collect`'s 422 skip and its interaction with the ceiling counter, `state_of`,
  `repo_of`, `is_ours`, `summarise`, and `render`'s banners.

## Fixed

**`track.py:59` — a failed rebuild reported a status code and threw away the reason.** This is the
whole of what the 2026-09-24 run left behind:

```
  File ".../track.py", line 120, in collect
    data = fetch(f"/search/issues?q={q}&per_page=100&page={page}&sort=created&order=desc", token)
  File ".../track.py", line 57, in fetch
    with urllib.request.urlopen(req, timeout=30) as r:
urllib.error.HTTPError: HTTP Error 422: Unprocessable Entity
```

`q` is interpolated inside the frame, so the trace shows the *template*, not the query. The author is
in a loop variable that never prints. And `urllib` puts nothing of the response body into the
exception, while GitHub's 422 on this endpoint always carries the answer:

```json
{"message": "Validation Failed",
 "errors": [{"message": "The listed users cannot be searched either because the users do not exist
              or you do not have permission to view the information.",
             "resource": "Search", "field": "q", "code": "invalid"}]}
```

An operator with that body in front of them can tell "this account cannot be searched" from "this
query is malformed" in one reading. Without it there is nothing to go on, which is exactly the state
four audits recorded. A tracker whose job is to be believable about outreach numbers cannot fail
mutely.

**Fixed** in `fetch`: on the failure it is about to re-raise, it raises an `HTTPError` carrying the
**path it asked for** and GitHub's own message.

```
BEFORE:  HTTP Error 422: Unprocessable Entity
AFTER :  HTTP Error 422: Unprocessable Entity for /search/issues?q=author:PANDeveloper001+type:pr&
         per_page=100&page=1&sort=created&order=desc -- Validation Failed; The listed users cannot be
         searched either because the users do not exist or you do not have permission to view the
         information.
```

Deliberately still an `HTTPError` with the **same `code`**: `collect` branches on `e.code == 422` to
skip an unsearchable author instead of aborting the table, and repackaging the exception as a new type
would have turned a graceful skip into a crash — trading a diagnosis for the very failure it explains.
A law pins that.

`_error_detail` is bounded (4 KiB) and cannot raise: a body that is not JSON is reported as text, an
empty body adds nothing rather than a dangling separator, and an `fp` whose `read` fails leaves the
original error intact. A diagnostic that throws would replace the real fault with its own.

**Laws: 6 added.** Against the unchanged `track.py`, **3 fail** — the three that assert the new
information is there:

```
FAIL: test_a_failed_request_names_the_query_and_githubs_own_reason
  AssertionError: 'author:PANDeveloper001+type:pr' not found in 'HTTP Error 422: Unprocessable Entity'
FAIL: test_a_body_that_is_not_json_is_reported_as_text
  AssertionError: 'upstream connect error' not found in 'HTTP Error 502: Unprocessable Entity'
FAIL: test_reading_the_body_never_becomes_the_failure
  AssertionError: 'author:x+type:pr' not found in 'HTTP Error 422: Unprocessable Entity'
```

The other three pass either way by design — they pin that the code survives, that a 422 author is
still skipped rather than fatal, and that an empty body reads cleanly. Said plainly because a law that
could never have failed is not evidence of a fix; it is evidence the fix broke nothing.

**Before: 3 failed, 24 passed. After: 27 passed.** `py_compile` clean.

## Found, NOT fixed

- **The Action is still off and the table is still stale.** `track.yml` is `disabled_manually` and
  `data.json` is stamped 2026-09-25 05:24 UTC, so the README's "the state GitHub reports right now" is
  false today and stays false until someone re-enables the workflow. Nothing in this change reaches the
  published table before then — including the better error message, which is most useful on the run
  that re-enables it. **Re-enabling a workflow is an operator action on the release path and this audit
  does not do it.** It is also the one action that would turn the item below from unknown into known.
- **Whether the 422 is `PANDeveloper001`-specific or systemic is still unestablished** — carried for the
  fifth time, but for a different reason now. It is no longer that the failure says nothing; it is that
  the failure cannot be provoked from here. The next real run will say which.
- **A 403 that is not a rate limit costs 60 seconds before it is reported.** `fetch` treats every 403
  as a rate limit and sleeps 20s then 40s. A 403 from a token without the right scope is not going to
  improve on retry; GitHub distinguishes them by `x-ratelimit-remaining: 0` and by the body. Reading
  that header to skip the sleeps is a behaviour change, not a diagnosis, so it is kept out of this pull
  request rather than smuggled in beside it.
- **`render`'s stale-row banner counts rows that WERE checked.** `stale` is every row by a skipped
  author, but if `type:issue` succeeded and only `type:pr` answered 422, that author's freshly-fetched
  issue rows are counted as unchecked. The code's own comment says observed behaviour is both kinds
  422ing together, so this is latent, and it errs toward claiming less certainty than it has — the safe
  direction. Recorded rather than changed, because changing it means deciding what a one-kind 422 means
  and nothing observed says yet.
- **`summarise` counts `answered` from `comments > 0`**, which includes our own follow-ups and bots.
  The README says "have at least one reply", which is literally what is measured, so it is not a false
  claim — but it is not a count of people. Narrowing it needs a per-issue comment fetch. Carried.

## Not verified

- **`track.py` against the live search API, for the fifth time and the same reason.** This session is
  bound to its own repositories, so `GET /search/issues?q=author:dhyabi2+type:pr` answers **403** before
  it reaches GitHub — with and without a token. Neither the real 422 nor a real truncation was
  reproduced. Everything above was exercised against fakes.
- **`fetch`'s rate-limit backoff against a real 403 from the search API.** Provoking one on purpose is
  not something an audit should do.

## Secrets

Clean. No key, seed, token or `.env` in the tree or in this change. `track.yml` reads
`secrets.GITHUB_TOKEN`, the Action's own scoped token, and no credential is written anywhere.
