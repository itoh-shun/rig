"""Progress reports observations only; they never participate in acceptance."""
import subprocess
import sys
import time

import pytest

from rig_workbench.orchestrate.progress import ProgressReporter, notify


class Output:
    def __init__(self):
        self.lines = []
    def err(self, text):
        self.lines.append((time.monotonic(), text))
    def out(self, text):
        raise AssertionError('progress must never use artifact stdout')


def test_observer_exceptions_and_missing_observer_are_harmless():
    def broken(event):
        raise RuntimeError('cannot affect the gate')
    notify(None, 'operation_started', phase='GENERATE')
    notify(broken, 'operation_started', phase='GENERATE')


def test_safe_metadata_only_and_control_characters_cannot_inject_lines():
    seen = []
    notify(seen.append, 'operation_started', phase='GENERATE', step_id='a\nFAKE PASS',
           prompt='SECRET_PROMPT', command='SECRET_COMMAND', stdout='SECRET_STDOUT',
           stderr='SECRET_STDERR', token='SECRET_TOKEN')
    assert len(seen) == 1
    assert set(seen[0]) == {'event', 'phase', 'step_id'}
    assert '\n' not in seen[0]['step_id']
    assert 'SECRET' not in repr(seen)


def test_delayed_child_gets_real_heartbeat_and_shutdown_joins_timer():
    out = Output()
    reporter = ProgressReporter(out=out, interval_seconds=0.02)
    with reporter.running():
        notify(reporter, 'operation_started', run_id='run', phase='GENERATE', step_id='implement')
        subprocess.run([sys.executable, '-c', 'import time; time.sleep(0.12)'], check=True)
        notify(reporter, 'operation_finished', run_id='run', phase='GENERATE', step_id='implement', outcome='PROCESS_EXITED')
    lines = [line for _, line in out.lines]
    assert any('heartbeat' in line for line in lines)
    assert not any('%' in line for line in lines)
    count = len(lines)
    time.sleep(0.05)
    assert len(out.lines) == count
    assert not reporter._thread or not reporter._thread.is_alive()


def test_exception_in_scope_stops_heartbeat_thread():
    reporter = ProgressReporter(out=Output(), interval_seconds=0.01)
    with pytest.raises(RuntimeError):
        with reporter.running():
            notify(reporter, 'operation_started', phase='CHECK')
            raise RuntimeError('child error')
    assert not reporter._thread or not reporter._thread.is_alive()


def test_concurrent_operations_are_reported_as_multiple_active_operations():
    out = Output()
    reporter = ProgressReporter(out=out, interval_seconds=0.01)
    with reporter.running():
        notify(reporter, 'operation_started', phase='VERIFY', step_id='one', provider='mock', role='verifier')
        notify(reporter, 'operation_started', phase='VERIFY', step_id='two', provider='mock', role='verifier')
        time.sleep(0.04)
        notify(reporter, 'operation_finished', phase='VERIFY', step_id='two', provider='mock', role='verifier', outcome='PROCESS_EXITED')
        time.sleep(0.02)
        notify(reporter, 'operation_finished', phase='VERIFY', step_id='one', provider='mock', role='verifier', outcome='PROCESS_EXITED')
    assert any('active=2' in line for _, line in out.lines)
    assert any('active=1' in line for _, line in out.lines)


def test_real_stderr_heartbeat_is_visible_before_process_finishes():
    source = '''import subprocess,sys
from rig_workbench.orchestrate.progress import ProgressReporter,notify
with ProgressReporter(interval_seconds=0.03).running() as observer:
    notify(observer,'operation_started',phase='GENERATE',step_id='edit')
    subprocess.run([sys.executable,'-c','import time;time.sleep(0.4);print("CHILD_SECRET")'],capture_output=True,check=True)
    notify(observer,'operation_finished',phase='GENERATE',step_id='edit',outcome='PROCESS_EXITED')
print('ARTIFACT')
'''
    process = subprocess.Popen([sys.executable, '-c', source], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        first = process.stderr.readline()
        second = process.stderr.readline()
        assert 'operation_started' in first
        assert 'heartbeat' in second
        assert process.poll() is None
        stdout, stderr = process.communicate(timeout=5)
        assert stdout == 'ARTIFACT\n'
        assert 'CHILD_SECRET' not in first + second + stderr
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('interval', [0, -1, True, None, '1', float('nan'), float('inf')])
def test_invalid_heartbeat_interval_refused(interval):
    with pytest.raises(ValueError):
        ProgressReporter(interval_seconds=interval)


def test_finished_summary_suppresses_late_heartbeat():
    out = Output()
    reporter = ProgressReporter(out=out, interval_seconds=0.01)
    with reporter.running():
        notify(reporter, 'operation_started', phase='CHECK')
        time.sleep(0.025)
        notify(reporter, 'run_finished', outcome='DONE')
        time.sleep(0.025)
    assert out.lines[-1][1] == '[progress] run_finished outcome=DONE'


def test_bad_presenter_cannot_keep_timer_alive():
    class BrokenOutput:
        def err(self, text):
            raise BrokenPipeError('consumer disconnected')
    reporter = ProgressReporter(out=BrokenOutput(), interval_seconds=0.01)
    with reporter.running():
        notify(reporter, 'operation_started', phase='GENERATE')
        time.sleep(0.025)
        notify(reporter, 'run_finished', outcome='BLOCKED')
    assert not reporter._thread.is_alive()


def test_copyable_next_command_preserves_quoted_long_paths():
    import shlex
    command = shlex.join(['/usr/bin/python3', '/opt/rig source/orchestrate.py', 'status',
                         '/private/run state/' + 'x' * 2200 + '.json', '--json'])
    out = Output()
    reporter = ProgressReporter(out=out)
    notify(reporter, 'run_finished', outcome='DONE', next_action='inspect_results', next_command=command)
    line = out.lines[-1][1]
    assert line.split('next_command=', 1)[1] == command
    assert shlex.split(line.split('next_command=', 1)[1])[-1] == '--json'
