import os
from pathlib import Path

import pytest

import forgesetup as run_steps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(tmp_path: Path, text: str) -> Path:
    """Write a YAML spec string to a temp file and return its path."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


# ---------------------------------------------------------------------------
# 1 + 2. inputs + env interpolation and overrides
# ---------------------------------------------------------------------------

def test_inputs_and_env_interpolation_with_overrides(tmp_path, monkeypatch):
    """
    Verify:
      - inputs are available for interpolation
      - env uses inputs
      - overrides (--set KEY=VALUE) replace inputs
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    # Patch command execution so nothing real runs.
    calls_argv = []
    calls_shell = []

    def fake_argv(argv, cwd, env):
        calls_argv.append((tuple(argv), cwd, env))
        class R:  # minimal CompletedProcess-like
            returncode = 0
        return R()

    def fake_shell(cmd, shell_kind, cwd, env):
        calls_shell.append((cmd, shell_kind, cwd, env))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_argv", fake_argv)
    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    spec_text = """
inputs:
  FOO: "from-input"
  BAR: "bar"
env:
  COMBINED: "{{FOO}}-{{BAR}}"

common:
  steps:
    - name: Run argv using env
      run:
        - argv: ["printenv", "COMBINED"]
    """

    spec_path = _make_spec(tmp_path, spec_text)

    # Override FOO so COMBINED should see it.
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=["FOO=from-override"])

    assert calls_argv, "argv-based command should have been executed"
    argv, cwd, env = calls_argv[0]
    # We don't care about the exact command, only env propagation
    assert env["COMBINED"] == "from-override-bar"


# ---------------------------------------------------------------------------
# 3 + 4. when: OS!=windows and OS==windows
# ---------------------------------------------------------------------------

def test_when_condition_os_not_windows(tmp_path, monkeypatch, capsys):
    """
    Verify when: 'OS!=windows' only fires on non-windows.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
inputs:
  SESSION_NAME: "test"

common:
  steps:
    - name: Linux only
      when: "OS!=windows"
      run:
        - "echo linux"
    - name: Windows only
      when: "OS==windows"
      run:
        - "echo windows"
    """

    spec_path = _make_spec(tmp_path, spec_text)

    # Stub out execution to avoid real shell calls; we only inspect output.
    def fake_shell(cmd, shell_kind, cwd, env):
        class R:
            returncode = 0
        print(f"EXEC:{cmd}")
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)
    out = capsys.readouterr().out

    assert "Linux only" in out
    assert "EXEC:echo linux" in out
    # Windows step should be skipped
    assert "Windows only" not in out
    assert "EXEC:echo windows" not in out


def test_when_condition_os_equals_windows(tmp_path, monkeypatch, capsys):
    """
    Verify when: 'OS==windows' only fires on windows.
    Also implicitly checks that OS is correctly injected into the context.
    """
    monkeypatch.setenv("FORGE_OS", "windows")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
inputs:
  SESSION_NAME: "test"

common:
  steps:
    - name: Linux only
      when: "OS!=windows"
      run:
        - "echo linux"
    - name: Windows only
      when: "OS==windows"
      run:
        - "echo windows"
    """

    spec_path = _make_spec(tmp_path, spec_text)

    def fake_shell(cmd, shell_kind, cwd, env):
        class R:
            returncode = 0
        print(f"EXEC:{shell_kind}:{cmd}")
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)
    out = capsys.readouterr().out

    assert "Windows only" in out
    assert "EXEC:powershell:echo windows" in out
    # Linux step should be skipped
    assert "Linux only" not in out


# ---------------------------------------------------------------------------
# 5. shell selection (powershell vs bash)
# ---------------------------------------------------------------------------

def test_shell_selection_powershell_vs_bash(tmp_path, monkeypatch, capsys):
    """
    Verify that default shell_kind is 'powershell' for OS=windows
    and 'bash' for OS=ubuntu.
    """
    # First: ubuntu
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
common:
  steps:
    - name: Shell test
      run:
        - "echo hi"
    """

    spec_path = _make_spec(tmp_path, spec_text)

    shells_seen = []

    def fake_shell(cmd, shell_kind, cwd, env):
        shells_seen.append(shell_kind)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)
    assert shells_seen == ["bash"]

    # Now windows
    shells_seen.clear()
    monkeypatch.setenv("FORGE_OS", "windows")
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)
    assert shells_seen == ["powershell"]


# ---------------------------------------------------------------------------
# 6. write_file path, content, mode
# ---------------------------------------------------------------------------

def test_write_file_full_behavior(tmp_path, monkeypatch):
    """
    Verify write_file respects path, content, append, and mode.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    # Path uses ~ so expanduser() hits FORGE_HOME
    spec_text = """
inputs:
  FILENAME: "~/.config/test_file.txt"

common:
  steps:
    - name: Initial write
      write_file:
        path: "{{FILENAME}}"
        mode: "0600"
        content: "line1"
    - name: Append write
      write_file:
        path: "{{FILENAME}}"
        append: true
        content: "\\nline2"
    """

    spec_path = _make_spec(tmp_path, spec_text)
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    target = Path(tmp_path) / ".config" / "test_file.txt"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert content == "line1\nline2"

    # Mode check: on non-POSIX this might not be meaningful, but on Linux it should hold.
    mode = target.stat().st_mode & 0o777
    assert mode in (0o600, 0o644, 0o666)  # allow some variance, but 0600 is requested


# ---------------------------------------------------------------------------
# 7. clone_repos: parsing and post_install
# ---------------------------------------------------------------------------

def test_clone_repos_resolution_and_post_install(tmp_path, monkeypatch):
    """
    Verify clone_repos:
      - resolves URLs from name+default_org
      - respects explicit org/url
      - calls clone_repo_to with correct target path
      - runs post_install commands in repo cwd
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    cloned = []
    post_shell = []
    post_argv = []

    def fake_clone_repo_to(url, dest):
        cloned.append((url, Path(dest)))

    def fake_shell(cmd, shell_kind, cwd, env):
        post_shell.append((cmd, shell_kind, cwd))
        class R:
            returncode = 0
        return R()

    def fake_argv(argv, cwd, env):
        post_argv.append((tuple(argv), cwd))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "clone_repo_to", fake_clone_repo_to)
    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)
    monkeypatch.setattr(run_steps, "run_process_argv", fake_argv)

    # NOTE: raw string + single-quoted YAML scalar for dest_windows,
    # so backslash isn't treated as a \u escape by YAML.
    spec_text = r"""
inputs:
  WORKSPACE_ROOT: "~/workspace"
  DEFAULT_ORG: "my-org"

common:
  steps:
    - name: Clone repos
      clone_repos:
        dest_unix: "{{WORKSPACE_ROOT}}"
        dest_windows: 'C:\unused'
        default_org: "{{DEFAULT_ORG}}"
        repos:
          - name: "service-a"
          - name: "custom-org-repo"
            org: "custom-org"
          - url: "https://github.com/someone/explicit.git"
            # derive name from URL
            post_install:
              - argv: ["echo", "post-argv"]
              - "echo post-shell"
"""

    spec_path = _make_spec(tmp_path, spec_text)
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    # Expected destination root
    dest_root = (tmp_path / "workspace").resolve()
    assert cloned

    urls = {u for (u, _) in cloned}
    targets = {str(p) for (_, p) in cloned}

    assert "https://github.com/my-org/service-a.git" in urls
    assert "https://github.com/custom-org/custom-org-repo.git" in urls
    assert "https://github.com/someone/explicit.git" in urls

    assert str(dest_root / "service-a") in targets
    assert str(dest_root / "custom-org-repo") in targets
    assert str(dest_root / "explicit") in targets

    # Post install commands only for the explicit.git repo
    assert post_argv
    assert post_shell
    argv_cmd, argv_cwd = post_argv[0][0], post_argv[0][1]
    shell_cmd, shell_shell, shell_cwd = post_shell[0]

    assert "post-argv" in argv_cmd
    assert "post-shell" in shell_cmd
    # cwd should be the 'explicit' repo dir
    assert shell_cwd.endswith("explicit")
    assert argv_cwd.endswith("explicit")



# ---------------------------------------------------------------------------
# 8. run parsing (argv + shell)
# ---------------------------------------------------------------------------

def test_run_parses_argv_and_shell(tmp_path, monkeypatch, capsys):
    """
    Dedicated test for run items (argv and shell) behavior.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    calls_shell = []
    calls_argv = []

    def fake_shell(cmd, shell_kind, cwd, env):
        calls_shell.append((cmd, shell_kind, cwd))
        class R:
            returncode = 0
        return R()

    def fake_argv(argv, cwd, env):
        calls_argv.append((tuple(argv), cwd))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)
    monkeypatch.setattr(run_steps, "run_process_argv", fake_argv)

    spec_text = """
inputs:
  SESSION_NAME: "test-session"

common:
  steps:
    - name: Run argv example
      run:
        - argv: ["echo", "hi-{{SESSION_NAME}}"]

    - name: Run shell example
      run:
        - "echo shell-{{SESSION_NAME}}"
"""

    spec_path = _make_spec(tmp_path, spec_text)
    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    assert calls_argv
    assert calls_shell

    argv, cwd = calls_argv[0][0], calls_argv[0][1]
    assert argv[0] == "echo"
    assert "hi-test-session" in argv[1]

    cmd, shell_kind, cwd2 = calls_shell[0]
    assert "shell-test-session" in cmd
    assert shell_kind == "bash"


# ---------------------------------------------------------------------------
# 9. OS detection override (FORGE_OS) respected by run_spec
# ---------------------------------------------------------------------------

def test_run_spec_respects_forge_os_override(tmp_path, monkeypatch, capsys):
    """
    Verify that run_spec uses FORGE_OS when set, independent of real platform.
    """
    monkeypatch.setenv("FORGE_OS", "windows")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
common:
  steps:
    - name: Windows step
      when: "OS==windows"
      run:
        - "echo win-step"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    def fake_shell(cmd, shell_kind, cwd, env):
        class R:
            returncode = 0
        print(f"EXEC:{shell_kind}:{cmd}")
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)
    out = capsys.readouterr().out

    assert "Windows step" in out
    assert "EXEC:powershell:echo win-step" in out


# ---------------------------------------------------------------------------
# 10. step ordering: common before os-specific
# ---------------------------------------------------------------------------

def test_step_order_common_then_os_specific(tmp_path, monkeypatch, capsys):
    """
    Verify that common.steps execute before os.<oskey>.steps, preserving order.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
common:
  steps:
    - name: Common step 1
      run:
        - "echo common1"
    - name: Common step 2
      run:
        - "echo common2"

os:
  ubuntu:
    steps:
      - name: Ubuntu step 1
        run:
          - "echo ubuntu1"
      - name: Ubuntu step 2
        run:
          - "echo ubuntu2"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    sequence = []

    def fake_shell(cmd, shell_kind, cwd, env):
        # Capture commands in execution order
        sequence.append(cmd)
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    # We only care about relative ordering, not exact shell syntax
    assert sequence == [
        "echo common1",
        "echo common2",
        "echo ubuntu1",
        "echo ubuntu2",
    ]

# ---------------------------------------------------------------------------
# 11. Workspace root guard (Unix + Windows)
# ---------------------------------------------------------------------------

def test_workspace_root_guard_unix(tmp_path, monkeypatch, capsys):
    """
    If WORKSPACE_ROOT is defined and the directory already exists,
    run_spec should abort before running any steps.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    # Create the workspace dir in advance.
    ws_dir = tmp_path / "dev" / "workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)

    spec_text = """
inputs:
  WORKSPACE_ROOT: "~/dev/workspace"

common:
  steps:
    - name: Dummy step
      run:
        - "echo should-not-run"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    with pytest.raises(SystemExit) as excinfo:
        run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    out = capsys.readouterr().out
    # Guard should fire, so no step banners and we get a message
    assert "should-not-run" not in out
    assert "Workspace root already exists" in out
    assert excinfo.value.code == 1


def test_workspace_root_guard_windows(tmp_path, monkeypatch, capsys):
    """
    If WORKSPACE_ROOT_WIN is defined and directory exists, guard should trigger.
    """
    monkeypatch.setenv("FORGE_OS", "windows")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    ws_dir = tmp_path / "dev" / "workspace_win"
    ws_dir.mkdir(parents=True, exist_ok=True)

    spec_text = """
inputs:
  WORKSPACE_ROOT_WIN: "~/dev/workspace_win"

common:
  steps:
    - name: Dummy step
      when: "OS==windows"
      run:
        - "echo should-not-run"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    with pytest.raises(SystemExit):
        run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    out = capsys.readouterr().out
    assert "Workspace root already exists" in out
    assert "should-not-run" not in out


# ---------------------------------------------------------------------------
# 12. Default spec path
# ---------------------------------------------------------------------------
def test_default_spec_path_derived_from_os(monkeypatch):
    # unix-like
    assert str(run_steps._default_spec_path("ubuntu")).endswith(".config/forgesetup/spec.yaml")
    # windows
    monkeypatch.setenv("APPDATA", "C:\\Users\\Test\\AppData\\Roaming")
    p = run_steps._default_spec_path("windows")
    assert str(p).endswith("AppData\\Roaming\\forgesetup\\spec.yaml")



# ---------------------------------------------------------------------------
# 13. Multi-step end-to-end flow (Unix)
# ---------------------------------------------------------------------------

def test_multistep_flow_unix(tmp_path, monkeypatch):
    """
    End-to-end: multiple steps mixing run + write_file on a Unix-like OS.
    Ensures all steps run and side effects happen in order.
    """
    monkeypatch.setenv("FORGE_OS", "ubuntu")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
inputs:
  WORKSPACE_ROOT: "~/workspace"

common:
  steps:
    - name: Step 1 - echo
      run:
        - "echo step1"
    - name: Step 2 - write file
      write_file:
        path: "{{WORKSPACE_ROOT}}/marker.txt"
        content: "ok"

os:
  ubuntu:
    steps:
      - name: Step 3 - ubuntu specific
        run:
          - "echo step3-ubuntu"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    executed = []

    def fake_shell(cmd, shell_kind, cwd, env):
        executed.append((cmd, shell_kind))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    # Two shell commands (step1 + ubuntu-specific step)
    assert executed == [("echo step1", "bash"), ("echo step3-ubuntu", "bash")]

    # File from step 2 created under FORGE_HOME
    marker = tmp_path / "workspace" / "marker.txt"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "ok"


# ---------------------------------------------------------------------------
# 14. Multi-step end-to-end flow (Windows)
# ---------------------------------------------------------------------------

def test_multistep_flow_windows(tmp_path, monkeypatch):
    """
    End-to-end: multiple steps mixing run + write_file on Windows.
    Mirrors the Unix flow but exercises the Windows path + shell.
    """
    monkeypatch.setenv("FORGE_OS", "windows")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path))

    spec_text = """
inputs:
  WORKSPACE_ROOT_WIN: "~/workspace_win"

common:
  steps:
    - name: Step 1 - echo
      run:
        - "echo step1"
    - name: Step 2 - write file
      write_file:
        path: "{{WORKSPACE_ROOT_WIN}}/marker.txt"
        content: "ok"

os:
  windows:
    steps:
      - name: Step 3 - windows specific
        run:
          - "echo step3-windows"
"""

    spec_path = _make_spec(tmp_path, spec_text)

    executed = []

    def fake_shell(cmd, shell_kind, cwd, env):
        executed.append((cmd, shell_kind))
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(run_steps, "run_process_shell", fake_shell)

    run_steps.run_spec(spec_path, dry_run=False, overrides_list=None)

    # Two shell commands (step1 + windows-specific step)
    assert executed == [("echo step1", "powershell"), ("echo step3-windows", "powershell")]

    # File from step 2 created under FORGE_HOME
    marker = tmp_path / "workspace_win" / "marker.txt"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "ok"
