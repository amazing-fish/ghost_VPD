"""Run Ghost's offline CI locally, with pinned dependencies and durable evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "vpd_skill"


def default_state_dir() -> Path:
    if os.name == "nt" and Path("D:/").is_dir():
        return Path("D:/ghost-ci")
    return ROOT / ".local-ci"


def requirements(path: Path) -> dict[str, str]:
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            name, version = line.split("==")
            pins[name.strip()] = version.strip()
    return pins


def verify_environment(python: str, pins: dict[str, str], env: dict) -> dict:
    code = "import sys,json,importlib.metadata as m; print(json.dumps({'python':list(sys.version_info[:3]),'packages':{n:m.version(n) for n in json.loads(sys.argv[1])}}))"
    result = subprocess.run([python, "-c", code, json.dumps(pins)], env=env,
                            check=True, capture_output=True, text=True)
    info = json.loads(result.stdout)
    if info["python"][:2] != [3, 12]:
        raise RuntimeError(f"CI requires Python 3.12.x, got {info['python']}")
    if info["packages"] != pins:
        raise RuntimeError(f"Pinned dependency mismatch: expected {pins}, got {info['packages']}")
    return info


def run_stage(name: str, command: list[str], run_dir: Path, env: dict) -> int:
    print(f"[{name}] {subprocess.list2cmdline(command)}", flush=True)
    with (run_dir / f"{name}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=SKILL, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                   errors="replace")
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True,
                                   encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python 3.12 executable")
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--skip-install", action="store_true",
                        help="Use --python unchanged; still verify every pinned version. Installer is NOT tested.")
    args = parser.parse_args(argv)
    state = args.state_dir.expanduser().resolve()
    # A unique run directory means pytest never clears a previous run's evidence.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = state / "runs" / run_id
    tmp_dir = run_dir / "tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create evidence directory {run_dir}: {exc}", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env.update(TEMP=str(tmp_dir), TMP=str(tmp_dir), TMPDIR=str(tmp_dir),
               PIP_CACHE_DIR=str(state / "pip-cache"), PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    # Host-level test selection must not silently turn a full run into a subset.
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env.pop("PYTHONPATH", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    evidence = {"status": "running", "platform": platform.platform(),
                "installer": "skipped" if args.skip_install else "pending",
                "run_dir": str(run_dir), "stages": {}}
    result_path = run_dir / "result.json"
    print(f"Evidence: {run_dir}", flush=True)
    exit_code = 1
    try:
        evidence["head"] = git_output("rev-parse", "HEAD")
        evidence["working_tree"] = git_output("status", "--porcelain")
        evidence["requirements_sha256"] = hashlib.sha256((SKILL / "requirements.txt").read_bytes()).hexdigest()
        python = args.python
        if not args.skip_install:
            # Reject unsupported bootstrap Python before creating/installing anything.
            version = subprocess.check_output([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], text=True).strip()
            if version != "3.12":
                raise RuntimeError(f"CI requires Python 3.12.x, got {version}")
            if os.name == "nt":
                shell = shutil.which("pwsh") or shutil.which("powershell")
                if not shell:
                    raise RuntimeError("PowerShell is required for the Windows installer checks")
                command = [shell, "-NoProfile", "-File", str(SKILL / "install.ps1"), "-Python", python, "-SkipDoctor"]
            else:
                venv = SKILL / ".venv"
                if not (venv / "bin" / "python").is_file():
                    subprocess.run([python, "-m", "venv", str(venv)], env=env, check=True)
                command = [str(venv / "bin" / "python"), "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"]
            evidence["install_command"] = command
            code = run_stage("install", command, run_dir, env)
            evidence["stages"]["install"] = code
            evidence["installer"] = "passed" if code == 0 else "failed"
            if code:
                raise RuntimeError(f"Install failed (exit {code}); pytest was not run")
            python = str(SKILL / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        evidence["environment"] = verify_environment(python, requirements(SKILL / "requirements.txt"), env)
        command = [python, "-m", "pytest", "-q", "-o", "tmp_path_retention_policy=failed",
                   "-o", f"cache_dir={run_dir / 'pytest-cache'}",
                   f"--junitxml={run_dir / 'junit.xml'}"]
        evidence["pytest_command"] = command
        exit_code = run_stage("pytest", command, run_dir, env)
        evidence["stages"]["pytest"] = exit_code
        evidence["status"] = "passed" if exit_code == 0 else "failed"
    except KeyboardInterrupt:
        evidence["status"] = "interrupted"
        exit_code = 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        evidence["status"] = "blocked"
        evidence["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    finally:
        evidence["exit_code"] = exit_code
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        result_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{evidence['status']}: {result_path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
