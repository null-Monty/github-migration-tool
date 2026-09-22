import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from gh_migrate import cli
from gh_migrate.advice import Context, advise
from gh_migrate.github import ApiError, _error_message
from gh_migrate.migrate import (
    ALREADY, ALREADY_TAKEN, BLOCKED, FAILED, NAME_TAKEN, NO_ADMIN, NOT_FOUND, PENDING, READY, REFUSED,
    UNVERIFIED, VERIFIED, RepoTask, preflight, transfer_all,
)
from gh_migrate.ui import Console


class FakeGitHub:
    """In-memory stand-in for the parts of the GitHub API the tool uses.

    A transfer completes after `polls_until_moved` reads of the source path (None: nobody ever
    accepts it). Once moved, the old path redirects to the new one, as on GitHub.
    `target_can_see` False models a private repo that lands in an account the token can't read.
    """

    def __init__(self, polls_until_moved=1, target_can_see=True, me="old"):
        self.me = me
        self.users = {"old": "User", "new": "Organization"}
        self.repos = {}
        self.redirects = {}
        self.pending = {}
        self.moved_ids = set()
        self.transfers = []
        self.polls_until_moved = polls_until_moved
        self.target_can_see = target_can_see

    def add_repo(self, owner, name, *, admin=True, private=False):
        self.repos[(owner.lower(), name.lower())] = {
            "id": len(self.repos) + 1, "name": name, "full_name": f"{owner}/{name}",
            "private": private, "owner": {"login": owner}, "permissions": {"admin": admin},
        }

    def get(self, path):
        parts = path.strip("/").split("/")
        if parts == ["user"]:
            return {"login": self.me}
        if parts[0] == "users":
            return {"login": parts[1], "type": self.users[parts[1]]} if parts[1] in self.users else None
        _, owner, name = parts
        key = (owner.lower(), name.lower())
        if key in self.pending:
            self._tick(key)
        repo = self.repos.get(self.redirects.get(key, key))
        if repo is None or (repo["id"] in self.moved_ids and not self.target_can_see):
            return None
        return dict(repo)

    def post(self, path, body):
        _, owner, name, _ = path.strip("/").split("/")
        if name.startswith("boom"):
            raise ApiError(422, "Validation Failed")
        if name.startswith("taken"):
            raise ApiError(422, "Validation Failed: Repository has already been taken")
        self.transfers.append((owner, name, body["new_owner"]))
        self.pending[(owner.lower(), name.lower())] = {
            "owner": body["new_owner"], "polls_left": self.polls_until_moved,
        }
        return {}

    def _tick(self, key):
        request = self.pending[key]
        if request["polls_left"] is None:
            return
        request["polls_left"] -= 1
        if request["polls_left"] < 0:
            new_owner = self.pending.pop(key)["owner"]
            repo = self.repos.pop(key)
            repo["owner"] = {"login": new_owner}
            repo["full_name"] = f"{new_owner}/{repo['name']}"
            new_key = (new_owner.lower(), key[1])
            self.repos[new_key] = repo
            self.redirects[key] = new_key
            self.moved_ids.add(repo["id"])


def run_transfer(gh, tasks, wait=30):
    clock = iter(range(0, 10_000, 5)).__next__
    transfer_all(gh, "old", "new", tasks, wait=wait, interval=0, sleep=lambda _: None, clock=clock)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()

    def test_ready(self):
        self.gh.add_repo("old", "app", private=True)
        task = preflight(self.gh, "old", "new", "APP")
        self.assertEqual((task.name, task.status), ("app", READY))

    def test_missing_repo_is_blocked(self):
        self.assertEqual(preflight(self.gh, "old", "new", "nope").status, BLOCKED)

    def test_needs_admin(self):
        self.gh.add_repo("old", "app", admin=False)
        task = preflight(self.gh, "old", "new", "app")
        self.assertEqual(task.status, BLOCKED)
        self.assertIn("admin", task.detail)

    def test_name_clash_at_target(self):
        self.gh.add_repo("old", "app")
        self.gh.add_repo("new", "app")
        task = preflight(self.gh, "old", "new", "app")
        self.assertEqual(task.status, BLOCKED)
        self.assertIn("name taken", task.detail)

    def test_already_at_target(self):
        self.gh.add_repo("new", "app")
        self.gh.redirects[("old", "app")] = ("new", "app")  # GitHub redirects the old path
        self.assertEqual(preflight(self.gh, "old", "new", "app").status, ALREADY)


class TransferTests(unittest.TestCase):
    def test_transfer_is_verified_at_target(self):
        gh = FakeGitHub()
        gh.add_repo("old", "app")
        task = preflight(gh, "old", "new", "app")
        run_transfer(gh, [task])
        self.assertEqual(task.status, VERIFIED)
        self.assertEqual(gh.transfers, [("old", "app", "new")])
        self.assertEqual(gh.get("/repos/new/app")["id"], task.repo_id)
        self.assertEqual(gh.get("/repos/old/app")["owner"]["login"], "new")  # old path redirects

    def test_unaccepted_transfer_stays_pending(self):
        gh = FakeGitHub(polls_until_moved=None)
        gh.add_repo("old", "app")
        task = preflight(gh, "old", "new", "app")
        run_transfer(gh, [task], wait=20)
        self.assertEqual(task.status, PENDING)

    def test_repo_invisible_after_move_is_unverified(self):
        gh = FakeGitHub(target_can_see=False)
        gh.add_repo("old", "app", private=True)
        task = preflight(gh, "old", "new", "app")
        run_transfer(gh, [task])
        self.assertEqual(task.status, UNVERIFIED)

    def test_one_failure_does_not_stop_the_rest(self):
        gh = FakeGitHub()
        gh.add_repo("old", "boom")
        gh.add_repo("old", "app")
        tasks = [preflight(gh, "old", "new", n) for n in ("boom", "app")]
        run_transfer(gh, tasks)
        self.assertEqual([t.status for t in tasks], [FAILED, VERIFIED])
        self.assertEqual(tasks[0].detail, "Validation Failed")

    def test_already_taken_error_gets_its_own_advice(self):
        gh = FakeGitHub()
        gh.add_repo("old", "taken-repo")
        gh.add_repo("old", "boom-repo")
        tasks = [preflight(gh, "old", "new", n) for n in ("taken-repo", "boom-repo")]
        run_transfer(gh, tasks)
        self.assertEqual([t.cause for t in tasks], [ALREADY_TAKEN, REFUSED])
        ctx = Context(me="old", source="old", target="new", source_is_org=False, target_is_org=False)
        headings = [item.heading for item in advise(tasks, ctx)]
        self.assertEqual(headings, [
            "Already pending, or name taken: taken-repo", "GitHub refused: boom-repo",
        ])

    def test_blocked_and_already_done_tasks_are_never_transferred(self):
        gh = FakeGitHub()
        gh.add_repo("new", "done")
        tasks = [preflight(gh, "old", "new", "done"), preflight(gh, "old", "new", "missing")]
        run_transfer(gh, tasks)
        self.assertEqual(gh.transfers, [])


class CliTests(unittest.TestCase):
    def run_cli(self, gh, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--source", "old", "--target", "new", *argv], client=gh)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_changes_nothing(self):
        gh = FakeGitHub()
        gh.add_repo("old", "app")
        code, out, _ = self.run_cli(gh, "app", "--dry-run")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(gh.transfers, [])
        self.assertIn("Dry run", out)

    def test_any_blocked_repo_aborts_before_transferring_anything(self):
        gh = FakeGitHub()
        gh.add_repo("old", "app")
        code, out, _ = self.run_cli(gh, "app", "missing", "--yes")
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(gh.transfers, [])
        self.assertIn("Nothing was changed", out)

    def test_full_run_from_repos_file(self):
        gh = FakeGitHub(polls_until_moved=0)
        for name in ("a", "b", "c"):
            gh.add_repo("old", name)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "repos.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# my repos\na\nold/b  # with owner\n\nc\na\n")
            code, out, _ = self.run_cli(gh, "-f", path, "--yes", "--wait", "0")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(sorted(t[1] for t in gh.transfers), ["a", "b", "c"])
        self.assertIn("3 done", out)
        self.assertNotIn("pending", out)

    def test_pending_exit_code(self):
        gh = FakeGitHub(polls_until_moved=None)
        gh.add_repo("old", "app")
        code, out, _ = self.run_cli(gh, "app", "--yes", "--wait", "0")
        self.assertEqual(code, cli.EXIT_PENDING)
        self.assertIn("1 pending", out)

    def test_declined_confirmation_transfers_nothing(self):
        gh = FakeGitHub()
        gh.add_repo("old", "app")
        with mock.patch("builtins.input", return_value="nope"):
            code, out, _ = self.run_cli(gh, "app")
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(gh.transfers, [])
        self.assertIn("Aborted", out)

    def test_wrong_account_explains_how_to_sign_in_as_the_source(self):
        gh = FakeGitHub(me="someone-else")
        gh.add_repo("old", "app", admin=False, private=True)
        code, out, _ = self.run_cli(gh, "app", "missing")
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("signed in as someone-else", out)
        self.assertIn("Sign in as old, not someone-else", out)
        self.assertIn("gh auth login", out)
        self.assertIn("GH_MIGRATE_TOKEN", out)

    def test_right_account_without_admin_gets_token_advice(self):
        gh = FakeGitHub()
        gh.add_repo("old", "app", admin=False)
        _, out, _ = self.run_cli(gh, "app")
        self.assertIn("Use a token with admin access: app", out)
        self.assertIn("Administration: Read and write", out)
        self.assertNotIn("Sign in as", out)

    def test_org_source_without_admin_asks_for_owner(self):
        gh = FakeGitHub(me="alice")
        gh.users["old"] = "Organization"
        gh.add_repo("old", "app", admin=False)
        _, out, _ = self.run_cli(gh, "app")
        self.assertIn("Ask an owner of old for admin access: app", out)
        self.assertNotIn("Sign in as", out)

    def test_output_is_plain_ascii_when_not_a_terminal(self):
        gh = FakeGitHub(me="someone-else")
        gh.add_repo("old", "app", admin=False)
        _, out, _ = self.run_cli(gh, "app")
        self.assertNotIn("\033", out)
        out.encode("ascii")

    def test_repo_from_another_owner_is_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--source", "old", "--target", "new", "someone/app"], client=FakeGitHub())


if __name__ == "__main__":
    unittest.main()


class AdviceTests(unittest.TestCase):
    def ctx(self, **overrides):
        values = dict(me="old", source="old", target="new", source_is_org=False, target_is_org=True)
        return Context(**{**values, **overrides})

    def test_name_taken_and_not_found_each_get_their_own_advice(self):
        tasks = [
            RepoTask("a", BLOCKED, cause=NAME_TAKEN),
            RepoTask("b", BLOCKED, cause=NOT_FOUND),
            RepoTask("c", READY),
        ]
        headings = [item.heading for item in advise(tasks, self.ctx())]
        self.assertEqual(headings, ["Not found: b", "new already has: a"])

    def test_token_from_environment_says_to_replace_it(self):
        tasks = [RepoTask("a", BLOCKED, cause=NO_ADMIN)]
        item = advise(tasks, self.ctx(me="other", token_origin="$GITHUB_TOKEN"))[0]
        text = " ".join(item.body)
        self.assertIn("GITHUB_TOKEN variable", text)
        self.assertNotIn("gh auth login", text)

    def test_windows_gets_powershell_syntax(self):
        tasks = [RepoTask("a", BLOCKED, cause=NO_ADMIN)]
        for windows, expected in ((True, "$env:GH_MIGRATE_TOKEN"), (False, "export GH_MIGRATE_TOKEN")):
            item = advise(tasks, self.ctx(me="other", windows=windows))[0]
            self.assertIn(expected, " ".join(item.body))


class ConsoleTests(unittest.TestCase):
    def test_paint_only_emits_escape_codes_when_color_is_on(self):
        self.assertEqual(Console(io.StringIO(), color=False).paint("x", "red"), "x")
        self.assertEqual(Console(io.StringIO(), color=True).paint("x", "red", "bold"), "\033[31;1mx\033[0m")


class ErrorMessageTests(unittest.TestCase):
    def message(self, body):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        return _error_message(urllib.error.HTTPError("u", 422, "Unprocessable", {}, io.BytesIO(payload)))

    def test_validation_errors_are_included(self):
        body = {"message": "Validation Failed", "errors": [
            {"resource": "Repository", "code": "custom", "message": "name already exists on this account"},
            {"resource": "Repository", "field": "new_owner", "code": "invalid"},
            "plain string",
        ]}
        self.assertEqual(
            self.message(body),
            "Validation Failed: name already exists on this account; Repository new_owner invalid; plain string",
        )

    def test_message_only(self):
        self.assertEqual(self.message({"message": "Not Found"}), "Not Found")

    def test_non_json_body_is_returned_as_is(self):
        self.assertEqual(self.message(b"<html>bad gateway</html>"), "<html>bad gateway</html>")
