"""Standalone update worker: stdlib only, run by Python outside the target venv."""
import base64
import json
import os
import subprocess
import sys


def run_plan(plan):
    try:
        subprocess.run(plan["command"], check=True)
        actual = subprocess.run([plan["python"], "-I", "-c",
                                 "from q4r import __version__; print(__version__)"],
                                capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        if actual != plan["version"]:
            raise ValueError("Installed version verification failed; expected " + plan["version"])
        print("Q4R " + actual + " installed successfully.", flush=True)
    except KeyboardInterrupt:
        print("\nUpdate interrupted. Run q4r --version to check the installation.")
        return 130
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Update failed: " + str(exc))
        print("The update was not confirmed. Run q4r --version to check the installation.")
        return 1
    if plan["restart_args"] is not None:
        print("Restarting Q4R...", flush=True)
        try:
            arguments = [plan["python"], "-I", "-m", "q4r", "--no-update-check", *plan["restart_args"]]
            if os.name == "nt":
                arguments = [subprocess.list2cmdline([arg]) for arg in arguments]
            os.execv(plan["python"], arguments)
        except OSError as exc:
            print("Update installed; automatic restart failed: " + str(exc) + ". Run q4r again.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_plan(json.loads(base64.urlsafe_b64decode(sys.argv[1]))))
