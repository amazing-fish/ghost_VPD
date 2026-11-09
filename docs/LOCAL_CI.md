# Local CI

Daily Ghost checks run on the developer's computer, not GitHub-hosted runners.
No device is required: this is the existing offline pytest suite, not an APP build
or real-device acceptance test.

## Windows (D drive)

Keep the checkout/worktree on D: so `vpd_skill/.venv` is also on D:.
From the repository root, using Python 3.12.x:

```powershell
python scripts/run_local_ci.py
```

The runner executes the existing `install.ps1` (without skill linking), verifies
all exact versions in `vpd_skill/requirements.txt`, and runs the full pytest suite.
It does not change execution policy, paging/virtual-memory settings, install a
self-hosted runner, configure a service, or connect to a real device.

When D: exists, the default state directory is `D:/ghost-ci`: pip cache, temporary
files, pytest cache, logs and JUnit results all go there. On other machines it is
`.local-ci/` under the checkout. Override explicitly when needed:

```powershell
python scripts/run_local_ci.py --state-dir D:/Develop/Cache/ghost-ci --python D:/Develop/Tool/Miniconda/python.exe
```

Each invocation creates a fresh `runs/<UTC-time>-<random>/` directory containing
`install.log`, `pytest.log`, `junit.xml` and `result.json`. The result records HEAD,
dirty working-tree state, OS, Python/package versions, commands and stage exits.
Successful pytest temporary fixtures are discarded by pytest's existing retention
policy; assertion-failure fixtures remain. Previous runs are never deleted by the
runner. Review and remove obsolete run directories manually when disk space is low.

## Already provisioned / offline environment

To use an existing Python environment without changing it:

```powershell
python scripts/run_local_ci.py --skip-install --python D:/path/to/.venv/Scripts/python.exe
```

Every pinned dependency and Python 3.12 are still checked before tests. A mismatch
or missing dependency fails closed. `installer: skipped` explicitly means the
Windows installation flow was not checked. Do not label that run an installer pass.
Default installation needs the configured package index when a pinned wheel is not
already installed/cached. Do not replace pinned versions to work around blocked access.

## Linux

Use the same command from a Linux checkout with Python 3.12.x; it creates
`vpd_skill/.venv`, installs the same requirements and runs the same full suite.
Specify a local writable `--state-dir` as appropriate. Linux and native Windows
results remain separate evidence: a Windows pass does not imply Linux/WSL passed.
Do not enable WSL or change security settings just to run this command.

## Cloud fallback and transition

The GitHub workflow has **no pull_request, push or schedule triggers**. Manual
`workflow_dispatch` remains only as an explicit emergency fallback, and its jobs
are skipped unless `allow_cloud_minutes` is set to true. That option can consume
hosted-runner quota. The local runner never dispatches or reruns Actions.

These changes only become the default-branch policy when merged. Until then,
maintainers should disable `VPD Skill tests` in GitHub Actions and cancel active
runs if they must immediately stop quota usage. Existing queued/running jobs are
not cancelled by changing YAML. Branch protection requiring the old hosted check
may need a maintainer decision; this change does not bypass or edit protections.

Before merging later changes, attach the applicable local `result.json`/test
summary to the review and retain failure logs. No cloud green badge is expected
for ordinary pushes/PRs, and missing Linux evidence must remain visible.
