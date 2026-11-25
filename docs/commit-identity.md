# Commit identity and GitHub integration

Contributor commits use `I am One <weareone@oneunity.earth>` as both author and
committer. The CI identity gate checks **every commit added by a pull request**,
including contributor-made merge commits. It checks the actual base-to-head
range, not GitHub's temporary test merge. Full history is required. Missing
revisions, failed enumeration, an empty incoming range, or a shallow checkout
fail the gate.

Use GitHub's ordinary **Merge pull request** method after the pull request meets
the repository rules and its CI passes. This preserves the reviewed contributor
commits. GitHub creates a separate integration commit using platform metadata;
contributors cannot supply GitHub's committer identity. Rebase and squash are
not supported by this identity gate because they rewrite the reviewed commits.
Repository rules and merge settings remain unchanged.

On a normal push to `master`, CI independently checks all commits newly reachable
between the event's exact `before` and `after` SHAs. Contributor commits still
require both collective identities. A noncollective commit is accepted only
when all of these checks succeed:

- It is a two-parent integration commit on the new first-parent history.
- GitHub's read-only API returns the identical commit SHA, ordered parents, and
  author/committer metadata, with a valid verified signature and the GitHub
  `web-flow` committer account (ID `19864447`).
- GitHub associates it with exactly one closed, merged pull request into this
  repository's `master`; the PR's merge SHA and head SHA match the integration
  commit and its second parent exactly. The first parent extends the push's
  prior first-parent history. The PR's recorded base must be an ancestor of
  that first parent: GitHub can retain an earlier PR base when another PR lands
  before this one.

All newly introduced side-branch commits are checked separately, so an accepted
integration commit cannot hide a contributor's wrong author or committer.
API errors, missing evidence, forced/deleted/created branch events, and history
errors fail closed. The API token has only read access in the house-rules job;
the validator does not print tokens, API bodies, or rejected personal identities.

This is an explicit distinction between contribution identity and authenticated
platform integration metadata. It does not rewrite or retroactively recertify
existing repository history. Existing content, manifest, route, accessibility,
deployment dependencies, and branch protections remain in force.

Run the gate regressions with:

```sh
python3 -m unittest discover -s tests -p 'test_commit_identity.py' -v
```

GitHub references: [merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github),
[associated pull requests](https://docs.github.com/en/rest/commits/commits#list-pull-requests-associated-with-a-commit),
and [commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification).
