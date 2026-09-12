"""Deterministic secret scanner backing `no_secret_leak` (issue #273, scoped).

Covers: every named pattern class, masking (the raw secret never appears in a
finding), the generic entropy detector (catches random base64, skips lockfile
hash paths), clean-tree no-findings, diff-scoped scanning, and the fail-grade
gate integration in a scratch repo (secret in the task diff → no_secret_leak
failed with a masked excerpt, and a hand-written --set passed refused rather
than recorded — see tests/test_gate_sensor_authority.py for that rule).
"""

import base64
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from rig_workbench.workbench.secrets import (apply_secret_sensor,
                                             digest_under_label,
                                             entropy_allowlisted,
                                             git_id_under_label, mask,
                                             scan_diff_text,
                                             scan_line, scan_paths,
                                             scan_worktree_diff,
                                             shannon_entropy)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

# One representative sample per pattern class (all synthetic).
SAMPLES = [
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_access_key", "ASIAJ4X9K2M7Q1R5T8W3"),
    ("private_key_pem", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private_key_pem", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("github_token", "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    ("github_token", "github_pat_11ABCDEFG0abcdefghijklmnopqrstuv"),
    ("slack_token", "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo"),
    ("anthropic_api_key", "sk-ant-api03-AbCd1234EfGh5678IjKl9012MnOp"),
    ("openai_api_key", "sk-Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56"),
    ("google_api_key", "AIzaSyD9x2Qw8Rt4Yv6Ub1Zc3Ln5Mo7Pq9Sr0Tu"),
    ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"),
]

RANDOM_B64_40 = "R9k2mVxZ8qLpW3nYtB7cJdF5hGsA1uEoNiP4KrTe"  # 40 chars, high entropy


# ── pattern classes ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,secret", SAMPLES, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SAMPLES)])
def test_each_pattern_class_detected(kind, secret):
    findings = scan_line(f'token = "{secret}"  # planted', "src/config.py", 7)
    assert any(f["kind"] == kind for f in findings), findings
    f = next(f for f in findings if f["kind"] == kind)
    assert f["path"] == "src/config.py" and f["line"] == 7


def test_named_pattern_not_double_reported_as_entropy():
    secret = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # 40 chars, also entropy-shaped
    findings = scan_line(f"x = '{secret}'", "a.py", 1)
    assert [f["kind"] for f in findings] == ["github_token"]


def test_plain_prose_line_is_clean():
    assert scan_line("the quick brown fox jumps over the lazy dog", "a.py", 1) == []


# ── masking ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,secret", SAMPLES, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SAMPLES)])
def test_masking_raw_secret_absent_from_finding(kind, secret):
    findings = scan_line(secret, "a.py", 1)
    for f in findings:
        assert secret not in f["masked_excerpt"]
        assert secret not in json.dumps(f)          # nowhere in the whole finding
        assert secret[4:-2] not in f["masked_excerpt"]  # the middle is really gone


def test_mask_shape():
    assert mask("AKIAIOSFODNN7EXAMPLE").startswith("AKIA")
    assert mask("AKIAIOSFODNN7EXAMPLE").endswith("LE")
    assert mask("short") == "*****"  # ≤8 chars: fully masked


# ── entropy detector ──────────────────────────────────────────────────────────
def test_entropy_detector_catches_random_40char_base64(tmp_path):
    assert shannon_entropy(RANDOM_B64_40) > 4.5  # sanity: the fixture really is high-entropy
    p = tmp_path / "settings.py"
    p.write_text(f'SIGNING_KEY = "{RANDOM_B64_40}"\n', encoding="utf-8")
    findings = scan_paths([tmp_path])
    assert any(f["kind"] == "high_entropy" for f in findings)
    assert all(RANDOM_B64_40 not in f["masked_excerpt"] for f in findings)


def test_entropy_detector_skips_lockfile_hash_paths(tmp_path):
    sha512 = "sha512-" + RANDOM_B64_40 + "mB7xQ2kVjNfR5tYcW9zL3pD1gHsE8uAoKi=="
    for name in ("package-lock.json", "Cargo.lock", "go.sum"):
        (tmp_path / name).write_text(f'"integrity": "{sha512}"\n', encoding="utf-8")
    assert scan_paths([tmp_path]) == []
    # path-part based allowlisting too (vendored / VCS trees)
    assert entropy_allowlisted("node_modules/pkg/dist/index.js")
    assert entropy_allowlisted(".git/objects/pack/whatever.idx")
    assert not entropy_allowlisted("src/settings.py")


def test_named_patterns_still_fire_inside_lockfiles():
    # Only the entropy heuristic is allowlisted; a real token is a leak anywhere.
    findings = scan_line("resolved: AKIAIOSFODNN7EXAMPLE", "yarn.lock", 3)
    assert [f["kind"] for f in findings] == ["aws_access_key"]


def test_drill_corpus_fixtures_are_entropy_allowlisted_but_not_leak_proof():
    """A planted-credential case has to contain something that looks like a credential.

    The entropy heuristic is silenced under `corpora/` so drill's fixtures do not
    fail `no_secret_leak` by construction. Named patterns must still fire there —
    otherwise the allowlist would be a place to hide a real leak.
    """
    corpus_file = "skills/engine/corpora/fixture/cases/ts-mixed-violations/head/cache.ts"
    assert entropy_allowlisted(corpus_file)

    # Scan the real fixture rather than a restated copy: the copy would drift, and
    # the literal would itself trip the scanner from this (non-allowlisted) file.
    planted = (REPO_ROOT / corpus_file).read_text(encoding="utf-8").splitlines()
    assert any(len(re.findall(r"[a-f0-9]{32,}", ln)) for ln in planted), \
        "fixture no longer carries a high-entropy planted credential"
    for i, ln in enumerate(planted, 1):
        assert scan_line(ln, corpus_file, i) == []

    # ...but a real vendor-formatted key planted in the same tree is still a leak.
    # Reuse SAMPLES rather than restating a token literal: a fresh one would show
    # up as an added line in every future diff scan of this file.
    kind, sample = SAMPLES[0]
    findings = scan_line(f"const k = '{sample}'", corpus_file, 8)
    assert [f["kind"] for f in findings] == [kind]


def test_hex_lockfile_hash_vs_source_file():
    # Derived, not pasted, like every other fixture here: an unlabelled hex64 written
    # into this file as a literal is an added high-entropy line in its own diff.
    hex64 = hashlib.sha256(b"rig workbench lockfile hash fixture").hexdigest()
    assert scan_line(hex64, "go.sum", 1) == []                       # allowlisted path
    assert any(f["kind"] == "high_entropy" for f in scan_line(hex64, "src/app.py", 1))


# ── a digest under a digest label ─────────────────────────────────────────────
# Computed, never pasted. A literal digest written into this module is an added
# high-entropy line in every diff that touches it, and the diff-scoped scan behind
# `no_secret_leak` reports added lines — so pasting the fixtures would make the test
# suite for the scanner fail the scanner. Hashing a fixed seed gives the same three
# values on every run, with the one property the tests actually need: real hex of
# real digest length.
_FIXTURE_SEED = b"rig workbench secret-scan fixture"
SHA256_HEX = hashlib.sha256(_FIXTURE_SEED).hexdigest()
SHA1_HEX = hashlib.sha1(_FIXTURE_SEED).hexdigest()
SHA512_HEX = hashlib.sha512(_FIXTURE_SEED).hexdigest()
# The base64 counterpart, likewise derived rather than typed: 43 chars, the length a
# base64-encoded sha256 lands on, and the charset the rule refuses to exempt.
B64_OF_DIGEST_LEN = base64.urlsafe_b64encode(bytes.fromhex(SHA256_HEX)).decode().rstrip("=")


def test_the_fixtures_are_the_shapes_the_rules_turn_on():
    """Guard the derivation: a seed change must not silently weaken the cases below."""
    assert len(SHA256_HEX) == 64 and len(SHA1_HEX) == 40 and len(SHA512_HEX) == 128
    assert all(re.fullmatch(r"[0-9a-f]+", h) for h in (SHA256_HEX, SHA1_HEX, SHA512_HEX))
    # Every one of them clears the hex entropy threshold, so "still reported" cases
    # below really are the rule speaking and not a low-entropy accident.
    assert all(shannon_entropy(h) > 3.0 for h in (SHA256_HEX, SHA1_HEX, SHA512_HEX))
    assert len(B64_OF_DIGEST_LEN) == 43 and shannon_entropy(B64_OF_DIGEST_LEN) > 4.5


@pytest.mark.parametrize("line", [
    f'        "body_sha256": "{SHA256_HEX}",',
    f'sha256 = "{SHA256_HEX}"',
    f"sha256:{SHA256_HEX}",
    f"checksum: {SHA256_HEX}",
    f'"content_hash": "{SHA256_HEX}"',
    f'"blake2b": "{SHA256_HEX}"',
])
def test_hex_digest_under_a_digest_label_is_not_a_secret(line):
    """An attestation table is a table of hashes, and says so on every line.

    The value is the output of a hash function over something public, published so
    anyone can recompute it; the entropy heuristic cannot tell it from a key, but the
    line can. Moving one such table between modules used to fail `no_secret_leak` on
    every row of it.
    """
    assert scan_line(line, "src/composition.py", 1) == []


def test_the_same_hex_without_a_label_is_still_reported():
    # The label is the whole of the exemption: nothing about the value itself is safe.
    findings = scan_line(f'"value": "{SHA256_HEX}"', "src/composition.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    assert SHA256_HEX not in findings[0]["masked_excerpt"]


def test_sha1_and_sha512_lengths_are_exempt_and_other_hex_lengths_are_not():
    assert scan_line(f'"sha1": "{SHA1_HEX}"', "src/app.py", 1) == []
    assert scan_line(f'"sha512": "{SHA512_HEX}"', "src/app.py", 1) == []
    # Exactly sha1/sha256/sha512 output length, nothing else: a 48-char hex blob under
    # a digest label is not the shape of any digest this rule knows about.
    hex48 = SHA256_HEX[:48]
    assert [f["kind"] for f in scan_line(f'"digest": "{hex48}"', "src/app.py", 1)] == ["high_entropy"]


def test_a_labelled_base64_token_is_never_exempted():
    """Charset carries the rule, because it is the part an attacker cannot cheaply fake.

    Base64 of digest length is indistinguishable from base64 of key length, so a
    `sha256` label over a base64 value is exactly what hiding a key would look like.
    """
    findings = scan_line(f'"body_sha256": "{B64_OF_DIGEST_LEN}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


def test_named_patterns_still_fire_under_a_digest_label():
    # Only the entropy heuristic is silenced; a vendor-formatted credential is a leak
    # wherever it is written, and a `checksum:` in front of it changes nothing.
    kind, sample = SAMPLES[0]
    findings = scan_line(f'"checksum": "{sample}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == [kind]


# ── a git object id under its label ───────────────────────────────────────────
@pytest.mark.parametrize("label", [
    "source_commit", "git_commit", "commit",
    "source_git_blob", "git_blob", "blob",
    "tree", "object_id", "oid",
])
def test_git_object_id_under_a_git_id_label_is_not_a_secret(label):
    """The other digest an attestation table cannot avoid: where the text was read from.

    A commit or blob id is the coordinate that makes the binding checkable at all, and
    it is hex by construction because that is how git addresses content.
    """
    assert scan_line(f'"{label}": "{SHA1_HEX}"', "src/composition.py", 1) == []


def test_checksum_and_digest_are_whole_keys_and_still_vouch():
    """The closed list keeps the two plain English ones, as whole keys only.

    `checksum` and `digest` name a digest and nothing else; it is `digest_auth_secret`
    and `password_hash` — the families that only start or end with one — that were the
    way in, and those are closed above. A `_`-joined prefix is allowed before the five
    common keys and no suffix after any of them.
    """
    assert scan_line(f'checksum = "{SHA256_HEX}"', "src/app.py", 1) == []
    assert scan_line(f'"digest": "{SHA512_HEX}"', "src/app.py", 2) == []
    assert scan_line(f'"source_excerpt_sha256": "{SHA256_HEX}"', "src/app.py", 3) == []
    assert scan_line(f'"blob_checksum": "{SHA256_HEX}"', "src/app.py", 4) == []


def test_the_git_id_label_class_applies_to_40_hex_only():
    # A git id is a sha1 and is always 40 hex. A 64-hex value under a `commit` label is
    # not a git id, and the label does not reach it.
    findings = scan_line(f'"commit": "{SHA256_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    assert [f["kind"] for f in scan_line(f'"commit": "{SHA512_HEX}"', "src/app.py", 2)] == ["high_entropy"]
    # …and without a label of either class, 40 hex is still reported.
    assert [f["kind"] for f in scan_line(f'"value": "{SHA1_HEX}"', "src/app.py", 3)] == ["high_entropy"]


def test_a_key_that_merely_contains_a_label_does_not_carry_it():
    """Reversing an earlier pin: `commit_token` is reported again.

    It was exempt while the label was any word within 40 characters of the value. A
    security review measured what that bought: six ordinary key names, none of them
    naming a digest, each silencing a 40-hex value beside it. The label is now the
    value's own key, matched whole, so a key that merely *contains* one carries
    nothing.
    """
    findings = scan_line(f'"commit_token": "{SHA1_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


# The six key names the review measured: each contains a former label as a substring
# (`rev` in prev/revenue/revoked, `commit` in committee, `tree` in street, `blob` in
# blobstore) and each silenced the 40-hex value beside it. 40 hex is a live credential
# shape — Datadog application keys, CircleCI tokens, legacy GitHub PATs — that no
# named pattern covers, so these are the regression cases that matter most here.
@pytest.mark.parametrize("key", [
    "prev_api_key", "revenue_api_token", "revoked_key",
    "committee_api_key", "street_service_key", "blobstore_key",
])
def test_substring_of_a_git_id_label_no_longer_silences_a_40_hex_value(key):
    findings = scan_line(f'{key} = "{SHA1_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


@pytest.mark.parametrize("key", ["digest_auth_secret", "password_hash", "session_hash"])
def test_a_key_that_is_not_a_digest_key_reports_64_hex(key):
    """`secrets.token_hex(32)` is 64 hex, and so is every key it makes.

    An AES-256 key, an HMAC key, a session key and a Sentry auth token all have this
    exact shape, so a key family that merely ends in `_hash` or begins with `digest`
    cannot be allowed to vouch for one. Bare `_hash` is gone from the label set, and
    the prefix rule runs one way only: `body_sha256` yes, `digest_auth_secret` no.
    """
    findings = scan_line(f'{key} = "{SHA256_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


def test_the_label_window_is_the_same_line_only():
    # A label on the previous line does not vouch for a value on this one — otherwise
    # any file with the word `digest` anywhere in it would silence the detector below.
    assert scan_line('        "body_sha256":', "src/app.py", 1) == []
    findings = scan_line(f'        "{SHA256_HEX}",', "src/app.py", 2)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    # …nor does a label far enough back on the same line to belong to another field.
    far = f'"sha256": "", "pad": "{"-" * 40}", "k": "{SHA256_HEX}"'
    assert [f["kind"] for f in scan_line(far, "src/app.py", 3)] == ["high_entropy"]
    # Both label classes are anchored the same way, so both stay on their own line.
    assert scan_line('        "source_commit":', "src/app.py", 4) == []
    assert [f["kind"] for f in scan_line(f'        "{SHA1_HEX}",', "src/app.py", 5)] == ["high_entropy"]


@pytest.mark.parametrize("predicate,key", [
    (digest_under_label, "body_sha256"),
    (git_id_under_label, "source_commit"),
])
def test_the_charset_guard_holds_when_the_predicate_is_called_directly(predicate, key):
    """Both predicates are public, so each re-checks the charset itself.

    Through `scan_line` the guard is unreachable — that caller has already decided the
    token is hex before it asks — and the base64 case a few tests up is killed by the
    length set, not the charset. Called directly with a 40-char base64 token, which
    passes every length test either rule applies, only the charset guard is left to
    say no. It is the load-bearing half of the rule, so it is pinned where it can
    actually fail.
    """
    line = f'"{key}": "{RANDOM_B64_40}"'
    assert predicate(line, RANDOM_B64_40, line.index(RANDOM_B64_40)) is False
    # …and the same call with real hex of the same length is True, so the assertion
    # above is the charset talking and not the anchor failing to match.
    hex_line = f'"{key}": "{SHA1_HEX}"'
    assert predicate(hex_line, SHA1_HEX, hex_line.index(SHA1_HEX)) is True


def test_the_three_accepted_label_forms_and_nothing_else():
    """A key and its separator, the colon form, and the whole-word prose form."""
    for line in (f'"body_sha256": "{SHA256_HEX}"',   # mapping
                 f'sha256 = "{SHA256_HEX}"',          # assignment
                 f"sha256:{SHA256_HEX}",              # the form digests are quoted in
                 f"sha256 {SHA256_HEX}"):             # prose / Markdown
        assert scan_line(line, "src/app.py", 1) == [], line
    # Not a form: anything between the key and the value that is not a separator.
    for line in (f'sha256 -> {SHA256_HEX}',
                 f'sha256, {SHA256_HEX}',
                 f'sha256("{SHA256_HEX}")'):
        assert [f["kind"] for f in scan_line(line, "src/app.py", 1)] == ["high_entropy"], line


# ── clean tree ────────────────────────────────────────────────────────────────
def test_clean_tree_has_no_findings(tmp_path):
    (tmp_path / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\nGREETING = 'hello world'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n\nA perfectly ordinary readme.\n", encoding="utf-8")
    assert scan_paths([tmp_path]) == []


def test_binary_file_is_skipped(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + b"AKIAIOSFODNN7EXAMPLE")
    assert scan_paths([tmp_path]) == []


# ── diff-scoped scan ──────────────────────────────────────────────────────────
def test_scan_diff_text_reports_added_lines_with_new_file_lines():
    diff = (
        "diff --git a/src/cfg.py b/src/cfg.py\n"
        "index 111..222 100644\n"
        "--- a/src/cfg.py\n"
        "+++ b/src/cfg.py\n"
        "@@ -0,0 +10,2 @@\n"
        "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        "+OTHER = 1\n"
        "@@ -20 +30 @@\n"
        "-OLD = 'AKIAJ4X9K2M7Q1R5T8W3'\n"
        "+NEW = 'nothing secret here'\n"
    )
    findings = scan_diff_text(diff)
    assert len(findings) == 1  # removed lines are not scanned
    assert findings[0] == {"path": "src/cfg.py", "line": 10, "kind": "aws_access_key",
                           "masked_excerpt": findings[0]["masked_excerpt"]}
    assert "AKIAIOSFODNN7EXAMPLE" not in findings[0]["masked_excerpt"]


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def test_scan_worktree_diff_sees_uncommitted_and_untracked(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\nTOKEN = '" + "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo'\n",
                                 encoding="utf-8")  # uncommitted edit
    (repo / "new.env").write_text("KEY=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")  # untracked
    kinds = {f["kind"] for f in scan_worktree_diff(repo, sha)}
    assert {"slack_token", "aws_access_key"} <= kinds


def test_scan_worktree_diff_clean_worktree_no_findings(tmp_path):
    repo, sha = make_repo(tmp_path)
    assert scan_worktree_diff(repo, sha) == []


# ── gate sensor (unit) ────────────────────────────────────────────────────────
def make_state(repo, sha):
    task = {"worktree_path": str(repo), "base_commit": sha}
    acc = {"checks": [{"name": "no_secret_leak", "status": "pending", "detail": ""}]}
    return task, acc


def test_sensor_fails_check_on_secret_in_diff(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak")
    task, acc = make_state(repo, sha)
    notes = apply_secret_sensor(repo, tmp_path, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "failed"  # fail-grade, unlike the schema sensor
    assert check["secret_findings"]
    assert "AKIAIOSFODNN7EXAMPLE" not in "\n".join(notes + check["secret_findings"])
    assert any("no_secret_leak failed" in n for n in notes)


def test_sensor_writes_its_verdict_over_a_passed_check(tmp_path):
    """A `passed` already on the check — from this invocation's `--set`, or from an
    evaluation before the secret was added — is not an answer the scan defers to."""
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak")
    task, acc = make_state(repo, sha)
    acc["checks"][0]["status"] = "passed"
    notes = apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    assert "secret_override" not in acc["checks"][0]
    assert any("no_secret_leak failed" in n for n in notes)


def test_sensor_resets_its_own_failure_when_secret_removed(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    task, acc = make_state(repo, sha)
    apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    (repo / "cfg.py").unlink()
    apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "secret_findings" not in acc["checks"][0]


def test_sensor_noop_without_criterion_or_worktree(tmp_path):
    repo, sha = make_repo(tmp_path)
    acc = {"checks": [{"name": "tests_pass_or_explained", "status": "pending", "detail": ""}]}
    assert apply_secret_sensor(repo, tmp_path, {"worktree_path": str(repo), "base_commit": sha}, acc) == []
    task, acc = make_state(repo, sha)
    assert apply_secret_sensor(repo, tmp_path, {"worktree_path": None, "base_commit": sha}, acc) == []


# ── end to end through the CLI (scratch repo, real worktree) ──────────────────
def cli(repo, wt_root, *args):
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root))
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


def test_gate_integration_secret_in_diff_fails_no_secret_leak(tmp_path):
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "add config", "--type", "feature", "--slug", "add-config")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    wt = wt_root / task_id
    assert wt.is_dir()

    (wt / "config.py").write_text('AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "plant secret")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 1  # findings must block: gate is FAILED
    assert "no_secret_leak" in r.stdout
    assert "potential secret(s) detected" in r.stdout
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout + r.stderr  # masked everywhere
    assert "AKIA" in r.stdout  # masked excerpt shows the prefix

    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_secret_leak")
    assert check["status"] == "failed"
    assert check["secret_findings"]
    assert all("AKIAIOSFODNN7EXAMPLE" not in ln for ln in check["secret_findings"])

    # scan-secrets --diff exposes the same findings, masked, exit 1
    r = cli(repo, wt_root, "scan-secrets", "--diff", task_id)
    assert r.returncode == 1
    assert "aws_access_key" in r.stdout and "AKIAIOSFODNN7EXAMPLE" not in r.stdout

    # the sensor's verdict is not overridable by hand: the gate refuses the declaration
    r = cli(repo, wt_root, "gate", task_id, "--set", "no_secret_leak=passed")
    assert r.returncode == 2, r.stdout + r.stderr   # a usage error, not a failed gate
    assert "you set 'passed' — the sensor measured 'failed'" in r.stdout + r.stderr
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_secret_leak")
    assert check["status"] == "failed"



def test_scan_secrets_cli_clean_paths_exits_zero(tmp_path):
    repo, _sha = make_repo(tmp_path)
    r = cli(repo, tmp_path / "wt", "scan-secrets", ".")
    assert r.returncode == 0, r.stderr
    assert "No potential secrets found." in r.stdout


def test_an_attestation_table_lands_through_the_gate_without_an_override(tmp_path):
    """The whole point, end to end: the case that motivated the rule.

    Moving `_JAPANESE_MATERIAL_ATTESTATIONS` between modules put four `*_sha256` rows
    and two `source_commit` rows into a diff and failed `no_secret_leak` on lines that
    had not changed at all. An ordinary source path, no allowlist, no `--set` escape
    hatch: the diff-scoped scan must simply come back empty.
    """
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "move attestations", "--type", "refactor",
            "--slug", "move-attestations")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    wt = wt_root / task_id

    rows = []
    for material in ("technical", "conversation"):
        body = hashlib.sha256(material.encode()).hexdigest()
        excerpt = hashlib.sha256(f"{material}-excerpt".encode()).hexdigest()
        commit = hashlib.sha1(f"{material}-commit".encode()).hexdigest()
        blob = hashlib.sha1(f"{material}-blob".encode()).hexdigest()
        rows += [
            f'    "{material}": {{',
            f'        "source_git_blob": "{blob}",',
            f'        "source_commit": "{commit}",',
            f'        "source_excerpt_sha256": "{excerpt}",',
            f'        "body_sha256": "{body}",',
            "    },",
        ]
    (wt / "composition.py").write_text(
        "ATTESTATIONS = {\n" + "\n".join(rows) + "\n}\n", encoding="utf-8")
    _git(wt, "add", "composition.py")
    _git(wt, "commit", "-q", "-m", "move the attestation table")

    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert scan_worktree_diff(pathlib.Path(task["worktree_path"]), task["base_commit"]) == []

    r = cli(repo, wt_root, "scan-secrets", "--diff", task_id)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No potential secrets found." in r.stdout

    # …and the same table with one real credential added is still caught, so the case
    # above is the rule working and not the scan failing to look.
    kind, sample = SAMPLES[0]
    (wt / "composition.py").write_text(f'TOKEN = "{sample}"\n', encoding="utf-8")
    r = cli(repo, wt_root, "scan-secrets", "--diff", task_id)
    assert r.returncode == 1 and kind in r.stdout and sample not in r.stdout


def test_signed_eval_evidence_is_entropy_allowlisted_but_not_leak_proof():
    """A signed evaluation result is hashes almost end to end.

    One digest per prompt surface, a sha256 per captured stream, the case hash, four
    commit ids and the attestation signature — the things that let anyone recompute
    the binding, and indistinguishable from a credential to an entropy heuristic.
    Committing the first one produced 223 findings and a failed `no_secret_leak`
    (#447), which is not a one-off: every PR that lands evidence hits it, including
    the maintainer path `validate.yml` documents. A criterion overridden by hand every
    time is one nobody reads.
    """
    evidence = "evals/evidence/style-persona-qiita-tech-writer/current.json"
    assert entropy_allowlisted(evidence)
    assert scan_line(f'"result_sha256": "{RANDOM_B64_40}"', evidence, 1) == []


def test_the_evidence_allowlist_is_anchored_and_still_reports_real_credentials():
    """Two ways this could become a hiding place, both closed.

    `evidence` is too ordinary a directory name to silence wherever it appears, so
    the rule is anchored at the repository root rather than matched at any depth like
    `ALLOW_DIR_PARTS`. And only the entropy heuristic is silenced: a vendor-formatted
    credential written into an evidence file is still a leak and still reported.
    """
    assert not entropy_allowlisted("src/evidence/collected.json")
    assert not entropy_allowlisted("docs/evals/evidence/example.md")
    # Compared as path components rather than as a string prefix, so a path that only
    # starts with the tree cannot borrow its silence. Raised by an adversarial review
    # of this change: git never hands these callers a `..`, but proving that no caller
    # ever will is more expensive, and more fragile, than not depending on it.
    assert not entropy_allowlisted("evals/evidence/../elsewhere/current.json")
    assert not entropy_allowlisted("evals/evidence-notes/current.json")
    # The forms that are legitimately the same path still resolve to it.
    assert entropy_allowlisted("./evals/evidence/case/current.json")
    assert entropy_allowlisted("evals\\evidence\\case\\current.json")

    evidence = "evals/evidence/some-case/current.json"
    findings = scan_line('"note": "AKIAIOSFODNN7EXAMPLE"', evidence, 2)
    assert [f["kind"] for f in findings] == ["aws_access_key"]
