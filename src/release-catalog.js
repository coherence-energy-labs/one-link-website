// This reviewed source file is the promotion boundary. Environment variables
// select an entry; they cannot supply or replace release evidence. There are no
// approved entries yet. See docs/release-promotion.md before adding one.
export const REVIEWED_RELEASE_CATALOG = Object.freeze({
  schema_version: 1,
  releases: Object.freeze({}),
});

export const GITHUB_REPO = "https://github.com/coherence-energy-labs/one-link";
export const VERSIONED_RELEASE_TAG_RE = /^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z](?:[0-9A-Za-z.-]*[0-9A-Za-z])?)?$/;
export const DESKTOP_ARTIFACTS = Object.freeze({
  "windows-x86_64": "one-link-windows-x86_64.zip",
  "windows-arm64": "one-link-windows-arm64.zip",
  "macos-arm64": "one-link-macos-arm64.zip",
  "linux-x86_64": "one-link-linux-x86_64.zip",
  "linux-arm64": "one-link-linux-arm64.zip",
});

const SHA256_RE = /^[a-f0-9]{64}$/;
const COMMIT_RE = /^[a-f0-9]{40}$/;
const SOURCE_NAMES = Object.freeze({
  zip: "one-link-source.zip",
  tar_gz: "one-link-source.tar.gz",
});
const RECORD_NAMES = Object.freeze({
  checksums: "SHA256SUMS",
  update_manifest: "UPDATE_MANIFEST.json",
  sbom: "sbom.cdx.json",
});
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exactKeys = (value, keys) => isRecord(value)
  && Object.keys(value).length === keys.length && keys.every(key => hasOwn(value, key));
const validIdentity = value => isRecord(value) && Number.isSafeInteger(value.size_bytes)
  && value.size_bytes > 0 && typeof value.sha256 === "string" && SHA256_RE.test(value.sha256);
const validArtifact = (value, name) => exactKeys(value, ["name", "size_bytes", "sha256", "sigstore"])
  && value.name === name && validIdentity(value)
  && exactKeys(value.sigstore, ["size_bytes", "sha256"]) && validIdentity(value.sigstore);

function validReview(review) {
  if (!exactKeys(review, ["status", "catalog_review_url", "source_review_url", "reviewed_at", "signature_verification", "source_verification"])) return false;
  if (review.status !== "approved" || review.signature_verification !== "passed"
      || review.source_verification !== "passed") return false;
  if (!/^https:\/\/github\.com\/coherence-energy-labs\/one-link-website\/pull\/[1-9]\d*$/.test(review.catalog_review_url)
      || !/^https:\/\/github\.com\/coherence-energy-labs\/one-link\/pull\/[1-9]\d*$/.test(review.source_review_url)) return false;
  const timestamp = Date.parse(review.reviewed_at);
  return typeof review.reviewed_at === "string" && Number.isFinite(timestamp)
    && new Date(timestamp).toISOString() === review.reviewed_at && timestamp <= Date.now();
}

// This validates the recorded evidence contract. Signature, source approval,
// artifact bytes, and workflow results must be independently checked during
// catalog review. A Worker response is not fresh cryptographic verification.
export function validateReleaseCatalog(catalog, tag) {
  const fail = error => ({ ok: false, error });
  if (typeof tag !== "string" || !VERSIONED_RELEASE_TAG_RE.test(tag)) return fail("invalid versioned release tag");
  if (!exactKeys(catalog, ["schema_version", "releases"]) || catalog.schema_version !== 1
      || !isRecord(catalog.releases)) return fail("invalid reviewed release catalog");
  if (!hasOwn(catalog.releases, tag)) return fail("no reviewed artifact catalog exists for the configured release");
  const entry = catalog.releases[tag];
  if (!exactKeys(entry, ["tag", "source_commit", "review", "evidence", "artifacts", "source_archives"])
      || entry.tag !== tag || typeof entry.source_commit !== "string" || !COMMIT_RE.test(entry.source_commit)) return fail("release source identity is invalid");
  if (!validReview(entry.review)) return fail("release evidence has not passed catalog and source review");
  const evidence = entry.evidence;
  if (!exactKeys(evidence, ["workflow_run_url", "signer_identity", "oidc_issuer", "python_wheel_reproducibility", ...Object.keys(RECORD_NAMES)])
      || !/^https:\/\/github\.com\/coherence-energy-labs\/one-link\/actions\/runs\/[1-9]\d*$/.test(evidence.workflow_run_url)
      || evidence.signer_identity !== `${GITHUB_REPO}/.github/workflows/release.yml@refs/tags/${tag}`
      || evidence.oidc_issuer !== "https://token.actions.githubusercontent.com"
      || evidence.python_wheel_reproducibility !== "reviewed-verified") return fail("release workflow evidence is incomplete or mismatched");
  for (const [key, name] of Object.entries(RECORD_NAMES)) {
    if (!validArtifact(evidence[key], name)) return fail(`invalid ${key} evidence identity`);
  }
  if (!exactKeys(entry.artifacts, Object.keys(DESKTOP_ARTIFACTS))) return fail("the complete five-platform ZIP catalog is required");
  for (const [platform, name] of Object.entries(DESKTOP_ARTIFACTS)) {
    if (!validArtifact(entry.artifacts[platform], name)) return fail(`invalid artifact identity for ${platform}`);
  }
  if (!isRecord(entry.source_archives)
      || Object.keys(entry.source_archives).some(format => !hasOwn(SOURCE_NAMES, format))) return fail("invalid full-source archive catalog");
  for (const [format, artifact] of Object.entries(entry.source_archives)) {
    if (!validArtifact(artifact, SOURCE_NAMES[format])) return fail(`invalid full-source archive identity for ${format}`);
  }
  return { ok: true, entry };
}

export function releaseArtifactEvidence(entry, artifact) {
  const base = `${GITHUB_REPO}/releases/download/${entry.tag}`;
  const describe = record => ({
    name: record.name,
    url: `${base}/${record.name}`,
    size_bytes: record.size_bytes,
    sha256: record.sha256,
    sigstore: {
      url: `${base}/${record.name}.sigstore`,
      size_bytes: record.sigstore.size_bytes,
      sha256: record.sigstore.sha256,
    },
  });
  return {
    ...describe(artifact),
    source: {
      repository: GITHUB_REPO,
      commit: entry.source_commit,
      url: `${GITHUB_REPO}/commit/${entry.source_commit}`,
    },
    review: { ...entry.review },
    evidence: {
      workflow_run_url: entry.evidence.workflow_run_url,
      signer_identity: entry.evidence.signer_identity,
      oidc_issuer: entry.evidence.oidc_issuer,
      python_wheel_reproducibility: entry.evidence.python_wheel_reproducibility,
      ...Object.fromEntries(Object.keys(RECORD_NAMES).map(key => [key, describe(entry.evidence[key])])),
    },
  };
}

// R2 checks the supplied SHA-256 during upload. Custom metadata alone is not
// sufficient: the returned object's native checksum and size must match the
// reviewed release bytes before the Worker can serve the source mirror.
export function matchesReviewedSourceObject(object, entry, artifact) {
  if (!object || object.size !== artifact.size_bytes
      || object.customMetadata?.source_commit !== entry.source_commit
      || object.customMetadata?.release_tag !== entry.tag) return false;
  const checksum = object.checksums?.sha256;
  let bytes;
  if (checksum instanceof ArrayBuffer) bytes = new Uint8Array(checksum);
  else if (ArrayBuffer.isView(checksum)) bytes = new Uint8Array(checksum.buffer, checksum.byteOffset, checksum.byteLength);
  else return false;
  if (bytes.byteLength !== 32) return false;
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("") === artifact.sha256;
}
