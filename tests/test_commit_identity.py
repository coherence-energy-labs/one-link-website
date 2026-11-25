"""Real git-history regressions with deterministic, mocked GitHub evidence."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_commit_identity as gate


REPOSITORY = "example/identity-fixture"
OUTSIDER = ("Fixture Contributor", "contributor@example.invalid")


class IdentityGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "repository"
        self.path.mkdir()
        self.git("init", "-q", "--initial-branch=master")
        self.tree = self.git("mktree", input="")
        self.sequence = 0
        self.repo = gate.Repository(self.path)
        self.base = self.commit()
        self.checkout(self.base)

    def git(self, *args, input=None, env=None):
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        environment.update(env or {})
        return subprocess.run(["git", *args], cwd=self.path, input=input, text=True,
                              capture_output=True, check=True, env=environment).stdout.strip()

    def commit(self, parents=(), author=gate.COLLECTIVE, committer=gate.COLLECTIVE):
        self.sequence += 1
        options = [option for parent in parents for option in ("-p", parent)]
        return self.git("commit-tree", self.tree, *options,
                        input=f"Identity fixture {self.sequence}\n",
                        env={"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
                             "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1],
                             "GIT_AUTHOR_DATE": "2026-11-25T00:00:00Z",
                             "GIT_COMMITTER_DATE": "2026-11-25T00:00:00Z"})

    def checkout(self, sha):
        self.git("update-ref", "HEAD", sha)

    def pr_event(self, head):
        return {"repository": {"full_name": REPOSITORY}, "pull_request": {
            "base": {"sha": self.base, "ref": "master", "repo": {"full_name": REPOSITORY}},
            "head": {"sha": head}}}

    def push_event(self, head):
        return {"repository": {"full_name": REPOSITORY}, "ref": "refs/heads/master",
                "before": self.base, "after": head, "created": False,
                "deleted": False, "forced": False}

    @staticmethod
    def no_api(path):
        raise AssertionError("No API request is expected for this check")

    def validate(self, event_name, event, head, api=None, repo=None):
        return gate.validate(event_name, event, head, REPOSITORY,
                             repo or self.repo, api or self.no_api)

    def merged(self, base=None, head=None):
        base = base or self.base
        head = head or self.commit((base,))
        merge = self.commit((base, head), author=OUTSIDER, committer=gate.PLATFORM_COMMITTER)
        self.checkout(merge)
        local = self.repo.commit(merge)
        remote = {"sha": merge, "parents": [{"sha": parent} for parent in local.parents],
                  "committer": {"login": "web-flow", "id": gate.WEB_FLOW_ID},
                  "commit": {"author": dict(zip(("name", "email"), local.author)),
                             "committer": dict(zip(("name", "email"), local.committer)),
                             "verification": {"verified": True, "reason": "valid",
                                              "signature": "mocked GitHub verification evidence",
                                              "payload": "mocked GitHub verification evidence"}}}
        pulls = [{"number": self.sequence, "state": "closed", "merged_at": "2026-11-25T00:00:00Z",
                  "merge_commit_sha": merge,
                  "base": {"sha": base, "ref": "master", "repo": {"full_name": REPOSITORY}},
                  "head": {"sha": head}}]
        evidence = {f"/commits/{merge}?per_page=1": remote,
                    f"/commits/{merge}/pulls?per_page=100": pulls}
        return merge, evidence

    def test_pr_accepts_all_collective_commits_and_excludes_synthetic_merge(self):
        first = self.commit((self.base,))
        head = self.commit((first,))
        synthetic, _ = self.merged(head=head)
        result = self.validate("pull_request", self.pr_event(head), synthetic)
        self.assertIn("all 2 incoming PR commits", result)

    def test_pr_checks_older_incoming_author_and_committer(self):
        for role in ("author", "committer"):
            with self.subTest(role=role):
                bad = self.commit((self.base,), **{role: OUTSIDER})
                head = self.commit((bad,))
                self.checkout(head)
                with self.assertRaisesRegex(gate.GateError, "collective author and committer"):
                    self.validate("pull_request", self.pr_event(head), head)

    def test_contributor_merge_commits_have_no_identity_exception(self):
        left = self.commit((self.base,))
        right = self.commit((self.base,))
        head = self.commit((left, right), committer=gate.PLATFORM_COMMITTER)
        self.checkout(head)
        with self.assertRaisesRegex(gate.GateError, "collective author and committer"):
            self.validate("pull_request", self.pr_event(head), head)

    def test_missing_base_or_head_fails(self):
        head = self.commit((self.base,))
        self.checkout(head)
        for side in ("base", "head"):
            with self.subTest(side=side):
                event = self.pr_event(head)
                event["pull_request"][side]["sha"] = "f" * 40
                with self.assertRaises(gate.GateError):
                    self.validate("pull_request", event, head)

    def test_empty_or_invalid_revision_range_fails(self):
        for value in (self.base, "0" * 40, "--all", "short"):
            with self.subTest(value=value):
                event = self.pr_event(value)
                with self.assertRaises(gate.GateError):
                    self.validate("pull_request", event, self.base)

    def test_git_enumeration_failure_cannot_become_success(self):
        head = self.commit((self.base,))
        self.checkout(head)
        original = self.repo.git

        def fail_rev_list(*args):
            if args[0] == "rev-list":
                raise gate.GateError("fixture traversal failed")
            return original(*args)

        with patch.object(self.repo, "git", side_effect=fail_rev_list):
            with self.assertRaisesRegex(gate.GateError, "traversal failed"):
                self.validate("pull_request", self.pr_event(head), head)

    def test_actual_shallow_checkout_is_rejected(self):
        head = self.commit((self.base,))
        self.checkout(head)
        shallow = Path(self.temporary.name) / "shallow"
        self.git("clone", "--quiet", "--depth=1", "--no-local", self.path.as_uri(), str(shallow))
        repo = gate.Repository(shallow)
        self.assertEqual(repo.git("rev-parse", "--is-shallow-repository"), "true")
        with self.assertRaisesRegex(gate.GateError, "shallow"):
            self.validate("pull_request", self.pr_event(head), head, repo=repo)

    def test_push_accepts_authenticated_normal_merge(self):
        merge, evidence = self.merged()
        result = self.validate("push", self.push_event(merge), merge, evidence.__getitem__)
        self.assertIn("2 new commits", result)
        self.assertIn("1 authenticated GitHub merge", result)

    def test_recorded_pr_base_may_precede_actual_merge_parent(self):
        # Observed on runtime PR #73: its recorded base preceded PR #72, which
        # landed first and became the actual first parent of PR #73's merge.
        advanced_base = self.commit((self.base,))
        reviewed_head = self.commit((self.base,))
        merge, evidence = self.merged(base=advanced_base, head=reviewed_head)
        pr = evidence[f"/commits/{merge}/pulls?per_page=100"][0]
        pr["base"]["sha"] = self.base
        event = self.push_event(merge)
        event["before"] = advanced_base
        result = self.validate("push", event, merge, evidence.__getitem__)
        self.assertIn("1 authenticated GitHub merge", result)
        pr["base"]["sha"] = self.commit()
        with self.assertRaises(gate.GateError):
            self.validate("push", event, merge, evidence.__getitem__)

    def test_push_checks_contributor_identity_despite_verified_merge(self):
        for role in ("author", "committer"):
            with self.subTest(role=role):
                bad = self.commit((self.base,), **{role: OUTSIDER})
                merge, evidence = self.merged(head=bad)
                with self.assertRaisesRegex(gate.GateError, "side-branch"):
                    self.validate("push", self.push_event(merge), merge, evidence.__getitem__)

    def test_push_does_not_accept_noncollective_linear_commit(self):
        head = self.commit((self.base,), author=OUTSIDER, committer=gate.PLATFORM_COMMITTER)
        self.checkout(head)
        with self.assertRaisesRegex(gate.GateError, "not a normal GitHub merge"):
            self.validate("push", self.push_event(head), head)

    def test_side_branch_merge_cannot_claim_platform_exception(self):
        left = self.commit((self.base,))
        right = self.commit((self.base,))
        side = self.commit((left, right), author=OUTSIDER, committer=gate.PLATFORM_COMMITTER)
        merge, evidence = self.merged(head=side)
        with self.assertRaisesRegex(gate.GateError, "side-branch"):
            self.validate("push", self.push_event(merge), merge, evidence.__getitem__)

    def test_multiple_integrations_require_independent_evidence(self):
        first, first_evidence = self.merged()
        second, second_evidence = self.merged(base=first)
        evidence = first_evidence | second_evidence
        result = self.validate("push", self.push_event(second), second, evidence.__getitem__)
        self.assertIn("2 authenticated GitHub merge integrations", result)
        del evidence[f"/commits/{first}/pulls?per_page=100"]
        with self.assertRaises(KeyError):
            self.validate("push", self.push_event(second), second, evidence.__getitem__)

    def test_forged_or_mismatched_platform_provenance_is_rejected(self):
        merge, original = self.merged()
        commit_path = f"/commits/{merge}?per_page=1"
        cases = [
            ("wrong-sha", lambda row: row.update(sha=self.base)),
            ("wrong-parents", lambda row: row["parents"].reverse()),
            ("wrong-account", lambda row: row["committer"].update(id=123)),
            ("wrong-login", lambda row: row["committer"].update(login="untrusted-fixture")),
            ("wrong-author", lambda row: row["commit"]["author"].update(name="different")),
            ("wrong-committer", lambda row: row["commit"]["committer"].update(email="different@example.invalid")),
            ("unverified", lambda row: row["commit"]["verification"].update(verified=False)),
            ("bad-reason", lambda row: row["commit"]["verification"].update(reason="invalid")),
            ("missing-signature", lambda row: row["commit"]["verification"].update(signature=None)),
            ("missing-payload", lambda row: row["commit"]["verification"].update(payload=None)),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                evidence = copy.deepcopy(original)
                mutate(evidence[commit_path])
                with self.assertRaises(gate.GateError):
                    self.validate("push", self.push_event(merge), merge, evidence.__getitem__)

    def test_synthetic_or_wrong_merged_pr_association_is_rejected(self):
        merge, original = self.merged()
        pulls_path = f"/commits/{merge}/pulls?per_page=100"
        cases = [
            ("open", lambda pr: pr.update(state="open")),
            ("unmerged", lambda pr: pr.update(merged_at=None)),
            ("wrong-merge", lambda pr: pr.update(merge_commit_sha=self.base)),
            ("wrong-base", lambda pr: pr["base"].update(sha="f" * 40)),
            ("wrong-head", lambda pr: pr["head"].update(sha="f" * 40)),
            ("wrong-target", lambda pr: pr["base"].update(ref="other")),
            ("wrong-repository", lambda pr: pr["base"]["repo"].update(full_name="other/repository")),
            ("missing-number", lambda pr: pr.pop("number")),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                evidence = copy.deepcopy(original)
                mutate(evidence[pulls_path][0])
                with self.assertRaises(gate.GateError):
                    self.validate("push", self.push_event(merge), merge, evidence.__getitem__)
        for count in (0, 2, 100):
            with self.subTest(association_count=count):
                evidence = copy.deepcopy(original)
                evidence[pulls_path] *= count
                with self.assertRaises(gate.GateError):
                    self.validate("push", self.push_event(merge), merge, evidence.__getitem__)

    def test_api_failure_stops_push_validation(self):
        merge, _ = self.merged()

        def failed_api(path):
            raise gate.GateError("GitHub evidence unavailable")

        with self.assertRaisesRegex(gate.GateError, "evidence unavailable"):
            self.validate("push", self.push_event(merge), merge, failed_api)

    def test_unexpected_push_event_and_checkout_are_rejected(self):
        merge, evidence = self.merged()
        for field, value in (("created", True), ("deleted", True), ("forced", True),
                             ("before", "0" * 40), ("ref", "refs/heads/other"),
                             ("after", self.base)):
            with self.subTest(field=field):
                event = self.push_event(merge)
                event[field] = value
                with self.assertRaises(gate.GateError):
                    self.validate("push", event, merge, evidence.__getitem__)
        with self.assertRaisesRegex(gate.GateError, "Checkout"):
            self.validate("push", self.push_event(merge), self.base, evidence.__getitem__)

    def test_non_ancestor_push_range_is_rejected(self):
        unrelated = self.commit()
        self.checkout(unrelated)
        with self.assertRaises(gate.GateError):
            self.validate("push", self.push_event(unrelated), unrelated)

    def test_cli_propagates_missing_revision_failure(self):
        event = self.pr_event("f" * 40)
        event_file = Path(self.temporary.name) / "event.json"
        event_file.write_text(json.dumps(event), encoding="utf-8")
        environment = dict(os.environ, GITHUB_EVENT_PATH=str(event_file),
                           GITHUB_EVENT_NAME="pull_request", GITHUB_SHA=self.base,
                           GITHUB_REPOSITORY=REPOSITORY, GITHUB_TOKEN="")
        result = subprocess.run([sys.executable, str(Path(gate.__file__))], cwd=self.path,
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("identity check is incomplete", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
