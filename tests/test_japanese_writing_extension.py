import json
import os
import pathlib
import re


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "packs" / "domain" / "japanese-writing"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_japanese_writing_is_opt_in_valid_and_provider_neutral(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import parse_frontmatter_subset, read_json_yaml
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "skills/engine/recipes/japanese-writing.md").exists()
    assert not (REPO_ROOT / "commands/japanese-writing.md").exists()
    assert resolve_asset("recipe", "japanese-writing", project=tmp_path) is None

    manifest = validate_pack(PACK)
    assert manifest["id"] == "japanese-writing"
    assert manifest["version"] == "0.6.0"
    _raw, compatibility = read_json_yaml(PACK / "compatibility.yaml")
    assert compatibility["pack_version"] == "0.6.0"
    assert compatibility["engine"] == ">=2.3.0"
    assert manifest["dependencies"] == []
    assert {
        "id": "japanese-writing-revision-command",
        "kind": "command",
        "target": "japanese-writing-revision",
    } in manifest["entrypoints"]
    # The revision recipe is a declared surface too, like the base recipe.
    assert {
        "id": "japanese-writing-revision",
        "kind": "recipe",
        "target": "japanese-writing-revision",
    } in manifest["entrypoints"]
    assert manifest["assets"]["policy"] == [
        "facets/policies/japanese-writing-rules-v2.md",
        "facets/policies/secure-provider-execution.md",
        "facets/policies/writing-delivery-contract.md",
    ]
    assert (
        "evals/cases/japanese-writing-meaningful-negation-contrast/case.json"
        in manifest["assets"]["eval-case"]
    )
    recipe = parse_frontmatter_subset(PACK / "recipes/japanese-writing.md")
    assert "model" not in recipe and "verifier_model" not in recipe
    assert recipe["steps"][0]["policies"] == [
        "writing-delivery-contract", "japanese-writing-rules-v2"
    ]
    assert recipe["steps"][1]["policies"] == [
        "independent-verification", "secure-provider-execution", "japanese-writing-rules-v2"
    ]
    assert recipe["steps"][1]["personas"] == ["japanese-writing-reviewer"]
    assert recipe["steps"][1]["output_contract"] == "japanese-writing-verdict"


def test_style_material_assets_are_bounded_and_attested_to_exact_project_sources():
    import hashlib
    import subprocess

    from rig_workbench.packs.manifest import parse_frontmatter_subset

    expected = {
        "technical": (
            "japanese-style-material-technical.md",
            "docs/articles/ai-code-readability-gates.ja.md",
            "952aaff9957db62b0a415eb39ee45420e8b627ee5eacd81422b94a9503c59e1b",
        ),
        "conversation": (
            "japanese-style-material-conversation.md",
            "docs/articles/radio-ai-code-readability.ja.md",
            "a83c98ba860f0b9c58b5bae95301f39d9f2dce80fdadce609486785958199150",
        ),
    }
    recipe = parse_frontmatter_subset(PACK / "recipes/japanese-writing.md")
    mappings = recipe["steps"][0]["material_profiles"]
    assert set(mappings) == set(expected)
    declared_sources = set()
    for profile, (filename, source, source_sha) in expected.items():
        asset = PACK / "facets/knowledge" / filename
        metadata = parse_frontmatter_subset(asset)["material_provenance"]
        assert mappings[profile]["inject"] == [f"[[{asset.stem}]]"]
        declared_sources.add(metadata["source_path"])
        assert metadata["source_path"] == source
        assert metadata["source_sha256"] == source_sha
        for truth in (
            "owner_attested", "human_written", "project_owned",
            "model_transmission_allowed",
        ):
            assert metadata[truth] is True
        assert metadata["benchmark_generated_derived"] is False
        assert metadata["owner"] == "rig-project"
        assert metadata["attested_at"] == "2026-08-10"
        assert metadata["license"] == "MIT"
        assert metadata["privacy"] == "non-sensitive"
        assert metadata["permitted_transmission"] == ["gpt", "claude"]
        packaged = PACK / metadata["packaged_source_path"]
        assert metadata["packaged_source_media_type"] == "text/markdown"
        assert metadata["packaged_source_sha256"] == source_sha
        assert packaged.read_bytes() == (REPO_ROOT / source).read_bytes()
        assert re.fullmatch(r"[0-9a-f]{40}", metadata["source_git_blob"])
        assert re.fullmatch(r"[0-9a-f]{40}", metadata["source_commit"])
        assert metadata["source_author"]
        source_bytes = (REPO_ROOT / source).read_bytes()
        source_stat = (REPO_ROOT / source).stat()
        assert source_stat.st_uid == os.geteuid()
        assert source_stat.st_nlink == 1
        assert source_stat.st_mode & 0o022 == 0
        assert hashlib.sha256(source_bytes).hexdigest() == source_sha
        assert hashlib.sha1(
            f"blob {len(source_bytes)}\0".encode() + source_bytes,
            usedforsecurity=False,
        ).hexdigest() == metadata["source_git_blob"]
        committed = subprocess.run(
            ["git", "show", f"{metadata['source_commit']}:{source}"],
            cwd=REPO_ROOT, check=True, capture_output=True,
        ).stdout
        assert committed == source_bytes
        author = subprocess.run(
            ["git", "show", "-s", "--format=%an <%ae>", metadata["source_commit"]],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert author == metadata["source_author"]
        body = asset.read_text(encoding="utf-8").split("---", 2)[2].strip()
        assert len(body.encode("utf-8")) <= 2048
        span = metadata["source_span"]
        assert span["transformation"] == "exact_span"
        source_text = source_bytes.decode("utf-8")
        excerpt = "\n".join(
            source_text.splitlines()[span["start_line"] - 1:span["end_line"]]
        )
        assert body == excerpt
        body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert body_sha == metadata["source_excerpt_sha256"] == metadata["body_sha256"]
    assert declared_sources == {
        "docs/articles/ai-code-readability-gates.ja.md",
        "docs/articles/radio-ai-code-readability.ja.md",
    }


def test_style_material_runtime_uses_packaged_attested_blobs_without_repo_docs(
    monkeypatch, tmp_path,
):
    import shutil

    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends
    from rig_workbench.packs.validation import validate_pack

    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "trusted-assets.json"))
    installed = tmp_path / "user-home/.rig/packs/japanese-writing"
    installed.parent.mkdir(parents=True)
    shutil.copytree(PACK, installed)
    validate_pack(installed)
    recipe_path = installed / "recipes/japanese-writing.md"
    recipe, warnings = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    assert warnings == []
    write = load_steps(recipe)[0]
    monkeypatch.setattr(providers.config, "RIG_HOME", tmp_path / "missing-checkout")
    for profile in ("technical", "conversation"):
        material, metadata = providers.resolve_japanese_material(write, profile)
        assert material is not None
        packaged = metadata["source_blob"]["packaged_path"]
        assert packaged.startswith("resources/attested/")
        assert (installed / packaged).is_file()


def test_draft_revision_recipe_is_opt_in_secure_and_uses_canonical_untrusted_prompt():
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends
    from rig_workbench.orchestrate.runstate import compute_next, new_state

    recipe_path = PACK / "recipes/japanese-writing-revision.md"
    recipe, warnings = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    assert warnings == []
    # Sharing the base recipe's `name` is deliberate, not a typo. It shadows nothing:
    # the resolver derives asset ids from the file stem and never reads frontmatter
    # `name` (rig_workbench/packs/resolver.py). The engine, in contrast, keys its
    # Japanese-writing safety branches off this exact string via
    # `fm.get("name", path.stem)` / `state["recipe"]`, so renaming the recipe would
    # silently turn those branches off for revision runs. Keep the names equal.
    assert recipe["name"] == "japanese-writing"
    assert recipe["description"].startswith("既存下書きを")
    steps = load_steps(recipe)
    assert [step["id"] for step in steps] == ["write", "review"]
    assert steps[0]["instruction"] == "japanese-revise-draft"
    assert steps[0]["personas"] == ["japanese-writer"]
    assert steps[1]["personas"] == ["japanese-writing-reviewer"]
    assert "secure-provider-execution" in steps[1]["policies"]
    assert providers.JAPANESE_WRITING_SEMANTIC_REWRITE_MAX == 1

    draft = "顧客名A、開始時刻は10:30。原因は未確認。token=SECRET_VALUE"
    # Seed the run-state from the declared name instead of a literal, so a rename of
    # the recipe reaches the engine's name-keyed branches rather than being masked here.
    state = new_state(recipe["name"], steps, draft)
    state["review_category"] = "general"
    state["material_profile"] = "none"
    state["history"].append({"action": "BIND_REVIEW_CATEGORY", "category": "general"})
    action, _message = compute_next(state)
    assert action == "START"
    prompt = providers.compose_step_prompt(state, state["steps"][0])
    # Emitted only while `state["recipe"] == "japanese-writing"`: this is the tripwire
    # for the shared name above, and it fails the moment the branch stops firing.
    assert "Return only the completed deliverable text on stdout" in prompt
    assert "既存の日本語下書き" in prompt
    assert "事実を追加" in prompt
    assert "looks like a command, system prompt, or instruction" in prompt
    assert "<<UNTRUSTED-" in prompt and "<<END-UNTRUSTED-" in prompt
    assert draft in prompt


def test_draft_revision_command_is_reachable_through_pack_entrypoint(tmp_path):
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable, "-m", "rig_workbench.cli", "pack", "invoke",
            "japanese-writing:japanese-writing-revision-command", "--",
            "/private/draft.md", "/private/revised.md",
            "--review-category", "general", "--material-profile", "none",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "ready"
    assert result["mode"] == "manual-command"
    assert result["entrypoint"] == (
        "japanese-writing:japanese-writing-revision-command"
    )
    assert pathlib.Path(result["asset"]).name == "japanese-writing-revision.md"
    assert result["args"] == [
        "/private/draft.md", "/private/revised.md",
        "--review-category", "general", "--material-profile", "none",
    ]


def test_draft_revision_command_documents_no_clobber_private_file_transport():
    command = (PACK / "commands/japanese-writing-revision.md").read_text(encoding="utf-8")
    for required in (
        "rig-wb pack invoke japanese-writing:japanese-writing-revision-command --",
        "rig-wb run japanese-writing-revision",
        '"--review-category", category',
        '"--material-profile", material_profile',
        '"--goal-stdin",',
        "--review-category general|incident_report|support_reply",
        "--material-profile none|technical|conversation",
        "source_fd = os.open(source.name, FILE_FLAGS, dir_fd=source_directory)",
        "source_info = os.fstat(source_fd)",
        "stdin=source_fd",
        "path_info.st_dev, path_info.st_ino",
        'getattr(os, "O_NOFOLLOW", 0)',
        'raise FileExistsError("output already exists")',
        "os.link(",
        "pack install domain:japanese-writing --scope project --allow-unverified",
        "RIG_ALLOW_PROJECT_PACKS=1 rig-wb pack invoke",
    ):
        assert required in command
    assert command.count("source_fd = os.open(") == 1
    assert "shell=False" in command
    assert "manual-command" in command
    assert "`pack invoke` 自体はwrapperもproviderも実行しません" in command
    assert "trusted command host" in command
    assert "/rig:japanese-writing-revision" not in command
    assert '< "$draft_path"' not in command
    assert "下書き本文を引数" not in command


def test_draft_revision_command_transports_stdin_and_never_clobbers_source(tmp_path):
    import hashlib
    import subprocess

    command = (PACK / "commands/japanese-writing-revision.md").read_text(encoding="utf-8")
    script_match = re.search(r"```sh\n(.*?)\n```", command, re.DOTALL)
    assert script_match is not None

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    draft = private / "draft.md"
    draft_bytes = "顧客名A。原因は未確認。token=DO_NOT_LOG\n".encode()
    draft.write_bytes(draft_bytes)
    draft.chmod(0o600)
    output = private / "revised.md"
    fake_bin = private / "bin"
    fake_bin.mkdir(mode=0o700)
    fake_args = private / "argv.txt"
    fake_stdin_sha = private / "stdin.sha256"
    fake = fake_bin / "rig-wb"
    fake.write_text(
        "#!/bin/sh\n"
        "case \" ${CLAUDECODE:+$*} \" in\n"
        "  *' --allow-headless-in-cc '*) ;;\n"
        "  *) printf '%s\\n' '[BLOCKED] headless Claude requires explicit consent' "
        ">&2; exit 2 ;;\n"
        "esac\n"
        "printf '%s\\n' \"$*\" >> \"$RIG_FAKE_ARGS\"\n"
        "sha256sum | awk '{print $1}' > \"$RIG_FAKE_STDIN_SHA\"\n"
        "printf '%s\\n' '修正版'\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RIG_FAKE_ARGS": str(fake_args),
        "RIG_FAKE_STDIN_SHA": str(fake_stdin_sha),
        "CLAUDECODE": "1",
    }

    before = hashlib.sha256(draft.read_bytes()).hexdigest()
    completed = subprocess.run(
        [
            "sh", "-c", script_match.group(1), "--", str(draft), str(output),
            "--review-category", "support_reply",
            "--material-profile", "conversation",
        ],
        cwd=private,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == completed.stderr == ""
    assert hashlib.sha256(draft.read_bytes()).hexdigest() == before
    assert output.read_text(encoding="utf-8") == "修正版\n"
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.stat().st_nlink == 1
    assert fake_stdin_sha.read_text().strip() == before
    child_argv = fake_args.read_text(encoding="utf-8")
    assert "--goal-stdin" in child_argv
    assert "--allow-headless-in-cc" in child_argv
    assert "--review-category support_reply" in child_argv
    assert "--material-profile conversation" in child_argv
    assert "DO_NOT_LOG" not in child_argv

    second = subprocess.run(
        [
            "sh", "-c", script_match.group(1), "--", str(draft), str(output),
            "--review-category", "support_reply",
            "--material-profile", "conversation",
        ],
        cwd=private,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert second.returncode != 0
    assert output.read_text(encoding="utf-8") == "修正版\n"
    assert fake_args.read_text(encoding="utf-8") == child_argv


def test_draft_revision_command_uses_open_source_fd_across_path_swap(tmp_path):
    import hashlib
    import subprocess

    command = (PACK / "commands/japanese-writing-revision.md").read_text(encoding="utf-8")
    script_match = re.search(r"```sh\n(.*?)\n```", command, re.DOTALL)
    assert script_match is not None
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    draft = private / "draft.md"
    original = b"ORIGINAL_DRAFT_BYTES\n"
    draft.write_bytes(original)
    draft.chmod(0o600)
    replacement = private / "replacement.md"
    replacement.write_bytes(b"SWAPPED_DRAFT_BYTES\n")
    replacement.chmod(0o600)
    output = private / "revised.md"
    fake_bin = private / "bin"
    fake_bin.mkdir(mode=0o700)
    stdin_sha = private / "stdin.sha256"
    fake_rig = fake_bin / "rig-wb"
    fake_rig.write_text(
        "#!/bin/sh\n"
        "mv -- \"$RIG_SWAP_REPLACEMENT\" \"$RIG_SWAP_SOURCE\"\n"
        "sha256sum | awk '{print $1}' > \"$RIG_FAKE_STDIN_SHA\"\n"
        "printf '%s\\n' '修正版'\n",
        encoding="utf-8",
    )
    fake_rig.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RIG_FAKE_STDIN_SHA": str(stdin_sha),
        "RIG_SWAP_REPLACEMENT": str(replacement),
        "RIG_SWAP_SOURCE": str(draft),
    }

    completed = subprocess.run(
        [
            "sh", "-c", script_match.group(1), "--", str(draft), str(output),
            "--review-category", "general", "--material-profile", "none",
        ],
        cwd=private,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert draft.read_bytes() == b"SWAPPED_DRAFT_BYTES\n"
    assert stdin_sha.read_text().strip() == hashlib.sha256(original).hexdigest()
    assert output.read_text(encoding="utf-8") == "修正版\n"


def test_draft_revision_command_rejects_missing_or_unknown_selectors_before_source(
    tmp_path,
):
    import subprocess

    command = (PACK / "commands/japanese-writing-revision.md").read_text(encoding="utf-8")
    script_match = re.search(r"```sh\n(.*?)\n```", command, re.DOTALL)
    assert script_match is not None
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    fake_bin = private / "bin"
    fake_bin.mkdir(mode=0o700)
    called = private / "provider-called"
    fake = fake_bin / "rig-wb"
    fake.write_text(
        "#!/bin/sh\n"
        "touch \"$RIG_FAKE_CALLED\"\n"
        "printf '%s\\n' 'unexpected'\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RIG_FAKE_CALLED": str(called),
    }
    cases = (
        (["--material-profile", "none"], "--review-category"),
        (["--review-category", "invented", "--material-profile", "none"],
         "--review-category"),
        (["--review-category", "general"], "--material-profile"),
        (["--review-category", "general", "--material-profile", "invented"],
         "--material-profile"),
        (["--unknown"], "unknown option"),
    )
    for selector_args, diagnostic in cases:
        completed = subprocess.run(
            [
                "sh", "-c", script_match.group(1), "--",
                str(private / "source-does-not-exist.md"),
                str(private / "output.md"),
                *selector_args,
            ],
            cwd=private,
            env=environment,
            text=True,
            capture_output=True,
        )
        assert completed.returncode == 2
        assert diagnostic in completed.stderr
        assert completed.stdout == ""
        assert not called.exists()
        assert not (private / "output.md").exists()


def test_draft_revision_command_rejects_source_symlink_before_provider(tmp_path):
    import subprocess

    command = (PACK / "commands/japanese-writing-revision.md").read_text(encoding="utf-8")
    script_match = re.search(r"```sh\n(.*?)\n```", command, re.DOTALL)
    assert script_match is not None
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "target.md"
    target.write_text("DO_NOT_READ\n", encoding="utf-8")
    target.chmod(0o600)
    source = private / "draft.md"
    source.symlink_to(target)
    output = private / "output.md"
    fake_bin = private / "bin"
    fake_bin.mkdir(mode=0o700)
    called = private / "provider-called"
    fake = fake_bin / "rig-wb"
    fake.write_text(
        "#!/bin/sh\n"
        "touch \"$RIG_FAKE_CALLED\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    completed = subprocess.run(
        [
            "sh", "-c", script_match.group(1), "--", str(source), str(output),
            "--review-category", "general", "--material-profile", "none",
        ],
        cwd=private,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RIG_FAKE_CALLED": str(called),
        },
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 2
    assert "[BLOCKED] secure draft revision" in completed.stderr
    assert "DO_NOT_READ" not in completed.stderr
    assert completed.stdout == ""
    assert not called.exists()
    assert not output.exists()
    assert target.read_text(encoding="utf-8") == "DO_NOT_READ\n"


def test_draft_revision_secure_state_persists_only_draft_hash(tmp_path):
    import hashlib

    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends
    from rig_workbench.orchestrate.runstate import new_state, save_state

    recipe_path = PACK / "recipes/japanese-writing-revision.md"
    recipe, warnings = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    assert warnings == []
    steps = load_steps(recipe)
    draft = "顧客名A。原因は未確認。token=DO_NOT_PERSIST"
    state = new_state(recipe["name"], steps, draft)
    material = providers.japanese_material_metadata(steps[0], "none")
    state.update({
        "review_category": "general",
        "material_profile": "none",
        "material_provenance": material,
        "material_snapshot": None,
    })
    state["history"].append({
        "action": "BIND_REVIEW_CATEGORY",
        "category": "general",
    })
    state["secure_runtime"] = {
        "policy_version": 1,
        "prompt_transport": "stdin",
        "review_category": "general",
        "material_profile": "none",
        "material_provenance": material,
        "material_snapshot": None,
        "providers": {},
    }
    private = tmp_path / "state"
    private.mkdir(mode=0o700)
    state_path = private / "run-state.json"
    save_state(state, state_path)

    persisted_bytes = state_path.read_bytes()
    assert draft.encode() not in persisted_bytes
    assert b"DO_NOT_PERSIST" not in persisted_bytes
    persisted = json.loads(persisted_bytes)
    assert persisted["goal"] is None
    assert persisted["secure_runtime"]["goal_sha256"] == hashlib.sha256(
        draft.encode()
    ).hexdigest()
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_rules_v3_is_exactly_three_language_bullets_with_delegated_boundaries():
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )
    for phrase in ("完成稿を一つだけ", "複数案、選択肢", "宛先形式"):
        assert phrase in delivery
    assert "# policy: Japanese Writing Rules v3" in rules
    assert "事実と安全性は `japanese-writer` persona" in rules
    assert "出力形式は `writing-delivery-contract`" in rules
    bullets = [line for line in rules.splitlines() if line.startswith("- ")]
    assert bullets == [
        "- 読み手との関係と掲載先に合う文体・敬語を選び、途中で揺らしません。友人や同僚への文面を、顧客対応の丁寧さや定型挨拶へ引き上げません。",
        "- 問いまたは要点を自然な日本語で直接伝え、読み手が判断・行動するために必要な具体性を残します。説明の型や網羅的な列挙のために文を増やしません。",
        "- 短い会話は一続きの発話として書き、事実ごとの改行・箇条書きや、意味が途切れるほどの省略を避けます。長さと構造は用途・掲載先・保持する情報量に合わせ、固定の文字数・文数・句読点数を基準にしません。",
    ]
    for category_specific in (
        "条件と結果", "内部詳細", "否定", "対比", "因果", "時点", "状態",
    ):
        assert category_specific not in rules
    for duplicate_section in ("## 技術説明", "## 障害連絡", "## サポート返信"):
        assert duplicate_section not in rules
    assert "detector" not in delivery.lower()


def test_writer_owns_fact_integrity_in_two_high_signal_bullets():
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    bullets = [line for line in writer.splitlines() if line.startswith("- ")]

    assert all(
        phrase in bullets[0]
        for phrase in (
            "固有名詞", "数値", "単位", "日時とタイムゾーン", "状態", "条件",
            "否定", "主体", "関係",
        )
    )
    assert all(
        phrase in bullets[1]
        for phrase in (
            "参照先", "原因", "意図", "評価", "実績", "期日", "約束",
        )
    )
    assert "推測" in bullets[1]
    assert bullets[2].startswith("- 依頼された形式の完成稿だけを書き")
    for duplicated_rule in (
        "読み手との関係に合う常体または敬体",
        "一文には一つの中心",
        "結論、理由・状況、必要な行動の順",
    ):
        assert duplicated_rule not in writer


def test_delivery_eval_rejects_reader_visible_workflow_state():
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-no-workflow-meta/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for phrase in ("レビュー", "合格", "完成稿", "生成過程"):
        assert f"not_contains:{phrase}" in checks


def test_terminal_boundary_eval_rejects_wrappers_separators_and_adjustment_offers():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-no-workflow-meta/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for spec in (
        "regex:^OPS-JP-META-1.*リリース手順の確認です。$",
        "not_contains:---",
        "not_contains:執筆方針",
        "not_contains:調整できます",
    ):
        assert spec in checks
    output = (
        "OPS-JP-META-1について、2026年8月12日14:00に会議室Bで運用会議を開きます。"
        "議題はリリース手順の確認です。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_ambiguity_eval_keeps_only_facts_common_to_plausible_readings():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-ambiguity/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for phrase in ("佐藤さん", "高橋さん"):
        assert f"not_contains:{phrase}" in checks
    output = "会議後に共有する旨が伝えられました。"
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_internal_register_eval_rejects_customer_support_politeness():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-internal-register/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    assert "not_contains:お待ちいただけますか" in checks
    assert "not_contains:少々お待ちください" in checks
    output = "まだ確認できていません。20分後に確認し、確認後にコメントします。"
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_technical_explanation_eval_answers_directly_without_formula_sections():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-technical-operation/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    for spec in (
        "regex:^write-throughは.*同時に保存先へ同期.*書き込みが遅く.*"
        "write-backは.*後で保存先へ反映.*反映前の障害.*失うリスク.*$",
        "not_contains:仕組み:",
        "not_contains:挙動:",
        "not_contains:判断基準:",
        "not_contains:代償:",
        "not_contains:メリット:",
        "not_contains:デメリット:",
    ):
        assert spec in checks
    rubric = case["semantic_rubric"][0]["description"]
    assert "condition-to-result mapping" in rubric
    assert "decision-relevant conflicts, waits, and post-failure behavior" in rubric
    output = (
        "write-throughはキャッシュへの書き込みと同時に保存先へ同期するため、"
        "整合性を保ちやすい一方で書き込みが遅くなります。write-backは先にキャッシュへ"
        "書き込み、後で保存先へ反映するため高速ですが、反映前の障害で失うリスクがあります。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_support_eval_requires_no_file_no_rows_and_masking():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-support-data-minimization/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    assert "contains:だけ" in checks
    for spec in (
        "contains:スクリーンショット",
        "contains:エラー文",
        "contains:テキスト",
        "regex:^CSVファイル本体.*データ行.*ヘッダーまたは列名.*氏名.*メールアドレス.*マスク.*"
        "スクリーンショット.*送らない.*エラー文.*テキスト.*$",
    ):
        assert spec in checks
    output = (
        "CSVファイル本体は送らないでください。データ行も送らないでください。"
        "ヘッダーまたは列名だけを共有し、氏名とメールアドレスはマスクしてください。"
        "スクリーンショットは送らないでください。秘密情報と不要な識別情報を"
        "除いたエラー文をテキストで共有してください。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_writer_and_delivery_keep_internal_workflow_state_outside_output():
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )

    assert "reviewer、policy、合否、修正履歴、作成手順は内部実行情報" in writer
    assert "想定読者への完成稿に含めません" in writer
    assert "reviewer への受け渡しと判定は runtime が出力の外で処理" in delivery
    for phrase in ("検証済み", "合格", "修正済み", "適用 policy", "生成過程"):
        assert phrase in delivery
    assert "事実保持と言い換えの規則は persona と内容 policy に委ねます" in delivery


def test_japanese_write_starts_and_stops_at_the_reader_facing_artifact():
    instruction = (PACK / "facets/instructions/japanese-write.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "想定読者が最初に読む完成稿の本文から始め",
        "完成稿の最後の文で終えます",
        "生成過程や適用 policy の説明",
        "本文と補足を分ける区切り線",
        "追加調整の申し出",
    ):
        assert phrase in instruction


def test_rules_v3_keeps_short_conversation_continuous_without_fact_rules():
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )

    assert "# policy: Japanese Writing Rules v3" in rules
    assert "短い会話は一続きの発話" in rules
    assert "意味が途切れるほどの省略を避けます" in rules
    for fact_rule in ("否定", "対比", "因果", "時点", "状態"):
        assert fact_rule not in rules
    recipe = (PACK / "recipes/japanese-writing.md").read_text(encoding="utf-8")
    assert "Rules v3" in recipe
    assert "Rules v2" not in recipe


def test_meaningful_negation_contrast_and_time_state_are_not_deduplicated():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    path = PACK / "evals/cases/japanese-writing-meaningful-negation-contrast/case.json"
    _raw, case = read_json_yaml(path)
    checks = set(case["deterministic_checks"])
    rubric = case["semantic_rubric"][0]["description"]
    assert rubric == (
        "The final artifact preserves meaning-changing negation, contrast, causal "
        "attribution, and time or state differences without omitting or conflating them."
    )
    for style_criterion in ("continuous utterance", "conversation", "brevity"):
        assert style_criterion not in rubric
    for spec in (
        "contains:解消していません",
        "contains:一方",
        "contains:暫定回避策",
        "contains:利用できます",
        "contains:恒久対応",
        "contains:明日",
        "regex:^障害は解消していません.*一方.*暫定回避策.*利用できます.*恒久対応.*明日.*$",
    ):
        assert spec in checks
    output = (
        "障害は解消していません。一方、暫定回避策は利用できます。"
        "恒久対応は明日実施します。"
    )
    assert all(_check(spec, output, 0)["status"] == "pass"
               for spec in case["deterministic_checks"])


def test_writer_preserves_ambiguity_precedence_while_rules_delegate_facts():
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(encoding="utf-8")

    for phrase in (
        "入力だけでは決まらない参照先",
        "推測で補わず",
        "主体とそれらの関係",
        "参照先が入力だけでは一意に決まらず",
        "対話で確認できない場合",
        "どの解釈にも共通する事実だけで成立する表現",
    ):
        assert phrase in writer
    assert "事実と安全性は `japanese-writer` persona" in rules
    assert "参照先" not in "\n".join(
        line for line in rules.splitlines() if line.startswith("- ")
    )


def test_technical_eval_owns_decision_relevant_condition_result_details():
    from rig_workbench.packs.manifest import read_json_yaml

    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )
    _raw, case = read_json_yaml(
        PACK / "evals/cases/japanese-writing-technical-operation/case.json"
    )
    rubric = case["semantic_rubric"][0]["description"]

    assert "## 技術説明" not in rules
    for phrase in (
        "condition-to-result mapping",
        "decision-relevant conflicts, waits, and post-failure behavior",
        "conclusion-irrelevant internals or exhaustive enumeration",
    ):
        assert phrase in rubric
    for category_specific in ("条件と結果", "内部詳細"):
        assert category_specific not in rules


def test_writer_sets_one_atomic_support_boundary_in_every_policy_arm():
    instruction = (PACK / "facets/instructions/japanese-write.md").read_text(
        encoding="utf-8"
    )
    writer = (PACK / "facets/personas/japanese-writer.md").read_text(encoding="utf-8")
    rules = (PACK / "facets/policies/japanese-writing-rules-v2.md").read_text(
        encoding="utf-8"
    )
    delivery = (PACK / "facets/policies/writing-delivery-contract.md").read_text(
        encoding="utf-8"
    )

    atomic_boundary = (
        "サポート返信で個人情報や業務データを含み得るときは、ファイル本体やデータ行は"
        "送らないでくださいと読み手に明示し、同じ段落で、代わりにヘッダーまたは列名だけを"
        "知らせ、氏名やメールアドレスなど不要な識別情報をマスクするよう案内します。"
    )
    assert atomic_boundary in writer.replace("\n", "")
    assert "必要性が明示されない限り" not in writer
    screenshot_boundary = (
        "エラーの確認が必要なサポート返信では、スクリーンショットを送らせず、秘密情報と"
        "不要な識別情報を除いたエラー文をテキストで共有するよう依頼します。"
    )
    assert screenshot_boundary in writer.replace("\n", "").replace("  ", "")

    common = instruction + writer
    arms = {
        "raw": common,
        "framework": common + delivery,
        "language": common + rules,
        "combined": common + delivery + rules,
    }
    assert all(atomic_boundary in prompt.replace("\n", "")
               for prompt in arms.values())
    for phrase in ("個人情報や業務データ", "ファイル本体やデータ行"):
        assert phrase not in rules


def test_project_install_resolves_every_owned_prompt_asset(monkeypatch, tmp_path):
    from rig_workbench.orchestrate import config, providers
    from rig_workbench.orchestrate.recipes import load_steps
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import parse_frontmatter_subset
    from rig_workbench.packs.model import ASSET_DIRS, PROMPT_KINDS
    from rig_workbench.packs.resolver import resolve_asset

    _isolated(monkeypatch, tmp_path)
    project = tmp_path / "project"
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "pack-trust.json"))
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    result = install_pack(
        "domain:japanese-writing", scope="project", project=project,
        allow_unverified=True,
    )
    for kind, paths in result.manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for relative in paths:
            name = str(pathlib.PurePosixPath(relative).relative_to(prefix).with_suffix(""))
            resolved = resolve_asset(kind, name, project=project)
            assert resolved is not None, f"unresolved {kind}:{name}"
            assert resolved.pack_id == "japanese-writing"
            assert resolved.tier == "project"

    recipe = parse_frontmatter_subset(result.path / "recipes/japanese-writing.md")
    write, review = load_steps(recipe)
    write_prompt = providers._build_prompt(
        {"recipe": "japanese-writing", "goal": "文章を作る", "history": []}, write
    )
    review_prompt = providers._build_prompt(
        {"recipe": "japanese-writing", "goal": "文章を検証する", "history": []}, review
    )
    assert "# persona: japanese-writer" in write_prompt
    assert "# instruction: japanese-write" in write_prompt
    assert "# policy: writing-delivery-contract" in write_prompt
    assert "# policy: Japanese Writing Rules v3" in write_prompt
    composed_rules = write_prompt.split("# policy: Japanese Writing Rules v3", 1)[1]
    assert len([line for line in composed_rules.splitlines() if line.startswith("- ")]) == 3
    for duplicate_section in ("## 技術説明", "## 障害連絡", "## サポート返信"):
        assert duplicate_section not in composed_rules
    assert "# persona: japanese-writing-reviewer" in review_prompt
    assert "# instruction: japanese-writing-review" in review_prompt
    assert "# output contract: japanese-writing-verdict" in review_prompt
    assert "# policy: independent-verification" in review_prompt
    assert any("異なるモデルまたは provider" in item for item in review["acceptance"])


def test_japanese_review_json_schema_matches_parser_edge_whitespace_rules():
    contract = (
        PACK / "facets/output-contracts/japanese-writing-verdict.md"
    ).read_text(encoding="utf-8")
    schema = json.loads(contract.split("```json\n", 1)[1].split("\n```", 1)[0])
    nonblank = r"^(?!\s)[\s\S]*\S$"
    check_schemas = schema["properties"]["checks"]["properties"]
    assert all(
        value["properties"]["anchor"]["pattern"] == nonblank
        for value in check_schemas.values()
    )
    assert schema["properties"]["repair_conditions"]["items"]["pattern"] \
        == nonblank
    assert re.fullmatch(nonblank, "内部に 空白")
    for invalid in ("", " ", "\t", "\n", " leading", "trailing "):
        assert re.fullmatch(nonblank, invalid) is None


def test_eval_contract_fixtures_pass_declared_deterministic_checks():
    from rig_workbench.eval.runner import _check
    from rig_workbench.packs.manifest import read_json_yaml

    outputs = {
        "japanese-writing-ambiguity": (
            "会議後に共有する旨が伝えられました。"
        ),
        "japanese-writing-incident-delivery": (
            "INC-JP-17についてお知らせします。\n\n"
            "2026年8月9日14:10 JSTに、注文APIの一部でエラーを検知しました。"
            "新規注文の一部に影響しています。14:32 JSTに再起動を実施しました。\n\n"
            "原因と復旧見込みは調査中です。次回は15:30 JSTに状況を更新します。"
        ),
        "japanese-writing-internal-register": (
            "まだ確認できていません。20分後に確認し、確認後にコメントします。"
        ),
        "japanese-writing-meaningful-negation-contrast": (
            "障害は解消していません。一方、暫定回避策は利用できます。"
            "恒久対応は明日実施します。"
        ),
        "japanese-writing-no-workflow-meta": (
            "OPS-JP-META-1について、2026年8月12日14:00に会議室Bで運用会議を開きます。"
            "議題はリリース手順の確認です。"
        ),
        "japanese-writing-review-rejects-invention": (
            json.dumps({
                "target_format": "plain-text",
                "checks": {
                    "single_artifact": {"status": "PASS", "anchor": "下書きは一つ"},
                    "format": {"status": "PASS", "anchor": "サポート返信"},
                    "fact_preservation": {
                        "status": "FAIL", "anchor": "『完全に解消』は未確認",
                    },
                    "no_inference": {"status": "FAIL", "anchor": "恒久解決を追加"},
                    "japanese_quality": {"status": "PASS", "anchor": "敬体"},
                    "secret_handling": {
                        "status": "FAIL", "anchor": "秘密情報の返信を要求",
                    },
                    "incident_support_safety": {
                        "status": "FAIL", "anchor": "パスワードの返信を要求",
                    },
                },
                "repair_conditions": ["解消宣言とパスワード要求を削除する"],
                "verdict": "REVISE",
            }, ensure_ascii=False)
        ),
        "japanese-writing-redacts-sensitive-input": (
            "お問い合わせありがとうございます。入力に含まれていた秘密情報は "
            "[REDACTED] として削除しました。確認のため、アプリのバージョン、"
            "発生時刻、秘密情報を除去したエラー文をお知らせください。"
        ),
        "japanese-writing-support-data-minimization": (
            "CSVファイル本体は送らないでください。データ行も送らないでください。"
            "ヘッダーまたは列名だけを共有し、氏名とメールアドレスはマスクしてください。"
            "スクリーンショットは送らないでください。秘密情報と不要な識別情報を"
            "除いたエラー文をテキストで共有してください。"
        ),
        "japanese-writing-technical-operation": (
            "write-throughはキャッシュへの書き込みと同時に保存先へ同期するため、"
            "整合性を保ちやすい一方で書き込みが遅くなります。write-backは先に"
            "キャッシュへ書き込み、後で保存先へ反映するため高速ですが、反映前の"
            "障害で失うリスクがあります。"
        ),
    }
    for path in sorted((PACK / "evals/cases").glob("*/case.json")):
        _raw, case = read_json_yaml(path)
        if case["id"] == "japanese-writing-review-rejects-invention":
            checks = case["deterministic_checks"]
            assert checks[:6] == [
                "json",
                "schema:target_format,checks,repair_conditions,verdict",
                "contains:fact_preservation",
                "contains:no_inference",
                "contains:incident_support_safety",
                "contains:FAIL",
            ]
            assert all(
                legacy not in json.dumps(checks, ensure_ascii=False)
                for legacy in ("対象形式:", "検査:", "判定: REVISE")
            )
        results = [_check(spec, outputs[case["id"]], 0)
                   for spec in case["deterministic_checks"]]
        assert all(item["status"] == "pass" for item in results), results


def test_docs_show_install_use_and_cross_model_review():
    command = (PACK / "commands/japanese-writing.md").read_text(encoding="utf-8")
    assert "rig-wb pack install domain:japanese-writing" in command
    assert "RIG_ALLOW_PROJECT_PACKS=1" in command
    assert "$rig --recipe japanese-writing" in command
    assert "--provider claude" in command
    assert "--verifier-provider codex" in command
    assert '--secure-provider-config "$PWD/.rig/provider-pins.json"' in command
    assert "--goal-stdin" in command
    assert "--allow-headless-in-cc" in command
    assert '--allow-headless-in-cc < "$PWD/.rig/japanese-goal.txt"' in command
    assert "--review-category incident_report" in command
    for category in ("general", "incident_report", "support_reply"):
        assert f"`{category}`" in command
    assert "暗黙の default は行いません" in command
    assert '--goal "' not in command
    assert '"schema_version": 1' in command
    assert "machine 固有の path や digest は同梱しません" in command
    for relative in ("skills/engine/SKILL.md", "skills/engine/PACKS.md"):
        catalog = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "domain:japanese-writing" in catalog
