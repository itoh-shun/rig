"""The pack compatibility version must move with release metadata."""
import pytest

from rig_workbench.validation import release


@pytest.mark.parametrize('engine_version', ['3.2.0', '3.1.0'])
def test_release_validation_detects_pack_engine_version_drift(tmp_path, monkeypatch, engine_version):
    files = {
        '.claude-plugin/plugin.json': '{"version": "3.2.0"}',
        'CHANGELOG.md': '## [3.2.0] - 2026-09-18',
        'pyproject.toml': 'version = "3.2.0"',
        'rig_workbench/__init__.py': '__version__ = "3.2.0"',
        'rig_workbench/packs/model.py': f'ENGINE_VERSION = "{engine_version}"',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    records = []
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, '_emit', lambda level, text: records.append((level, text)))
    release.check_release_metadata()
    engine_records = [(level, text) for level, text in records if 'packs/model.py' in text]
    assert len(engine_records) == 1
    assert engine_records[0][0] == ('PASS' if engine_version == '3.2.0' else 'FAIL')


def test_shipped_pack_engine_matches_release_version():
    from rig_workbench import __version__
    from rig_workbench.packs.model import ENGINE_VERSION
    assert ENGINE_VERSION == __version__
