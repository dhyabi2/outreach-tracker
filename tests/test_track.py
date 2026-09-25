"""Laws for the outreach tracker. Each is one sentence plus an observable test."""
import json
import os
import sys
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


if __name__ == "__main__":
    unittest.main()