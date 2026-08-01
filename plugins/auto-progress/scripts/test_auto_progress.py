from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import auto_progress


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_template_is_valid(self) -> None:
        config = auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
        validated = auto_progress.validate_config(config)
        self.assertEqual(
            "feature/your-base-branch", validated["project"]["base_branch"]
        )
        self.assertEqual(4, validated["schema_version"])
        self.assertEqual(
            ["AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"],
            [item["path"] for item in validated["repository_guidance"]["documents"]],
        )
        self.assertEqual(
            "docs/auto-progress/rejection-rules.md",
            validated["paths"]["rejection_rules"],
        )

    def test_shell_expression_is_rejected(self) -> None:
        config = auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
        config["validation"]["steps"][0]["program"] = "dotnet && echo unsafe"
        with self.assertRaises(auto_progress.AutoProgressError):
            auto_progress.validate_config(config)

    def test_version_one_requires_explicit_migration(self) -> None:
        config = auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
        config["schema_version"] = 1
        with self.assertRaisesRegex(
            auto_progress.AutoProgressError, "configure-auto-progress migrate"
        ):
            auto_progress.validate_config(config)

    def test_v3_migration_adds_v4_policy_and_individual_guidance_shas(self) -> None:
        config = auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
        config["schema_version"] = 3
        config.pop("repository_guidance")
        config["paths"].pop("rejection_rules")
        shas = ["1" * 40, "", "3" * 40]

        with patch(
            "auto_progress.git",
            return_value=type("GitResult", (), {"stdout": "true\n"})(),
        ), patch(
            "auto_progress._repository_guidance_blob_sha", side_effect=shas
        ) as blob_sha:
            migrated = auto_progress.migrate_config_v4(config, repo=Path("repo"))

        self.assertEqual(4, migrated["schema_version"])
        self.assertEqual(
            "docs/auto-progress/rejection-rules.md",
            migrated["paths"]["rejection_rules"],
        )
        self.assertEqual(
            shas,
            [item["blob_sha"] for item in migrated["repository_guidance"]["documents"]],
        )
        self.assertEqual(
            ["AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md"],
            [call.args[1] for call in blob_sha.call_args_list],
        )

    def test_v4_migration_rejects_non_v3_source(self) -> None:
        config = auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
        with self.assertRaisesRegex(auto_progress.AutoProgressError, "only schema_version 3"):
            auto_progress.migrate_config_v4(config, repo=Path("repo"))


class IdentityTests(unittest.TestCase):
    def test_id_format_uses_dotted_date(self) -> None:
        with patch("auto_progress.secrets.token_hex", return_value="a1b2c3d4"):
            generated = auto_progress.make_id(
                "improvement",
                "Asia/Shanghai",
                datetime.fromisoformat("2026-07-29T11:00:00+00:00"),
            )
        self.assertEqual("IMP-2026.07.29-a1b2c3d4", generated)

    def test_remote_forms_normalize_equally(self) -> None:
        https = auto_progress.normalize_remote(
            "https://github.com/ExampleOwner/ExampleUnityProject.git"
        )
        ssh = auto_progress.normalize_remote(
            "git@github.com:ExampleOwner/ExampleUnityProject.git"
        )
        self.assertEqual(https, ssh)
        self.assertEqual(
            auto_progress.make_project_id(https, "feature"),
            auto_progress.make_project_id(ssh, "feature"),
        )


class LedgerTests(unittest.TestCase):
    def event(self, event_id: str, event_type: str, **extra: object) -> dict[str, object]:
        return {
            "event_id": event_id,
            "maintenance_day": "2026-07-30",
            "event_type": event_type,
            "timestamp": "2026-07-30T19:30:00+08:00",
            **extra,
        }

    def test_monthly_append_deduplication_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_id = "example-unity-project-test"
            auto_progress.append_ledger(
                self.event(
                    "EVT-2026.07.30-00000001",
                    "commit_created",
                    improvement_id="IMP-2026.07.30-a1b2c3d4",
                ),
                project_id,
                root,
                "Asia/Shanghai",
            )
            auto_progress.append_ledger(
                self.event(
                    "EVT-2026.07.30-00000002",
                    "pr_opened",
                    improvement_id="IMP-2026.07.30-a1b2c3d4",
                    pull_request=42,
                ),
                project_id,
                root,
                "Asia/Shanghai",
            )
            path = root / "example-unity-project-test-2026-07.jsonl"
            self.assertTrue(path.exists())
            events = auto_progress.read_events([path])
            summary = auto_progress.summarize_events(events)
            self.assertEqual(1, summary["completed_days"])
            self.assertEqual(1, summary["pr_opened"])
            self.assertEqual(0, summary["allowance_days"])
            self.assertEqual(0, summary["implementation"]["implemented"])
            with self.assertRaises(auto_progress.AutoProgressError):
                auto_progress.append_ledger(
                    self.event("EVT-2026.07.30-00000001", "run_failed"),
                    project_id,
                    root,
                    "Asia/Shanghai",
                )

    def test_absolute_paths_and_sensitive_keys_are_rejected(self) -> None:
        event = self.event("EVT-2026.07.30-00000003", "run_failed")
        event["token"] = "do-not-store"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(auto_progress.AutoProgressError):
                auto_progress.append_ledger(
                    event, "test", Path(directory), "Asia/Shanghai"
                )

    def test_daily_allowance_is_atomic_and_allows_only_same_task_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.fromisoformat("2026-07-30T19:30:00+08:00")
            with patch(
                "auto_progress.secrets.token_hex",
                side_effect=["00000001", "00000002", "00000003"],
            ):
                first = auto_progress.claim_daily_allowance(
                    "example-unity-project-test",
                    root,
                    "Asia/Shanghai",
                    "RUN-2026.07.30-a1b2c3d4",
                    "implement-batch",
                    "scheduled",
                    now,
                )
                retry = auto_progress.claim_daily_allowance(
                    "example-unity-project-test",
                    root,
                    "Asia/Shanghai",
                    "RUN-2026.07.30-a1b2c3d4",
                    "implement-batch",
                    "scheduled",
                    now,
                )
                self.assertTrue(first["claimed"])
                self.assertTrue(retry["safe_retry"])
                with self.assertRaisesRegex(
                    auto_progress.AutoProgressError, "already claimed"
                ):
                    auto_progress.claim_daily_allowance(
                        "example-unity-project-test",
                        root,
                        "Asia/Shanghai",
                        "RUN-2026.07.30-deadbeef",
                        "implement-batch",
                        "scheduled",
                        now,
                    )

            summary = auto_progress.summarize_events(
                auto_progress.read_events(
                    auto_progress.ledger_files(root, "example-unity-project-test")
                )
            )
            self.assertEqual(1, summary["allowance_days"])
            self.assertEqual(
                {"implement-batch": 1},
                summary["allowance_days_by_task_type"],
            )

    def test_manual_implementation_and_discovery_are_allowance_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for task_type in ("implement-batch", "discover-improvements"):
                result = auto_progress.claim_daily_allowance(
                    "example-unity-project-test",
                    root,
                    "Asia/Shanghai",
                    "RUN-2026.07.30-a1b2c3d4",
                    task_type,
                    "manual",
                )
                self.assertTrue(result["exempt"])
            self.assertEqual([], list(root.glob("*.jsonl")))

    def test_discovery_commit_does_not_complete_implementation_day(self) -> None:
        events = [
            self.event(
                "EVT-2026.07.30-00000004",
                "daily_allowance_claimed",
                run_id="RUN-2026.07.30-a1b2c3d4",
                task_type="discover-improvements",
            ),
            self.event(
                "EVT-2026.07.30-00000005",
                "commit_created",
                run_id="RUN-2026.07.30-a1b2c3d4",
                task_type="discover-improvements",
            ),
            self.event(
                "EVT-2026.07.30-00000006",
                "branch_pushed",
                run_id="RUN-2026.07.30-a1b2c3d4",
                task_type="discover-improvements",
            ),
        ]
        summary = auto_progress.summarize_events(events)
        self.assertEqual(0, summary["completed_days"])
        self.assertEqual(0, summary["pushed_days"])
        self.assertEqual(0, summary["allowance_days"])
        self.assertEqual(1, summary["legacy_allowance_days"])
        self.assertEqual(1, summary["discovery"]["sessions"])


if __name__ == "__main__":
    unittest.main()
