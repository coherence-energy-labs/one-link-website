# Reviewed release download promotion

The public Worker keeps the existing `auto-latest` download behavior while
`VERSIONED_RELEASE_TAG` is unset. This preparation does not change production
configuration, publish binaries, update R2, or edit signed static assets.

`src/release-catalog.js` currently contains no approved release entries. Setting a
version tag without a complete matching catalog returns HTTP 503. There is no
fallback to the rolling channel and no environment-provided catalog override.

## Artifact contract

The authoritative tagged release workflow publishes complete standalone ZIP
bundles for these desktop targets:

| Target | Required asset |
| --- | --- |
| Windows x86_64 | `one-link-windows-x86_64.zip` |
| Windows arm64 | `one-link-windows-arm64.zip` |
| macOS arm64 | `one-link-macos-arm64.zip` |
| Linux x86_64 | `one-link-linux-x86_64.zip` |
| Linux arm64 | `one-link-linux-arm64.zip` |

The catalog requires all five. Tagged default routes select ZIPs. Explicit EXE,
MSI, DMG, AppImage, and tar.gz desktop requests return an unavailable-format
response with the supported ZIP routes. Intel macOS remains unavailable; an
unknown User-Agent does not select a guessed desktop artifact.

The rolling publisher is a separate unsigned prerelease channel. Its manually
dispatched workflow is not a substitute for source review or the tagged release
signing and updater gates. Do not publish through it to work around a blocked
merge or release.

## Evidence required before activation

Use the actual approved runtime source commit and actual completed tagged
release. Do not enter a proposed tag, estimated byte size, synthetic hash, test
fixture, or unapproved candidate in the production catalog.

1. Confirm the runtime source change has its required independent approval and
   checks, is merged into the protected default branch, and is the exact commit
   resolved by the version tag. Record its full 40-character commit SHA and the
   source review URL.
2. Confirm the tagged `release.yml` workflow completed every required quality,
   bundle, signing, and actual wheel reproducibility gate for that commit. Record
   its GitHub Actions run URL. The separate rolling workflow is not accepted as
   the signer identity.
3. Download the five ZIPs, `SHA256SUMS`, `UPDATE_MANIFEST.json`, `sbom.cdx.json`,
   and every corresponding `.sigstore` bundle. Measure exact bytes and SHA-256
   for each file and each bundle. Verify each Sigstore signature using the exact
   certificate identity
   `https://github.com/coherence-energy-labs/one-link/.github/workflows/release.yml@refs/tags/<tag>`
   and issuer `https://token.actions.githubusercontent.com`. Check the trusted
   signature verification result, workflow evidence, and source relationship;
   merely finding a signature file is insufficient.
4. Cross-check every artifact digest and size against the authenticated release
   manifests. Confirm the update manifest binds the expected version, tag,
   source commit, platform, and asset. Compare the source commit with the
   approved source and the release workflow. Preserve the verifier output as
   review evidence. The Python wheel reproducibility gate does not establish
   byte reproducibility of the desktop bundles.
5. Add the measured records to `REVIEWED_RELEASE_CATALOG.releases[<tag>]` in a
   reviewable website change. Record the catalog review URL, source review URL,
   review timestamp in canonical UTC ISO format, and successful source and
   signature verification. Have the catalog reviewed before activation.

Each entry has exactly these fields:

| Field | Required content |
| --- | --- |
| `tag`, `source_commit` | Exact version tag and approved full source SHA |
| `review` | `status: approved`, `catalog_review_url`, `source_review_url`, `reviewed_at`, `signature_verification: passed`, `source_verification: passed` |
| `evidence` | `workflow_run_url`, exact `signer_identity`, `oidc_issuer`, `python_wheel_reproducibility: reviewed-verified`, and the `checksums`, `update_manifest`, `sbom` records |
| `artifacts` | Exactly the five target keys in the table above, each containing its measured artifact record |
| `source_archives` | Empty unless authenticated full-source release archives exist; optional `zip` and `tar_gz` records follow the source requirements below |

An artifact or evidence record has exactly `name`, `size_bytes`, `sha256`, and
`sigstore`. The `sigstore` object contains its own `size_bytes` and `sha256`.
Sizes are positive safe integers and hashes are lowercase 64-character SHA-256.
URLs are derived from the fixed repository, exact tag, and allowlisted filenames;
the catalog cannot supply arbitrary download URLs.

Catalog validation checks completeness and consistency of the recorded evidence.
It does not query live review state, rerun Sigstore, or download and hash binaries
on each request. Reviewers must establish those facts before approving the
catalog. The JSON route exposes the recorded source, exact bytes, reference hash,
signature bundle, and verification evidence with this scope. Redirect headers
are reference values from that catalog. A browser redirect does not verify
downloaded bytes. GitHub assets can still be replaced, so clients must verify
the signature and checksum before using the download.

## Full-source mirror

A Python sdist is not a full repository source archive. Do not relabel or repack
it as one. If the tagged release does not publish authenticated full-source
archives, keep `source_archives` empty. The versioned `/download/source` route
then returns `source-not-published` without reading `latest/` or any R2 object.

To enable the mirror, first extend the authoritative runtime release to produce
the complete approved source archive(s), seal them before checksums and signing,
publish `one-link-source.zip` and/or `one-link-source.tar.gz` with their Sigstore
bundles, and independently verify those exact bytes. This release preparation
does not make that runtime change or publish an archive.

Record each available archive under `source_archives.zip` or
`source_archives.tar_gz`. Mirror those exact verified bytes to the existing
`RELEASES` R2 binding using the version-specific key
`releases/<tag>/<asset-name>`. Supply the SHA-256 as an R2 upload checksum so R2
checks the bytes during upload, and set custom metadata `source_commit` and
`release_tag` to the exact catalog values. Read the stored object back and verify
its bytes and metadata before promotion. Do not overwrite `latest/` or use
custom hash metadata as a replacement for the native R2 checksum.

The Worker requires matching native SHA-256, byte size, source commit, and tag
before serving a versioned source mirror. A missing store, object, catalog
entry, or mismatch fails closed. The route does not buffer and rehash a large
archive for each visitor; it checks the native upload checksum against the
reviewed identity. The signed GitHub archive remains the independent reference.

## Activation and validation

Keep `VERSIONED_RELEASE_TAG` unset until the required release and reviewed
catalog exist. Then prepare a website change that selects that exact tag and
updates the download and verification copy for ZIP bundles and the measured
release evidence. Review every localized surface that names installer formats.
Regenerate signed static assets only with the established manifest signing key
and verify the exact generated bundle. Do not replace or export the key.

Run the targeted route contracts:

```sh
node --test tests/release-truth.test.mjs tests/download-human-urls.test.mjs tests/versioned-downloads.test.mjs
```

The existing CI release-truth job uses `node --test tests/*.test.mjs`, so it picks
up the new catalog and routing tests automatically. Complete the other existing
website gates, including generated-surface and signed-manifest checks, before
the approved deployment. After deployment, probe all five default and explicit
ZIP routes, unsupported formats, JSON evidence, unknown clients, source mirror
availability, and redirects from the alternate host. Compare the public
responses with the reviewed tag, source commit, exact sizes, and hashes.

An HTTP 503 after setting the tag is a failed activation, not permission to
silently serve a different release. Roll back the approved deployment/config
explicitly if necessary and report the actual active channel.
