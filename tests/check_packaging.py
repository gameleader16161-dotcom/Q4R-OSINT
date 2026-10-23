"""Build and install from a checkout containing generated reports and a nested clone.

This runs separately from unit tests because it creates an isolated environment
and lets pip provision the declared build backend.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile


def run(command, directory):
    subprocess.run([str(x) for x in command], cwd=directory, check=True)


def check(project, work_dir=None):
    if work_dir:
        work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="q4r-package-", dir=work_dir) as temp:
        root = Path(temp).resolve()
        checkout = root / "checkout"
        checkout.mkdir()
        # Copy source explicitly: never copy the user's real case evidence into a build test.
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(project / name, checkout / name)
        shutil.copytree(project / "q4r", checkout / "q4r", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cases = checkout / "cases" / "example-case"
        cases.mkdir(parents=True)
        (cases / "report.json").write_text('{"synthetic_case": true}', encoding="utf-8")
        unrelated = checkout / "unrelated_package"
        unrelated.mkdir()
        (unrelated / "__init__.py").write_text("# Must never be installed.\n", encoding="utf-8")
        nested = checkout / "Q4R-OSINT" / "q4r"
        nested.mkdir(parents=True)
        (nested / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
        wheels = root / "wheels"
        run([sys.executable, "-m", "pip", "wheel", checkout, "--no-deps", "--no-cache-dir", "--wheel-dir", wheels], root)
        wheel, = wheels.glob("q4r_osint-*.whl")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            unexpected = [name for name in names if name.split("/", 1)[0] != "q4r"
                          and not name.split("/", 1)[0].endswith(".dist-info")]
            if unexpected:
                raise AssertionError("Unexpected files in wheel: " + repr(unexpected))
            if "q4r/sites.json" not in names:
                raise AssertionError("Installed search registry is missing")
        environment = root / "installed"
        venv.EnvBuilder(with_pip=True).create(environment)
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binary / ("python.exe" if os.name == "nt" else "python")
        command = binary / ("q4r.exe" if os.name == "nt" else "q4r")
        run([python, "-m", "pip", "install", "--no-deps", wheel], root)
        expected = re.search(r'__version__ = "([^"]+)"', (project / "q4r" / "__init__.py").read_text()).group(1)
        actual = subprocess.check_output([str(command), "--version"], cwd=root, text=True).strip()
        if actual != "Q4R " + expected:
            raise AssertionError(f"Installed command is stale: {actual}; expected {expected}")
        # -I and a working directory outside the checkout prevent accidentally importing source.
        run([python, "-I", "-c", "from importlib.metadata import version; from q4r import __version__; "
             "from q4r.core import load_sites; assert version('q4r-osint') == __version__; assert len(load_sites()) == 48"], root)
        run([command, "scan", "--username", "example_handle", "--offline", "--out", root / "installed-case"], root)
        run([command, "verify", root / "installed-case"], root)
        # Exercise the real standalone worker and exec restart without a live update.
        # The package was genuinely installed above; only the installer command is a fixture.
        worker = subprocess.check_output([str(python), "-I", "-c",
                                         "import q4r.update_worker as w; print(w.__file__)"], cwd=root, text=True).strip()
        plan = dict(command=[str(python), "-I", "-c", "print('Installer fixture completed')"],
                    python=str(python), version=expected, restart_args=["--version"])
        payload = base64.urlsafe_b64encode(json.dumps(plan).encode()).decode("ascii")
        output = subprocess.check_output([sys.executable, "-I", worker, payload], cwd=root, text=True, timeout=30)
        if "installed successfully" not in output or "Restarting Q4R..." not in output or output.splitlines()[-1] != "Q4R " + expected:
            raise AssertionError("Standalone update worker/restart failed: " + output)
        print("PASS: standalone worker verifies the installed version and restarts isolated Q4R.")
        print("PASS: dirty checkout builds; generated cases and nested clones are excluded; installed CLI and registry work.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    check(Path(__file__).resolve().parents[1], args.work_dir)
