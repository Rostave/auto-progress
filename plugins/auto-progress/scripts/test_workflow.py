from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auto_progress
import workflow


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "RUN-2026.08.01-a1b2c3d4"


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): "
            f"{completed.stderr or completed.stdout}"
        )
    return completed.stdout.strip()


def valid_config() -> dict[str, object]:
    config = copy.deepcopy(
        auto_progress.load_config(PLUGIN_ROOT / "assets" / "auto-progress.toml")
    )
    config["schema_version"] = 3
    config["tools"] = {"source_control": "git", "review_host": "github"}
    config["workspace"] = {"additional_ignore_patterns": []}
    config["unity_mcp"] = {"mode": "disabled"}
    return config


class ConfigV3Tests(unittest.TestCase):
    def test_v3_unity_url_and_ignore_patterns_are_validated(self) -> None:
        config = valid_config()
        config["workspace"] = {
            "additional_ignore_patterns": ["BuildCache/**", "*.local"]
        }
        config["unity_mcp"] = {
            "mode": "optional",
            "adapter": "coplaydev-unity-mcp",
            "transport": "streamable_http",
            "url": "http://127.0.0.1:8080/mcp",
            "expected_project_root": ".",
            "connect_timeout_seconds": 5,
            "operation_timeout_minutes": 10,
        }
        validated = auto_progress.validate_config(config)
        self.assertEqual("optional", validated["unity_mcp"]["mode"])

        for invalid_url in (
            "http://192.168.1.10:8080/mcp",
            "http://user:password@localhost:8080/mcp",
            "http://localhost:8080/mcp?token=secret",
        ):
            candidate = copy.deepcopy(config)
            candidate["unity_mcp"]["url"] = invalid_url
            with self.subTest(url=invalid_url), self.assertRaises(
                auto_progress.AutoProgressError
            ):
                auto_progress.validate_config(candidate)

        for invalid_pattern in ("!keep-this", "../outside/**", "C:/outside/**"):
            candidate = copy.deepcopy(config)
            candidate["workspace"]["additional_ignore_patterns"] = [invalid_pattern]
            with self.subTest(pattern=invalid_pattern), self.assertRaisesRegex(
                auto_progress.AutoProgressError, "additional_ignore|must not"
            ):
                auto_progress.validate_config(candidate)

    def test_enabled_v2_requires_human_migration_before_automatic_work(self) -> None:
        config = valid_config()
        config["schema_version"] = 2
        config["unity_mcp"] = {
            "enabled": True,
            "provider": "CoplayDev/unity-mcp",
            "expected_project_root": ".",
            "refresh_after_checkout": True,
        }
        with self.assertRaises(auto_progress.AutoProgressError) as captured:
            auto_progress.validate_config(config)
        self.assertEqual("unity_mcp_migration_required", captured.exception.reason_code)

        disabled = copy.deepcopy(config)
        disabled["unity_mcp"]["enabled"] = False
        normalized = auto_progress.validate_config(disabled)
        self.assertEqual({"mode": "disabled"}, normalized["unity_mcp"])


class AdapterAndStateTests(unittest.TestCase):
    def test_adapter_routing_is_explicit_and_capability_checked(self) -> None:
        config = auto_progress.validate_config(valid_config())
        routed = workflow.route_adapters(config, "implement-batch")
        self.assertEqual("git", routed["source_control"].adapter_id)
        self.assertEqual("github", routed["review_host"].adapter_id)
        self.assertIn("content_fingerprint", routed["source_control"].capabilities)
        self.assertIn("draft_review", routed["review_host"].capabilities)

        unknown = copy.deepcopy(config)
        unknown["tools"]["review_host"] = "not-registered"
        with self.assertRaises(auto_progress.AutoProgressError) as captured:
            workflow.route_adapters(unknown, "implement-batch")
        self.assertEqual("adapter_unregistered", captured.exception.reason_code)

    def test_state_store_replaces_atomically_and_tracks_unfinished_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = workflow.StateStore(Path(directory))
            state = {
                "state_schema_version": workflow.STATE_SCHEMA_VERSION,
                "project_id": "example-project",
                "run_id": RUN_ID,
                "terminal": False,
                "checkpoints": {},
            }
            with patch("workflow.os.replace", wraps=os.replace) as replace:
                path = store.save(state)
            replace.assert_called_once()
            self.assertEqual(state, store.load("example-project", RUN_ID))
            self.assertEqual([RUN_ID], store.unfinished("example-project"))
            self.assertEqual([], list(path.parent.glob("*.tmp")))

            state["terminal"] = True
            store.save(state)
            self.assertEqual([], store.unfinished("example-project"))


class GitWorkflowTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        run_git(repo, "init", "-b", "main")
        run_git(repo, "config", "user.name", "AutoProgress Test")
        run_git(repo, "config", "user.email", "auto-progress@example.invalid")
        (repo / "Assets").mkdir()
        (repo / "Assets" / "Base.cs").write_text("class Base {}\n", encoding="utf-8")
        run_git(repo, "add", "Assets/Base.cs")
        run_git(repo, "commit", "-m", "base")
        return repo

    def write_improvement(self, repo: Path, improvement_id: str) -> Path:
        path = (
            repo
            / "docs"
            / "auto-progress"
            / "improvements"
            / f"{improvement_id}--queued.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nid: {improvement_id}\nstate: queued\n---\n\n# Improvement\n",
            encoding="utf-8",
        )
        return path

    def test_content_fingerprint_uses_temporary_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            base = run_git(repo, "rev-parse", "HEAD")
            (repo / "Assets" / "Base.cs").write_text(
                "class Base { int Value; }\n", encoding="utf-8"
            )
            (repo / "Assets" / "Staged.cs").write_text(
                "class Staged {}\n", encoding="utf-8"
            )
            run_git(repo, "add", "Assets/Staged.cs")
            index_before = run_git(repo, "diff", "--cached", "--name-status")

            snapshot = workflow._index_snapshot(repo, base)

            self.assertEqual(index_before, run_git(repo, "diff", "--cached", "--name-status"))
            self.assertEqual(
                {"Assets/Base.cs", "Assets/Staged.cs"}, set(snapshot["changed_paths"])
            )
            self.assertEqual(2, snapshot["csharp_files"])

    def test_snapshot_excludes_additionally_ignored_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            base = run_git(repo, "rev-parse", "HEAD")
            (repo / "Generated").mkdir()
            (repo / "Generated" / "human.tmp").write_text("human\n", encoding="utf-8")
            (repo / "Assets" / "Change.cs").write_text("class Change {}\n", encoding="utf-8")

            snapshot = workflow._index_snapshot(repo, base, ["Generated/**"])

            self.assertEqual(["Assets/Change.cs"], snapshot["changed_paths"])

    def test_additional_ignore_and_base_collision_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repo(Path(directory))
            old = run_git(repo, "rev-parse", "HEAD")
            (repo / "Generated").mkdir()
            collision = repo / "Generated" / "from-base.txt"
            collision.write_text("tracked on base\n", encoding="utf-8")
            run_git(repo, "add", "Generated/from-base.txt")
            run_git(repo, "commit", "-m", "base adds generated file")
            base = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "switch", "--detach", old)
            (repo / "Generated").mkdir(exist_ok=True)
            collision.write_text("local human file\n", encoding="utf-8")

            inspected = workflow.inspect_workspace(repo, ["Generated/**"])
            self.assertEqual([], inspected["blocking"])
            self.assertEqual(["Generated/from-base.txt"], inspected["ignored_untracked"])
            self.assertEqual(
                ["Generated/from-base.txt"],
                workflow._target_collisions(repo, base, inspected["ignored_untracked"]),
            )

    def test_prepare_failure_restores_and_does_not_leave_unfinished_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = PLUGIN_ROOT / "assets" / "auto-progress.toml"
            text = template.read_text(encoding="utf-8")
            text = text.replace(
                'base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1
            )
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            run_git(repo, "add", ".codex/auto-progress.toml")
            run_git(repo, "commit", "-m", "configure v3")

            remote = root / "origin.git"
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                text=True,
                capture_output=True,
                check=True,
            )
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            original_head = run_git(repo, "rev-parse", "HEAD")
            (repo / "human-work.txt").write_text("do not touch\n", encoding="utf-8")
            state_root = root / "state"

            result = workflow.prepare_run(
                repo,
                RUN_ID,
                "implement-batch",
                "main",
                state_root,
                skip_github=True,
                trigger_source="manual",
            )

            self.assertEqual("failed", result["status"])
            self.assertEqual("dirty_workspace", result["reason_code"])
            self.assertEqual("main", run_git(repo, "branch", "--show-current"))
            self.assertEqual(original_head, run_git(repo, "rev-parse", "HEAD"))
            self.assertFalse(
                run_git(repo, "branch", "--list", workflow._branch_name(RUN_ID, "implement-batch"))
            )
            project_id = auto_progress.make_project_id(str(remote), "main")
            self.assertEqual([], workflow.StateStore(state_root).unfinished(project_id))

    def test_prepare_and_finish_deliver_then_restore_primary_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = (PLUGIN_ROOT / "assets" / "auto-progress.toml").read_text(encoding="utf-8")
            text = template.replace(
                'base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1
            )
            text = text.replace('program = "dotnet"', 'program = "git"', 1)
            text = text.replace(
                'args = ["msbuild", "YourUnityProject.sln", "-nologo", "-verbosity:minimal"]',
                'args = ["diff", "--check"]',
                1,
            )
            text = text.replace('mode = "optional"', 'mode = "disabled"', 1)
            start = text.index('[unity_mcp]')
            text = text[:start] + '[unity_mcp]\nmode = "disabled"\n'
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            (repo / "AGENTS.md").write_text("Keep changes focused.\n", encoding="utf-8")
            improvement_dir = repo / "docs" / "auto-progress" / "improvements"
            improvement_dir.mkdir(parents=True)
            queued = improvement_dir / "IMP-2026.07.30-a1b2c3d4--queued.md"
            queued.write_text(
                "---\nid: IMP-2026.07.30-a1b2c3d4\nstate: queued\n---\n\n# Improvement\n",
                encoding="utf-8",
            )
            run_git(
                repo,
                "add",
                ".codex/auto-progress.toml",
                "AGENTS.md",
                str(queued.relative_to(repo)),
            )
            run_git(repo, "commit", "-m", "configure v3")
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            original = run_git(repo, "rev-parse", "HEAD")
            state_root = root / "state"

            prepared = workflow.prepare_run(
                repo, RUN_ID, "implement-batch", "main", state_root,
                skip_github=True, trigger_source="manual",
            )
            self.assertEqual("completed", prepared["status"])
            guidance = prepared["facts"]["repository_guidance"]
            agents = next(item for item in guidance["documents"] if item["path"] == "AGENTS.md")
            self.assertTrue(agents["changed"])
            self.assertTrue(Path(agents["cache_path"]).exists())
            project_id = auto_progress.make_project_id(str(remote), "main")
            reused_guidance = workflow._refresh_repository_guidance(
                repo, workflow.StateStore(state_root).load(project_id, RUN_ID)["config"],
                workflow.StateStore(state_root), project_id,
            )
            reused_agents = next(
                item for item in reused_guidance["documents"] if item["path"] == "AGENTS.md"
            )
            self.assertFalse(reused_agents["changed"])
            (repo / "Assets" / "Delivered.cs").write_text("class Delivered {}\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "improvements": [
                            {
                                "id": "IMP-2026.07.30-a1b2c3d4",
                                "summary": "Add a delivered type.",
                                "acceptance": "The configured validation succeeds.",
                                "design_tradeoffs": "Minimal isolated example.",
                                "expected_paths": ["Assets/Delivered.cs"],
                            }
                        ],
                        "run_record_path": f"docs/auto-progress/runs/{RUN_ID}.md",
                    }
                ),
                encoding="utf-8",
            )
            finished = workflow.finish_run(project_id, RUN_ID, manifest, state_root)

            self.assertEqual("completed", finished["status"], finished)
            self.assertEqual("main", run_git(repo, "branch", "--show-current"))
            self.assertEqual(original, run_git(repo, "rev-parse", "HEAD"))
            branch = workflow._branch_name(RUN_ID, "implement-batch")
            self.assertTrue(run_git(repo, "ls-remote", "--heads", "origin", branch))
            state = workflow.StateStore(state_root).load(project_id, RUN_ID)
            self.assertTrue(state["terminal"])
            self.assertEqual("succeeded", state["terminal_result"])
            self.assertNotIn("status_revision", state)
            tree = run_git(repo, "ls-tree", "-r", "--name-only", f"origin/{branch}")
            self.assertIn(
                "docs/auto-progress/improvements/IMP-2026.07.30-a1b2c3d4--implemented.md",
                tree,
            )
            self.assertNotIn(
                "docs/auto-progress/improvements/IMP-2026.07.30-a1b2c3d4--queued.md",
                tree,
            )
            summary = auto_progress.summarize_events(
                auto_progress.read_events(
                    auto_progress.ledger_files(state_root / "ledger", project_id)
                )
            )
            self.assertEqual(1, summary["implementation"]["implemented"])

    def test_finish_excludes_staged_disjoint_human_change_and_restores_it_unstaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = (PLUGIN_ROOT / "assets" / "auto-progress.toml").read_text(encoding="utf-8")
            text = template.replace(
                'base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1
            )
            text = text.replace('program = "dotnet"', 'program = "git"', 1)
            text = text.replace(
                'args = ["msbuild", "YourUnityProject.sln", "-nologo", "-verbosity:minimal"]',
                'args = ["diff", "--check"]',
                1,
            )
            start = text.index("[unity_mcp]")
            text = text[:start] + '[unity_mcp]\nmode = "disabled"\n'
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            improvement = self.write_improvement(repo, "IMP-2026.08.01-55555555")
            run_git(repo, "add", ".codex/auto-progress.toml", str(improvement.relative_to(repo)))
            run_git(repo, "commit", "-m", "configure")
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            original = run_git(repo, "rev-parse", "HEAD")
            state_root = root / "state"
            prepared = workflow.prepare_run(
                repo, RUN_ID, "implement-batch", "main", state_root,
                skip_github=True, trigger_source="manual",
            )
            self.assertEqual("completed", prepared["status"], prepared)

            (repo / "Assets" / "Delivered.cs").write_text("class Delivered {}\n", encoding="utf-8")
            (repo / "Assets" / "Base.cs").write_text("class Base { int Human; }\n", encoding="utf-8")
            run_git(repo, "add", "Assets/Base.cs")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "improvements": [{
                    "id": "IMP-2026.08.01-55555555",
                    "summary": "Add a delivered type.",
                    "acceptance": "The configured validation succeeds.",
                    "design_tradeoffs": "Independent from the human edit.",
                    "expected_paths": ["Assets/Delivered.cs"],
                }],
                "run_record_path": f"docs/auto-progress/runs/{RUN_ID}.md",
            }), encoding="utf-8")
            project_id = auto_progress.make_project_id(str(remote), "main")

            result = workflow.finish_run(project_id, RUN_ID, manifest, state_root)

            self.assertEqual("completed", result["status"], result)
            self.assertEqual("main", run_git(repo, "branch", "--show-current"))
            self.assertIn("Human", (repo / "Assets" / "Base.cs").read_text(encoding="utf-8"))
            self.assertEqual("Assets/Base.cs", run_git(repo, "diff", "--name-only"))
            self.assertEqual("", run_git(repo, "diff", "--cached", "--name-only"))
            branch = workflow._branch_name(RUN_ID, "implement-batch")
            self.assertEqual("class Base {}", run_git(repo, "show", f"origin/{branch}:Assets/Base.cs"))
            self.assertEqual("1", run_git(repo, "rev-list", "--count", f"{original}..origin/{branch}"))
            state = workflow.StateStore(state_root).load(project_id, RUN_ID)
            self.assertEqual(["Assets/Base.cs"], state["bypass_changes"]["paths"])
            self.assertEqual(["Assets/Base.cs"], state["bypass_changes"]["unstaged_paths"])

    def test_rename_delivery_stages_old_and_new_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = (PLUGIN_ROOT / "assets" / "auto-progress.toml").read_text(encoding="utf-8")
            text = template.replace('base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1)
            text = text.replace('program = "dotnet"', 'program = "git"', 1)
            text = text.replace(
                'args = ["msbuild", "YourUnityProject.sln", "-nologo", "-verbosity:minimal"]',
                'args = ["diff", "--check"]', 1,
            )
            start = text.index('[unity_mcp]')
            text = text[:start] + '[unity_mcp]\nmode = "disabled"\n'
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            improvement = self.write_improvement(repo, "IMP-2026.08.01-11111111")
            run_git(repo, "add", ".codex/auto-progress.toml", str(improvement.relative_to(repo)))
            run_git(repo, "commit", "-m", "configure v3")
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            state_root = root / "state"
            self.assertEqual(
                "completed",
                workflow.prepare_run(repo, RUN_ID, "implement-batch", "main", state_root, skip_github=True, trigger_source="manual")["status"],
            )
            (repo / "Assets" / "Base.cs").rename(repo / "Assets" / "Renamed.cs")
            manifest = root / "rename.json"
            manifest.write_text(json.dumps({
                "improvements": [{
                    "id": "IMP-2026.08.01-11111111",
                    "summary": "Rename the type source.",
                    "acceptance": "Only the new path remains.",
                    "design_tradeoffs": "Pure rename.",
                    "expected_paths": ["Assets/Base.cs", "Assets/Renamed.cs"],
                }],
                "run_record_path": f"docs/auto-progress/runs/{RUN_ID}.md",
            }), encoding="utf-8")
            project_id = auto_progress.make_project_id(str(remote), "main")

            result = workflow.finish_run(project_id, RUN_ID, manifest, state_root)

            self.assertEqual("completed", result["status"], result)
            branch = workflow._branch_name(RUN_ID, "implement-batch")
            tree = run_git(repo, "ls-tree", "-r", "--name-only", f"origin/{branch}")
            self.assertIn("Assets/Renamed.cs", tree)
            self.assertNotIn("Assets/Base.cs", tree)
            self.assertFalse(run_git(repo, "status", "--porcelain"))

    def test_discovery_finish_uses_and_restores_primary_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = (PLUGIN_ROOT / "assets" / "auto-progress.toml").read_text(encoding="utf-8")
            text = template.replace('base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1)
            start = text.index('[unity_mcp]')
            text = text[:start] + '[unity_mcp]\nmode = "disabled"\n'
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            run_git(repo, "add", ".codex/auto-progress.toml")
            run_git(repo, "commit", "-m", "configure v3")
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            state_root = root / "state"
            prepared = workflow.prepare_run(
                repo, RUN_ID, "discover-improvements", "main", state_root,
                skip_github=True, trigger_source="manual",
            )
            self.assertEqual("completed", prepared["status"], prepared)
            project_id = auto_progress.make_project_id(str(remote), "main")
            state = workflow.StateStore(state_root).load(project_id, RUN_ID)
            worktree = Path(state["workspace_path"])
            candidate = worktree / "docs" / "auto-progress" / "improvements" / "IMP-2026.08.01-22222222--queued.md"
            candidate.parent.mkdir(parents=True)
            candidate.write_text(
                "---\nid: IMP-2026.08.01-22222222\nstate: queued\n---\n\n# Candidate\n",
                encoding="utf-8",
            )
            manifest = root / "discovery.json"
            manifest.write_text(json.dumps({
                "improvements": [{
                    "id": "IMP-2026.08.01-22222222",
                    "summary": "Document a bounded candidate.",
                    "acceptance": "The candidate is reviewable.",
                    "design_tradeoffs": "Documentation only.",
                    "expected_paths": ["docs/auto-progress/improvements/IMP-2026.08.01-22222222--queued.md"],
                }],
                "run_record_path": f"docs/auto-progress/runs/{RUN_ID}.md",
            }), encoding="utf-8")

            result = workflow.finish_run(project_id, RUN_ID, manifest, state_root)

            self.assertEqual("completed", result["status"], result)
            self.assertEqual(repo.resolve(), worktree.resolve())
            self.assertEqual("main", run_git(repo, "branch", "--show-current"))
            self.assertFalse((state_root / "workspaces" / project_id / "discovery").exists())
            state = workflow.StateStore(state_root).load(project_id, RUN_ID)
            self.assertTrue(state["terminal"])

    def test_recover_resumes_after_first_item_commit_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.make_repo(root)
            template = (PLUGIN_ROOT / "assets" / "auto-progress.toml").read_text(encoding="utf-8")
            text = template.replace('base_branch = "feature/your-base-branch"', 'base_branch = "main"', 1)
            text = text.replace('program = "dotnet"', 'program = "git"', 1)
            text = text.replace(
                'args = ["msbuild", "YourUnityProject.sln", "-nologo", "-verbosity:minimal"]',
                'args = ["diff", "--check"]', 1,
            )
            start = text.index('[unity_mcp]')
            text = text[:start] + '[unity_mcp]\nmode = "disabled"\n'
            (repo / ".codex").mkdir()
            (repo / ".codex" / "auto-progress.toml").write_text(text, encoding="utf-8")
            first_doc = self.write_improvement(repo, "IMP-2026.08.01-33333333")
            second_doc = self.write_improvement(repo, "IMP-2026.08.01-44444444")
            run_git(
                repo,
                "add",
                ".codex/auto-progress.toml",
                str(first_doc.relative_to(repo)),
                str(second_doc.relative_to(repo)),
            )
            run_git(repo, "commit", "-m", "configure v3")
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-u", "origin", "main")
            state_root = root / "state"
            prepared = workflow.prepare_run(
                repo, RUN_ID, "implement-batch", "main", state_root,
                skip_github=True, trigger_source="manual",
            )
            self.assertEqual("completed", prepared["status"])
            (repo / "Assets" / "One.cs").write_text("class One {}\n", encoding="utf-8")
            (repo / "Assets" / "Two.cs").write_text("class Two {}\n", encoding="utf-8")
            manifest = root / "two-items.json"
            manifest.write_text(json.dumps({
                "improvements": [
                    {
                        "id": "IMP-2026.08.01-33333333",
                        "summary": "Add the first type.",
                        "acceptance": "One.cs is delivered.",
                        "design_tradeoffs": "Independent path.",
                        "expected_paths": ["Assets/One.cs"],
                    },
                    {
                        "id": "IMP-2026.08.01-44444444",
                        "summary": "Add the second type.",
                        "acceptance": "Two.cs is delivered.",
                        "design_tradeoffs": "Independent path.",
                        "expected_paths": ["Assets/Two.cs"],
                    },
                ],
                "run_record_path": f"docs/auto-progress/runs/{RUN_ID}.md",
            }), encoding="utf-8")
            real_save = workflow.StateStore.save

            def crash_after_commit(store: workflow.StateStore, state: dict[str, object]) -> Path:
                if str(state.get("current_stage", "")).startswith("record_change_"):
                    raise KeyboardInterrupt("simulated crash")
                return real_save(store, state)

            with patch.object(workflow.StateStore, "save", new=crash_after_commit):
                with self.assertRaises(KeyboardInterrupt):
                    workflow.finish_run(
                        auto_progress.make_project_id(str(remote), "main"),
                        RUN_ID,
                        manifest,
                        state_root,
                    )
            project_id = auto_progress.make_project_id(str(remote), "main")
            interrupted = workflow.StateStore(state_root).load(project_id, RUN_ID)
            interrupted["skip_github"] = False
            workflow.StateStore(state_root).save(interrupted)
            real_run = workflow._run

            def fake_gh(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if args[0] == "gh":
                    return subprocess.CompletedProcess(args, 0, "", "")
                return real_run(args, **kwargs)

            review = {
                "number": 17,
                "url": "https://example.invalid/review/17",
                "isDraft": True,
                "headRefOid": None,
                "baseRefName": "main",
            }
            with patch("workflow._run", side_effect=fake_gh), patch(
                "workflow._open_review", side_effect=[None, review]
            ):
                recovered = workflow.recover_run(project_id, RUN_ID, state_root)

            self.assertEqual("completed", recovered["status"], recovered)
            state = workflow.StateStore(state_root).load(project_id, RUN_ID)
            self.assertEqual(2, len(state["item_revisions"]))
            self.assertEqual(17, state["review_handle"]["number"])
            self.assertIn("content_hash", state["review_document"])
            self.assertTrue(state["terminal"])
            self.assertEqual("main", run_git(repo, "branch", "--show-current"))
            branch = workflow._branch_name(RUN_ID, "implement-batch")
            self.assertEqual(
                "2",
                run_git(repo, "rev-list", "--count", f"main..origin/{branch}"),
            )
            for improvement_id, code_path in (
                ("IMP-2026.08.01-33333333", "Assets/One.cs"),
                ("IMP-2026.08.01-44444444", "Assets/Two.cs"),
            ):
                committed_paths = run_git(
                    repo,
                    "show",
                    "--format=",
                    "--name-only",
                    state["item_revisions"][improvement_id],
                ).splitlines()
                self.assertIn(code_path, committed_paths)
                self.assertIn(
                    f"docs/auto-progress/improvements/{improvement_id}--implemented.md",
                    committed_paths,
                )


if __name__ == "__main__":
    unittest.main()
