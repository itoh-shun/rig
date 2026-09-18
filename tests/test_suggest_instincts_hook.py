"""Retired Stop reminder stays silent even under a stale host registration."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'hooks/suggest-instincts.sh'


def test_stop_reminder_is_not_registered_and_startup_instincts_remain():
    hooks = json.loads((ROOT / 'hooks/hooks.json').read_text())['hooks']
    assert 'suggest-instincts.sh' not in json.dumps(hooks)
    assert 'inject-instincts.sh' in json.dumps(hooks['SessionStart'])


@pytest.mark.parametrize('payload', ['not json', 'null', '{}',
    '{"stop_hook_active":true}', '{"stop_hook_active":false,"session_id":"new"}'])
@pytest.mark.parametrize('host', ['claude', 'codex', 'provider'])
def test_stale_registration_is_silent_and_creates_no_state(tmp_path, payload, host):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path), 'XDG_STATE_HOME': str(tmp_path)}
    env.update({'CLAUDE_PLUGIN_ROOT': str(ROOT)} if host == 'claude' else
               {'PLUGIN_ROOT': str(ROOT)} if host == 'codex' else {'RIG_PROVIDER_SUBPROCESS': '1'})
    result = subprocess.run(['/bin/sh', str(HOOK)], input=payload, text=True,
                            capture_output=True, env=env, cwd=tmp_path, timeout=5)
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    assert list(tmp_path.iterdir()) == []


def test_previously_eligible_rig_session_is_now_silent(tmp_path):
    (tmp_path / '.rig').mkdir()
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text('/rig:go bugfix', encoding='utf-8')
    payload = json.dumps({'stop_hook_active': False, 'session_id': 'formerly-eligible',
                          'transcript_path': str(transcript)})
    result = subprocess.run(['/bin/sh', str(HOOK)], input=payload, text=True,
                            capture_output=True, cwd=tmp_path, timeout=5,
                            env={'PATH': '/usr/bin:/bin', 'XDG_STATE_HOME': str(tmp_path)})
    assert (result.returncode, result.stdout, result.stderr) == (0, '', '')
    assert not (tmp_path / 'rig').exists()
