"""Warnings for worker CLIs that differ from the adapter-verified versions."""

import json
import os
import sys
import unittest

from test_crew import FAKE, Base, crew
from crew import adapters


class VersionWarningUnitTests(unittest.TestCase):
    def test_version_parsing_and_warnings(self):
        verified = adapters.VERIFIED_VERSIONS
        self.assertIsNone(adapters.version_warning("codex", f"codex-cli {verified['codex']}"))
        self.assertIsNone(adapters.version_warning("claude", f"{verified['claude']} (Claude Code)"))
        self.assertIsNone(adapters.version_warning("agy", None))
        self.assertIn("differs from the adapter-verified", adapters.version_warning("agy", "9.9.9"))
        self.assertIn("no version number", adapters.version_warning("codex", "codex-cli dev"))
        self.assertEqual(adapters.version_number("2.1.330 (Claude Code)"), "2.1.330")


class VersionWarningCliTests(Base):
    def set_version(self, agent: str, text: str) -> None:
        script = f"import sys; print({text!r}) if '--version' in sys.argv else None"
        os.environ[f"CREW_BIN_{agent.upper()}"] = json.dumps([sys.executable, "-c", script])

    def test_detect_and_run_summary(self):
        self.set_version("codex", f"codex-cli {adapters.VERIFIED_VERSIONS['codex']}")
        self.set_version("claude", "2.0.0 (Claude Code)")
        code, out, _ = crew("detect")
        self.assertEqual(code, 0)
        self.assertIn("VERSION WARNING: claude 2.0.0 differs from the adapter-verified", out)
        self.assertNotIn("VERSION WARNING: codex", out)
        data = json.loads(crew("detect", "--json")[1])["clis"]
        self.assertIsNone(data["codex"]["version_warning"])
        self.assertEqual(data["claude"]["verified_version"], adapters.VERIFIED_VERSIONS["claude"])
        os.environ["CREW_BIN_CODEX"] = json.dumps(FAKE + ["codex"])
        _, state = self.run_task()  # fake codex worker reports "fake-codex 1.0"
        self.assertIn("VERSION WARNING: codex 1.0 differs", crew("check", "T-1")[1])
        self.assertEqual(state["cli_version"], "fake-codex 1.0")

    def test_prerelease_is_not_the_verified_version(self):
        want = adapters.VERIFIED_VERSIONS["codex"]
        self.assertIn("differs", adapters.version_warning("codex", f"codex-cli {want}-beta.1"))
        self.assertIsNone(adapters.version_warning("codex", f"codex-cli {want}"))
