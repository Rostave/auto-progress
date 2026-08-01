from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import render_review


RUN_ID = "RUN-2026.07.30-a1b2c3d4"
IMP_ONE = "IMP-2026.07.29-11111111"
IMP_TWO = "IMP-2026.07.30-22222222"


def implementation_input() -> tuple[dict[str, object], dict[str, object]]:
    manifest: dict[str, object] = {
        "improvements": [
            {
                "id": IMP_ONE,
                "source": "automatic-discovery",
                "result": "succeeded",
                "summary": "Avoid repeated allocation | in the hot path.",
                "acceptance": ["The cached value is reused.", "Existing behavior remains covered."],
                "design_tradeoffs": ["Keep the cache private."],
                "selection_reason": "Highest-value compatible queued item.",
                "expected_paths": ["Assets/Runtime/Cache.cs"],
            }
        ],
        "run_record_path": f"docs/auto-progress/runs/2026.07.30-{RUN_ID}.md",
    }
    facts: dict[str, object] = {
        "run_id": RUN_ID,
        "task_type": "implement-batch",
        "base_branch": "main",
        "work_branch": "codex/auto-progress/run-2026.07.30-a1b2c3d4-implement-batch",
        "base_revision": "a" * 40,
        "changed_paths": [
            {"path": "Assets/Runtime/Cache.cs", "status": "modified", "mode": "100644"},
            "Assets/Tests/CacheTests.cs",
        ],
        "budget": {
            "aggregate_all_files": 2,
            "aggregate_changed_lines": 34,
            "per_item": {IMP_ONE: {"all_files": 2, "changed_lines": 34}},
        },
        "validation": {"baseline": "passed", "final": "passed", "csharp": "passed"},
        "unity": {"status": "not_run"},
        "content_fingerprint": "sha256:" + "b" * 64,
        "commits": {IMP_ONE: "c" * 40},
        "rejection_hits": [],
        "rollback": [f"Revert the commit for {IMP_ONE}."],
        "timeline": ["baseline passed", "final validation passed"],
        "revision": "d" * 40,
        "review": {"number": 42, "url": "https://example.invalid/review/42"},
    }
    return manifest, facts


class RenderContractTests(unittest.TestCase):
    def test_implementation_review_is_deterministic_and_does_not_mutate_input(self) -> None:
        manifest, facts = implementation_input()
        original_manifest = copy.deepcopy(manifest)
        original_facts = copy.deepcopy(facts)

        first = render_review.render_document(
            render_review.IMPLEMENT_BATCH_REVIEW, manifest, facts
        )
        second = render_review.render_document(
            render_review.IMPLEMENT_BATCH_REVIEW,
            copy.deepcopy(manifest),
            copy.deepcopy(facts),
        )

        self.assertEqual(first, second)
        self.assertEqual(original_manifest, manifest)
        self.assertEqual(original_facts, facts)
        self.assertEqual(
            f"[AutoProgress][{RUN_ID}] Implement 1 improvements", first["title"]
        )
        self.assertTrue(first["body"].endswith("\n"))
        self.assertIn("⚠ 未经过 Unity Editor", first["body"])
        self.assertIn("Verified content fingerprint", first["body"])
        self.assertIn("Assets/Runtime/Cache.cs", first["body"])
        expected_hash = hashlib.sha256(
            first["title"].encode("utf-8")
            + b"\0"
            + first["body"].encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected_hash, first["content_hash"])

    def test_unknown_template_and_local_template_path_are_rejected(self) -> None:
        manifest, facts = implementation_input()
        for template_id in ("custom", "C:/templates/pr.md", "../pr.md"):
            with self.subTest(template_id=template_id):
                with self.assertRaisesRegex(render_review.RenderError, "unknown template_id"):
                    render_review.render_document(template_id, manifest, facts)

    def test_library_contract_accepts_only_json_values(self) -> None:
        manifest, facts = implementation_input()
        manifest["opaque"] = object()
        with self.assertRaisesRegex(render_review.RenderError, "only JSON values"):
            render_review.render_document(
                render_review.IMPLEMENT_BATCH_REVIEW, manifest, facts
            )

    def test_semantic_text_cannot_inject_a_fact_section(self) -> None:
        manifest, facts = implementation_input()
        improvement = manifest["improvements"][0]  # type: ignore[index]
        improvement["summary"] = "Claim\n## Fake validation"  # type: ignore[index]
        improvement["acceptance"] = ["Works\n## Fake budget"]  # type: ignore[index]
        rendered = render_review.render_document(
            render_review.IMPLEMENT_BATCH_REVIEW, manifest, facts
        )
        self.assertNotIn("\n## Fake validation", rendered["body"])
        self.assertNotIn("\n## Fake budget", rendered["body"])
        self.assertIn("Claim<br>## Fake validation", rendered["body"])

        manifest, facts = implementation_input()
        facts["duration"] = float("nan")
        with self.assertRaisesRegex(render_review.RenderError, "NaN or infinity"):
            render_review.render_document(
                render_review.IMPLEMENT_BATCH_REVIEW, manifest, facts
            )

    def test_run_record_excludes_self_revision_and_review_url(self) -> None:
        manifest, facts = implementation_input()
        result = render_review.render_document(render_review.RUN_RECORD, manifest, facts)
        self.assertEqual(f"AutoProgress run {RUN_ID}", result["title"])
        self.assertIn(IMP_ONE, result["body"])
        self.assertIn("## Timeline", result["body"])
        self.assertNotIn(str(facts["revision"]), result["body"])
        self.assertNotIn("example.invalid", result["body"])
        self.assertNotIn("Review URL", result["body"])

    def test_code_facts_control_results_not_manifest_claims(self) -> None:
        manifest, facts = implementation_input()
        improvement = manifest["improvements"][0]  # type: ignore[index]
        improvement["result"] = "succeeded"  # type: ignore[index]
        facts["improvements"] = {
            IMP_ONE: {"result": "reverted", "reason": "Final validation failed."}
        }
        review = render_review.render_document(
            render_review.IMPLEMENT_BATCH_REVIEW, manifest, facts
        )
        record = render_review.render_document(render_review.RUN_RECORD, manifest, facts)
        self.assertIn("reverted", review["body"])
        self.assertIn("Final validation failed.", review["body"])
        self.assertIn("reverted", record["body"])

    def test_absolute_managed_project_paths_are_rejected(self) -> None:
        manifest, facts = implementation_input()
        facts["changed_paths"] = ["C:/private/Unity/Assets/File.cs"]
        with self.assertRaisesRegex(render_review.RenderError, "repository-relative"):
            render_review.render_document(render_review.RUN_RECORD, manifest, facts)


class DiscoveryTests(unittest.TestCase):
    def test_discovery_review_uses_documented_title_and_boundaries(self) -> None:
        manifest = {
            "review_focus": "Runtime allocation and editor diagnostics",
            "improvements": [
                {
                    "id": IMP_ONE,
                    "summary": "Cache the lookup result.",
                    "evidence_paths": ["Assets/Runtime/Cache.cs"],
                },
                {
                    "id": IMP_TWO,
                    "summary": "Improve a bounded diagnostic.",
                    "evidence_paths": ["Assets/Editor/Diagnostics.cs"],
                },
            ],
        }
        facts = {
            "run_id": RUN_ID,
            "task_type": "discover-improvements",
            "base_branch": "main",
            "base_revision": "a" * 40,
            "reviewed_files": 12,
            "reviewed_source_lines": 900,
        }
        rendered = render_review.render_document(
            render_review.DISCOVER_IMPROVEMENTS_REVIEW, manifest, facts
        )
        self.assertEqual(
            f"[AutoProgress][Discovery][{RUN_ID}] Add 2 candidates",
            rendered["title"],
        )
        self.assertIn("Candidates proposed: 2", rendered["body"])
        self.assertIn("Documentation-only discovery", rendered["body"])
        self.assertIn(IMP_ONE, rendered["body"])
        self.assertIn(IMP_TWO, rendered["body"])

    def test_zero_candidate_discovery_is_valid(self) -> None:
        rendered = render_review.render_document(
            render_review.DISCOVER_IMPROVEMENTS_REVIEW,
            {"review_focus": "No qualifying findings", "candidates": []},
            {
                "run_id": RUN_ID,
                "task_type": "discover-improvements",
                "reviewed_files": 4,
                "reviewed_source_lines": 120,
            },
        )
        self.assertIn("Add 0 candidates", rendered["title"])


class PreviewCliTests(unittest.TestCase):
    def test_preview_reads_json_and_writes_json_without_side_effects(self) -> None:
        manifest, facts = implementation_input()
        payload = {
            "template_id": render_review.IMPLEMENT_BATCH_REVIEW,
            "manifest": manifest,
            "facts": facts,
        }
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            before = sorted(path.name for path in Path(directory).iterdir())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = render_review.main(["preview", "--input", str(input_path)])
            after = sorted(path.name for path in Path(directory).iterdir())

        self.assertEqual(0, exit_code)
        self.assertEqual(before, after)
        output = json.loads(stdout.getvalue())
        self.assertEqual(set(output), {"title", "body", "content_hash"})

    def test_invalid_preview_json_fails_as_bounded_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text("not-json", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = render_review.main(["preview", "--input", str(input_path)])
        self.assertEqual(2, exit_code)
        self.assertFalse(json.loads(stderr.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
