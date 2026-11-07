import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import yaml

import ci_metadata
import release_from_ci


SHA = "a" * 40
REPOSITORY = "owner/project"
REPOSITORY_ID = "123"


def successful_run(**overrides):
    run = {
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/ci.yml",
        "event": "push",
        "head_branch": "main",
        "repository": {"id": int(REPOSITORY_ID), "full_name": REPOSITORY},
        "head_repository": {"id": int(REPOSITORY_ID), "full_name": REPOSITORY},
        "head_sha": SHA,
    }
    run.update(overrides)
    return run


class ResolveTests(unittest.TestCase):
    def test_accepts_successful_same_repository_main_ci_run(self):
        with patch.dict(release_from_ci.os.environ, {"GITHUB_REPOSITORY_ID": REPOSITORY_ID}), \
                patch.object(release_from_ci, "api", return_value=successful_run()) as api:
            self.assertEqual(release_from_ci.resolve("12345"), SHA)
        api.assert_called_once_with("actions/runs/12345")

    def test_rejects_untrusted_run_provenance(self):
        rejected = (
            {"status": "in_progress"},
            {"conclusion": "failure"},
            {"path": ".github/workflows/other.yml"},
            {"event": "pull_request"},
            {"event": "schedule"},
            {"head_branch": "feature"},
            {"repository": {"id": 456, "full_name": "fork/project"}},
            {"head_repository": {"id": 789, "full_name": "contributor/project"}},
        )
        with patch.dict(release_from_ci.os.environ, {"GITHUB_REPOSITORY_ID": REPOSITORY_ID}):
            for override in rejected:
                with self.subTest(override=override), \
                        patch.object(release_from_ci, "api", return_value=successful_run(**override)):
                    with self.assertRaisesRegex(ValueError, "successful completed CI run"):
                        release_from_ci.resolve("12345")

    def test_repository_rename_alias_is_accepted_by_stable_id(self):
        run = successful_run(
            repository={"id": int(REPOSITORY_ID), "full_name": "art-gur/tdlib-rs-1"},
            head_repository={"id": int(REPOSITORY_ID), "full_name": "art-gur/tdlib-rs-1"},
        )
        with patch.dict(release_from_ci.os.environ, {"GITHUB_REPOSITORY_ID": REPOSITORY_ID}), \
                patch.object(release_from_ci, "api", return_value=run):
            self.assertEqual(release_from_ci.resolve("12345"), SHA)

    def test_rejects_non_numeric_run_id_before_api_lookup(self):
        with patch.object(release_from_ci, "api") as api:
            with self.assertRaisesRegex(ValueError, "numeric"):
                release_from_ci.resolve("123abc")
        api.assert_not_called()


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        (self.assets / "tdlib.zip").write_bytes(b"validated fixture")
        self.env = {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_REPOSITORY_ID": REPOSITORY_ID,
            "SOURCE_RUN_ID": "12345",
            "RELEASE_TAG": "v1.2.3",
            "RELEASE_REPOSITORY": "art-gur/tdlib-rs-1",
        }

    def publish(self, *, retarget=False, release=None, commit=None, tag="v1.2.3", **env):
        with patch.dict(release_from_ci.os.environ, self.env | env), \
                patch.object(release_from_ci, "resolve", return_value=SHA), \
                patch.object(release_from_ci, "api") as api, \
                patch.object(release_from_ci.subprocess, "run") as run:
            api.side_effect = [{"id": int(REPOSITORY_ID)}, release, commit, None]
            error = None
            try:
                release_from_ci.publish(tag, SHA, self.assets, retarget=retarget)
            except ValueError as caught:
                error = caught
            return error, api, run

    def test_rejects_identity_mismatches_and_published_release_before_mutation(self):
        cases = (
            ("v9.9.9", {}, None, "tag does not match"),
            ("v1.2.3", {"GITHUB_REPOSITORY_ID": "999"}, None, "repository mismatch"),
            ("v1.2.3", {}, {"draft": False}, "release already exists"),
        )
        for tag, env, release, message in cases:
            with self.subTest(message=message):
                error, api, run = self.publish(tag=tag, release=release, commit=None,
                                               **env)
                self.assertIsNotNone(error)
                self.assertRegex(str(error), message)
                self.assertFalse(any(c.args[0] in ("git/refs", "git/refs/tags/v1.2.3")
                                     for c in api.call_args_list))
                run.assert_not_called()

    def test_wrong_source_sha_stops_before_release_lookup_or_mutation(self):
        with patch.dict(release_from_ci.os.environ, self.env), \
                patch.object(release_from_ci, "resolve", return_value="b" * 40), \
                patch.object(release_from_ci, "api") as api, \
                patch.object(release_from_ci.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "source commit changed"):
                release_from_ci.publish("v1.2.3", SHA, self.assets)
        api.assert_not_called()
        run.assert_not_called()

    def test_existing_tag_requires_explicit_retarget_and_no_mutation_without_it(self):
        error, api, run = self.publish(commit={"sha": "b" * 40})
        self.assertRegex(str(error), "retarget_unpublished_tag explicitly")
        self.assertEqual([c.args[0] for c in api.call_args_list],
                         ["", "releases/tags/v1.2.3", "commits/v1.2.3"])
        run.assert_not_called()

    def test_retargets_unpublished_tag_then_creates_draft_then_publishes(self):
        _, api, run = self.publish(retarget=True, commit={"sha": "b" * 40})
        self.assertEqual(api.call_args_list, [
            call("", repository="art-gur/tdlib-rs-1"),
            call("releases/tags/v1.2.3", missing_ok=True),
            call("commits/v1.2.3", missing_ok=True),
            call("git/refs/tags/v1.2.3", "PATCH", {"sha": SHA, "force": True}),
        ])
        self.assertEqual(run.call_args_list, [
            call(["gh", "release", "create", "v1.2.3", "--repo", REPOSITORY,
                  "--target", SHA, "--verify-tag", "--draft", "--generate-notes",
                  str(self.assets / "tdlib.zip")], check=True),
            call(["gh", "release", "edit", "v1.2.3", "--repo", REPOSITORY,
                  "--draft=false"], check=True),
        ])

    def test_creates_missing_tag_before_draft_then_publishes(self):
        _, api, run = self.publish(commit=None)
        self.assertEqual(api.call_args_list[3], call(
            "git/refs", "POST", {"ref": "refs/tags/v1.2.3", "sha": SHA}))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][2], "create")
        self.assertEqual(run.call_args_list[1].args[0][2], "edit")

    def test_existing_draft_blocks_republication(self):
        error, _, run = self.publish(release={"draft": True, "target_commitish": "b" * 40},
                                     commit={"sha": SHA})
        self.assertRegex(str(error), "release already exists")
        run.assert_not_called()

    def test_failed_draft_upload_never_publishes(self):
        with patch.dict(release_from_ci.os.environ, self.env), \
                patch.object(release_from_ci, "resolve", return_value=SHA), \
                patch.object(release_from_ci, "api", side_effect=[{"id": int(REPOSITORY_ID)}, None, {"sha": SHA}]), \
                patch.object(release_from_ci.subprocess, "run",
                             side_effect=release_from_ci.subprocess.CalledProcessError(1, "gh")) as run:
            with self.assertRaises(release_from_ci.subprocess.CalledProcessError):
                release_from_ci.publish("v1.2.3", SHA, self.assets)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][2], "create")


class WorkflowTests(unittest.TestCase):
    def test_manual_workflow_downloads_selected_run_artifacts_without_native_rebuild(self):
        workflow = yaml.safe_load(
            (ci_metadata.ROOT / ".github/workflows/release-from-ci.yml").read_text())
        self.assertEqual(set(workflow[True]), {"workflow_dispatch"})
        inputs = workflow[True]["workflow_dispatch"]["inputs"]
        self.assertIn("run_id", inputs)
        self.assertIn("tag", inputs)
        self.assertFalse(inputs["retarget_unpublished_tag"]["default"])
        steps = workflow["jobs"]["release"]["steps"]
        download = next(step for step in steps if step.get("uses") == "actions/download-artifact@v4")
        self.assertEqual(download["with"]["run-id"], "${{ inputs.run_id }}")
        self.assertEqual(download["with"]["pattern"], "tdlib-*")
        self.assertNotIn("build-tdlib", workflow["jobs"])
        self.assertFalse(any("build-tdlib.yml" in str(step) for step in steps))
        identity = next(step for step in steps if step.get("name") == "Validate release identity")
        self.assertIn("release_from_ci.py identity", identity["run"])
        self.assertLess(
            next(i for i, step in enumerate(steps) if step.get("name") == "Package and validate all platforms"),
            next(i for i, step in enumerate(steps) if step.get("name") == "Publish validated artifacts without rebuilding"),
        )


if __name__ == "__main__":
    unittest.main()
