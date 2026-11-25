import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';

import worker, { download } from '../src/worker.js';
import {
  REVIEWED_RELEASE_CATALOG, DESKTOP_ARTIFACTS,
  validateReleaseCatalog,
} from '../src/release-catalog.js';

// Synthetic contract fixtures only. These are not published release evidence
// and never enter the production catalog or any deployment configuration.
const TAG = 'v0.0.0-test.1';
const COMMIT = '1234567890abcdef1234567890abcdef12345678';
const REPO = 'https://github.com/coherence-energy-labs/one-link';
const BASE = `${REPO}/releases/download/${TAG}`;
const SOURCE_BYTES = new TextEncoder().encode('synthetic full-source fixture');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const identity = bytes => ({ size_bytes: bytes.length, sha256: digest(bytes) });
const artifact = (name, bytes = Buffer.from(`synthetic ${name}`)) => ({
  name, ...identity(bytes), sigstore: identity(Buffer.from(`synthetic bundle ${name}`)),
});

function fixture({ source = false } = {}) {
  return {
    schema_version: 1,
    releases: {
      [TAG]: {
        tag: TAG,
        source_commit: COMMIT,
        review: {
          status: 'approved',
          catalog_review_url: 'https://github.com/coherence-energy-labs/one-link-website/pull/999999',
          source_review_url: `${REPO}/pull/999999`,
          reviewed_at: '2026-01-01T00:00:00.000Z',
          signature_verification: 'passed',
          source_verification: 'passed',
        },
        evidence: {
          workflow_run_url: `${REPO}/actions/runs/999999`,
          signer_identity: `${REPO}/.github/workflows/release.yml@refs/tags/${TAG}`,
          oidc_issuer: 'https://token.actions.githubusercontent.com',
          python_wheel_reproducibility: 'reviewed-verified',
          checksums: artifact('SHA256SUMS'),
          update_manifest: artifact('UPDATE_MANIFEST.json'),
          sbom: artifact('sbom.cdx.json'),
        },
        artifacts: Object.fromEntries(Object.entries(DESKTOP_ARTIFACTS).map(([platform, name]) => [platform, artifact(name)])),
        source_archives: source ? {
          zip: artifact('one-link-source.zip', SOURCE_BYTES),
          tar_gz: artifact('one-link-source.tar.gz', SOURCE_BYTES),
        } : {},
      },
    },
  };
}

function route(spec, { catalog = fixture(), env = {}, headers = {}, method = 'GET' } = {}) {
  return download(
    { VERSIONED_RELEASE_TAG: TAG, ...env }, spec,
    new Request(`https://weareone-link.org/download/${spec}`, { method, headers }), catalog,
  );
}

test('the production catalog has no unreviewed release activation', async () => {
  assert.equal(validateReleaseCatalog(REVIEWED_RELEASE_CATALOG, TAG).ok, false);
  const response = await worker.fetch(new Request('https://weareone-link.org/download/windows'), {
    VERSIONED_RELEASE_TAG: TAG,
    // Runtime bindings cannot substitute a catalog, even when well formed.
    REVIEWED_RELEASE_CATALOG: fixture(),
  }, {});
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('Location'), null);
});

test('a reviewed catalog routes all five default desktop downloads to complete ZIPs', async () => {
  const catalog = fixture();
  for (const [platform, name] of Object.entries(DESKTOP_ARTIFACTS)) {
    for (const spec of [platform, `${platform}-zip`]) {
      const response = await route(spec, { catalog, headers: { Accept: 'application/json' } });
      assert.equal(response.status, 200, spec);
      const body = await response.json();
      const record = catalog.releases[TAG].artifacts[platform];
      assert.equal(body.url, `${BASE}/${name}`);
      assert.equal(body.format, 'zip');
      assert.equal(body.artifact.name, name);
      assert.equal(body.artifact.size_bytes, record.size_bytes);
      assert.equal(body.artifact.sha256, record.sha256);
      assert.equal(body.artifact.source.commit, COMMIT);
      assert.equal(body.artifact.source.url, `${REPO}/commit/${COMMIT}`);
      assert.deepEqual(body.artifact.sigstore, { ...record.sigstore, url: `${BASE}/${name}.sigstore` });
      assert.equal(body.artifact.evidence.checksums.url, `${BASE}/SHA256SUMS`);
      assert.equal(body.artifact.evidence.update_manifest.url, `${BASE}/UPDATE_MANIFEST.json`);
      assert.equal(body.artifact.evidence.sbom.url, `${BASE}/sbom.cdx.json`);
      assert.equal(body.release.mutable, true);
      assert.equal(body.release.immutability, 'not-enforced');
      assert.equal(body.integrity.signature, 'reviewed-sigstore');
      assert.equal(body.integrity.attestation, 'not-verified');
      assert.equal(body.integrity.reproducible_build, 'not-verified');
      assert.match(body.note, /does not.*prove desktop reproducibility/);
    }
  }
});

test('browser and automatic redirects carry the reviewed reference hash and exact source', async () => {
  for (const [spec, ua, platform] of [
    ['windows-x64', '', 'windows-x86_64'],
    ['linux-arm/', '', 'linux-arm64'],
    ['auto', 'Windows NT 10.0; Win64; x64', 'windows-x86_64'],
    ['auto', 'Linux aarch64', 'linux-arm64'],
  ]) {
    const catalog = fixture();
    const response = await route(spec, { catalog, headers: { Accept: 'text/html', 'User-Agent': ua } });
    assert.equal(response.status, 302, spec);
    const record = catalog.releases[TAG].artifacts[platform];
    assert.equal(response.headers.get('Location'), `${BASE}/${record.name}`);
    assert.equal(response.headers.get('X-Artifact-SHA256'), record.sha256);
    assert.equal(response.headers.get('X-One-Link-Artifact-Size'), String(record.size_bytes));
    assert.equal(response.headers.get('X-One-Link-Source-Commit'), COMMIT);
    assert.equal(response.headers.get('X-One-Link-Artifact-Signature'), 'reviewed-sigstore');
    assert.equal(response.headers.get('X-One-Link-Version-Pinned'), 'true');
  }
});

test('tagged installer requests fail explicitly without inventing a redirect', async () => {
  for (const spec of [
    'windows-x86_64-installer', 'windows.exe', 'windows-arm64.msi',
    'macos-arm64.dmg', 'macos-arm64-installer',
    'linux-x86_64.AppImage', 'linux-arm64-installer',
    'macos-arm64-dmg', 'linux-x86_64-appimage', 'windows-x86_64-msi',
    'linux-x86_64.tar.gz',
  ]) {
    const response = await route(spec, { headers: { Accept: 'text/html' } });
    assert.equal(response.status, 404, spec);
    assert.equal(response.headers.get('Location'), null);
    const body = await response.json();
    assert.equal(body.status, 'unsupported-release-format');
    assert.ok(body.available.every(name => name.endsWith('-zip')));
  }
});

test('versioned routing does not guess Intel Mac or unknown-client artifacts', async () => {
  const intel = await route('macos-x86_64', { headers: { Accept: 'application/json' } });
  assert.equal(intel.status, 503);
  assert.equal(intel.headers.get('Location'), null);
  const unknown = await route('auto', { headers: { Accept: 'application/json', 'User-Agent': 'unknown-client' } });
  assert.equal(unknown.status, 300);
  assert.equal(unknown.headers.get('Location'), null);
});

test('incomplete, mismatched, or unapproved catalog evidence prevents every release redirect', async () => {
  const mutations = [
    c => { c.schema_version = 2; },
    c => { c.releases = {}; },
    c => { c.releases[TAG].tag = 'v0.0.1'; },
    c => { c.releases[TAG].source_commit = 'short-sha'; },
    c => { c.releases[TAG].review.status = 'pending'; },
    c => { c.releases[TAG].review.signature_verification = 'not-verified'; },
    c => { c.releases[TAG].review.source_verification = 'not-verified'; },
    c => { c.releases[TAG].review.catalog_review_url = 'https://example.org/approved'; },
    c => { c.releases[TAG].review.source_review_url = `${REPO}/pull/999999?approved=true`; },
    c => { c.releases[TAG].review.reviewed_at = '2999-01-01T00:00:00.000Z'; },
    c => { c.releases[TAG].evidence.signer_identity = `${REPO}/.github/workflows/publish_rolling.yml@refs/heads/master`; },
    c => { c.releases[TAG].evidence.oidc_issuer = 'https://example.org'; },
    c => { c.releases[TAG].evidence.workflow_run_url = `${REPO}/actions/runs/999999#claimed-success`; },
    c => { c.releases[TAG].evidence.python_wheel_reproducibility = 'not-verified'; },
    c => { delete c.releases[TAG].evidence.checksums; },
    c => { c.releases[TAG].evidence.update_manifest.sha256 = ''; },
    c => { c.releases[TAG].evidence.sbom.sigstore.size_bytes = 0; },
    c => { delete c.releases[TAG].artifacts['linux-arm64']; },
    c => { c.releases[TAG].artifacts['macos-x86_64'] = artifact('one-link-macos-x86_64.zip'); },
    c => { c.releases[TAG].artifacts['windows-x86_64'].name = 'one-link-setup-x86_64.exe'; },
    c => { c.releases[TAG].artifacts['windows-x86_64'].sha256 = 'a'.repeat(63); },
    c => { c.releases[TAG].artifacts['windows-x86_64'].size_bytes = Number.MAX_SAFE_INTEGER + 1; },
    c => { delete c.releases[TAG].artifacts['windows-x86_64'].sigstore; },
    c => { c.releases[TAG].artifacts['windows-x86_64'].url = 'https://example.org/alternate.zip'; },
    c => { c.releases[TAG].source_archives.zip = artifact('../one-link-source.zip'); },
  ];
  for (const mutate of mutations) {
    const catalog = fixture();
    mutate(catalog);
    const response = await route('windows-x86_64', { catalog });
    assert.equal(response.status, 503, mutate.toString());
    assert.equal(response.headers.get('Location'), null);
  }
});

test('rolling routes retain their existing behavior even if the unused catalog is invalid', async () => {
  const response = await route('windows-x86_64', {
    env: { VERSIONED_RELEASE_TAG: '' }, catalog: null,
  });
  assert.equal(response.status, 302);
  assert.equal(response.headers.get('Location'), `${REPO}/releases/download/auto-latest/one-link-setup-x86_64.exe`);
  assert.equal(response.headers.get('X-One-Link-Artifact-Signature'), 'not-published');
});

function sourceObject() {
  return {
    body: SOURCE_BYTES,
    size: SOURCE_BYTES.length,
    checksums: { sha256: Uint8Array.from(Buffer.from(digest(SOURCE_BYTES), 'hex')).buffer },
    customMetadata: { source_commit: COMMIT, release_tag: TAG },
  };
}

test('a missing reviewed full-source artifact cannot use an existing rolling mirror', async () => {
  let reads = 0;
  const response = await route('source', {
    env: { RELEASES: { get: async () => { reads += 1; return sourceObject(); } } },
  });
  assert.equal(response.status, 503);
  assert.equal((await response.json()).status, 'source-not-published');
  assert.equal(reads, 0);
});

test('a versioned source mirror exposes evidence only for the exact checked object', async () => {
  for (const [ua, name] of [['Windows NT 10.0', 'one-link-source.zip'], ['Linux', 'one-link-source.tar.gz']]) {
    let key = null;
    const response = await route('source', {
      catalog: fixture({ source: true }),
      env: { RELEASES: { get: async requested => { key = requested; return sourceObject(); } } },
      headers: { Accept: 'application/json', 'User-Agent': ua },
    });
    assert.equal(response.status, 200);
    assert.equal(key, `releases/${TAG}/${name}`);
    const body = await response.json();
    assert.equal(body.artifact.url, `${BASE}/${name}`);
    assert.equal(body.artifact.sha256, digest(SOURCE_BYTES));
    assert.equal(body.artifact.size_bytes, SOURCE_BYTES.length);
    assert.equal(body.artifact.source.commit, COMMIT);
    assert.equal(body.integrity.signature, 'reviewed-sigstore');
  }
  const response = await route('source', {
    catalog: fixture({ source: true }),
    env: { RELEASES: { get: async () => sourceObject() } },
    headers: { 'User-Agent': 'Windows NT 10.0' },
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Length'), String(SOURCE_BYTES.length));
  assert.equal(response.headers.get('X-Artifact-SHA256'), digest(SOURCE_BYTES));
  assert.equal(digest(Buffer.from(await response.arrayBuffer())), digest(SOURCE_BYTES));
});

test('mismatched source size, native checksum, tag, or commit cannot be served', async () => {
  for (const mutate of [
    o => { o.size += 1; },
    o => { o.checksums.sha256 = new Uint8Array(32).buffer; },
    o => { o.checksums.sha256 = new Uint8Array(31).buffer; },
    o => { o.checksums = {}; o.customMetadata.sha256 = digest(SOURCE_BYTES); },
    o => { o.customMetadata.release_tag = 'auto-latest'; },
    o => { o.customMetadata.source_commit = 'a'.repeat(40); },
  ]) {
    const object = sourceObject();
    mutate(object);
    Object.defineProperty(object, 'body', { get() { throw new Error('unverified bytes must not be read'); } });
    const response = await route('source', {
      catalog: fixture({ source: true }),
      env: { RELEASES: { get: async () => object } },
    });
    assert.equal(response.status, 503, mutate.toString());
    assert.equal((await response.json()).status, 'invalid-source-object');
  }
});
