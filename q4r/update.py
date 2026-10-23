"""Opt-in installation of a newer version from the fixed official repository."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen

from . import __version__


REPOSITORY = "gameleader16161-dotcom/Q4R-OSINT"
API = "https://api.github.com/repos/" + REPOSITORY
MAX_METADATA = 128 * 1024


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", value):
        raise ValueError("Invalid stable version")
    return tuple(int(part) for part in value.split("."))


def read_metadata(url):
    request = Request(url, headers={"User-Agent": "Q4R/" + __version__, "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=3) as response:
        body = response.read(MAX_METADATA + 1)
    if len(body) > MAX_METADATA:
        raise ValueError("Update metadata is too large")
    return body.decode("utf-8")


def latest_version():
    # Pin the version check and installer to the same commit, even if main moves.
    head = json.loads(read_metadata(API + "/git/ref/heads/main"))
    sha = head.get("object", {}).get("sha", "")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid official commit")
    source = read_metadata(f"https://raw.githubusercontent.com/{REPOSITORY}/{sha}/q4r/__init__.py")
    match = re.search(r'^__version__ = "([0-9.]+)"$', source, re.MULTILINE)
    if not match:
        raise ValueError("Version was not published")
    version_key(match[1])
    return match[1], sha


def installation_command(sha):
    """Update the running environment; never invoke sudo or modify system Python."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid official commit")
    prefix = Path(sys.prefix).resolve()
    if prefix == Path(sys.base_prefix).resolve():
        raise ValueError("Automatic installation needs a pipx or virtualenv install. Install Q4R with bash install.sh first.")
    # A checkout can shadow an installed package when using python -m q4r.
    if not Path(__file__).resolve().is_relative_to(prefix):
        raise ValueError("Running from source. Launch the installed q4r command to use automatic updates.")
    archive = f"https://github.com/{REPOSITORY}/archive/{sha}.zip"
    env = os.environ.copy()
    env["PIP_NO_INPUT"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    metadata = prefix / "pipx_metadata.json"
    if metadata.is_file():
        data = json.loads(metadata.read_text(encoding="utf-8"))
        if data.get("main_package", {}).get("package") != "q4r-osint" or prefix.name != "q4r-osint":
            raise ValueError("This pipx environment is customized. Update it using its original installation command.")
        pipx = shutil.which("pipx")
        if not pipx:
            raise ValueError("pipx is unavailable on PATH; restore pipx before updating.")
        # Match the running pipx home, including custom or global installations.
        env["PIPX_HOME"] = str(prefix.parent.parent)
        return [pipx, "install", "--force", archive], env
    return [sys.executable, "-m", "pip", "install", "--upgrade", archive], env


def install_update(version, sha):
    version_key(version)
    command, env = installation_command(sha)
    print("Updating Q4R from the official repository...", flush=True)
    subprocess.run(command, env=env, check=True)
    # Isolated Python prevents an old clone in the current directory shadowing the install.
    actual = subprocess.run([sys.executable, "-I", "-c",
                             "from q4r import __version__; print(__version__)"],
                            capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    if actual != version:
        raise ValueError("Installed version verification failed; expected " + version)
    print("Q4R " + version + " installed successfully.", flush=True)


def check_update(argv, explicit=False, check_only=False):
    """Return None to continue scanning, or an exit code after an explicit action."""
    try:
        version, sha = latest_version()
        if version_key(version) <= version_key(__version__):
            if explicit:
                print("Q4R is up to date (" + __version__ + ").")
            return 0 if explicit else None
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        # A network/rate-limit failure must not prevent a normal scan.
        print("[Q4R] Update check unavailable (" + type(exc).__name__ + "); continuing.")
        return 1 if explicit else None
    except KeyboardInterrupt:
        print("\nUpdate check interrupted.")
        return 130
    print(f"A Q4R update is available: {__version__} -> {version}")
    if check_only or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Run q4r update in a terminal to install it.")
        return 0 if explicit else None
    try:
        while True:
            choice = input("Would you like to update? [Y/N] (default: N): ").strip().casefold()
            if choice in ("", "n"):
                return 0 if explicit else None
            if choice == "y":
                break
            print("Please enter Y to update or N to continue.")
        install_update(version, sha)
    except EOFError:
        return 0 if explicit else None
    except KeyboardInterrupt:
        print("\nUpdate interrupted. Run q4r --version to check the installation.")
        return 130
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print("Update failed: " + str(exc))
        print("The update was not confirmed. Run q4r --version to check the installation.")
        return 1
    if explicit:
        return 0
    print("Restarting Q4R...", flush=True)
    # Restart in a clean interpreter; don't keep running old imported modules.
    try:
        os.execv(sys.executable, [sys.executable, "-I", "-m", "q4r", "--no-update-check", *argv])
    except OSError as exc:
        print("Update installed; automatic restart failed: " + str(exc) + ". Run q4r again.")
        return 1
    return 0


def automatic_update(argv, disabled=False, offline=False):
    if disabled or offline or os.getenv("Q4R_NO_UPDATE_CHECK") == "1":
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return check_update(argv)
