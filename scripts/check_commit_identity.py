#!/usr/bin/env python3
"""Check incoming identities and authenticate GitHub's normal merge metadata."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


COLLECTIVE = ("I am One", "weareone@oneunity.earth")
PLATFORM_COMMITTER = ("GitHub", "noreply@github.com")
WEB_FLOW_ID = 19864447
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class GateError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise GateError(message)


def commit_sha(value):
    require(isinstance(value, str) and SHA_PATTERN.fullmatch(value)
            and value != "0" * 40, "Missing or invalid commit SHA")
    return value


@dataclass(frozen=True)
class Commit:
    sha: str
    author: tuple
    committer: tuple
    parents: tuple

    @property
    def collective(self):
        return self.author == COLLECTIVE and self.committer == COLLECTIVE


class Repository:
    def __init__(self, path="."):
        self.path = path

    def git(self, *args):
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", *args], cwd=self.path,
                check=True, capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError(f"git {args[0]} failed; identity check is incomplete") from exc
        return result.stdout.rstrip("\n")

    def complete(self):
        require(self.git("rev-parse", "--is-shallow-repository") == "false",
                "Full git history is required; shallow identity checks are invalid")

    def between(self, base, head, first_parent=False):
        for sha in (base, head):
            require(self.git("cat-file", "-t", commit_sha(sha)) == "commit",
                    "Revision is not a commit")
        options = ["--first-parent"] if first_parent else []
        # Checked subprocess execution is deliberate: failed traversal must never
        # become an empty successful grep pipeline.
        shas = self.git("rev-list", "--reverse", *options, f"{base}..{head}").splitlines()
        require(bool(shas), "No incoming commits were enumerated")
        return [commit_sha(sha) for sha in shas]

    def commit(self, sha):
        fields = self.git(
            "show", "--no-patch", "--no-show-signature",
            "--format=%an%x00%ae%x00%cn%x00%ce%x00%P", commit_sha(sha),
        ).split("\0")
        require(len(fields) == 5, "Could not read commit identities")
        return Commit(sha, tuple(fields[:2]), tuple(fields[2:4]),
                      tuple(commit_sha(parent) for parent in fields[4].split()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class GitHubAPI:
    """Bounded, read-only public GitHub API requests; never follow token redirects."""

    def __init__(self, repository, token):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
                "Invalid repository name")
        self.root = f"https://api.github.com/repos/{repository}"
        self.headers = {"Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "one-link-identity-gate"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def __call__(self, path):
        request = urllib.request.Request(self.root + path, headers=self.headers, method="GET")
        try:
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=20) as response:
                require(response.status == 200, "GitHub evidence request did not succeed")
                content = response.read(4 * 1024 * 1024 + 1)
            require(len(content) <= 4 * 1024 * 1024, "GitHub evidence exceeded size limit")
            return json.loads(content)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            # Do not print request headers, tokens, response bodies, or identities.
            raise GateError("GitHub evidence unavailable or invalid; refusing integration exception") from exc


def verify_integration(commit, repository, repo, api):
    require(len(commit.parents) == 2 and commit.committer == PLATFORM_COMMITTER,
            f"Noncollective commit {commit.sha} is not a normal GitHub merge")
    remote = api(f"/commits/{commit.sha}?per_page=1")
    require(remote.get("sha") == commit.sha, "GitHub commit SHA mismatch")
    require(tuple(parent["sha"] for parent in remote.get("parents", [])) == commit.parents,
            "GitHub merge parents do not match local commit")
    metadata = remote.get("commit", {})
    require(tuple(metadata.get("author", {}).get(key) for key in ("name", "email")) == commit.author
            and tuple(metadata.get("committer", {}).get(key) for key in ("name", "email")) == commit.committer,
            "GitHub identity metadata does not match local commit")
    platform = remote.get("committer") or {}
    proof = metadata.get("verification", {})
    require(platform.get("login") == "web-flow" and platform.get("id") == WEB_FLOW_ID
            and proof.get("verified") is True and proof.get("reason") == "valid"
            and bool(proof.get("signature")) and bool(proof.get("payload")),
            "Integration lacks verified GitHub web-flow provenance")
    associated = api(f"/commits/{commit.sha}/pulls?per_page=100")
    require(isinstance(associated, list) and 0 < len(associated) < 100,
            "Missing or incomplete merged-PR association evidence")
    matches = [pr for pr in associated
               if pr.get("state") == "closed" and pr.get("merged_at")
               and pr.get("merge_commit_sha") == commit.sha
               and pr.get("base", {}).get("ref") == "master"
               and pr.get("base", {}).get("repo", {}).get("full_name") == repository
               and pr.get("head", {}).get("sha") == commit.parents[1]]
    require(len(matches) == 1, "Merge is not uniquely tied to a merged PR with the exact head parent")
    # GitHub can retain the PR's earlier base SHA when another PR lands first.
    # The actual first parent is already bound to the push history and signed
    # commit. The recorded PR base must be its ancestor, not necessarily equal.
    base = commit_sha(matches[0]["base"]["sha"])
    repo.git("merge-base", "--is-ancestor", base, commit.parents[0])
    number = matches[0].get("number")
    require(type(number) is int and number > 0, "Invalid merged PR number")
    return number


def validate(event_name, event, checkout_sha, repository, repo, api):
    require(event.get("repository", {}).get("full_name") == repository, "Event repository mismatch")
    repo.complete()
    require(repo.git("rev-parse", "HEAD") == commit_sha(checkout_sha), "Checkout does not match event SHA")
    if event_name == "pull_request":
        pr = event["pull_request"]
        require(pr["base"]["ref"] == "master"
                and pr["base"]["repo"]["full_name"] == repository, "Unexpected PR target")
        commits = repo.between(pr["base"]["sha"], pr["head"]["sha"])
        for sha in commits:
            require(repo.commit(sha).collective,
                    f"Incoming PR commit {sha} must have collective author and committer")
        return f"Validated collective author and committer on all {len(commits)} incoming PR commits"
    require(event_name == "push" and event.get("ref") == "refs/heads/master", "Unsupported identity-check event")
    require(all(event.get(key) is False for key in ("created", "deleted", "forced")),
            "Expected an ordinary update of the existing master branch")
    base, head = commit_sha(event["before"]), commit_sha(event["after"])
    require(head == checkout_sha, "Push head does not match checkout")
    repo.git("merge-base", "--is-ancestor", base, head)
    commits = repo.between(base, head)
    first_parent = repo.between(base, head, first_parent=True)
    previous = base
    for sha in first_parent:
        parents = repo.commit(sha).parents
        require(parents and parents[0] == previous, "Push does not extend the prior first-parent history")
        previous = sha
    require(previous == head, "Incomplete push first-parent history")
    integrations = 0
    for sha in commits:
        commit = repo.commit(sha)
        if commit.collective:
            continue
        # No exception is available to contributor commits on a PR's side branch.
        require(sha in first_parent, f"Incoming side-branch commit {sha} lacks collective identity")
        verify_integration(commit, repository, repo, api)
        integrations += 1
    return f"Validated {len(commits)} new commits, including {integrations} authenticated GitHub merge integrations"


def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        repository = os.environ["GITHUB_REPOSITORY"]
        print(validate(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"],
                       repository, Repository(), GitHubAPI(repository, os.environ.get("GITHUB_TOKEN", ""))))
    except (GateError, OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        message = str(exc) if isinstance(exc, GateError) else "Missing or malformed identity-check input"
        print(f"Identity check failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
