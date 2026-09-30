# outreach-tracker — audit, 2026-09-30

Audited at `52000aa` (head of `main`). Python 3.11.15, stdlib only.

Baseline: **30 passed, 3 subtests** — both under `pytest` and under the exact command CI runs
(`python3 -m unittest discover -s tests -t .`). After this change: **33 passed.** Running the suite
leaves the repository's own `README.md` and `data.json` untouched, which is worth stating in a
repository whose tests drive `main()`.

## What was checked

- `track.py` end to end: `fetch`'s retry and its 422/403 branches, `_error_detail`, `repo_of`,
  `is_ours`, `state_of`, `rows_from`, `collect`'s pagination and ceiling report, `summarise`,
  `payload`, `render`'s escaping and truncation, and `main`'s carry-over branch driven with
  `collect` stubbed.
- The title-truncation path re-checked for the escape it could break: `render` escapes `|` to `\|`
  and only then cuts at 87 characters, so a cut can leave a trailing `\` — but `"..."` is always
  appended after it, so the backslash is never adjacent to the column separator. Sound.
- `.github/workflows/track.yml`: it names no credential but `secrets.GITHUB_TOKEN` and writes
  nothing outside this repository.
- **The published files themselves**, which no earlier audit had opened. That is where the findings
  are.

## Urgent — the published `data.json` is malformed and disagrees with the table

`data.json` and `README.md` are written by the same `main()` call, seconds apart. They do not match:

    README.md   881 submissions across 598 repositories   _Generated 2026-09-25 05:24 UTC._
    data.json   886 submissions across 603 repositories   "generated": "2026-09-29T03:54:22Z"

`data.json` was not written by this tool. Three things prove it: `payload()` always writes
`unverified_authors` and `truncated_queries`, and **both keys are gone**; `generated` is ISO-8601
where `main()` writes `%Y-%m-%d %H:%M UTC`; and commit `52000aa` (2026-09-29) touched `data.json`
without touching `README.md`, which `main()` cannot do. Five rows were appended there by hand, and
**each lacks `state`, `url` and `author`** — so the repository's own machine-readable half now
breaks the repository's own code:

    >>> track.summarise(json.load(open("data.json"))["rows"])
    KeyError: 'state'

The five are real outreach (`Parad0x-Labs/dna-x402#61`, `Floe-Labs/floe-cookbook#77`,
`microchipgnu/MCPay#57`, `nyx-builds/mcp-payments#2`, `teardrop-ai/teardrop#3`), so they must not be
deleted — and their `state` and `author` cannot be recovered offline, so they must not be guessed
either. **The repair is one live rebuild**: `collect()` refetches all 886 rows with their real
fields and `main()` rewrites both files together. That needs two things this audit does not have:

1. GitHub search access — `GET /search/issues` answers **403** to this session, which is bound to
   its own repositories. Six audits have now been unable to exercise `collect()` live.
2. **The scheduled Action is off.** `.github/workflows/track.yml` is `state: disabled_manually`, and
   its three runs — the only three it has ever had — all failed, the last on 2026-09-24, every one
   at "Rebuild the table" on an uncaught `HTTP Error 422` from `/search/issues`. That 422 has since
   been handled (`collect` skips an unindexable author), so the cause of those failures is fixed;
   the switch is not. Nothing has rebuilt the table since 2026-09-25, while the header says it
   carries "the state GitHub reports right now".

Re-enabling a workflow is a change to this repository's automation, so it is left to the owner.

## Found and fixed (this pull request)

**A row read back from `data.json` that lacks a field the table is built from killed the rebuild
with a bare `KeyError`.** The carry-over branch catches `OSError` and `ValueError` precisely so a
malformed `data.json` degrades instead of aborting — and a missing key is neither. Measured, with
`collect` stubbed and one such row in `data.json`:

    File "track.py", line 364, in main
        rows.sort(key=row_order)
    File "track.py", line 46, in row_order
        return (STATE_ORDER.get(r["state"], 9), r["repo"], r["number"] or 0)
    KeyError: 'state'

Nothing is published and nothing says why. The shape is not hypothetical — it is the shape of the
five rows above; they survive today only because they also lack `author`, and the author match is
the filter that happens to drop them first. One field away from a dead rebuild.

The two filters already there (a dict, and a `url`) now ask for the rest of what the row is used
*by*: `row_order` indexes `state`, `repo` and `number`, and `render` indexes `title`, `kind`, `url`,
`author` and `created_at`. A row missing any of them is skipped and **counted on stderr**, the way
the corrupt-file case beside it already is.

Three laws, one of which derives the checked field list from `rows_from`'s own output so the guard
cannot come to name a field a real row does not have. All three fail against the unfixed file — two
on the `KeyError` above, one on `CARRIED_ROW_FIELDS` not existing.

## Not verified

- `track.py` against the live search API, for the same reason as every previous audit (403). This
  change never touches the network: it filters a list already in memory.
- **Whether any repository in the table has since been deleted.** Sampling 20 of the 603 repository
  links returned 403 for all 20 — including `langgenius/dify` and `assafelovic/gpt-researcher`,
  which plainly exist — because this session's proxy refuses any repository outside its own scope.
  A 403 there is not evidence of a dead link, and this audit reports none.

## Secrets

Clean. No key, seed, token or `.env` in the tree or in this change. `test_no_token_ever_reaches_the
_published_files` still passes.
