"""Laws for the outreach tracker. Each is one sentence plus an observable test."""
import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import track  # noqa: E402


def item(repo, number=1, state="open", merged=None, pr=False, title="t", comments=0):
    it = {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "number": number,
        "title": title,
        "state": state,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": "2026-09-10T00:00:00Z",
        "updated_at": "2026-09-11T00:00:00Z",
        "comments": comments,
    }
    if pr:
        it["pull_request"] = {"merged_at": merged}
    return it


import unittest


class OutreachTable(unittest.TestCase):
    def test_work_on_a_repo_we_own_is_never_outreach(self):
        """An issue on a repository under an account we control reached no maintainer and is excluded."""
        rows = track.rows_from(
            [
                item("PANDeveloper001/awesome-x402"),   # our fork of someone else's project
                item("dhyabi2/nano-agent"),             # the owner's own account
                item("PanDeveloper001/x402"),           # ownership is case-insensitive
                item("xpaysh/awesome-x402", number=1555),
            ],
            "PANDeveloper001",
        )
        assert [r["repo"] for r in rows] == ["xpaysh/awesome-x402"]


    def test_merged_is_never_flattened_into_closed(self):
        """A merged PR is the only state that means someone accepted our work, so it is its own state."""
        assert track.state_of(item("them/x", pr=True, state="closed", merged="2026-09-12T00:00:00Z")) == "merged"
        assert track.state_of(item("them/x", pr=True, state="closed", merged=None)) == "closed"
        assert track.state_of(item("them/x", state="open")) == "open"


    def test_a_pull_request_reaches_the_table(self):
        """The law above proves `state_of` can say merged. It says nothing about whether a pull
        request ever arrives to be judged - and none did. The published table carried 389 rows, every
        one an issue and `0 merged`, while `author:dhyabi2 type:pr` returned 178 pull requests on
        other people's repositories. The query asked for `type:issue`, which excludes them.

        The fake below answers the way the endpoint does: it serves whichever kind the query names,
        and refuses a query that names neither with the 422 the real API returns."""
        served = []

        def fake_fetch(path, token=None):
            served.append(path)
            if "is%3Apull-request" in path or "is:pull-request" in path or "type:pr" in path:
                return {"items": [item("them/x", number=7, pr=True, state="closed",
                                       merged="2026-09-12T00:00:00Z", title="Add Nano (XNO)")]}
            if "is:issue" in path or "type:issue" in path:
                return {"items": [item("them/y", number=8, title="Nano settlement rail")]}
            raise AssertionError(f"422: the search API requires a kind qualifier: {path}")

        real, track.fetch = track.fetch, fake_fetch
        try:
            rows, skipped = track.collect(token=None, authors=("dhyabi2",))
        finally:
            track.fetch = real

        kinds = sorted(r["kind"] for r in rows)
        assert kinds == ["issue", "pr"], f"pull requests never reached the table: {kinds}"
        assert track.summarise(rows)["merged"] == 1, track.summarise(rows)
        assert any("pull-request" in p or "type:pr" in p for p in served), served


    def test_the_table_carries_every_row_with_a_link_that_resolves(self):
        """Each row renders the repository, the number, the state and a link to the real thread."""
        rows = track.rows_from([item("xpaysh/awesome-x402", number=1555, title="Add Nano (XNO)")], "PANDeveloper001")
        md = track.render(rows, "2026-09-19 00:00 UTC")
        assert "| open | [xpaysh/awesome-x402](https://github.com/xpaysh/awesome-x402) " in md
        assert "[#1555](https://github.com/xpaysh/awesome-x402/issues/1555)" in md
        assert "Add Nano (XNO)" in md


    def test_a_pipe_in_a_title_cannot_break_the_table(self):
        """A title containing a pipe is escaped, or one hostile title silently destroys every row below it."""
        rows = track.rows_from([item("them/x", title="a | b")], "PANDeveloper001")
        line = [l for l in track.render(rows, "now").splitlines() if "them/x" in l][0]
        assert line.replace("\\|", "").count("|") == 9, line
        assert "a \\| b" in line


    def test_an_empty_table_says_so_rather_than_rendering_nothing(self):
        """Zero outreach is a real and publishable answer; a blank table reads as a broken generator."""
        md = track.render([], "now")
        assert "nothing has left the house yet" in md
        assert "**0** submissions" in md


    def test_the_summary_counts_replies_because_a_reply_is_the_only_proof_a_person_read_it(self):
        s = track.summarise(
            track.rows_from(
                [item("them/x", 1, comments=3), item("them/y", 2, comments=0), item("them/x", 3, comments=1)],
                "PANDeveloper001",
            )
        )
        assert s == {"total": 3, "merged": 0, "open": 3, "closed": 0, "repos": 2, "answered": 2}


    def test_no_token_ever_reaches_the_published_files(self):
        """The generator reads a token from the environment; nothing it writes may contain one."""
        # Built by concatenation on purpose: a literal token-shaped string in the repo trips the secret
        # scanner that gates every publish, and a law must never be the reason a push is refused.
        fake = "gh" + "p_" + "0" * 36
        os.environ["GITHUB_TOKEN"] = fake
        try:
            rows = track.rows_from([item("them/x")], "PANDeveloper001")
            blob = track.render(rows, "now") + json.dumps(rows)
            assert fake not in blob and "gh" + "p_" not in blob
        finally:
            os.environ.pop("GITHUB_TOKEN", None)



    def test_collect_skips_an_author_github_will_not_index(self):
        """An account invisible to search answers 422: that author's rows are skipped, not the
        whole table. (Observed: an anonymous-invisible account returns 422 on both `type:issue`
        and `type:pr`, so skipping the author is the honest answer — it is not an auth failure.)"""
        served = []

        def fake_fetch(path, token=None):
            served.append(path)
            if "author:PANDeveloper001" in path:
                raise urllib.error.HTTPError(path, 422, "Unprocessable Entity", {}, None)
            if "type:pr" in path:
                return {"items": [item("them/x", number=7, pr=True, state="closed",
                                       merged="2026-09-12T00:00:00Z", title="Add Nano (XNO)")]}
            return {"items": [item("them/y", number=8, title="Nano settlement rail")]}

        real, track.fetch = track.fetch, fake_fetch
        try:
            rows, skipped = track.collect(token=None, authors=("PANDeveloper001", "dhyabi2"))
        finally:
            track.fetch = real

        assert skipped == ["PANDeveloper001"], skipped
        # the live author's rows still reach the table
        assert {r["repo"] for r in rows} == {"them/x", "them/y"}, rows
        assert any("author:PANDeveloper001" in p for p in served)

    def test_collect_skips_422_only_and_reexposes_other_errors(self):
        """A 422 is an indexable-account problem and is skipped; a genuinely different failure
        (403 rate limit exhausted, a bad query) must still abort, not be swallowed."""
        def fake_fetch(path, token=None):
            if "author:dhyabi2" in path:
                raise urllib.error.HTTPError(path, 403, "rate limit", {}, None)
            return {"items": []}

        real, track.fetch = track.fetch, fake_fetch
        try:
            self.assertRaises(urllib.error.HTTPError, track.collect,
                              token=None, authors=("dhyabi2",))
        finally:
            track.fetch = real

    def test_a_carried_over_row_is_never_published_as_current_state(self):
        """`main` keeps a 422'd author's last-known rows so they do not vanish from the table, and
        stamps the file with a fresh `Generated` time under a heading promising "the state GitHub
        reports right now". Nothing it wrote said WHICH rows were not fetched, so a reader could not
        tell remembered state from live state. The published files must say it themselves — a warning
        on stderr is read by the Action's log and by nobody else."""
        rows = track.rows_from([item("them/x", 1)], "PANDeveloper001")
        rows += track.rows_from([item("them/y", 2)], "dhyabi2")

        md = track.render(rows, "now", ["PANDeveloper001"])
        preamble = md.split("| State |")[0]
        assert "PANDeveloper001" in preamble, "the table says nothing about the skip"
        assert "1 of these rows were not checked" in preamble, preamble

        data = track.payload(rows, "now", ["PANDeveloper001"])
        assert data["unverified_authors"] == ["PANDeveloper001"], data["unverified_authors"]

        # A clean run must stay quiet: a standing disclaimer nobody can act on is worse than none.
        assert "not checked in this run" not in track.render(rows, "now")
        assert track.payload(rows, "now")["unverified_authors"] == []


    def _rebuild_with_previous(self, previous_text, live):
        """Run main() in a temp dir whose data.json holds `previous_text`. Returns (rc, stderr, published)."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "data.json"), "w") as f:
                f.write(previous_text)
            with patched(track, _token=lambda: "x",
                         # *a/**k: this stub stands in for collect, and main() legitimately passes it
                         # more than a token. Pinning the exact signature here made an unrelated
                         # change to collect look like four broken laws.
                         collect=lambda *a, **k: (list(live), ["PANDeveloper001"]),
                         __file__=os.path.join(d, "track.py")):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = track.main()          # must not raise
            with open(os.path.join(d, "data.json")) as f:
                return rc, err.getvalue(), json.load(f)

    def test_a_corrupt_previous_table_does_not_abort_the_rebuild(self):
        """A data.json that cannot be parsed must cost the carried-over rows, not the whole run.

        main() opens data.json "w" to rebuild it, so a killed or cancelled run leaves a truncated
        file. Reloading it then raises json.JSONDecodeError -- a ValueError, not an OSError -- so the
        one branch written to degrade gracefully aborted instead, and every later run aborted with
        it."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        for name, bad in (
            ("truncated mid-write", '{"rows": [{"url": "https://github.com/them/x/iss'),
            ("not an object at all", "[1, 2, 3]"),
            ("empty file", ""),
        ):
            with self.subTest(name):
                rc, err, published = self._rebuild_with_previous(bad, live)
                assert rc == 0, rc
                assert "could not reload data.json" in err, err
                # the live row is still published, and the table does not claim the missing ones
                assert [r["url"] for r in published["rows"]] == [live[0]["url"]], published["rows"]
                assert published["unverified_authors"] == ["PANDeveloper001"]

    def test_a_previous_row_without_a_url_is_skipped_not_fatal(self):
        """The carry-over keys on "url". A parseable table whose rows lack one is not a reload
        failure -- those rows are simply not carried, with no warning, and nothing raises."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        rc, err, published = self._rebuild_with_previous(
            '{"rows": [{"author": "PANDeveloper001"}]}', live
        )
        assert rc == 0, rc
        assert "could not reload data.json" not in err, err
        assert [r["url"] for r in published["rows"]] == [live[0]["url"]], published["rows"]

    def test_the_skip_banner_never_claims_rows_that_are_not_in_the_table(self):
        """With nothing carried over, the banner said "0 of these rows were not checked in this
        run ... their rows are the ones last successfully fetched" -- describing rows the reader
        cannot see. When the count is zero the rows are absent, and the table must say that."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        preamble = track.render(live, "now", ["PANDeveloper001"]).split("| State |")[0]
        assert "0 of these rows were not checked" not in preamble, preamble
        assert "no earlier rows for them" in preamble, preamble
        assert "PANDeveloper001" in preamble
        # and the real carried-over case still reads the way it did
        carried = live + track.rows_from([item("them/x", 1)], "PANDeveloper001")
        other = track.render(carried, "now", ["PANDeveloper001"]).split("| State |")[0]
        assert "1 of these rows were not checked" in other, other


    def test_a_query_cut_off_at_the_ceiling_is_reported_not_hidden(self):
        """The search API returns at most 1000 results per query. collect() stopped at page 10 and
        said nothing, so a table missing rows read exactly like a complete one -- while the README's
        first sentence promises EVERY issue and pull request the swarm has opened.

        Here the fake API claims 1500 matches and hands over 1000. The shortfall must be named."""
        # The page number is counted, not parsed out of the path: `per_page=100` comes first in the
        # query string, so splitting on "page=" finds that instead and every page looks alike.
        calls = []

        def ceiling(path, token=None):
            calls.append(path)
            n = len(calls)
            return {"total_count": 1500,
                    "items": [item("them/r%d" % (n * 100 + i), number=i) for i in range(100)]}

        with patched(track, fetch=ceiling):
            truncated = []
            rows, skipped = track.collect(None, authors=("dhyabi2",), truncated=truncated)
        self.assertEqual(skipped, [])
        self.assertEqual(len(calls), 20, "10 pages per kind, 2 kinds: the ceiling is per query")
        self.assertEqual(len(rows), 2000, "the ceiling itself moved; this law assumed 10 pages of 100")
        self.assertEqual(truncated, ["dhyabi2/issue", "dhyabi2/pr"],
                         "a query cut off at 1000 results was not reported")

    def test_a_complete_query_is_never_called_truncated(self):
        """The counterpart: when GitHub hands over everything it says it has, nothing is reported.
        A banner that cried wolf on every run would be worse than no banner."""
        def complete(path, token=None):
            return {"total_count": 2, "items": [item("them/a", 1), item("them/b", 2)]}

        with patched(track, fetch=complete):
            truncated = []
            track.collect(None, authors=("dhyabi2",), truncated=truncated)
        self.assertEqual(truncated, [])

    def test_an_api_that_omits_total_count_is_not_guessed_at(self):
        """No total_count means no evidence either way, and a tracker whose point is honest counting
        must not invent a shortfall it cannot see."""
        def no_total(path, token=None):
            return {"items": [item("them/a", 1)]}

        with patched(track, fetch=no_total):
            truncated = []
            track.collect(None, authors=("dhyabi2",), truncated=truncated)
        self.assertEqual(truncated, [])

    def test_collect_still_works_without_the_truncated_argument(self):
        """The argument is optional: every existing caller keeps working untouched."""
        with patched(track, fetch=lambda path, token=None: {"total_count": 1, "items": [item("them/a", 1)]}):
            rows, skipped = track.collect(None, authors=("dhyabi2",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, [])

    def test_the_table_says_so_when_it_is_incomplete(self):
        """The banner has to reach the reader of the README, not just a log nobody keeps."""
        rows = track.rows_from([item("them/x", 1)], "dhyabi2")
        head = track.render(rows, "now", (), ["dhyabi2/issue"]).split("| State |")[0]
        self.assertIn("incomplete", head.lower())
        self.assertIn("1000", head)
        self.assertIn("dhyabi2/issue", head)
        self.assertIn("floor", head.lower(), "the total must be described as a floor, not the whole")

    def test_a_complete_table_carries_no_incompleteness_banner(self):
        rows = track.rows_from([item("them/x", 1)], "dhyabi2")
        head = track.render(rows, "now").split("| State |")[0]
        self.assertNotIn("incomplete", head.lower())

    def test_data_json_names_the_truncated_queries(self):
        """A consumer reading data.json must be able to tell a floor from a total without scraping
        the README prose."""
        rows = track.rows_from([item("them/x", 1)], "dhyabi2")
        self.assertEqual(track.payload(rows, "now", (), ["dhyabi2/pr"])["truncated_queries"],
                         ["dhyabi2/pr"])
        self.assertEqual(track.payload(rows, "now")["truncated_queries"], [])


class HttpErrors(unittest.TestCase):
    """A failed rebuild has to say WHICH query failed and WHY.

    The Action's own failing run of 2026-09-24 reported exactly this and nothing else:

        File "track.py", line 120, in collect
          data = fetch(f"/search/issues?q={q}&per_page=100&page={page}...", token)
        urllib.error.HTTPError: HTTP Error 422: Unprocessable Entity

    No author, no query, and none of GitHub's response body, which says plainly whether the account
    cannot be searched or the query is malformed. Four audits in a row recorded the cause as
    unestablished for want of those two facts.
    """

    VALIDATION_BODY = json.dumps({
        "message": "Validation Failed",
        "errors": [{
            "message": "The listed users cannot be searched either because the users do not exist "
                       "or you do not have permission to view the information.",
            "resource": "Search", "field": "q", "code": "invalid",
        }],
        "documentation_url": "https://docs.github.com/v3/search/",
    }).encode()

    @staticmethod
    def _raiser(code, body, url="https://api.github.com/x"):
        def fake(req, timeout=None):
            raise urllib.error.HTTPError(url, code, "Unprocessable Entity", {}, io.BytesIO(body))
        return fake

    def test_a_failed_request_names_the_query_and_githubs_own_reason(self):
        """The raised error carries the path asked for and the message GitHub sent back."""
        path = "/search/issues?q=author:PANDeveloper001+type:pr&per_page=100&page=1"
        with patched(track.urllib.request, urlopen=self._raiser(422, self.VALIDATION_BODY)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                track.fetch(path)
        text = str(caught.exception)
        self.assertIn("author:PANDeveloper001+type:pr", text)
        self.assertIn("Validation Failed", text)
        self.assertIn("cannot be searched", text)

    def test_the_status_code_survives_enrichment(self):
        """`collect` branches on `e.code == 422`, so the code must not be repackaged away."""
        with patched(track.urllib.request, urlopen=self._raiser(422, self.VALIDATION_BODY)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                track.fetch("/search/issues?q=author:x+type:pr")
        self.assertEqual(caught.exception.code, 422)

    def test_a_422_author_is_still_skipped_not_fatal(self):
        """End to end: the richer message must not cost the graceful skip it explains."""
        with patched(track.urllib.request, urlopen=self._raiser(422, self.VALIDATION_BODY)):
            rows, skipped = track.collect(None, authors=("ghost",))
        self.assertEqual(rows, [])
        self.assertEqual(skipped, ["ghost"])

    def test_a_body_that_is_not_json_is_reported_as_text(self):
        """An HTML error page or a proxy's plain text is still more than nothing."""
        with patched(track.urllib.request, urlopen=self._raiser(502, b"upstream connect error")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                track.fetch("/search/issues?q=author:x+type:pr")
        self.assertIn("upstream connect error", str(caught.exception))

    def test_reading_the_body_never_becomes_the_failure(self):
        """A diagnostic that raises would replace the real error with its own."""

        class Hostile(io.BytesIO):
            def read(self, *a):
                raise OSError("connection reset while reading the error body")

        def fake(req, timeout=None):
            raise urllib.error.HTTPError("https://api.github.com/x", 422, "Unprocessable Entity", {}, Hostile())

        with patched(track.urllib.request, urlopen=fake):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                track.fetch("/search/issues?q=author:x+type:pr")
        self.assertEqual(caught.exception.code, 422)
        self.assertIn("author:x+type:pr", str(caught.exception))

    def test_an_empty_body_adds_nothing_rather_than_a_dangling_separator(self):
        """A 404 with no body should read cleanly, not end in `--`."""
        with patched(track.urllib.request, urlopen=self._raiser(404, b"")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                track.fetch("/search/issues?q=author:x+type:pr")
        self.assertNotIn("--", str(caught.exception))


class CarriedRowsKeepTheTableInOrder(unittest.TestCase):
    """`main` appends carried-over rows to a list `collect` had already sorted.

    Without a re-sort the rendered table runs merged/open/closed and then starts over, and a
    carried-over MERGED pull request -- the one state in this table that means someone else
    accepted our work -- is rendered below every closed row. The published table has this
    today: its first 538 rows are live and perfectly ordered, and the 343 carried rows that
    follow hold 53 state inversions of their own.
    """

    def _row(self, repo, number, state, author):
        return {
            "repo": repo,
            "number": number,
            "title": "t",
            "kind": "pr",
            "state": state,
            "url": f"https://github.com/{repo}/pull/{number}",
            "author": author,
            "created_at": "2026-09-10T00:00:00Z",
            "updated_at": "2026-09-11T00:00:00Z",
            "comments": 0,
        }

    def _run_main(self, live_rows, skipped, previous_rows):
        """Drive main() with collect() stubbed, in a scratch directory."""
        here = tempfile.mkdtemp()
        with open(os.path.join(here, "data.json"), "w") as f:
            json.dump({"generated": "old", "summary": {}, "rows": previous_rows}, f)

        def fake_collect(token=None, authors=track.AUTHORS, truncated=None):
            return sorted(live_rows, key=track.row_order), skipped

        real_dirname = track.os.path.dirname

        def fake_dirname(path):
            return here if path.endswith("track.py") else real_dirname(path)

        with patched(track, collect=fake_collect), patched(track.os.path, dirname=fake_dirname):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(track.main(), 0)
        with open(os.path.join(here, "data.json")) as f:
            return json.load(f)["rows"]

    def test_a_carried_over_merged_pr_is_not_stranded_below_the_closed_rows(self):
        live = [
            self._row("them/a", 1, "merged", "dhyabi2"),
            self._row("them/b", 2, "open", "dhyabi2"),
            self._row("them/c", 3, "closed", "dhyabi2"),
        ]
        previous = [
            self._row("them/d", 4, "closed", "PANDeveloper001"),
            self._row("them/e", 5, "merged", "PANDeveloper001"),
            self._row("them/f", 6, "open", "PANDeveloper001"),
        ]
        rows = self._run_main(live, ["PANDeveloper001"], previous)
        states = [r["state"] for r in rows]
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            states,
            ["merged", "merged", "open", "open", "closed", "closed"],
            f"the table is not in state order: {states}",
        )

    def test_the_whole_table_is_monotonic_in_state_after_a_carry_over(self):
        """The property, not one arrangement: no row may rank above the row before it."""
        live = [self._row("them/live%d" % i, i, s, "dhyabi2")
                for i, s in enumerate(["open", "merged", "closed", "open"])]
        previous = [self._row("them/prev%d" % i, 100 + i, s, "PANDeveloper001")
                    for i, s in enumerate(["closed", "open", "merged", "closed", "merged"])]
        rows = self._run_main(live, ["PANDeveloper001"], previous)
        ranks = [track.STATE_ORDER[r["state"]] for r in rows]
        self.assertEqual(ranks, sorted(ranks), [r["state"] for r in rows])

    def test_collect_and_main_sort_by_the_same_rule(self):
        """One key, used twice. Two copies of the expression would be two things to keep in step."""
        rows = [self._row("them/b", 2, "closed", "x"), self._row("them/a", 1, "merged", "x")]
        self.assertEqual(
            [r["repo"] for r in sorted(rows, key=track.row_order)], ["them/a", "them/b"]
        )


class patched:
    """Temporarily set attributes on a module, restoring them afterwards."""

    def __init__(self, mod, **attrs):
        self.mod, self.attrs, self.old = mod, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.old[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(self.mod, k, v)
        return False


if __name__ == "__main__":
    unittest.main()