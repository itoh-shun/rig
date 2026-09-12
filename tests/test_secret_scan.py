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
import tempfile

import pytest

from rig_workbench.workbench.detection_corpus import corpus_root
from rig_workbench.workbench.secrets import (ALLOW_PATH_PREFIXES,
                                             BASE64_ENTROPY_THRESHOLD,
                                             RIG_CHECKOUT_MARKERS,
                                             apply_secret_sensor,
                                             digest_under_label,
                                             entropy_allowlisted,
                                             git_id_under_label,
                                             is_rig_checkout, mask,
                                             scan_diff_text,
                                             scan_file,
                                             scan_line, scan_paths,
                                             scan_root,
                                             scan_worktree_diff,
                                             shannon_entropy, split_key_value)

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

    The entropy heuristic is silenced under `skills/engine/corpora/` so drill's
    fixtures do not fail `no_secret_leak` by construction. Named patterns must still
    fire there — otherwise the allowlist would be a place to hide a real leak.
    """
    # `rig_checkout=True` is not decoration: the prefixes only apply inside one, and the
    # default is False so that a caller who never asked cannot silence anything.
    corpus_file = "skills/engine/corpora/fixture/cases/ts-mixed-violations/head/cache.ts"
    assert entropy_allowlisted(corpus_file, rig_checkout=True)

    # Scan the real fixture rather than a restated copy: the copy would drift, and
    # the literal would itself trip the scanner from this (non-allowlisted) file.
    planted = (REPO_ROOT / corpus_file).read_text(encoding="utf-8").splitlines()
    assert any(len(re.findall(r"[a-f0-9]{32,}", ln)) for ln in planted), \
        "fixture no longer carries a high-entropy planted credential"
    for i, ln in enumerate(planted, 1):
        assert scan_line(ln, corpus_file, i, rig_checkout=True) == []

    # ...but a real vendor-formatted key planted in the same tree is still a leak.
    # Reuse SAMPLES rather than restating a token literal: a fresh one would show
    # up as an added line in every future diff scan of this file.
    kind, sample = SAMPLES[0]
    findings = scan_line(f"const k = '{sample}'", corpus_file, 8, rig_checkout=True)
    assert [f["kind"] for f in findings] == [kind]


def test_only_rigs_own_corpora_are_allowlisted_not_any_folder_by_that_name(tmp_path,
                                                                            monkeypatch):
    """`corpora` used to be an ALLOW_DIR_PARTS entry, matched at any depth.

    That made the name itself a silencer: any project that calls a directory `corpora`
    — a linguistics dataset, a training set, anything — turned off the entropy
    heuristic for everything beneath it, in any repository rig scans, and a real
    credential parked there went unreported. `node_modules` and `.git` earn any-depth
    matching because their creators reserve the name; `corpora` is a word.

    rig's own fixture corpora have one address, so the exemption is written at it.
    """
    # Computed, never pasted, like every other fixture in this file.
    leak = hashlib.sha256(b"rig secret-scan corpora depth fixture").hexdigest()
    tree = make_rig_checkout(tmp_path / "proj")
    planted = tree / "some" / "project" / "corpora"
    planted.mkdir(parents=True)
    (tree / "skills" / "engine" / "corpora" / "fixture").mkdir(parents=True)
    (planted / "leak.txt").write_text(f"token={leak}\n", encoding="utf-8")
    (tree / "skills" / "engine" / "corpora" / "fixture" / "seeded.ts").write_text(
        f"const k = '{leak}'\n", encoding="utf-8")

    monkeypatch.chdir(tree)
    assert [(f["path"], f["kind"]) for f in scan_paths([pathlib.Path(".")])] == [
        ("some/project/corpora/leak.txt", "high_entropy")]

    assert not entropy_allowlisted("some/project/corpora/leak.txt")
    assert not entropy_allowlisted("corpora/leak.txt")
    # rig's real fixture corpus stays allowlisted — and at the address corpus_root()
    # actually builds, read off that function rather than restated here, so moving the
    # shipped corpus without moving the exemption fails this rather than going quiet.
    shipped = corpus_root().parts[-4:-1]          # ("skills", "engine", "corpora")
    assert shipped in ALLOW_PATH_PREFIXES
    assert entropy_allowlisted("/".join(shipped) + "/fixture/cases/x.ts", rig_checkout=True)
    # …and not when the caller cannot vouch for the checkout, which is the default.
    assert not entropy_allowlisted("/".join(shipped) + "/fixture/cases/x.ts")
    # …and only the entropy heuristic is silenced there, now as before.
    kind, sample = SAMPLES[0]
    findings = scan_line(f"const k = '{sample}'", "skills/engine/corpora/fixture/x.ts", 1,
                         rig_checkout=True)
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
# The same value with its padding left on: 44 chars ending in a single `=`. A base64
# value of this length is what makes the separator COUNT load-bearing rather than
# decorative — the padding is a second `=` in `api_key=<value>`, and the right part
# after the first one is 44 characters, far past every length guard.
B64_PADDED_OF_DIGEST_LEN = base64.urlsafe_b64encode(bytes.fromhex(SHA256_HEX)).decode()


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


# The prefix words that cost a prefix its vouch, each in a name someone would really
# write. `hmac_sha256` is the ordinary spelling of an HMAC-SHA256 key and is exactly 64
# hex — the digest shape it is not; the rest are the same trade under another word.
@pytest.mark.parametrize("key", [
    "hmac_sha256", "api_key_sha256", "secret_digest", "token_checksum",
    "session_key_sha256", "password_sha256", "passwd_digest", "auth_checksum",
])
def test_a_key_shaped_prefix_does_not_vouch_for_the_hex_beside_it(key):
    """`sha256` names the function, not the input — unless the input is itself a key.

    `<x>_sha256` reads "the sha256 of <x>", which is why the prefix rule exists. When
    `<x>` names a key the same name reads "that key, keyed-hashed", or just names the
    key; and an HMAC key, an API key and a session key are all 64 hex, exactly like the
    digest the label claims. The prefix was accepting any `_`-joined word at all.
    """
    findings = scan_line(f'{key} = "{SHA256_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    assert SHA256_HEX not in findings[0]["masked_excerpt"]


def test_a_denied_prefix_is_denied_at_sha1_length():
    # Not a property of 64 hex — and the 40-hex case is not rescued by the git-id
    # class either, which has no prefix rule of its own to lose.
    findings = scan_line(f'hmac_sha1 = "{SHA1_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


def test_a_denied_prefix_is_denied_at_sha512_length():
    findings = scan_line(f'"secret_sha512": "{SHA512_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


def test_the_whole_prefix_is_read_not_only_its_first_word():
    # `my_secret_body_sha256` reads as a digest right up to the word in the middle.
    findings = scan_line(f"my_secret_body_sha256: {SHA256_HEX}", "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


def test_case_is_not_a_way_out_of_the_denylist():
    # The labels are matched case-insensitively, so the denylist has to be too.
    findings = scan_line(f'HMAC_SHA256 = "{SHA256_HEX}"', "src/app.py", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]


@pytest.mark.parametrize("key", ["keystore_sha256", "authority_digest", "monkey_checksum"])
def test_the_denied_words_are_whole_words_not_substrings(key):
    """The mirror of the S1 lesson, applied to the denylist itself.

    `keystore` is not `key`, `authority` is not `auth`, `monkey` is not `key`. A
    denylist matched on substrings would take the exemption away from ordinary names
    the way the old label window handed it to ordinary names.
    """
    assert scan_line(f'{key} = "{SHA256_HEX}"', "src/app.py", 1) == []


@pytest.mark.parametrize("key", ["body_sha256", "source_excerpt_sha256", "file_sha256", "blob_checksum"])
def test_an_ordinary_prefix_still_vouches(key):
    # The retained half: the attestation-table keys the prefix rule was added for.
    assert scan_line(f'"{key}": "{SHA256_HEX}"', "src/composition.py", 1) == []


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


# ── an unquoted key=value line ────────────────────────────────────────────────
# `=` is base64 padding, so it is in the token charset, so an unquoted `.env` line
# arrives as ONE token: `api_key=<40 hex>` is 48 characters of mixed charset. It fails
# the hex test because of its own key's letters, and is then measured against the
# base64 threshold, which those same letters keep it below. The shape credentials are
# most often written in was the one shape the detector could not see.
def test_an_unquoted_env_line_is_scanned_as_a_value_under_its_key():
    assert [f["kind"] for f in scan_line(f"api_key={SHA1_HEX}", ".env", 1)] == ["high_entropy"]
    assert [f["kind"] for f in scan_line(f"secret_key={SHA256_HEX}", ".env", 2)] == ["high_entropy"]


def test_the_finding_for_a_split_token_masks_the_value_not_the_key():
    # The value is the secret, so the value is what gets masked — and the key, which is
    # not a secret, is not smuggled into the excerpt as though it were part of one.
    findings = scan_line(f"api_key={SHA1_HEX}", ".env", 1)
    excerpt = findings[0]["masked_excerpt"]
    assert SHA1_HEX not in excerpt and "api_key" not in excerpt
    assert excerpt.startswith(SHA1_HEX[:4]) and excerpt.endswith(SHA1_HEX[-2:])


def test_the_left_part_is_still_the_label_the_digest_rules_read():
    """Splitting hands the value to the same rules, with the same key in front of it.

    The point of the split is not to report more: it is to let the S1 rules see the
    pair they were written for. An unquoted attestation line is still an attestation
    line, and an unquoted credential line is now a credential line.
    """
    assert scan_line(f"body_sha256={SHA256_HEX}", "attest.env", 1) == []
    assert scan_line(f"source_commit={SHA1_HEX}", "attest.env", 2) == []
    # …including the denylist from the commit before this one.
    assert [f["kind"] for f in scan_line(f"hmac_sha256={SHA256_HEX}", "attest.env", 3)] == ["high_entropy"]


def test_the_colon_form_reports_and_exempts_without_any_split():
    """`:` is not in the token charset, so a colon form arrives already separated.

    The value comes through as a bare token with its key left on the line, which is
    what the label rules read — so both answers below are the same ones the colon form
    gave before the split rule existed.
    """
    assert [f["kind"] for f in scan_line(f"api_key:{SHA1_HEX}", ".env", 1)] == ["high_entropy"]
    assert scan_line(f"sha256:{SHA256_HEX}", ".env", 2) == []


def test_split_key_value_cuts_only_a_name_from_a_value():
    assert split_key_value(f"api_key={SHA1_HEX}") == ("api_key", SHA1_HEX)
    # A value with no separator is one value.
    assert split_key_value(SHA256_HEX) is None
    # Base64 padding is a second `=`, so there is no single place to cut — and the rule
    # refuses rather than guessing at one. The padded value is 44 characters, so it is
    # the separator COUNT refusing here and not a length guard: relax the count and this
    # token splits at the wrong `=`, with the value's own tail read as a second field.
    assert split_key_value(f"api_key={B64_PADDED_OF_DIGEST_LEN}") is None
    assert split_key_value(f"{B64_OF_DIGEST_LEN}==") is None
    # The left part must be a name: `<base64>=<base64>` is not a key and a value.
    assert split_key_value(f"{RANDOM_B64_40[:8]}+x={SHA1_HEX}") is None
    # …nor is anything that does not start like an identifier.
    assert split_key_value(f"9key={SHA1_HEX}") is None
    # The right part has to be long enough to be a token in its own right.
    assert split_key_value(f"api_key={SHA1_HEX[:31]}") is None


def test_a_left_part_of_token_length_is_left_merged():
    """A finding names the value, not the key, so a key-length left part stays merged.

    A left part 32 characters or longer is itself a candidate value; cutting there
    would leave it out of the excerpt the finding carries. Those stay whole and are
    scanned exactly as before.
    """
    long_name = "a" + SHA1_HEX[1:]  # identifier-shaped, and token length
    assert split_key_value(f"{long_name}={SHA256_HEX}") is None
    # The whole token is still what the detector sees, as it was before this change.
    line = f"{long_name}={SHA256_HEX}"
    assert [f["kind"] for f in scan_line(line, "src/app.py", 1)] == \
        [f["kind"] for f in scan_line(line.replace("=", "+"), "src/app.py", 1)]


def test_the_quoted_forms_are_untouched_by_the_split():
    # A quote ends the token, so a quoted line never had the two merged in the first
    # place; these are the S1 cases, and they answer exactly as they did.
    assert [f["kind"] for f in scan_line(f'api_key = "{SHA1_HEX}"', "src/app.py", 1)] == ["high_entropy"]
    assert scan_line(f'"body_sha256": "{SHA256_HEX}"', "src/app.py", 2) == []


def _b64_value(seed: bytes, n: int) -> str:
    """`n` characters of base64, derived from `seed` — computed here, never pasted."""
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode()[:n]


# The window the split opened, found by search over a fixed seed sequence rather than
# pasted: a 32-char base64 value whose OWN entropy is below the base64 threshold while
# `api_key=` + that same value is above it. Entropy per character is not monotone under
# taking a piece — the key's letters are characters the value does not repeat — so a
# rule that scored only the piece scored lower than the rule it replaced.
SPLIT_WINDOW_B64 = next(
    v for v in (_b64_value(b"split-window-%d" % i, 32) for i in range(1000))
    if shannon_entropy(v) <= BASE64_ENTROPY_THRESHOLD < shannon_entropy(f"api_key={v}"))


def test_the_split_does_not_lower_the_merged_tokens_score():
    """The regression the split introduced, pinned at the value it was measured on.

    `api_key=<32 chars of base64>` was reported before the split existed, because the
    merged token clears the base64 threshold. Judging the value alone dropped it: the
    value is shorter and its own characters repeat more. So both are scored, and either
    one is enough to report.
    """
    assert shannon_entropy(SPLIT_WINDOW_B64) <= BASE64_ENTROPY_THRESHOLD
    assert shannon_entropy(f"api_key={SPLIT_WINDOW_B64}") > BASE64_ENTROPY_THRESHOLD
    findings = scan_line(f"api_key={SPLIT_WINDOW_B64}", ".env", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    # The excerpt still names the value, which is the secret — not the key, which is not.
    assert SPLIT_WINDOW_B64 not in findings[0]["masked_excerpt"]
    assert "api_key" not in findings[0]["masked_excerpt"]


@pytest.mark.parametrize("n", [32, 34, 36])
def test_the_split_never_lowers_what_the_merged_token_already_scored(n):
    """The same property over 200 derived values per length, asserted per sample.

    Cheap stand-in for the Monte Carlo a security review ran: whenever the pre-split
    rule (score the merged token) would have reported, the rule in place now reports
    too. The final assertion keeps the test from passing vacuously — the window where
    the two rules disagree has to be exercised, or this proves nothing.
    """
    window = 0
    for i in range(200):
        value = _b64_value(b"monte-carlo-%d-%d" % (n, i), n)
        line = f"api_key={value}"
        merged_fires = shannon_entropy(line) > BASE64_ENTROPY_THRESHOLD
        value_fires = shannon_entropy(value) > BASE64_ENTROPY_THRESHOLD
        reported = bool(scan_line(line, ".env", 1))
        assert reported or not merged_fires, f"lost a merged-token finding at sample {i}"
        window += merged_fires and not value_fires
    assert window > 0, "no sample landed in the window, so this asserted nothing"


def test_an_unsplit_padded_base64_value_is_still_reported_whole():
    """Refusing to split is not refusing to look: the merged token is scored either way.

    `api_key=<44 chars of padded base64>` holds two `=` and stays one token, and the
    merged-token rule — the one that was there before any split existed — reports it.
    """
    findings = scan_line(f"api_key={B64_PADDED_OF_DIGEST_LEN}", ".env", 1)
    assert [f["kind"] for f in findings] == ["high_entropy"]
    assert B64_PADDED_OF_DIGEST_LEN not in findings[0]["masked_excerpt"]


def test_a_named_pattern_inside_an_unquoted_assignment_is_unaffected():
    # The named patterns run before the entropy pass and are not split-sensitive.
    kind, sample = SAMPLES[0]
    assert [f["kind"] for f in scan_line(f"AWS_ACCESS_KEY_ID={sample}", ".env", 1)] == [kind]


# ── the flooding corpus ───────────────────────────────────────────────────────
# Every rule here trades false negatives against false positives, and the second half
# of that trade is the one no single-case test measures: a rule that reports one more
# credential and fifty more attestation rows is a worse rule. A whole-tree scan answers
# that but is a one-off; this corpus is the durable, cheap form of the same question —
# one line per shape the rules turn on, and a finding count pinned against both drifts.
# Computed from the same seed as everything else, so no line is a pasted literal.
def _flood_corpus() -> list[tuple[str, bool]]:
    """(line, is expected to report) — the shapes, and the verdict each must keep."""
    rows: list[tuple[str, bool]] = []
    # Attestation tables: a digest or a git id under its own key, quoted. All exempt.
    rows += [(f'    "{k}": "{SHA256_HEX}",', False)
             for k in ("body_sha256", "source_excerpt_sha256", "blob_checksum", "content_hash")]
    rows += [(f'    "{k}": "{SHA1_HEX}",', False)
             for k in ("source_commit", "git_blob", "tree", "oid")]
    # The same tables written unquoted, the way an env file writes them. Still exempt.
    rows += [(f"body_sha256={SHA256_HEX}", False), (f"source_commit={SHA1_HEX}", False)]
    # `.env` credentials: the shapes the split was written for, plus the short base64
    # value that only the merged token scores.
    rows += [(f"api_key={SHA1_HEX}", True), (f"secret_key={SHA256_HEX}", True),
             (f"api_key={SPLIT_WINDOW_B64}", True)]
    # Key-shaped prefixes: a digest word does not launder the key in front of it.
    rows += [(f'{k} = "{SHA256_HEX}"', True)
             for k in ("hmac_sha256", "api_key_sha256", "secret_digest", "token_checksum",
                       "session_key_sha256", "password_sha256", "passwd_digest", "auth_checksum")]
    # The S1 bypass lines: ordinary key names that contain a label without being one.
    rows += [(f'{k} = "{SHA1_HEX}"', True)
             for k in ("prev_api_key", "revenue_api_token", "revoked_key", "committee_api_key",
                       "street_service_key", "blobstore_key", "commit_token")]
    rows += [(f'{k} = "{SHA256_HEX}"', True)
             for k in ("digest_auth_secret", "password_hash", "session_hash")]
    # …and ordinary code, which must stay silent however much of it there is.
    rows += [("def add(a, b):", False), ("    return a + b", False),
             ("GREETING = 'hello world'", False), ("# an ordinary comment", False),
             ("from pathlib import Path  # noqa", False)]
    return rows


def test_the_flooding_corpus_reports_exactly_the_lines_it_should(tmp_path):
    corpus = _flood_corpus()
    (tmp_path / "corpus.txt").write_text(
        "\n".join(line for line, _ in corpus) + "\n", encoding="utf-8")
    findings = scan_paths([tmp_path])
    assert {f["kind"] for f in findings} <= {"high_entropy"}
    assert {f["line"] for f in findings} == {i for i, (_, leak) in enumerate(corpus, 1) if leak}
    # One finding per reporting line, and the count is pinned: a rule that starts
    # reporting the attestation half, or stops reporting the credential half, moves it.
    assert len(findings) == 21


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


def plant_rig_markers(root: pathlib.Path) -> pathlib.Path:
    """A scratch tree the scanner recognises as rig itself. No git — see make_rig_checkout.

    ALLOW_PATH_PREFIXES only applies inside one (RIG_CHECKOUT_MARKERS), so a fixture that
    wants to exercise those prefixes has to be one. Planting by the constant rather than
    by two restated path literals is the point for these fixtures: change what identifies
    a rig checkout and they follow. The one test that must NOT follow is the one pinning
    what the constant means — it writes the names out, below.
    """
    root.mkdir(parents=True, exist_ok=True)
    for marker in RIG_CHECKOUT_MARKERS:
        f = root / marker
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# marker\n", encoding="utf-8")
    return root


def make_rig_checkout(root: pathlib.Path) -> pathlib.Path:
    """plant_rig_markers + `git init`: a rig checkout in the ordinary sense."""
    plant_rig_markers(root)
    _git(root, "init", "-q")
    return root


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


def test_the_evidence_allowlist_holds_however_the_tree_is_addressed(tmp_path, monkeypatch):
    """One tree, four ways of naming it, one set of findings.

    `rel` used to be whatever the caller typed. Scanned as `.` a path arrived as
    `evals/evidence/x.json`, whose leading components are the ALLOW_PATH_PREFIXES entry;
    scanned as `/abs/tree` the same file arrived as `/abs/tree/evals/evidence/x.json`,
    whose first component is `/`, so the prefix matched nothing and the whole
    signed-evidence tree reported. Measured on this repository: 264 findings by relative
    path, 501 by absolute, the 237 extra all `high_entropy` under `evals/evidence/`.

    The tree is a repository of its own so the anchor is decided rather than inherited
    from wherever pytest put tmp_path.
    """
    tree = make_rig_checkout(tmp_path / "proj")
    (tree / "evals" / "evidence").mkdir(parents=True)
    (tree / "src").mkdir()
    # Computed, never pasted — a literal high-entropy value in this file would be an
    # added line in this file's own diff scan. The same value sits in both places, so
    # what separates them is the path rule and nothing else.
    entropic = hashlib.sha256(b"rig secret-scan addressing fixture").hexdigest()
    (tree / "evals" / "evidence" / "x.json").write_text(
        json.dumps({"v": entropic}) + "\n", encoding="utf-8")
    (tree / "src" / "app.py").write_text(f'KEY = "{entropic}"\n', encoding="utf-8")

    monkeypatch.chdir(tree)
    by_dot = scan_paths([pathlib.Path(".")])
    by_absolute = scan_paths([tree])
    monkeypatch.chdir(tmp_path)
    by_relative_name = scan_paths([pathlib.Path("proj")])

    assert by_dot == by_absolute == by_relative_name
    # The evidence file is allowlisted every way; the identical value outside it is not,
    # which is what proves the fixture really is high-entropy rather than merely quiet.
    assert [(f["path"], f["kind"]) for f in by_dot] == [("src/app.py", "high_entropy")]

    # And the anchor is the repository root, not the argument: naming the evidence
    # subtree directly still produces the repo-relative path the allowlist reads.
    assert scan_paths([tree / "evals"]) == []
    assert entropy_allowlisted("evals/evidence/x.json", rig_checkout=True)


def test_the_prefixes_are_rig_facts_and_a_nested_checkout_does_not_inherit_them(
        tmp_path, monkeypatch):
    """`invocation_worktree` answers with the INNERMOST repository.

    So a checkout nested inside rig re-roots the prefix: scanning `some/project/`, which
    has its own `.git`, resolves the toplevel to `some/project`, and a leak at
    `some/project/evals/evidence/leak.txt` arrives as `evals/evidence/leak.txt` — the
    exact shape ALLOW_PATH_PREFIXES silences, under a repository the prefix was never
    about. Naming alone must not buy the exemption; being rig must.
    """
    outer = make_rig_checkout(tmp_path / "rig")
    inner = outer / "some" / "project" / "evals" / "evidence"
    inner.mkdir(parents=True)
    _git(outer / "some" / "project", "init", "-q")
    leak = hashlib.sha256(b"rig secret-scan nested checkout fixture").hexdigest()
    (inner / "leak.txt").write_text(f"token={leak}\n", encoding="utf-8")

    # Addressed at the nested repository, which is where the prefix used to re-root.
    assert [(f["path"], f["kind"]) for f in scan_paths([outer / "some" / "project"])] == [
        ("evals/evidence/leak.txt", "high_entropy")]
    # …and from the outer rig checkout, where the path does not match the prefix anyway.
    monkeypatch.chdir(outer)
    assert ("some/project/evals/evidence/leak.txt", "high_entropy") in [
        (f["path"], f["kind"]) for f in scan_paths([pathlib.Path(".")])]


def test_a_foreign_repo_with_the_same_directory_names_gets_no_exemption(tmp_path):
    """A name is not an address — the critique this module makes of a bare `corpora`.

    Nothing stops another project from having a top-level `evals/evidence/` or
    `skills/engine/corpora/`; there the entry is not a considered exemption but a
    coincidence of naming that would silence the heuristic over a whole tree. Outside a
    rig checkout the answer is "no exemption", never "some other exemption" — over-report
    is the safe direction for a scanner whose findings block an accept.
    """
    foreign = tmp_path / "someone-elses-project"
    (foreign / "evals" / "evidence").mkdir(parents=True)
    (foreign / "skills" / "engine" / "corpora").mkdir(parents=True)
    _git(foreign, "init", "-q")
    assert not is_rig_checkout(foreign)
    leak = hashlib.sha256(b"rig secret-scan foreign repo fixture").hexdigest()
    (foreign / "evals" / "evidence" / "x.json").write_text(
        json.dumps({"v": leak}) + "\n", encoding="utf-8")
    (foreign / "skills" / "engine" / "corpora" / "y.json").write_text(
        json.dumps({"v": leak}) + "\n", encoding="utf-8")

    assert sorted((f["path"], f["kind"]) for f in scan_paths([foreign])) == [
        ("evals/evidence/x.json", "high_entropy"),
        ("skills/engine/corpora/y.json", "high_entropy")]
    # The any-depth names are reserved by the tools that make them, so they still hold.
    (foreign / "node_modules").mkdir()
    (foreign / "node_modules" / "z.js").write_text(f'var k="{leak}"\n', encoding="utf-8")
    assert len(scan_paths([foreign])) == 2


def test_a_file_outside_any_repository_keeps_the_path_it_was_addressed_by(tmp_path,
                                                                         monkeypatch):
    """Naming files repository-relative must not shorten them to a basename.

    Outside a repository there is no root to be relative to, so the answer is the path
    the operator asked about. `conf.json` has thrown away the half that says which one.
    """
    deep = tmp_path / "deep" / "nested"
    deep.mkdir(parents=True)
    leak = hashlib.sha256(b"rig secret-scan outside-a-repo fixture").hexdigest()
    (deep / "conf.json").write_text(json.dumps({"v": leak}) + "\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert [f["path"] for f in scan_paths([pathlib.Path("deep/nested/conf.json")])] == [
        "deep/nested/conf.json"]
    assert [f["path"] for f in scan_paths([pathlib.Path("./deep/nested/conf.json")])] == [
        "deep/nested/conf.json"]
    # A directory root outside a repository is likewise relative to what was scanned.
    assert [f["path"] for f in scan_paths([pathlib.Path(".")])] == ["deep/nested/conf.json"]


def test_a_path_that_walks_upward_is_refused_before_any_rule_is_consulted():
    """Ordering the `..` guard after ALLOW_DIR_PARTS made the guard depend on position."""
    assert not entropy_allowlisted("node_modules/../secrets.env")
    assert not entropy_allowlisted(".git/../secrets.env")
    assert not entropy_allowlisted("evals/evidence/../elsewhere/x.json")
    assert not entropy_allowlisted("x/../y.lock")
    # …while the same names without the escape are exempt exactly as before.
    assert entropy_allowlisted("node_modules/pkg/dist/index.js")
    assert entropy_allowlisted("y.lock")


def test_a_rig_source_tree_that_is_not_a_git_working_tree_keeps_its_exemption(tmp_path,
                                                                             monkeypatch):
    """No `git init` here, on purpose — that is the whole fixture.

    git says where a root is; it does not say whose tree it is. A `git archive` extract,
    a release tarball, a vendored copy: same bytes, same layout, no `.git`. `scan_root`
    used to answer a hardcoded False there and drop the exemption, so scanning the
    extract of this repository produced 574 findings against a checkout's 264 — the
    extra 237 under `evals/evidence/` and 73 under `skills/engine/corpora/`, which is
    precisely the pile ALLOW_PATH_PREFIXES exists to remove, arriving through a second
    door.
    """
    entropic = hashlib.sha256(b"rig secret-scan no-git-extract fixture").hexdigest()

    def tree(root: pathlib.Path, *, markers: bool) -> pathlib.Path:
        if markers:
            plant_rig_markers(root)
        (root / "evals" / "evidence").mkdir(parents=True)
        (root / "src").mkdir(parents=True)
        (root / "evals" / "evidence" / "x.json").write_text(
            json.dumps({"v": entropic}) + "\n", encoding="utf-8")
        (root / "src" / "app.py").write_text(f'KEY = "{entropic}"\n', encoding="utf-8")
        assert not (root / ".git").exists(), "the fixture is only a fixture without git"
        return root

    extract = tree(tmp_path / "rig-extract", markers=True)
    assert is_rig_checkout(extract)
    assert scan_root(extract) == (None, True)
    assert [(f["path"], f["kind"]) for f in scan_paths([extract])] == [
        ("src/app.py", "high_entropy")]
    # …and by the relative address too, which is what the operator actually types.
    monkeypatch.chdir(extract)
    assert scan_paths([pathlib.Path(".")]) == scan_paths([extract])

    # An unmarked tree in the same no-repository situation gets no exemption.
    foreign = tree(tmp_path / "someone-elses-extract", markers=False)
    assert scan_root(foreign) == (None, False)
    assert sorted((f["path"], f["kind"]) for f in scan_paths([foreign])) == [
        ("evals/evidence/x.json", "high_entropy"), ("src/app.py", "high_entropy")]


def test_both_markers_are_required_and_neither_alone_will_do():
    """The names are written out here rather than read from the constant.

    Every other fixture plants by RIG_CHECKOUT_MARKERS so it follows a redefinition;
    this one is the definition, so it has to disagree with the constant when the constant
    changes. Reducing the tuple to a single entry, or relaxing the `all()` to `any()`,
    leaves the rest of this module green and fails here.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        assert not is_rig_checkout(root)

        bricks = root / "skills" / "engine" / "BRICKS.md"
        bricks.parent.mkdir(parents=True)
        bricks.write_text("# inventory\n", encoding="utf-8")
        assert is_rig_checkout(root) is False, "one marker is a file a project could have"

        scanner = root / "rig_workbench" / "workbench" / "secrets.py"
        scanner.parent.mkdir(parents=True)
        scanner.write_text("# scanner\n", encoding="utf-8")
        assert is_rig_checkout(root) is True

        # …and neither one alone, taken from the other side.
        bricks.unlink()
        assert is_rig_checkout(root) is False
        # A directory of the right name is not the file the marker names.
        bricks.mkdir()
        assert is_rig_checkout(root) is False


def test_every_sink_asks_before_it_exempts(tmp_path):
    """The default lives on entropy_allowlisted; each sink has to actually forward it.

    Flipping any one of scan_line / scan_file / scan_diff_text back to `True` while
    entropy_allowlisted stays `False` is invisible to a test that only ever calls
    entropy_allowlisted — so each sink is asked here, with no keyword, about the one path
    shape the prefixes would silence.
    """
    entropic = hashlib.sha256(b"rig secret-scan sink default fixture").hexdigest()
    rel = "evals/evidence/x.json"
    body = json.dumps({"v": entropic})

    # scan_line
    assert [f["kind"] for f in scan_line(body, rel, 1)] == ["high_entropy"]
    assert scan_line(body, rel, 1, rig_checkout=True) == []

    # scan_diff_text — the whole added-file diff, as the gate sees one
    diff = ("diff --git a/evals/evidence/x.json b/evals/evidence/x.json\n"
            "--- /dev/null\n"
            f"+++ b/{rel}\n"
            "@@ -0,0 +1,1 @@\n"
            f"+{body}\n")
    findings = scan_diff_text(diff)
    assert [(f["path"], f["kind"]) for f in findings] == [(rel, "high_entropy")]
    assert scan_diff_text(diff, rig_checkout=True) == []

    # scan_file
    f = tmp_path / "x.json"
    f.write_text(body + "\n", encoding="utf-8")
    assert [x["kind"] for x in scan_file(f, rel)] == ["high_entropy"]
    assert scan_file(f, rel, rig_checkout=True) == []


def test_a_linked_worktree_is_its_own_root_and_keeps_the_exemption(tmp_path):
    """In a linked worktree `.git` is a FILE, and the toplevel is the worktree itself.

    This is where rig actually runs — every task works in one — so the case that decides
    whether `evals/evidence/` is exempt during a task deserves a fixture rather than an
    argument. `invocation_worktree` asks git rather than looking for a `.git` directory,
    which is why it holds; nothing here would notice if that changed.
    """
    main = make_rig_checkout(tmp_path / "main")
    (main / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")

    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "task", str(linked))
    assert (linked / ".git").is_file(), "a linked worktree keeps a .git file, not a dir"

    assert scan_root(linked) == (".", True)
    entropic = hashlib.sha256(b"rig secret-scan linked worktree fixture").hexdigest()
    (linked / "evals" / "evidence").mkdir(parents=True)
    (linked / "evals" / "evidence" / "r.json").write_text(
        json.dumps({"v": entropic}) + "\n", encoding="utf-8")
    (linked / "src").mkdir()
    (linked / "src" / "app.py").write_text(f'KEY = "{entropic}"\n', encoding="utf-8")
    assert [(f["path"], f["kind"]) for f in scan_paths([linked])] == [
        ("src/app.py", "high_entropy")]


def test_the_gate_and_the_streaming_preview_agree_on_whose_evidence_is_exempt(tmp_path):
    """One sink, one answer, measured on both lanes of the same worktree.

    `stream-checks` is advertised as a preview of the gate's verdict, and it had copied
    `scan_worktree_diff`'s call sequence rather than calling it. The copy never measured
    whether the worktree was a rig checkout, so the fail-open default handed rig's
    ALLOW_PATH_PREFIXES to every repository rig is pointed at: on a foreign task the gate
    failed `evals/evidence/r.json` while its own preview printed no hints. A preview that
    contradicts the verdict is worse than no preview.
    """
    from rig_workbench.workbench import streaming

    # Computed, never pasted, like every other fixture here.
    leak = hashlib.sha256(b"rig secret-scan two-lane fixture").hexdigest()

    def probe(root: pathlib.Path, *, rig: bool) -> tuple[list[dict], list[dict]]:
        """(what the gate's sink says, what the streaming lane says) for one worktree."""
        if rig:
            make_rig_checkout(root)
        else:
            root.mkdir(parents=True)
            _git(root, "init", "-q")
        (root / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
        (root / "evals" / "evidence").mkdir(parents=True)
        (root / "evals" / "evidence" / "r.json").write_text(
            json.dumps({"v": leak}) + "\n", encoding="utf-8")
        run_d = root.parent / f"{root.name}-run"   # no recorded reviews: the anchor lane
        run_d.mkdir()                              # has nothing to say about this
        return scan_worktree_diff(root, base), streaming._scan_once(root, base, run_d)["secrets"]

    foreign_gate, foreign_stream = probe(tmp_path / "someone-elses-task", rig=False)
    assert [(f["path"], f["kind"]) for f in foreign_gate] == [
        ("evals/evidence/r.json", "high_entropy")]
    assert foreign_stream == foreign_gate

    rig_gate, rig_stream = probe(tmp_path / "rig-task", rig=True)
    assert rig_gate == []
    assert rig_stream == rig_gate


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
    assert entropy_allowlisted(evidence, rig_checkout=True)
    assert scan_line(f'"result_sha256": "{RANDOM_B64_40}"', evidence, 1,
                     rig_checkout=True) == []


def test_the_evidence_allowlist_is_anchored_and_still_reports_real_credentials():
    """Two ways this could become a hiding place, both closed.

    `evidence` is too ordinary a directory name to silence wherever it appears, so
    the rule is anchored at the repository root rather than matched at any depth like
    `ALLOW_DIR_PARTS`. And only the entropy heuristic is silenced: a vendor-formatted
    credential written into an evidence file is still a leak and still reported.
    """
    assert not entropy_allowlisted("src/evidence/collected.json", rig_checkout=True)
    assert not entropy_allowlisted("docs/evals/evidence/example.md", rig_checkout=True)
    # Compared as path components rather than as a string prefix, so a path that only
    # starts with the tree cannot borrow its silence. Raised by an adversarial review
    # of this change: git never hands these callers a `..`, but proving that no caller
    # ever will is more expensive, and more fragile, than not depending on it.
    assert not entropy_allowlisted("evals/evidence/../elsewhere/current.json",
                                   rig_checkout=True)
    assert not entropy_allowlisted("evals/evidence-notes/current.json", rig_checkout=True)
    # The forms that are legitimately the same path still resolve to it.
    assert entropy_allowlisted("./evals/evidence/case/current.json", rig_checkout=True)
    assert entropy_allowlisted("evals\\evidence\\case\\current.json", rig_checkout=True)
    # A third way it could become a hiding place, closed by the default: a caller that
    # never established which repository this path belongs to gets no exemption at all.
    assert not entropy_allowlisted("evals/evidence/case/current.json")

    evidence = "evals/evidence/some-case/current.json"
    # Through SAMPLES rather than the literal this line used to carry: adding
    # `rig_checkout=True` makes this an ADDED line, and an added line holding a real
    # token shape is a finding in this file's own diff scan — which is how the scanner
    # caught it. The rule the rest of this module already follows now applies here too.
    findings = scan_line(f'"note": "{SAMPLES[0][1]}"', evidence, 2, rig_checkout=True)
    assert [f["kind"] for f in findings] == ["aws_access_key"]
