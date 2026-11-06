"""Laws for the outreach tracker. Each is one sentence plus an observable test."""
import contextlib
import io
import json
import os
import sys
import tempfile
import urllib.error
from datetime import datetime, timezone

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


    def test_a_backslash_before_a_pipe_cannot_break_the_table(self):
        self.assertEqual(track._cell("a\\|b"), "a\\\\\\|b")
        self.assertEqual(track._cell("ends with \\"), "ends with \\\\")
        row = dict(track.rows_from([item("them/x", 1)], "dhyabi2")[0], title="x \\| y")
        line = [l for l in track.render([row], "now").splitlines() if "them/x" in l][0]
        # Drop escaped backslashes, then escaped pipes: 9 real separators must remain, no more.
        self.assertEqual(line.replace("\\\\", "").replace("\\|", "").count("|"), 9)

    def test_a_newline_in_a_title_cannot_break_the_table(self):
        """A multi-line title (or one with a pipe) stays inside its own row: whitespace collapses to one
        space and the pipe is escaped, so the row keeps all its cells and nothing spills onto a new line."""
        rows = track.rows_from([item("them/x", title="first line\nsecond | part\r\n\tthird")], "PANDeveloper001")
        rows[0]["title"] = "first line\nsecond | part\r\n\tthird"  # a carried title, not re-stripped
        md = track.render(rows, "now")
        lines = [l for l in md.splitlines() if "them/x" in l]
        assert len(lines) == 1, lines
        assert lines[0].replace("\\|", "").count("|") == 9, lines[0]
        assert "first line second \\| part third" in lines[0], lines[0]
        assert not any(l.startswith(("second", "third", "\tthird")) for l in md.splitlines()), md

    def test_a_live_item_with_null_comments_counts_as_unanswered_not_a_crash(self):
        """The search API can hand back `"comments": null` (or omit it); that is zero replies, not a TypeError."""
        missing = item("them/z", 4)
        del missing["comments"]
        rows = track.rows_from(
            [item("them/x", 1, comments=None), item("them/y", 2, comments="2"), missing, item("them/w", 5, comments=1)],
            "PANDeveloper001",
        )
        assert [r["comments"] for r in rows] == [0, 2, 0, 1], rows
        s = track.summarise(rows)
        assert s["answered"] == 2 and s["total"] == 4, s
        rows[0]["comments"] = None  # a row reaching summarise/render unrepaired
        assert track.summarise(rows)["answered"] == 2
        line = [l for l in track.render(rows, "now").splitlines() if "them/x" in l][0]
        assert "| 0 |" in line, line

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


    def _rebuild_with_previous(self, previous_text, live, skipped=("PANDeveloper001",)):
        """Run main() in a temp dir whose data.json holds `previous_text`. Returns (rc, stderr, published)."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "data.json"), "w") as f:
                f.write(previous_text)
            with patched(track, _token=lambda: "x",
                         # *a/**k: this stub stands in for collect, and main() legitimately passes it
                         # more than a token. Pinning the exact signature here made an unrelated
                         # change to collect look like four broken laws.
                         collect=lambda *a, **k: (list(live), list(skipped)),
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

    def test_a_live_open_thread_the_search_missed_is_carried_forward(self):
        """The search API under-reports this account's open threads, so a rebuild that trusts only
        search silently drops live open threads -- the one kind the swarm must never lose. A
        previously-tracked OPEN row that is not in the fresh set but still resolves as open on
        GitHub must be carried forward; one that has closed or 404s must not."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        old = track.rows_from([item("them/old", 1)], "dhyabi2")[0]
        old["url"] = "https://github.com/them/old/issues/1"
        dead = track.rows_from([item("them/dead", 9)], "dhyabi2")[0]
        dead["url"] = "https://github.com/them/dead/issues/9"
        dead["gone_once"] = True  # it already 404'd last run; a second 404 drops it
        prev = json.dumps({"rows": [old, dead]})
        with patched(track, _lookup=lambda url, token: {"state": "open"} if url.endswith("/them/old/issues/1") else None):
            rc, err, published = self._rebuild_with_previous(prev, live)
        assert rc == 0, rc
        urls = {r["url"] for r in published["rows"]}
        assert "https://github.com/them/old/issues/1" in urls, urls
        assert "https://github.com/them/dead/issues/9" not in urls, urls
        assert "carried forward 1" in err, err

    def test_open_rows_are_carried_forward_even_when_no_author_was_skipped(self):
        """The carry-forward guards against a search shortfall, which happens on an ordinary run.
        Gated on a 422 skip, it never ran when every author was searchable, so a live open thread
        the search left out vanished without a word."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        old = dict(track.rows_from([item("them/old", 1)], "dhyabi2")[0])
        gone = dict(track.rows_from([item("them/gone", 3)], "dhyabi2")[0], gone_once=True)
        closed = dict(track.rows_from([item("them/shut", 4, state="closed")], "dhyabi2")[0])
        asked = []

        def lookup(url, token):
            asked.append(url)
            if url == closed["url"]:
                return {"state": "closed"}
            return {"state": "open"} if url == old["url"] else None

        prev = json.dumps({"rows": [old, gone, closed]})
        with patched(track, _lookup=lookup):
            rc, err, published = self._rebuild_with_previous(prev, live, skipped=())
        assert rc == 0, rc
        urls = {r["url"] for r in published["rows"]}
        assert urls == {live[0]["url"], old["url"], closed["url"]}, urls
        assert closed["url"] in asked, "a closed row the search missed is looked up, not dropped"
        assert "carried forward 1" in err, err
        assert published["unverified_authors"] == []

    def test_a_rate_limited_lookup_keeps_open_rows_and_says_so(self):
        """A lookup that could not be made is not evidence the thread closed. Treating it as "not
        open" deleted every tracked open row on a rate limit or a missing token, with exit 0."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        prev_rows = [dict(track.rows_from([item(f"them/o{i}", i)], "dhyabi2")[0]) for i in range(1, 4)]
        calls = []

        def limited(url, token):
            calls.append(url)
            raise track.LookupFailed("HTTP 429", rate_limited=True)

        with patched(track, _lookup=limited):
            rc, err, published = self._rebuild_with_previous(
                json.dumps({"rows": prev_rows}), live, skipped=())
        assert rc == 0, rc
        urls = {r["url"] for r in published["rows"]}
        assert all(r["url"] in urls for r in prev_rows), urls
        assert len(calls) == 1, f"kept asking after a rate limit: {calls}"
        assert "rate-limited for 3" in err, err
        assert published["unverified_open_urls"] == sorted(r["url"] for r in prev_rows)

    @staticmethod
    def _fake_github(answers, asked=None):
        """A stand-in for urlopen: `answers` maps an API path suffix to a JSON body or an HTTP code."""

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout=None):
            url = req.full_url
            if asked is not None:
                asked.append(url)
            for suffix, answer in answers.items():
                if url.endswith(suffix):
                    if isinstance(answer, int):
                        raise urllib.error.HTTPError(url, answer, "x", {}, None)
                    return Resp(json.dumps(answer).encode())
            raise urllib.error.HTTPError(url, 404, "x", {}, None)

        return urlopen

    def test_a_carried_open_row_takes_its_live_replies_not_the_stale_ones(self):
        """The lookup fetches the live thread; keeping only its state published the reply count and
        updated_at from the last run, so a maintainer reply on a carried row never counted as
        answered."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        old = dict(track.rows_from([item("them/old", 1, comments=0)], "dhyabi2")[0])
        answers = {"/repos/them/old/issues/1": {"state": "open", "comments": 3,
                                                "updated_at": "2026-11-01T00:00:00Z", "title": "t"}}
        with patched(track.urllib.request, urlopen=self._fake_github(answers)):
            rc, err, published = self._rebuild_with_previous(
                json.dumps({"rows": [old]}), live, skipped=())
        assert rc == 0, rc
        row = next(r for r in published["rows"] if r["url"] == old["url"])
        self.assertEqual(row["state"], "open")
        self.assertEqual(row["comments"], 3)
        self.assertEqual(row["updated_at"], "2026-11-01T00:00:00Z")
        self.assertEqual(published["summary"]["answered"], 1)

    def test_a_carried_row_that_has_merged_or_closed_is_shown_in_its_new_state(self):
        """A previously-open thread the search missed and that has since merged or closed is still
        ours and still real: it must appear in its new state, not vanish. Only a 404/410 drops it."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        merged = dict(track.rows_from([item("them/m", 5, pr=True)], "dhyabi2")[0])
        merged["url"] = "https://github.com/them/m/pull/5"
        shut = dict(track.rows_from([item("them/c", 6)], "dhyabi2")[0])
        gone = dict(track.rows_from([item("them/g", 7)], "dhyabi2")[0], gone_once=True)
        answers = {
            "/repos/them/m/pulls/5": {"state": "closed", "merged_at": "2026-10-30T00:00:00Z",
                                      "comments": 2, "updated_at": "2026-10-30T00:00:00Z"},
            "/repos/them/c/issues/6": {"state": "closed", "comments": 1,
                                       "updated_at": "2026-10-29T00:00:00Z"},
            "/repos/them/g/issues/7": 404,
        }
        with patched(track.urllib.request, urlopen=self._fake_github(answers)):
            rc, err, published = self._rebuild_with_previous(
                json.dumps({"rows": [merged, shut, gone]}), live, skipped=())
        assert rc == 0, rc
        by_url = {r["url"]: r for r in published["rows"]}
        self.assertEqual(by_url[merged["url"]]["state"], "merged")
        self.assertEqual(by_url[shut["url"]]["state"], "closed")
        self.assertNotIn(gone["url"], by_url)
        self.assertEqual(published["summary"]["merged"], 1)
        self.assertEqual(published["unverified_open_urls"], [])
        states = [r["state"] for r in published["rows"]]
        self.assertEqual(states, sorted(states, key=track.STATE_ORDER.get), "table not in state order")

    def test_after_a_rate_limit_every_row_is_verified_within_a_few_runs(self):
        """With the lookups always starting at the same row, a rate limit after N lookups left the
        same tail rows unverified on every run, forever. The start must move between runs."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        prev_rows = [dict(track.rows_from([item(f"them/o{i}", i)], "dhyabi2")[0]) for i in range(1, 6)]
        verified = set()
        rng = __import__("random").Random(1234)
        for _ in range(40):
            asked = []
            budget = [1]

            def urlopen(req, timeout=None, asked=asked, budget=budget):
                asked.append(req.full_url)
                if budget[0] <= 0:
                    raise urllib.error.HTTPError(req.full_url, 429, "x", {}, None)
                budget[0] -= 1
                return self._fake_github({"": {"state": "open", "comments": 0}})(req, timeout)

            with patched(track.urllib.request, urlopen=urlopen), patched(track, random=rng):
                rc, err, published = self._rebuild_with_previous(
                    json.dumps({"rows": prev_rows}), live, skipped=())
            assert rc == 0, rc
            self.assertEqual(len(published["unverified_open_urls"]), 4)
            verified.update(set(r["url"] for r in prev_rows) - set(published["unverified_open_urls"]))
        self.assertEqual(verified, {r["url"] for r in prev_rows})

    def test_the_readme_names_open_rows_whose_state_was_not_checked(self):
        rows = track.rows_from([item("them/x", 1)], "dhyabi2")
        head = track.render(rows, "now", (), (), [rows[0]["url"]]).split("| State |")[0]
        self.assertIn("1 open rows were not checked", head)
        self.assertNotIn("not checked", track.render(rows, "now").split("| State |")[0])

    def test_still_open_raises_on_no_answer_and_says_false_only_when_gone(self):
        """404/410 is an answer (gone); 403/429/5xx/network are not, and must not read as closed."""
        url = "https://github.com/them/x/issues/1"

        def raising(code):
            def f(req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
            return f

        for code in (404, 410):
            with patched(track.urllib.request, urlopen=raising(code)):
                self.assertIs(track._still_open(url, None), False)
        # A bare 403 (no rate-limit headers) is about one repository, not a limit: see
        # test_a_plain_403_is_not_a_rate_limit_but_an_exhausted_quota_is.
        for code, limited in ((403, False), (429, True), (502, False)):
            with patched(track.urllib.request, urlopen=raising(code)):
                with self.assertRaises(track.LookupFailed) as cm:
                    track._still_open(url, None)
                self.assertIs(cm.exception.rate_limited, limited)

        def offline(req, timeout=None):
            raise urllib.error.URLError("down")
        with patched(track.urllib.request, urlopen=offline):
            with self.assertRaises(track.LookupFailed):
                track._still_open(url, None)

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
        # open and closed are queried separately, so a 2-kind account becomes 4 queries; the naive
        # mock returns a full page for every one of them, so each hits the ceiling at page 10.
        self.assertEqual(len(calls), 40, "10 pages x 2 kinds x 2 states: the ceiling is per query")
        self.assertEqual(len(rows), 4000, "the ceiling itself moved; this law assumed 10 pages of 100")
        self.assertEqual(truncated, ["dhyabi2/issue/open", "dhyabi2/issue/closed",
                                     "dhyabi2/pr/open", "dhyabi2/pr/closed"],
                         "a query cut off at 1000 results was not reported")

    def test_a_search_shortfall_below_the_ceiling_is_reported(self):
        """total_count > rows received is the only visible trace of a search shortfall (measured:
        568 open threads received where 846 were still open). Reporting only a full tenth page
        hid it entirely, so a table missing a third of its open rows read as complete."""
        def partial(path, token=None):
            return {"total_count": 956, "items": [item("them/a", 1)]}
        with patched(track, fetch=partial):
            tr = []
            track.collect(None, authors=("dhyabi2",), truncated=tr)
        self.assertEqual(tr, ["dhyabi2/issue/open", "dhyabi2/issue/closed",
                              "dhyabi2/pr/open", "dhyabi2/pr/closed"],
                         "a query that handed over fewer rows than it matched was not reported")

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


class AShapelessCarriedRowIsSkippedNotFatal(unittest.TestCase):
    """A row read back from data.json that lacks a field the table is built from.

    `main`'s carry-over branch catches OSError and ValueError precisely so a malformed data.json
    degrades instead of aborting the rebuild. A MISSING KEY raises KeyError, which is neither: with
    one such row present, `rows.sort(key=row_order)` died on a bare `KeyError: 'state'`, the rebuild
    published nothing, and nothing said why. This repository's own published data.json carries five
    rows of that shape today (appended 2026-09-29 without state, url or author), so the fixture
    below is the real thing rather than an invented one.
    """

    def _rebuild(self, previous_rows):
        here = tempfile.mkdtemp()
        with open(os.path.join(here, "data.json"), "w") as f:
            json.dump({"generated": "old", "summary": {}, "rows": previous_rows}, f)

        def fake_collect(token=None, authors=track.AUTHORS, truncated=None):
            return [], ["PANDeveloper001"]

        real_dirname = track.os.path.dirname

        def fake_dirname(path):
            return here if path.endswith("track.py") else real_dirname(path)

        err = io.StringIO()
        with patched(track, collect=fake_collect), patched(track.os.path, dirname=fake_dirname):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = track.main()
        with open(os.path.join(here, "data.json")) as f:
            return code, json.load(f)["rows"], err.getvalue()

    @staticmethod
    def _row(**over):
        row = {
            "repo": "them/a", "number": 1, "title": "t", "kind": "issue", "state": "open",
            "url": "https://github.com/them/a/issues/1", "author": "PANDeveloper001",
            "created_at": "2026-09-29T03:54:22Z", "updated_at": "2026-09-29T03:54:22Z",
            "comments": 0,
        }
        row.update(over)
        return row

    def test_the_fields_checked_are_the_fields_rows_from_produces(self):
        """Derived, not restated: the guard must name fields a real row actually has."""
        built = track.rows_from([{
            "repository_url": "https://api.github.com/repos/them/a", "number": 1, "title": "t",
            "state": "open", "html_url": "https://github.com/them/a/issues/1",
            "created_at": "x", "updated_at": "y", "comments": 0,
        }], "PANDeveloper001")
        self.assertEqual(len(built), 1)
        self.assertTrue(set(track.CARRIED_ROW_FIELDS) <= set(built[0]),
                        f"the guard names fields rows_from does not produce: "
                        f"{set(track.CARRIED_ROW_FIELDS) - set(built[0])}")
        self.assertTrue(track.usable(built[0]))

    def test_a_carried_row_without_state_does_not_abort_the_rebuild(self):
        shapeless = self._row()
        del shapeless["state"]
        code, rows, err = self._rebuild([shapeless])
        self.assertEqual(code, 0)
        self.assertEqual(rows, [], "a row the table cannot order must not be published")
        self.assertIn("missing fields", err, "the skip was silent")

    def test_a_shapeless_row_never_hides_a_good_one(self):
        good = self._row(repo="them/good", number=2,
                         url="https://github.com/them/good/issues/2")
        shapeless = self._row()
        del shapeless["state"]
        code, rows, _ = self._rebuild([shapeless, good])
        self.assertEqual(code, 0)
        self.assertEqual([r["repo"] for r in rows], ["them/good"])


class ARebuildNeverLosesThePreviousTable(unittest.TestCase):
    """data.json is the only memory of open threads the search API missed. Opening it with "w"
    before the new table was built meant any failure afterwards -- a carried row with
    `"comments": null` crashing `summarise`, `"title": null` crashing `render` -- left it 0 bytes,
    the next run carried nothing, and every live open thread the search had missed was lost."""

    PREV_README = "# previous README\n"

    @staticmethod
    def _row(**over):
        row = {
            "repo": "them/a", "number": 1, "title": "t", "kind": "issue", "state": "open",
            "url": "https://github.com/them/a/issues/1", "author": "dhyabi2",
            "created_at": "2026-09-29T03:54:22Z", "updated_at": "2026-09-29T03:54:22Z",
            "comments": 0,
        }
        row.update(over)
        return row

    def _run(self, previous_rows, live=(), skipped=(), lookup=None, **extra):
        """main() in a scratch dir. Returns (rc or the exception, stderr, data.json text, README text)."""
        d = tempfile.mkdtemp()
        prev_text = json.dumps({"generated": "old", "summary": {}, "rows": previous_rows})
        with open(os.path.join(d, "data.json"), "w") as f:
            f.write(prev_text)
        with open(os.path.join(d, "README.md"), "w") as f:
            f.write(self.PREV_README)
        attrs = dict(_token=lambda: "x", collect=lambda *a, **k: (list(live), list(skipped)),
                     __file__=os.path.join(d, "track.py"), **extra)
        if lookup is not None:
            attrs["_lookup"] = lookup
        err = io.StringIO()
        with patched(track, **attrs):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                try:
                    rc = track.main()
                except Exception as exc:  # noqa: BLE001 -- the outcome under test
                    rc = exc
        with open(os.path.join(d, "data.json")) as f:
            data_text = f.read()
        with open(os.path.join(d, "README.md")) as f:
            readme = f.read()
        leftovers = [n for n in os.listdir(d) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [], "a temp file was left behind")
        return rc, err.getvalue(), data_text, readme, prev_text

    @staticmethod
    def _limited(url, token):
        raise track.LookupFailed("HTTP 429", rate_limited=True)

    def test_a_carried_row_with_null_comments_or_title_is_repaired_not_fatal(self):
        bad = self._row(comments=None, title=None, number="7", created_at=None)
        rc, err, data_text, readme, _ = self._run([bad], lookup=self._limited)
        self.assertEqual(rc, 0, rc)
        rows = json.loads(data_text)["rows"]
        self.assertEqual(len(rows), 1, "the live open thread was lost")
        self.assertEqual((rows[0]["comments"], rows[0]["title"], rows[0]["number"]), (0, "", 7))
        self.assertIn(bad["url"], readme)

    def test_a_row_that_cannot_be_repaired_is_skipped_and_counted(self):
        good = self._row(repo="them/good", url="https://github.com/them/good/issues/2", number=2)
        for name, bad in (
            ("unknown state", self._row(state="weird")),
            ("url is not text", self._row(url=["x"])),
            ("repo is null", self._row(repo=None)),
        ):
            with self.subTest(name):
                rc, err, data_text, _, _ = self._run([bad, good], lookup=self._limited)
                self.assertEqual(rc, 0, rc)
                self.assertEqual([r["repo"] for r in json.loads(data_text)["rows"]], ["them/good"])
                self.assertIn("1 row(s) in data.json", err)

    def test_a_failure_while_building_the_table_leaves_both_files_untouched(self):
        """Whatever fails, the previous table survives byte for byte, so the next run can still
        carry its open threads forward."""
        def boom(*a, **k):
            raise RuntimeError("boom")

        for name in ("payload", "render", "summarise"):
            with self.subTest(name):
                rc, _, data_text, readme, prev_text = self._run(
                    [self._row()], lookup=self._limited, **{name: boom})
                self.assertIsInstance(rc, RuntimeError)
                self.assertEqual(data_text, prev_text, "data.json was clobbered")
                self.assertEqual(readme, self.PREV_README, "README.md was clobbered")

    def test_a_failed_write_leaves_the_file_as_it_was(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "data.json")
        with open(path, "w") as f:
            f.write("previous")

        def failing_replace(src, dst):
            raise OSError("disk full")

        with patched(track.os, replace=failing_replace):
            with self.assertRaises(OSError):
                track._write_atomic(path, "new")
        with open(path) as f:
            self.assertEqual(f.read(), "previous")
        self.assertEqual(os.listdir(d), ["data.json"], "temp file not cleaned up")


class ACarriedThreadStaysInTheTable(unittest.TestCase):
    """A carried row is one the search did not return. One that turned merged or closed was shown
    in its new state for exactly one run: next run the search still did not return it, only open
    rows were looked up, and it was dropped without a word -- a merged PR included."""

    _run = ARebuildNeverLosesThePreviousTable._run
    _row = staticmethod(ARebuildNeverLosesThePreviousTable._row)
    PREV_README = ARebuildNeverLosesThePreviousTable.PREV_README

    def _two_runs(self, answer):
        """Run 1: an open row the search misses is looked up and has merged. Run 2: feed run 1's
        data.json back in, search still missing it."""
        live = track.rows_from([item("them/y", 2)], "dhyabi2")
        row = self._row(repo="them/m", number=5, kind="pr", url="https://github.com/them/m/pull/5")
        merged = lambda url, token: {"state": "merged", "comments": 2}  # noqa: E731
        rc, _, data_text, _, _ = self._run([row], live=live, lookup=merged)
        self.assertEqual(rc, 0, rc)
        first = json.loads(data_text)["rows"]
        self.assertEqual({r["url"]: r["state"] for r in first}[row["url"]], "merged")
        rc, err, data_text, readme, _ = self._run(first, live=live, lookup=answer)
        self.assertEqual(rc, 0, rc)
        return row, json.loads(data_text), readme, err

    def test_a_carried_row_that_merged_is_still_shown_on_the_next_run(self):
        row, data, readme, _ = self._two_runs(lambda url, token: {"state": "merged", "comments": 2})
        by_url = {r["url"]: r for r in data["rows"]}
        self.assertIn(row["url"], by_url, "the merged PR vanished on the run after it merged")
        self.assertEqual(by_url[row["url"]]["state"], "merged")
        self.assertEqual(data["summary"]["merged"], 1)
        self.assertIn("| merged | [them/m]", readme)

    def test_it_is_kept_as_merged_when_the_recheck_is_rate_limited(self):
        def limited(url, token):
            raise track.LookupFailed("HTTP 429", rate_limited=True)

        row, data, _, err = self._two_runs(limited)
        self.assertEqual({r["url"]: r["state"] for r in data["rows"]}.get(row["url"]), "merged")
        self.assertEqual(data["unverified_open_urls"], [], "a merged row is not an open one")
        self.assertIn("could not re-check 1 carried merged/closed", err)

    def test_one_404_keeps_it_unverified_and_a_second_drops_it(self):
        row, data, _, err = self._two_runs(lambda url, token: None)
        by_url = {r["url"]: r for r in data["rows"]}
        self.assertIn(row["url"], by_url, "a single 404 dropped the thread for good")
        self.assertIs(by_url[row["url"]]["gone_once"], True)
        self.assertIn("404/410", err)
        rc, _, data_text, _, _ = self._run(data["rows"], lookup=lambda url, token: None)
        self.assertEqual(rc, 0, rc)
        self.assertNotIn(row["url"], {r["url"] for r in json.loads(data_text)["rows"]})

    def test_an_answer_after_one_404_clears_the_mark(self):
        row = self._row(gone_once=True)
        rc, _, data_text, _, _ = self._run([row], lookup=lambda url, token: {"state": "open"})
        self.assertEqual(rc, 0, rc)
        (kept,) = json.loads(data_text)["rows"]
        self.assertNotIn("gone_once", kept)

    def test_a_reopened_thread_the_search_once_returned_closed_is_not_lost(self):
        # Stored closed (from the search, never carried), now missing from the search: it is
        # looked up, and a thread that was reopened comes back as open.
        asked = []

        def lookup(url, token):
            asked.append(url)
            return {"state": "open", "comments": 3}

        closed = self._row(state="closed")
        rc, err, data_text, _, _ = self._run([closed], lookup=lookup)
        self.assertEqual(rc, 0, rc)
        self.assertEqual(asked, [closed["url"]])
        (row,) = json.loads(data_text)["rows"]
        self.assertEqual((row["state"], row["comments"], row["carried"]), ("open", 3, True))

    def test_an_uncarried_closed_row_is_kept_when_the_lookup_has_no_answer(self):
        closed = self._row(state="closed", url="https://github.com/them/z/issues/8",
                           repo="them/z", number=8)

        def limited(url, token):
            raise track.LookupFailed("HTTP 429", rate_limited=True)

        rc, err, data_text, _, _ = self._run([closed], lookup=limited)
        self.assertEqual(rc, 0, rc)
        self.assertEqual([r["url"] for r in json.loads(data_text)["rows"]], [closed["url"]])
        self.assertIn("could not re-check 1", err)

    def test_history_idle_past_the_limit_is_retired_and_not_looked_up_again(self):
        asked = []
        old = "2026-01-01T00:00:00Z"

        def lookup(url, token):
            asked.append(url)
            return {"state": "merged", "updated_at": old}

        row = self._row(state="merged", kind="pr", url="https://github.com/them/r/pull/3",
                        repo="them/r", number=3)
        rc, _, data_text, _, _ = self._run([row], lookup=lookup)
        self.assertEqual(rc, 0, rc)
        (first,) = json.loads(data_text)["rows"]
        self.assertIs(first["retired"], True)
        rc, err, data_text, _, _ = self._run([first], lookup=lookup)
        self.assertEqual(rc, 0, rc)
        self.assertEqual(asked, [row["url"]], "a retired row was looked up again")
        (second,) = json.loads(data_text)["rows"]
        self.assertEqual((second["url"], second["state"]), (row["url"], "merged"))
        self.assertIn("retired", err)

    def test_recent_history_is_not_retired(self):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = self._row(state="closed")
        rc, _, data_text, _, _ = self._run(
            [row], lookup=lambda url, token: {"state": "closed", "updated_at": recent})
        self.assertEqual(rc, 0, rc)
        (kept,) = json.loads(data_text)["rows"]
        self.assertNotIn("retired", kept)

    def test_a_retired_row_the_search_returns_again_is_a_fresh_row(self):
        live = track.rows_from([item("them/a", 1)], "dhyabi2")
        live[0]["url"] = "https://github.com/them/a/issues/1"
        retired = self._row(state="closed", retired=True, carried=True)
        rc, _, data_text, _, _ = self._run([retired], live=live, lookup=lambda u, t: 1 / 0)
        self.assertEqual(rc, 0, rc)
        (row,) = json.loads(data_text)["rows"]
        self.assertNotIn("retired", row)

    def test_open_rows_get_the_lookup_budget_before_carried_history(self):
        asked = []

        def lookup(url, token):
            asked.append(url)
            raise track.LookupFailed("HTTP 429", rate_limited=True)

        history = self._row(state="merged", carried=True, url="https://github.com/them/h/pull/9",
                            repo="them/h", number=9)
        live_open = self._row()
        self._run([history, live_open], lookup=lookup)
        self.assertEqual(asked, [live_open["url"]])


class AMass404IsNotEvidence(unittest.TestCase):
    """Two consecutive 404s dropped a row with no cap. A run in which most lookups 404 at once (an
    outage, a proxy, a rate-limit page served as 404) would have dropped many live rows."""

    _run = ARebuildNeverLosesThePreviousTable._run
    _row = staticmethod(ARebuildNeverLosesThePreviousTable._row)
    PREV_README = ARebuildNeverLosesThePreviousTable.PREV_README

    def _rows(self, n, **over):
        return [self._row(repo=f"them/r{i}", number=i, url=f"https://github.com/them/r{i}/issues/{i}",
                          **over) for i in range(1, n + 1)]

    def test_a_run_where_most_lookups_404_drops_nothing(self):
        prev = self._rows(10, gone_once=True)
        rc, err, data_text, _, _ = self._run(prev, lookup=lambda url, token: None)
        self.assertEqual(rc, 0, rc)
        data = json.loads(data_text)
        self.assertEqual(sorted(r["url"] for r in data["rows"]), sorted(r["url"] for r in prev),
                         "a mass 404 dropped live rows")
        self.assertIn("unreliable", err)
        self.assertEqual(len(data["unverified_open_urls"]), 10)

    def test_a_mass_404_does_not_advance_the_gone_mark(self):
        prev = self._rows(10)
        rc, _, data_text, _, _ = self._run(prev, lookup=lambda url, token: None)
        self.assertEqual(rc, 0, rc)
        rows = json.loads(data_text)["rows"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all("gone_once" not in r for r in rows), "a mass 404 set gone_once")

    def test_a_few_404s_among_many_answers_still_drop_their_ghosts(self):
        prev = self._rows(40, gone_once=True)
        gone = {prev[0]["url"], prev[1]["url"], prev[2]["url"]}
        rc, _, data_text, _, _ = self._run(
            prev, lookup=lambda url, token: None if url in gone else {"state": "open"})
        self.assertEqual(rc, 0, rc)
        urls = {r["url"] for r in json.loads(data_text)["rows"]}
        self.assertEqual(len(urls), 37)
        self.assertFalse(gone & urls)


    def test_404s_among_widespread_failures_are_not_believed_over_two_runs(self):
        prev = self._rows(100)
        gone = {r["url"] for r in prev[:10]}

        def lookup(url, token):
            if url in gone:
                return None
            raise track.LookupFailed("HTTP 503")

        rows = prev
        for run in (1, 2):
            rc, err, data_text, _, _ = self._run(rows, lookup=lookup)
            self.assertEqual(rc, 0, rc)
            rows = json.loads(data_text)["rows"]
            self.assertEqual(len(rows), 100, f"run {run} dropped rows during an outage")
            self.assertTrue(all("gone_once" not in r for r in rows), f"run {run} set gone_once")

    def test_a_failed_lookup_between_two_404s_clears_the_mark(self):
        prev = [self._row(gone_once=True)]
        rc, _, data_text, _, _ = self._run(
            prev, lookup=lambda u, t: (_ for _ in ()).throw(track.LookupFailed("HTTP 503")))
        self.assertEqual(rc, 0, rc)
        (row,) = json.loads(data_text)["rows"]
        self.assertNotIn("gone_once", row, "a run with no 404 kept the 404 streak alive")
        rc, _, data_text, _, _ = self._run([row], lookup=lambda u, t: None)
        self.assertEqual(rc, 0, rc)
        (row,) = json.loads(data_text)["rows"]
        self.assertIs(row["gone_once"], True, "two non-consecutive 404s dropped the row")

    def test_an_outage_run_between_two_404s_clears_the_mark(self):
        prev = self._rows(10, gone_once=True)
        rc, _, data_text, _, _ = self._run(prev, lookup=lambda u, t: None)
        self.assertEqual(rc, 0, rc)
        rows = json.loads(data_text)["rows"]
        self.assertEqual(len(rows), 10)
        self.assertTrue(all("gone_once" not in r for r in rows))


class ARetiredRowIsRecheckedSlowly(unittest.TestCase):
    """A retired row was never looked up again, so a thread that came back (reopened, repository
    public again) stayed closed in the table for good."""

    _run = ARebuildNeverLosesThePreviousTable._run
    _row = staticmethod(ARebuildNeverLosesThePreviousTable._row)
    PREV_README = ARebuildNeverLosesThePreviousTable.PREV_README

    @staticmethod
    def _ago(days):
        return (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    def test_a_retired_row_checked_long_ago_is_looked_up_and_restored(self):
        retired = self._row(state="closed", retired=True, carried=True, checked_at=self._ago(10))
        rc, _, data_text, _, _ = self._run(
            [retired], lookup=lambda url, token: {"state": "open", "comments": 4})
        self.assertEqual(rc, 0, rc)
        (row,) = json.loads(data_text)["rows"]
        self.assertEqual((row["state"], row["comments"]), ("open", 4), "the reopened thread stayed closed")
        self.assertNotIn("retired", row)

    def test_a_retired_row_never_checked_is_due(self):
        retired = self._row(state="closed", retired=True, carried=True)
        rc, _, data_text, _, _ = self._run([retired], lookup=lambda url, token: {"state": "open"})
        self.assertEqual(rc, 0, rc)
        self.assertEqual(json.loads(data_text)["rows"][0]["state"], "open")

    def test_a_recently_checked_retired_row_is_not_looked_up(self):
        retired = self._row(state="closed", retired=True, carried=True, checked_at=self._ago(1))
        rc, _, data_text, _, _ = self._run([retired], lookup=lambda u, t: 1 / 0)
        self.assertEqual(rc, 0, rc)
        self.assertEqual(json.loads(data_text)["rows"][0]["state"], "closed")

    def test_rechecks_are_bounded_per_run_oldest_first(self):
        n = track.RECHECK_RETIRED_PER_RUN + 5
        prev = [self._row(state="closed", retired=True, repo=f"them/r{i}", number=i,
                          url=f"https://github.com/them/r{i}/issues/{i}", checked_at=self._ago(8 + i))
                for i in range(n)]
        asked = []

        def lookup(url, token):
            asked.append(url)
            return {"state": "closed", "updated_at": "2026-01-01T00:00:00Z"}

        rc, _, data_text, _, _ = self._run(prev, lookup=lookup)
        self.assertEqual(rc, 0, rc)
        self.assertEqual(len(asked), track.RECHECK_RETIRED_PER_RUN)
        self.assertNotIn(prev[0]["url"], asked, "the most recently checked row was rechecked first")
        self.assertIn(prev[-1]["url"], asked)
        rows = json.loads(data_text)["rows"]
        self.assertEqual(len(rows), n)
        rechecked = [r for r in rows if r["url"] in set(asked)]
        self.assertTrue(all(r.get("retired") is True and r.get("checked_at") for r in rechecked))


    def test_rows_whose_recheck_always_fails_do_not_starve_the_queue(self):
        n = track.RECHECK_RETIRED_PER_RUN
        blocked = [self._row(state="closed", retired=True, repo=f"them/b{i}", number=i,
                             url=f"https://github.com/them/b{i}/issues/{i}") for i in range(n)]
        old = self._row(state="closed", retired=True, repo="them/old", number=1,
                        url="https://github.com/them/old/issues/1", checked_at=self._ago(36))
        blocked_urls = {r["url"] for r in blocked}
        asked = []

        def lookup(url, token):
            asked.append(url)
            if url in blocked_urls:
                exc = track.LookupFailed("HTTP 403")
                exc.outage = False  # about that one repository, not an outage
                raise exc
            return {"state": "open"}

        rows = blocked + [old]
        for _ in (1, 2):
            rc, _, data_text, _, _ = self._run(rows, lookup=lookup)
            self.assertEqual(rc, 0, rc)
            rows = json.loads(data_text)["rows"]
        self.assertIn(old["url"], asked, "the old retired row was never rechecked")
        self.assertEqual({r["url"]: r["state"] for r in rows}[old["url"]], "open")
        self.assertTrue(all(r.get("checked_at") for r in rows if r["url"] in blocked_urls))

    def test_a_checked_at_in_the_future_is_due(self):
        retired = self._row(state="closed", retired=True, checked_at="2099-01-01T00:00:00Z")
        rc, _, data_text, _, _ = self._run([retired], lookup=lambda url, token: {"state": "open"})
        self.assertEqual(rc, 0, rc)
        self.assertEqual(json.loads(data_text)["rows"][0]["state"], "open")


class ARenamedRepositoryIsOneThread(unittest.TestCase):
    """data.json holds a thread under its repository's old name; the search returns it under the
    new one. Comparing raw URLs saw two threads, looked the old one up (GitHub redirects), and
    published the same thread twice."""

    _run = ARebuildNeverLosesThePreviousTable._run
    _row = staticmethod(ARebuildNeverLosesThePreviousTable._row)
    PREV_README = ARebuildNeverLosesThePreviousTable.PREV_README

    def test_the_lookup_follows_the_rename_and_the_duplicate_is_dropped(self):
        live = track.rows_from([item("new-owner/new-name", 4)], "dhyabi2")
        old = self._row(repo="old-owner/old-name", number=4,
                        url="https://github.com/old-owner/old-name/issues/4")
        answers = {"/repos/old-owner/old-name/issues/4": {
            "state": "open", "comments": 1,
            "html_url": "https://github.com/new-owner/new-name/issues/4"}}
        with patched(track.urllib.request, urlopen=OutreachTable._fake_github(answers)):
            rc, _, data_text, _, _ = self._run([old], live=live)
        self.assertEqual(rc, 0, rc)
        rows = json.loads(data_text)["rows"]
        self.assertEqual([r["url"] for r in rows], [live[0]["url"]], "the thread is listed twice")

    def test_a_carried_row_from_a_renamed_repo_is_published_under_its_new_name(self):
        old = self._row(repo="old-owner/old-name", number=4,
                        url="https://github.com/old-owner/old-name/issues/4")
        answers = {"/repos/old-owner/old-name/issues/4": {
            "state": "open", "html_url": "https://github.com/new-owner/new-name/issues/4"}}
        with patched(track.urllib.request, urlopen=OutreachTable._fake_github(answers)):
            rc, _, data_text, _, _ = self._run([old])
        self.assertEqual(rc, 0, rc)
        rows = json.loads(data_text)["rows"]
        self.assertEqual([(r["repo"], r["url"]) for r in rows],
                         [("new-owner/new-name", "https://github.com/new-owner/new-name/issues/4")])

    def test_owner_and_repo_case_and_issue_or_pull_name_one_thread(self):
        live = track.rows_from([item("Them/X", 3, pr=True)], "dhyabi2")
        live[0]["url"] = "https://github.com/Them/X/pull/3"
        old = self._row(repo="them/x", number=3, url="https://github.com/them/x/issues/3")
        asked = []

        def lookup(url, token):
            asked.append(url)
            return {"state": "open"}

        rc, _, data_text, _, _ = self._run([old], live=live, lookup=lookup)
        self.assertEqual(rc, 0, rc)
        self.assertEqual(len(json.loads(data_text)["rows"]), 1)
        self.assertEqual(asked, [], "the search already had this thread")


class ALookupThatBreaksMidAnswerIsNotAnAnswer(unittest.TestCase):
    """`http.client` errors are neither `URLError` nor `OSError`: a malformed thread URL
    (`InvalidURL`) or a body cut off mid-read (`IncompleteRead`) escaped `_lookup` and killed the
    whole rebuild instead of keeping that one row as unverified."""

    def _raising(self, exc, on_read=False):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

            def read(self, *a):
                raise exc

        def urlopen(req, timeout=None):
            if on_read:
                return Resp(b"")
            raise exc

        return urlopen

    def test_an_incomplete_read_raises_lookup_failed(self):
        import http.client
        with patched(track.urllib.request, urlopen=self._raising(http.client.IncompleteRead(b"{", 10), on_read=True)):
            with self.assertRaises(track.LookupFailed) as caught:
                track._lookup("https://github.com/them/x/issues/1", None)
        self.assertFalse(caught.exception.rate_limited)

    def test_an_invalid_url_raises_lookup_failed(self):
        import http.client
        with patched(track.urllib.request, urlopen=self._raising(http.client.InvalidURL("bad"))):
            with self.assertRaises(track.LookupFailed):
                track._lookup("https://github.com/them/x/issues/1", None)

    def test_a_plain_403_is_not_a_rate_limit_but_an_exhausted_quota_is(self):
        """A 403 for one thread (no access to that repository) must not stop every later lookup;
        GitHub marks a real rate limit with X-RateLimit-Remaining: 0 or Retry-After."""
        for headers, limited in (({}, False), ({"X-RateLimit-Remaining": "0"}, True),
                                 ({"Retry-After": "60"}, True)):
            def urlopen(req, timeout=None, headers=headers):
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", headers, None)
            with patched(track.urllib.request, urlopen=urlopen):
                with self.assertRaises(track.LookupFailed) as caught:
                    track._lookup("https://github.com/them/x/issues/1", None)
            self.assertEqual(caught.exception.rate_limited, limited, headers)


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