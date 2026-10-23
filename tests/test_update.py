import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from q4r import __version__
from q4r.cli import main
from q4r import update


SHA = "a" * 40
NEW = "999.0.0"


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        for target, value in (("sys.stdout", self.output),):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.output.isatty = lambda: True
        terminal = patch("sys.stdin.isatty", return_value=True)
        terminal.start()
        self.addCleanup(terminal.stop)
        latest = patch("q4r.update.latest_version", return_value=(NEW, SHA))
        self.latest = latest.start()
        self.addCleanup(latest.stop)

    def test_numeric_versions_reject_untrusted_metadata(self):
        self.assertGreater(update.version_key("0.10.0"), update.version_key("0.9.9"))
        for value in (None, "v1.0.0", "1.0", "1.0.0; echo x", "1.0.0rc1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                update.version_key(value)

    def test_version_metadata_is_pinned_to_validated_commit_without_executing_it(self):
        # Call the saved implementation while the startup fixture stays isolated.
        with patch("q4r.update.read_metadata", side_effect=[json.dumps({"object":{"sha":SHA}}),
                   '__version__ = "1.2.3"\nraise RuntimeError("must not execute")\n']) as reader:
            self.assertEqual(_real_latest(), ("1.2.3", SHA))
        self.assertIn(SHA, reader.call_args_list[1].args[0])

    def test_invalid_commit_prevents_raw_file_request(self):
        with patch("q4r.update.read_metadata", return_value='{"object":{"sha":"bad;command"}}') as reader:
            with self.assertRaises(ValueError):
                _real_latest()
            reader.assert_called_once()

    def test_metadata_has_timeout_and_size_limit(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"x" * (update.MAX_METADATA + 1)
        with patch("q4r.update.urlopen", return_value=response) as open_url:
            with self.assertRaises(ValueError):
                update.read_metadata(update.API)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 3)
        response.read.assert_called_once_with(update.MAX_METADATA + 1)

    def test_yes_installs_then_restarts_preserving_scan_arguments(self):
        args = ["scan", "--username", "demo", "--browser"]
        with patch("builtins.input", return_value="Y"), patch("q4r.update.install_update") as install, \
             patch("q4r.update.os.execv") as restart:
            self.assertEqual(update.check_update(args), 0)
        install.assert_called_once_with(NEW, SHA)
        self.assertEqual(restart.call_args.args[1][1:], ["-I", "-m", "q4r", "--no-update-check", *args])
        self.assertIn("A Q4R update is available", self.output.getvalue())

    def test_no_or_enter_continues_without_installing(self):
        for answer in ("N", "n", "", " n "):
            with self.subTest(answer=answer), patch("builtins.input", return_value=answer), \
                 patch("q4r.update.install_update") as install:
                self.assertIsNone(update.check_update([]))
                install.assert_not_called()

    def test_invalid_choice_reprompts_and_lowercase_yes_works(self):
        with patch("builtins.input", side_effect=["u", "maybe", "y"]) as answer, \
             patch("q4r.update.install_update") as install:
            self.assertEqual(update.check_update([], explicit=True), 0)
        self.assertEqual(answer.call_count, 3)
        install.assert_called_once()

    def test_eof_continues_and_interrupt_has_a_clean_exit(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertIsNone(update.check_update([]))
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertEqual(update.check_update([]), 130)

    def test_current_or_older_remote_never_offers_downgrade(self):
        for version in (__version__, "0.0.1"):
            self.latest.return_value = (version, SHA)
            with patch("builtins.input", side_effect=AssertionError("prompted")):
                self.assertIsNone(update.check_update([]))
                self.assertEqual(update.check_update([], explicit=True), 0)

    def test_network_failure_does_not_block_scan(self):
        self.latest.side_effect = OSError("offline")
        self.assertIsNone(update.check_update([]))
        self.assertEqual(update.check_update([], explicit=True), 1)
        self.assertIn("Update check unavailable", self.output.getvalue())

    def test_check_interruption_exits_without_a_traceback(self):
        self.latest.side_effect = KeyboardInterrupt
        self.assertEqual(update.check_update([]), 130)

    def test_skip_flag_works_before_or_after_scan_subcommand(self):
        for args in (["--no-update-check", "scan", "--username", "demo"],
                     ["scan", "--username", "demo", "--no-update-check"]):
            with self.subTest(args=args), patch("q4r.cli.automatic_update", return_value=0) as automatic:
                self.assertEqual(main(args), 0)
                self.assertTrue(automatic.call_args.args[1])

    def test_check_only_or_noninteractive_never_installs(self):
        with patch("builtins.input", side_effect=AssertionError("prompted")):
            self.assertEqual(main(["update", "--check"]), 0)
            with patch("sys.stdin.isatty", return_value=False):
                self.assertEqual(main(["update"]), 0)

    def test_automatic_check_is_skipped_for_offline_disabled_and_noninteractive(self):
        with patch.dict("os.environ", {"Q4R_NO_UPDATE_CHECK":"1"}):
            self.assertIsNone(update.automatic_update([]))
        with patch.dict("os.environ", {"Q4R_NO_UPDATE_CHECK":""}):
            self.assertIsNone(update.automatic_update([], disabled=True))
            self.assertIsNone(update.automatic_update([], offline=True))
            with patch("sys.stdin.isatty", return_value=False):
                self.assertIsNone(update.automatic_update([]))
        self.latest.assert_not_called()

    def test_local_commands_help_and_version_never_check_for_updates(self):
        main(["sources"])
        with tempfile.TemporaryDirectory() as temp:
            main(["scan", "--username", "demo", "--offline", "--out", temp + "/case"])
            main(["results", temp + "/case"])
            main(["verify", temp + "/case"])
            main(["queries", temp + "/case"])
        for flag in ("--help", "--version"):
            with self.assertRaises(SystemExit):
                main([flag])
        self.latest.assert_not_called()

    def test_successful_startup_update_returns_before_target_prompts(self):
        with patch("q4r.cli.automatic_update", return_value=0), \
             patch("builtins.input", side_effect=AssertionError("target prompt")):
            self.assertEqual(main([]), 0)

    def test_failed_install_never_claims_success_or_restarts(self):
        with patch("builtins.input", return_value="y"), \
             patch("q4r.update.install_update", side_effect=subprocess.CalledProcessError(1, ["pip"])), \
             patch("q4r.update.os.execv") as restart:
            self.assertEqual(update.check_update([]), 1)
        restart.assert_not_called()
        self.assertNotIn("installed successfully", self.output.getvalue())

    def test_installer_uses_argument_list_and_verifies_in_isolated_interpreter(self):
        with patch("q4r.update.installation_command", return_value=(["pipx", "install", "--force", "official.zip"], {})), \
             patch("q4r.update.subprocess.run", side_effect=[Mock(), Mock(stdout=NEW+"\n")]) as run:
            update.install_update(NEW, SHA)
        self.assertIsInstance(run.call_args_list[0].args[0], list)
        self.assertTrue(run.call_args_list[0].kwargs["check"])
        self.assertEqual(run.call_args_list[1].args[0][1:3], ["-I", "-c"])
        self.assertIn("installed successfully", self.output.getvalue())

    def test_wrong_installed_version_is_a_failure(self):
        with patch("q4r.update.installation_command", return_value=(["installer"], {})), \
             patch("q4r.update.subprocess.run", side_effect=[Mock(), Mock(stdout="0.0.1\n")]):
            with self.assertRaises(ValueError):
                update.install_update(NEW, SHA)

    def test_installer_refuses_system_python_or_source_shadowing(self):
        with patch("q4r.update.sys.prefix", "/system"), patch("q4r.update.sys.base_prefix", "/system"):
            with self.assertRaises(ValueError):
                update.installation_command(SHA)
        with tempfile.TemporaryDirectory() as temp, patch("q4r.update.sys.prefix", temp):
            with self.assertRaisesRegex(ValueError, "Running from source"):
                update.installation_command(SHA)

    def test_pipx_update_targets_running_home_and_keeps_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)/"venvs"/"q4r-osint"
            prefix.mkdir(parents=True)
            (prefix/"pipx_metadata.json").write_text(json.dumps({"main_package":{"package":"q4r-osint"}}))
            with patch("q4r.update.sys.prefix", str(prefix)), \
                 patch("q4r.update.__file__", str(prefix/"lib"/"q4r"/"update.py")), \
                 patch("q4r.update.shutil.which", return_value="/bin/pipx"):
                command, env = update.installation_command(SHA)
            self.assertEqual(command[:3], ["/bin/pipx", "install", "--force"])
            self.assertEqual(command[-1], f"https://github.com/{update.REPOSITORY}/archive/{SHA}.zip")
            self.assertEqual(env["PIPX_HOME"], temp)
            self.assertEqual(env["PIP_NO_INPUT"], "1")

    def test_virtualenv_update_uses_its_own_python_and_only_official_archive(self):
        with tempfile.TemporaryDirectory() as temp, patch("q4r.update.sys.prefix", temp), \
             patch("q4r.update.__file__", str(Path(temp)/"lib"/"q4r"/"update.py")):
            command, _ = update.installation_command(SHA)
            self.assertEqual(command[1:5], ["-m", "pip", "install", "--upgrade"])
            with self.assertRaises(ValueError):
                update.installation_command("https://untrusted.example/code")


# Keep the implementation before per-test network fixtures replace its module attribute.
_real_latest = update.latest_version


if __name__ == "__main__":
    unittest.main()
