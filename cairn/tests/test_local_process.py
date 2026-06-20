from __future__ import annotations

import sys

from cairn.dispatcher.runtime.local.process import LocalManagedProcess


def _run(argv, timeout=30):
    p = LocalManagedProcess(argv, env={}, cwd=None)
    p.start()
    return p.communicate(timeout=timeout)


def test_runs_command_captures_stdout_and_exit():
    res = _run([sys.executable, "-c", "print('pong')"])
    assert res.returncode == 0
    assert "pong" in res.stdout
    assert res.timed_out is False


def test_nonzero_exit():
    res = _run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert res.returncode == 3


def test_timeout_marks_timed_out_and_kills():
    res = _run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert res.timed_out is True


def test_cwd_and_env_applied(tmp_path):
    p = LocalManagedProcess(
        [sys.executable, "-c", "import os; print(os.getcwd()); print(os.environ.get('CAIRN_T',''))"],
        env={"CAIRN_T": "yes"}, cwd=str(tmp_path),
    )
    p.start()
    res = p.communicate(timeout=30)
    assert str(tmp_path) in res.stdout
    assert "yes" in res.stdout
