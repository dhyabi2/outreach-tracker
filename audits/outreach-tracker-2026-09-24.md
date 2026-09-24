# outreach-tracker — audit, 2026-09-24

Audited as published: a clone of `main`, Python 3.11, no network beyond GitHub's own API.
The repository is a single module, `track.py`, plus seven laws and the Action that runs them, so the
audit read all of it rather than sampling.

## What was checked

- `python3 -m unittest discover -s tests -t .` — **7 tests, OK**, before any change.
- Every function in `track.py`: `_token`, `fetch` (retry and backoff), `repo_of`, `is_ours`,
  `state_of`, `rows_from`, `collect` (pagination and de-duplication), `summarise`, `render`, `main`.
- `.github/workflows/track.yml` — the trigger, the permissions, the order of the steps, and what it
  commits.
- The published `data.json` and `README.md` against what the code says it produces.
- A secret sweep of the tree: token shapes, seed-shaped hex, `nano_` addresses, private key blocks.
  **Nothing found.** The repository writes no credential and reads its token only from the
  environment; `test_no_token_ever_reaches_the_published_files` already pins that, and it holds.

## Found

1. **No pull request could ever reach the table.** `collect()` queried
   `author:{author}+type:issue`. `type:issue` and `type:pr` are exclusive qualifiers, so that one
   query returns issues and nothing else. Every pull request the swarm has ever opened was dropped
   before `rows_from` saw it.

   The published table says so plainly once you look for it: **389 rows, every one `kind: "issue"`,
   and `merged: 0`** — in a table whose own header counts merges and whose own comment says *"a
   merged PR is the only state in this table that means someone else accepted our work."* Against
   that, `author:dhyabi2 type:pr` returns **178 pull requests** on other people's repositories
   (`Custena/agent-payment-protocols`, `langflow-ai/langflow`, `OrcaQubits/awesome-agentic-commerce`
   and more). So the single most valuable column in the table was empty for a reason that had
   nothing to do with the work.

   The suite could not catch it. `test_merged_is_never_flattened_into_closed` hands `state_of` a
   hand-built item that already has a `pull_request` key, so it proves the *classifier* can say
   merged and says nothing about whether a pull request ever arrives to be classified. **Fixed** —
   both kinds are fetched, one query each, keeping the `type:` qualifier family already proven in
   production here.

## After

`python3 -m unittest discover -s tests -t .` → **8 tests, OK**. The new law,
`test_a_pull_request_reaches_the_table`, drives `collect()` through a fake `fetch` that answers the
way the endpoint does — serving whichever kind the query names and raising on a query that names
neither — and fails against the old code with `pull requests never reached the table: ['issue']`.

## Not verified

- **The regenerated table itself.** This audit ran in a sandbox whose proxy refuses every
  non-repository-scoped GitHub path, `/search/issues` included, so `track.py` could not be run
  end to end and no new `data.json` is in this change. The first scheduled run is the real proof.
  It fails safe if the new query is ever rejected: the workflow runs the laws before the rebuild,
  and `fetch` re-raises an HTTP error it cannot retry, so a bad query produces a failed job and
  commits nothing rather than publishing a truncated table.
- **The 1000-result ceiling.** `collect` stops at page 10 per query, which was never reached while
  one query was issued per author. With two, an author at 1000 issues *and* 1000 pull requests would
  silently truncate. Nothing is near that today (389 issues, 178 pull requests), so it is reported
  rather than changed; the honest fix is to notice `total_count` and say so in the table.
- `fetch`'s rate-limit backoff was read but not exercised — provoking a 403 from the search API on
  purpose is not something an audit should do.
