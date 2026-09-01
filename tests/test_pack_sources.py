"""Installing a pack from a named source, pinned to a commit (#523, slice S2).

The source is a real git repository on disk rather than a mocked one. What S2 has to get
right is almost entirely in git's behaviour — what a tag resolves to, what happens when it
moves, what a failure says on stderr — and a fake remote would let every one of those pass
while the real path stayed broken.
"""

import copy
import json
import pathlib
import subprocess

import pytest

from rig_workbench.packs.cli import cmd_pack
from rig_workbench.packs.installer import install_pack
from rig_workbench.packs.lock import LOCK_NAME, read_lock, refuse_credentials
from rig_workbench.packs.manifest import PACK_SCHEMA_VERSION, canonical, digest
from rig_workbench.packs.model import (ASSET_DIRS, AuthFailed, PackError, RevisionNotFound,
                                       SourceUnreachable)
from rig_workbench.packs.sources import (parse_spec, read_sources, resolve_revision,
                                         validate_source, verify_pin, write_sources)
from test_eval_cases import valid_case

RECIPE = "---\nname: hello\nsteps: []\n---\n"


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _pack_files(pack: pathlib.Path, pack_id: str) -> None:
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    (pack / "recipes" / "hello.md").write_text(RECIPE, encoding="utf-8")
    case = copy.deepcopy(valid_case())
    case["id"] = "hello-case"
    case["prompt_surfaces"] = ["recipe:hello"]
    case_path = pack / "evals" / "cases" / "hello-case" / "case.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(canonical(case), encoding="utf-8")
    assets = {kind: [] for kind in ASSET_DIRS}
    assets["recipe"] = ["recipes/hello.md"]
    assets["eval-case"] = ["evals/cases/hello-case/case.json"]
    manifest = {
        "pack_schema_version": PACK_SCHEMA_VERSION, "id": pack_id, "type": "skill",
        "version": "1.4.0", "kind": "project", "engine": "*", "dependencies": [],
        "assets": assets,
        "hashes": {item: digest(pack / item) for paths in assets.values() for item in paths},
        "provenance": {"source": "test", "created_at": "2026-08-27T00:00:00+00:00"},
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical({
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.4.0",
        "engine": "*", "platforms": ["any"],
    }), encoding="utf-8")


@pytest.fixture
def remote(tmp_path):
    """A git repository holding one pack at tag v1.4.0."""
    repo = tmp_path / "remote" / "rig-pack-northwind"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "packs@example.invalid")
    _git(repo, "config", "user.name", "packs")
    _pack_files(repo, "northwind")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "pack 1.4.0")
    _git(repo, "tag", "v1.4.0")
    return repo


@pytest.fixture
def project(tmp_path, remote):
    """A project declaring the repository above as the source `product`."""
    project = tmp_path / "project"
    project.mkdir()
    write_sources(project, {"product": {
        "scheme": "git+file",
        "url": str(remote.parent / "rig-pack-{pack}"),
    }})
    return project


def test_a_spec_installs_from_a_named_source_and_the_lock_pins_the_commit(project, remote):
    """The whole point of the slice: `product:northwind@1.4.0` reaches a private-style remote,
    and what lands is pinned to a commit rather than to whatever the tag says next week."""
    result = install_pack("product:northwind@1.4.0", scope="project", project=project,
                          allow_unverified=True)
    assert result.manifest["id"] == "northwind"

    entry, = read_lock(result.path.parent)["packs"]
    assert entry["source"]["type"] == "git"
    assert entry["source"]["path"] == "product:northwind@1.4.0"
    assert entry["source"]["source_id"] == "product"
    assert entry["source"]["revision"] == _git(remote, "rev-parse", "v1.4.0^{commit}")
    assert len(entry["source"]["sha256"]) == 64


def test_the_lock_records_the_source_name_and_never_the_url(project):
    """A lock that carried the URL would carry however the remote was addressed — including
    a credential — and would have to be rewritten by every consumer if the pack moved."""
    result = install_pack("product:northwind@1.4.0", scope="project", project=project,
                          allow_unverified=True)
    text = (result.path.parent / LOCK_NAME).read_text(encoding="utf-8")
    assert "product" in text
    assert "rig-pack-northwind" not in text
    assert "://" not in text


def test_a_moved_tag_is_refused_rather_than_installed(project, remote, monkeypatch):
    """`@1.4.0` has to mean one thing. Moving the tag between resolving it and fetching it is
    the race this refuses; without the re-check the fetch would quietly serve the new commit
    under the old version.

    The window is real but too narrow to hit by waiting, so the tag is moved from inside the
    fetch — patched on the installer's own binding, since patching the source module would
    leave the installer holding the original and the test would pass while proving nothing.
    """
    from rig_workbench.packs import installer as installer_module
    from rig_workbench.packs import sources as sources_module

    stale = _git(remote, "rev-parse", "v1.4.0^{commit}")

    def move_tag_then_fetch(source, pack, version, revision, destination):
        (remote / "recipes" / "hello.md").write_text(
            "---\nname: hello\nsteps: []\n---\n\nmoved\n", encoding="utf-8")
        _git(remote, "add", "-A")
        _git(remote, "commit", "--quiet", "-m", "different 1.4.0")
        _git(remote, "tag", "-f", "v1.4.0")
        return sources_module.fetch_revision(source, pack, version, revision, destination)

    monkeypatch.setattr(installer_module, "fetch_revision", move_tag_then_fetch)
    with pytest.raises(RevisionNotFound, match="moved while installing"):
        install_pack("product:northwind@1.4.0", scope="project", project=project,
                     allow_unverified=True)
    assert _git(remote, "rev-parse", "v1.4.0^{commit}") != stale


def test_a_missing_tag_is_revision_not_found_and_not_a_generic_failure(project):
    """The four failures the issue asks to distinguish are only useful if they arrive apart:
    a missing version is the author's problem, an unreachable host is the network's."""
    with pytest.raises(RevisionNotFound, match="no tag v9.9.9"):
        install_pack("product:northwind@9.9.9", scope="project", project=project,
                     allow_unverified=True)


def test_an_unreachable_source_is_not_reported_as_a_missing_version(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    write_sources(project, {"product": {
        "scheme": "git+file", "url": str(tmp_path / "nowhere" / "rig-pack-{pack}")}})
    with pytest.raises(SourceUnreachable) as raised:
        install_pack("product:northwind@1.4.0", scope="project", project=project,
                     allow_unverified=True)
    assert raised.value.reason == "source-unreachable"
    assert not isinstance(raised.value, RevisionNotFound)


def test_an_undeclared_source_names_what_is_declared(tmp_path):
    """A spec that does not resolve should say so in terms of the file the person edits."""
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(PackError, match="not declared in .rig/sources.json"):
        install_pack("absent:northwind@1.4.0", scope="project", project=project,
                     allow_unverified=True)


def test_reasons_are_distinct_and_machine_readable():
    """Every refusal a caller might branch on carries a stable label, and the ones that want
    opposite responses are not the same class."""
    assert AuthFailed("x").reason == "auth-failed"
    assert SourceUnreachable("x").reason == "source-unreachable"
    assert RevisionNotFound("x").reason == "revision-not-found"
    assert PackError("x").reason == "invalid-pack"
    assert not isinstance(AuthFailed("x"), SourceUnreachable)


def test_verify_pin_separates_a_moved_tag_from_a_source_that_cannot_be_read(project, remote):
    """`verify-sources` is where a moved tag stops being invisible, and it has to say which
    kind of trouble it found: logging in fixes one of these and never fixes the other."""
    result = install_pack("product:northwind@1.4.0", scope="project", project=project,
                          allow_unverified=True)
    entry, = read_lock(result.path.parent)["packs"]
    declared = read_sources(project)

    assert verify_pin(declared["product"], entry) == "ok"

    (remote / "recipes" / "hello.md").write_text(RECIPE + "\nmoved\n", encoding="utf-8")
    _git(remote, "add", "-A")
    _git(remote, "commit", "--quiet", "-m", "moved")
    _git(remote, "tag", "-f", "v1.4.0")
    assert verify_pin(declared["product"], entry) == "digest-mismatch"

    assert verify_pin({"scheme": "git+file", "url": "/nowhere/{pack}"}, entry) \
        == "source-unreachable"


def test_the_lock_writer_refuses_a_credential_rather_than_trusting_its_callers():
    """The rule is enforced where the bytes are written. A caller can be careful and still be
    wrong — a URL that carried userinfo, a field nobody thought about — and a rule that needs
    nobody to make that mistake is a wish, not a rule."""
    token = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
    with pytest.raises(PackError, match="would persist a credential"):
        refuse_credentials(json.dumps({"url": f"https://x:{token}@host/r"}).encode(),
                           where=LOCK_NAME)
    # The complaint does not echo the secret: reporting it would write it somewhere new.
    try:
        refuse_credentials(json.dumps({"url": f"https://x:{token}@host/r"}).encode(),
                           where=LOCK_NAME)
    except PackError as error:
        assert token not in str(error)
    refuse_credentials(b'{"source_id": "product", "revision": "a" }', where=LOCK_NAME)


def test_a_source_url_may_not_embed_credentials_or_omit_the_pack_placeholder():
    validate_source("product", {"scheme": "git+ssh", "url": "git@host:acme/rig-{pack}.git"})
    with pytest.raises(PackError, match="must not embed credentials"):
        validate_source("product", {"scheme": "git+https",
                                    "url": "https://user:token@host/rig-{pack}.git"})
    with pytest.raises(PackError, match=r"template containing \{pack\}"):
        validate_source("product", {"scheme": "git+https", "url": "https://host/fixed.git"})
    with pytest.raises(PackError, match="reserved"):
        validate_source("domain", {"scheme": "git+https", "url": "https://host/{pack}.git"})


@pytest.mark.parametrize("spec,expected", [
    ("product:northwind@1.4.0", ("product", "northwind", "1.4.0")),
    ("product:northwind@1.4.0-rc.1", ("product", "northwind", "1.4.0-rc.1")),
    ("domain:sales", None),
    ("domain:sales@1.0.0", None),          # the builtin alias namespace keeps its meaning
    ("official:thing@1.0.0", None),
    ("./local/path", None),
    ("product:northwind", None),              # a spec without a version is not pinned
    ("product:northwind@1.4", None),
])
def test_spec_parsing_leaves_local_paths_and_builtin_aliases_alone(spec, expected):
    """A named source must not quietly capture the shapes install already accepted."""
    assert parse_spec(spec) == expected


def test_source_cli_round_trips_and_refuses_a_duplicate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cmd_pack(["source", "add", "product", "--scheme", "git+https",
                     "--url", "https://host/rig-pack-{pack}.git"]) == 0
    assert cmd_pack(["source", "list"]) == 0
    assert "product" in capsys.readouterr().out
    assert cmd_pack(["source", "add", "product", "--scheme", "git+https",
                     "--url", "https://host/other-{pack}.git"]) != 0
    assert cmd_pack(["source", "remove", "product"]) == 0
    assert read_sources(tmp_path) == {}


def test_resolve_revision_returns_a_commit_for_a_real_tag(remote):
    source = {"scheme": "git+file", "url": str(remote.parent / "rig-pack-{pack}")}
    assert resolve_revision(source, "northwind", "1.4.0") \
        == _git(remote, "rev-parse", "v1.4.0^{commit}")
