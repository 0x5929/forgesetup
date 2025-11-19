# ForgeSetup — Declarative Local Dev Bootstrap

`ForgeSetup` is a small, OS-aware step runner for bootstrapping local developer machines from a **single declarative YAML file**.

You define what you want done (create workspace directories, write AWS config, clone repos, install tools, run commands) in a spec file. The runner:

- Detects the OS (`windows`, `ubuntu`, `fedora`, `arch`).
- Interpolates `inputs` and environment variables into steps.
- Executes a limited, predictable set of actions:
  - `write_file`
  - `clone_repos`
  - `run` (commands, via `argv` or shell).

It is intentionally thin and deterministic; the spec is the "brain".

---

## Requirements

- **Python**: 3.10+ (tested on 3.13)
- **Pipx**: 1.4.1+
- **Git**: 2.0.0+; required if you use `clone_repos`.
- The script will auto-install:
  - [`PyYAML`](https://pyyaml.org/) for YAML parsing.
  - [`distro`](https://pypi.org/project/distro/) for Linux distro detection.

---

## Installation

ForgeSetup is distributed as a normal Python package and is intended to be installed from the GitHub repo.

You can install it either with `pipx` (recommended for CLI tools) or with `pip` + a virtualenv.

### Recommended: pipx

First, ensure `pipx` is installed (most distros have a package or you can do `pip install --user pipx` and follow its PATH instructions).

Then install ForgeSetup from GitHub:

```bash
pipx install "git+https://github.com/0x5929/forgesetup.git@version"

# install a tagged release
pipx install "git+https://github.com/0x5929/forgesetup.git@v0.0.1"

# install the latest from develop
pipx install "git+https://github.com/0x5929/forgesetup.git@develop"

# after installing:
forgesetup --help
forgesetup <spec-file-path>

# Uninstallation
# & You can then reinstall a specific version or branch when needed:
pipx uninstall forgesetup

# reinstall latest dev branch
pipx install "git+https://github.com/0x5929/forgesetup.git@develop"
```

### Alternative: pip + virtualenv
If you prefer to manage your own virtualenv instead of pipx:

```bash

# Unix-like
python -m venv ~/.venvs/forgesetup
source ~/.venvs/forgesetup/bin/activate

pip install "git+https://github.com/0x5929/forgesetup.git@v0.0.1"

forgesetup --help
forgesetup ~/.config/forgesetup/spec.yaml
```

```powershell

# Windows (PowerShell)
py -m venv "$env:USERPROFILE\venvs\forgesetup"
& "$env:USERPROFILE\venvs\forgesetup\Scripts\Activate.ps1"

pip install "git+https://github.com/0x5929/forgesetup.git@v0.0.1"

forgesetup --help
forgesetup "$env:APPDATA\forgesetup\spec.yaml"
```

When you install into a virtualenv, the `forgesetup` command is only on PATH while that virtualenv is activated.
To use it later, open a new shell, activate the env again, and run `forgesetup`.

#### Uninstall with pip + virtualenv
```bash

# To uninstall
deactivate
rm -rf ~/.venvs/forgesetup

# If you prefer to keep the virtualenv and just remove the package
source ~/.venvs/forgesetup/bin/activate   #  # Windows: Activate.ps1
pip uninstall forgesetup
```


### Spec File and Default Locations

The spec is a YAML file. By default, if you do not pass a spec path, the runner looks at:

Unix-like:
`~/.config/forgesetup/spec.yaml`

Windows:
`%APPDATA%\forgesetup\spec.yaml`
(usually `C:\Users\<you>\AppData\Roaming\forgesetup\spec.yaml`)

You can copy the provided example into the default location:

# Unix
```bash

mkdir -p ~/.config/forgesetup
cp spec.example.yaml ~/.config/forgesetup/spec.yaml
```

# Windows (PowerShell)
```powershell

$cfg = Join-Path $env:APPDATA 'forgesetup'
New-Item -ItemType Directory -Force -Path $cfg | Out-Null
Copy-Item .\spec.example.yaml (Join-Path $cfg 'spec.yaml')
```

You can always override the location explicitly:

```bash

forgesetup ./my-custom-spec.yaml
```

---

## Spec Structure

At a high level:

```yaml
inputs:
  SESSION_NAME: "AWS Session"
  WORKSPACE_ROOT: "~/dev/workspace"     # unix
  WORKSPACE_ROOT_WIN: "C:\\dev\\workspace"  # windows
  DEFAULT_ORG: "my-org"
  # ...other inputs...

env:
  NPM_TOKEN: "{{NPM_TOKEN}}"

common:
  steps:
    - name: Some step
      run:
        - "echo hi"

os:
  ubuntu:
    steps:
      - name: Ubuntu only
        run:
          - "echo ubuntu"

  windows:
    steps:
      - name: Windows only
        shell: powershell
        run:
          - "echo windows"
```

Key pieces:

`inputs`

A simple mapping of variables used for interpolation:
- Referenced in strings as {{VAR}} or ${VAR}.
- Used to define paths (WORKSPACE_ROOT, etc.) and AWS parameters.

`env`

Additional environment variables available to all steps:
- Values are interpolated with inputs and other env entries.
- The merged environment is passed to all subprocesses.

Example:

```yaml
env:
  COMBINED: "{{FOO}}-{{BAR}}"
```

`common.steps` and os`.<oskey>.steps`
- `common.steps` run first on all OSes.
- `os.<oskey>.steps` (`os.ubuntu`, `os.windows`, etc.) append OS-specific steps.
- Order is preserved: **common** then **os-specific**.

Each step can contain:
- `name: purely for logging.
- `when`: condition on keys in the interpolation context (inputs, env, OS).
- `shell`: override shell type (`bash`/`powershell`).
- `workdir`: working directory for the step’s commands.
- `continue_on_error`: if `true`, keep going after a failing command.

## Step Types

```yaml
- name: Write AWS config
  write_file:
    path: "~/.aws/config"
    mode: "0600"
    content: |
      [default]
      region = {{AWS_DEFAULT_REGION}}
      output = {{AWS_DEFAULT_OUTPUT}}

```
- `path`: may contain `~` and interpolation.
- `content`: written as-is (block scalar).
- `mode`: octal string (e.g. `"0600"`).
- `append: true`: appends instead of overwriting.


## clone_repos
```yaml
- name: Clone repos (declarative)
  clone_repos:
    dest_unix: "{{WORKSPACE_ROOT}}"
    dest_windows: "{{WORKSPACE_ROOT_WIN}}"
    default_org: "{{DEFAULT_ORG}}"
    repos:
      - name: "SampleRepo"     # -> https://github.com/my-org/SampleRepo.git
        post_install:
          - argv: ["bash", "-lc", "if [ -f package.json ]; then npm ci; fi"]
          - "bash -lc 'if [ -f pyproject.toml ]; then pip install -e .; fi'"

      - name: "k8s-infra"
        post_install:
          - argv: ["bash", "-lc", "echo repo k8s-infra cloned"]

      - url: "https://github.com/someone/special-repo.git"
        # name absent -> derived from URL (special-repo)

```

URL resolution order:
1. repo.url if present.
2. https://github.com/{repo.org or default_org}/{repo.name}.git
3. If name contains / and no org/default_org, treat as org/name. post_install items are executed in each repo directory, using the same rules as run:
  - `{"argv": [...]}` -> executed as argv (no shell).
  - string -> executed via shell (`bash`/`powershell`).

`run`
Two forms:
- `argv` (preferred): safe, no shell parsing.
 
```yaml
run:
  - argv: ["echo", "hi-{{SESSION_NAME}}"]
```
- shell string: executed via `/bin/bash -lc` on Unix, `powershell` on Windows.

```yaml
run:
  - "echo shell-{{SESSION_NAME}}"
```

## Conditional Execution: `when`
The `when` field supports very simple expressions of the form
- `KEY==value`
- `KEY!=value`

The key is looked up in the interpolation context (inputs + env + `OS`).

Excample from the provided spec:
```yaml

- name: Create workspace root (linux)
  when: "OS!=windows"
  run:
    - mkdir -p "{{WORKSPACE_ROOT}}"

- name: Create workspace root (windows)
  when: "OS==windows"
  shell: powershell
  run:
    - New-Item -ItemType Directory -Force -Path "{{WORKSPACE_ROOT_WIN}}" | Out-Null
```

If the condition is false, the step is silently skipped (no banner printed).

---

## Workspace Root Safety Guard

If your spec defines:
- WORKSPACE_ROOT (Unix-like) or
- WORKSPACE_ROOT_WIN (Windows)

the runner checks whether that directory already exists before doing anything else.

If it exists, the run aborts with a message like:

```text

Workspace root already exists at /home/you/dev/workspace. Aborting to avoid clobbering an existing directory.
```

Exit code: `1`.

This prevents accidentally "re-bootstrapping" into an existing workspace and potentially clobbering things.

To proceed, either:
- Remove/rename the directory, or
- Change `WORKSPACE_ROOT`/`WORKSPACE_ROOT_WIN` in your spec.


## Environment Variables

Reserved knobs:

- FORGE_OS: overrides OS detection (useful for testing or forcing behavior).
  - Values: "windows", "ubuntu", "fedora", "arch".
- `FORGE_HOME`: overrides `HOME` and `USERPROFILE` for this process.
  - Useful in tests to contain all writes under a temp dir.

All other environment variables referenced in the spec are just normal env passes.

---

## Testing

Assuming you have `pytest` installed

```bash

# (from the repo root)
pytest
```

The tests exercise:
- Interpolation of `inputs` + `env`, including overrides via `--set`.
- `when: "OS!=windows"` / `when: "OS==windows"`.
- Shell selection (`bash` vs `powershell`).
- `write_file` path/content/append/mode behavior.
- `clone_repos` URL resolution and `post_install` hooks.
- `run` parsing for `argv` and shell items.
- `FORGE_OS` override behavior.
- Step ordering (`common` before `os.<oskey>`).
- Workspace guard for `WORKSPACE_ROOT` / `WORKSPACE_ROOT_WIN`.
- Multistep simulation test