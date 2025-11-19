#!/usr/bin/env python3
"""
run_steps.py — Declarative, OS-aware step runner for local dev bootstrap.

Purpose
-------
This script loads a YAML spec that describes a sequence of steps to run on a
developer machine (Windows, Ubuntu/Debian/Fedora/Arch). The YAML is declarative:
common steps + per-OS steps. Steps can write files, clone repos, or execute
commands. The runner's role is intentionally thin:

  * detect OS and expose it to `when:` expressions as `OS`
  * interpolate variables from `inputs` and environment into step fields
  * perform a small set of actions: write_file, clone_repos, run (commands)
  * prefer argv/list execution for commands when provided (safer than shell)
  * support `run` entries which are either shell strings (executed under
    platform shell) or mappings with `argv` (list) to run directly.

Security / Threat Model
-----------------------
* The runner executes whatever is described in the YAML. If the YAML is
  malicious (or tampered with), the runner will execute malicious actions.
  The YAML is therefore a privileged artifact and must be protected like any
  other build/deploy script (repo permissions, signed artifacts, reproducible
  CI, etc.).
* Using `argv` (list) avoids shell parsing and reduces injection risk for
  commands that don't require shell features. The runner prefers `argv` when
  present; fallback to shell strings is available for convenience.
* On Linux, many install steps require sudo — the attacker would still need
  appropriate privileges to escalate. But if the developer's machine is
  already compromised, that protection is moot (local compromise is out of
  scope).

Usage
-----
  python run_steps.py spec.yaml
  python run_steps.py spec.yaml --dry-run
  python run_steps.py spec.yaml --set SESSION_NAME=my-session --set NPM_TOKEN=xxx

Spec features used in this runner
--------------------------------
* inputs: mapping of variables available for interpolation
* env: global env values visible to steps
* common.steps: list of steps applied first
* os.<oskey>.steps: list of steps applied after common (appended)
* when: simple condition "KEY==value" or "KEY!=value" (evaluated against inputs + env)
* write_file: {path, content, mode?, append?}
* clone_repos: {dest_unix, dest_windows, repos: [ {name?, url?, org? , post_install?} ], default_org? }
    - repo resolution order: repo.url -> "https://github.com/{repo.org or clone_repos.default_org}/{repo.name}.git"
* run: list of commands OR list containing items which are either:
    - shell string (exec via /bin/bash -lc or PowerShell)
    - mapping with argv: ["cmd", "arg1", ...] - executed directly (no shell)

Implementation notes
--------------------
* This runner intentionally keeps logic minimal and deterministic.
* It does not attempt to be an orchestration engine — keep pre/post install steps
  short and idempotent.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # noqa
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml  # type: ignore


# -------------------------
# Hooks (patchable in tests)
# -------------------------
def run_process_shell(
    cmd: str,
    shell_kind: str,
    cwd: Optional[str],
    env: Optional[Dict[str, str]],
) -> subprocess.CompletedProcess:
    """
    Run a shell command via platform shell.

    On Windows: PowerShell.
    On Linux: /bin/bash -lc.
    """
    if shell_kind == "powershell":
        full = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
    else:
        full = ["/bin/bash", "-lc", cmd]
    return subprocess.run(full, cwd=cwd, env=env, check=True)


def run_process_argv(
    argv: List[str],
    cwd: Optional[str],
    env: Optional[Dict[str, str]],
) -> subprocess.CompletedProcess:
    """
    Run a command using argv list (no shell).

    Prefer this for commands that don't require shell expansion/globbing.
    """
    return subprocess.run(argv, cwd=cwd, env=env, check=True)


def clone_repo_to(url: str, dest: Path) -> None:
    """
    Clone a git repo to dest if dest doesn't already exist.

    Idempotent: if dest exists, print and return.
    """
    if dest.exists():
        print(f"skip clone: {dest} exists")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"cloning {url} -> {dest}")
    subprocess.run(["git", "clone", url, str(dest)], check=True)


def ensure_parent(path: Path) -> None:
    """Ensure parent directory exists for a file path."""
    path.parent.mkdir(parents=True, exist_ok=True)


def write_file(path: str, content: str, mode: Optional[str] = None, append: bool = False) -> None:
    """
    Write content to path (expands ~).

    If append=True and path exists, append content. Otherwise, overwrite.
    mode: octal string like "0600" if provided.
    """
    p = Path(path).expanduser()
    ensure_parent(p)
    if append and p.exists():
        p.write_text(p.read_text(encoding="utf-8") + content, encoding="utf-8")
    else:
        p.write_text(content, encoding="utf-8")
    if mode:
        try:
            os.chmod(p, int(str(mode), 8))
        except Exception:   # noqa
            # ignore chmod failures on non-POSIX systems
            pass


# Map of hook names, useful if you later want indirection
EXEC_HOOK = {
    "run_shell": run_process_shell,
    "run_argv": run_process_argv,
    "clone_repo": clone_repo_to,
    "write_file": write_file,
}


# -------------------------
# Utilities
# -------------------------
VAR_PATTERNS = [
    (re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"), 1),  # noqa {{VAR}}
    (re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"), 1),         # noqa ${VAR}
]


def detect_os() -> str:
    """
    Return normalized OS key:
      - 'windows'
      - 'ubuntu'
      - 'fedora'
      - 'arch'
    """
    sysname = platform.system().lower()
    if sysname == "windows":
        return "windows"
    if sysname == "linux":
        # lightweight distro detection — install 'distro' if missing
        try:
            import distro  # type: ignore
        except Exception:  # noqa
            subprocess.check_call([sys.executable, "-m", "pip", "install", "distro"])
            import distro  # type: ignore
        did = (distro.id() or "").lower()
        like = (distro.like() or "").lower()
        if did in ("ubuntu", "debian", "linuxmint", "pop") or "debian" in like:
            return "ubuntu"
        if did in ("fedora", "rhel", "centos", "rocky", "almalinux") or "rhel" in like or "fedora" in like:
            return "fedora"
        if did in ("arch", "manjaro", "endeavouros", "arcolinux"):
            return "arch"
        # fallback to ubuntu-style commands
        return "ubuntu"
    raise SystemExit("Unsupported OS")


def interpolate_string(s: str, ctx: Dict[str, Any]) -> str:
    """Interpolate {{VAR}} and ${VAR} using ctx dictionary."""
    out = s
    for pat, group in VAR_PATTERNS:
        while True:
            m = pat.search(out)
            if not m:
                break
            key = m.group(group)
            out = out[: m.start()] + str(ctx.get(key, "")) + out[m.end() :]
    return out


def deep_interpolate(obj: Any, ctx: Dict[str, Any]) -> Any:
    """Deeply interpolate strings inside lists/dicts."""
    if isinstance(obj, dict):
        return {k: deep_interpolate(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_interpolate(v, ctx) for v in obj]
    if isinstance(obj, str):
        return interpolate_string(obj, ctx)
    return obj


def safe_run_command(item: Any, shell_kind: str, cwd: Optional[str], env: Optional[Dict[str, str]]) -> None:
    """
    Execute a run-item. Supported forms:
      - string: executed in shell (bash/powershell)
      - dict with key 'argv': list form executed directly (preferred)
    """
    if isinstance(item, dict) and "argv" in item:
        argv = item["argv"]
        if not isinstance(argv, list):
            raise ValueError("argv must be a list")
        run_process_argv(argv, cwd=cwd, env=env)
        return
    if isinstance(item, str):
        run_process_shell(item, shell_kind, cwd=cwd, env=env)
        return
    raise ValueError("Unsupported run item type; use string or {'argv': [...]}.")


# -------------------------
# Repo clone helper
# -------------------------
def resolve_repo_url(entry: Dict[str, Any], default_org: Optional[str]) -> str:
    """
    Determine the repo URL using the following precedence:
      1. entry['url'] if provided
      2. construct url using entry['org'] or default_org and entry['name']
         -> "https://github.com/{org}/{name}.git"

    If neither url nor org+name can be resolved but name contains '/',
    treat it as "org/name" and build a GitHub URL.
    """
    if "url" in entry and entry["url"]:
        return entry["url"]
    name = entry.get("name")
    if not name:
        raise ValueError("repo entry must contain 'url' or 'name'")
    org = entry.get("org") or default_org
    if not org:
        if "/" in name:
            return f"https://github.com/{name}.git"
        raise ValueError("No org provided and default_org not set; cannot resolve repo URL")
    return f"https://github.com/{org}/{name}.git"


# -------------------------
# Main runner helpers
# -------------------------
def parse_overrides(kv_list: Optional[List[str]]) -> Dict[str, str]:
    """Parse --set KEY=VALUE pairs into a dict."""
    out: Dict[str, str] = {}
    if not kv_list:
        return out
    for kv in kv_list:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def merge_envs(*maps: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Merge maps into a single string->string env dict (later maps override earlier)."""
    out: Dict[str, str] = {}
    for m in maps or []:
        if not m:
            continue
        for k, v in m.items():
            out[str(k)] = str(v)
    return out


def run_spec(spec_path: Path, dry_run: bool = False, overrides_list: Optional[List[str]] = None) -> None:
    """
    Run all steps from a spec file.

    Arguments
    ---------
    spec_path: Path to YAML spec.
    dry_run:   If True, print what would be done but don't execute commands.
    overrides_list: list of "KEY=VALUE" command-line style overrides.
    """
    if not spec_path.exists():
        raise SystemExit(f"spec file not found: {spec_path}")

    with spec_path.open("r", encoding="utf-8") as fh:
        spec = yaml.safe_load(fh)

    # Allow tests or callers to simulate OS and HOME via env
    home_override = os.environ.get("FORGE_HOME")
    if home_override:
        os.environ["HOME"] = home_override
        os.environ["USERPROFILE"] = home_override  # Windows equivalent

    os_override = os.environ.get("FORGE_OS")
    os_key = os_override or detect_os()

    # Build context used for interpolation (inputs + overrides)
    inputs = spec.get("inputs", {}) or {}
    overrides = parse_overrides(overrides_list or [])
    ctx: Dict[str, Any] = {**inputs, **overrides}   # noqa
    ctx["OS"] = os_key  # expose OS for 'when' clauses

    # Global env resolution: process env, spec.env, os-specific env
    proc_env = dict(os.environ)
    env_global = spec.get("env", {}) or {}
    env_os = (spec.get("os", {}) or {}).get(os_key, {}).get("env", {}) or {}
    merged_env = merge_envs(proc_env, env_global, env_os)
    # interpolate merged env values
    merged_env = {k: interpolate_string(v, {**ctx, **merged_env}) for k, v in merged_env.items()}

    # Steps: common then os-specific
    common_steps = (spec.get("common", {}) or {}).get("steps", []) or []
    os_steps = ((spec.get("os", {}) or {}).get(os_key, {}) or {}).get("steps", []) or []
    steps = common_steps + os_steps

    default_shell = "powershell" if os_key == "windows" else "bash"

    for idx, raw_step in enumerate(steps, start=1):
        # interpolate step fields using ctx + merged_env
        step_ctx = {**ctx, **merged_env}
        step = deep_interpolate(raw_step, step_ctx)
        name = step.get("name") or f"step-{idx}"
        when = step.get("when")

        # Evaluate 'when' if present
        if when:
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=)\s*(.+?)\s*$", when)
            if m:
                k, op, val = m.group(1), m.group(2), m.group(3).strip().strip('"').strip("'")
                actual = str(step_ctx.get(k, ""))
                cond_ok = (actual == val) if op == "==" else (actual != val)
            else:
                cond_ok = False
            if not cond_ok:
                # Silent skip: do not print step name at all
                continue

        print(f"\n--- [{name}] ---")
        shell_kind = step.get("shell") or default_shell
        cwd = step.get("workdir") or None
        continue_on_error = bool(step.get("continue_on_error", False))
        step_env = merge_envs(merged_env, step.get("env", {}) or {})

        # Action: write_file
        if "write_file" in step:
            wf = step["write_file"]
            path = wf["path"]
            content = wf.get("content", "")
            mode = wf.get("mode")
            append = bool(wf.get("append", False))
            if dry_run:
                print(f"DRY-RUN write_file -> {path}")
            else:
                write_file(path, content, mode=mode, append=append)
            continue

        # Action: clone_repos
        if "clone_repos" in step:
            cr = step["clone_repos"]
            dest_unix = cr.get("dest_unix")
            dest_windows = cr.get("dest_windows")
            default_org = cr.get("default_org") or (spec.get("inputs", {}) or {}).get("DEFAULT_ORG")
            repos = cr.get("repos", [])

            dest = dest_windows if os_key == "windows" else dest_unix
            if not dest:
                raise SystemExit("clone_repos must specify dest_unix or dest_windows")

            dest_path = Path(interpolate_string(dest, step_ctx)).expanduser().resolve()
            if dry_run:
                print(f"DRY-RUN clone_repos -> {dest_path} ({len(repos)} repos)")
            else:
                for entry in repos:
                    try:
                        url = resolve_repo_url(entry, default_org)
                    except Exception as e:
                        raise SystemExit(f"repo resolution error: {e}")
                    name = entry.get("name") or Path(url.rstrip("/").split("/")[-1]).stem
                    target = dest_path / name
                    try:
                        clone_repo_to(url, target)
                    except subprocess.CalledProcessError as e:
                        print(f"[ERROR] git clone failed: {e}")
                        if continue_on_error:
                            continue
                        raise

                    # optional per-repo post_install commands (run like normal run-items)
                    post = entry.get("post_install") or []
                    for pitem in post:
                        try:
                            if isinstance(pitem, dict) and "argv" in pitem:
                                if dry_run:
                                    print(f"DRY-RUN repo post argv -> {pitem['argv']}")
                                else:
                                    run_process_argv(pitem["argv"], cwd=str(target), env=step_env)
                            else:
                                if dry_run:
                                    print(f"DRY-RUN repo post shell -> {pitem}")
                                else:
                                    run_process_shell(str(pitem), shell_kind, cwd=str(target), env=step_env)
                        except subprocess.CalledProcessError as e:
                            print(f"[ERROR] post_install failed for {name}: {e}")
                            if continue_on_error:
                                continue
                            raise
            continue

        # Action: run (list)
        run_items = step.get("run")
        if run_items is None:
            print("No-op step")
            continue
        if not isinstance(run_items, list):
            raise SystemExit("run must be a list")

        if dry_run:
            for itm in run_items:
                print(f"DRY-RUN run -> {itm}")
            continue

        for itm in run_items:
            try:
                safe_run_command(itm, shell_kind=shell_kind, cwd=cwd, env=step_env)
            except subprocess.CalledProcessError as e:
                print(f"[ERROR] run item failed: {e}")
                if continue_on_error:
                    print("continue_on_error=true -> continuing")
                    continue
                raise

    print("\nAll steps complete.")



def main() -> None:
    """CLI entrypoint."""
    p = argparse.ArgumentParser(description="Declarative OS-aware bootstrap runner")
    p.add_argument("spec", help="YAML spec file path")
    p.add_argument("--dry-run", action="store_true", help="Print steps instead of running")
    p.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        help="Override input: --set KEY=VALUE (can be passed multiple times)",
    )
    args = p.parse_args()
    run_spec(Path(args.spec), dry_run=args.dry_run, overrides_list=args.overrides)


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    main()
