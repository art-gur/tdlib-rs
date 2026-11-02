import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

import ci_metadata
import package_release
from relocate_pkgconfig import relocate


class ReleaseOrderTests(unittest.TestCase):
    def workflow(self, name):
        return yaml.safe_load((ci_metadata.ROOT / f".github/workflows/{name}.yml").read_text())

    def test_download_is_transitively_blocked_by_publication(self):
        jobs = self.workflow("cd")["jobs"]
        self.assertEqual(jobs["download"]["needs"], "release_github")
        self.assertTrue(jobs["download"]["with"]["download"])
        current = "download"
        chain = []
        while current:
            chain.append(current)
            self.assertNotIn("if", jobs[current])  # default success gate must remain
            current = jobs[current].get("needs")
        self.assertEqual(chain, ["download", "release_github", "native", "build-tdlib", "checks"])
        publish = jobs["release_github"]["steps"][-1]["run"]
        self.assertLess(publish.index("--draft --generate-notes"), publish.index("--draft=false"))

    def test_branch_ci_never_requires_a_release(self):
        workflow = self.workflow("ci")
        # PyYAML's YAML 1.1 loader treats the Actions `on` key as True.
        self.assertEqual(set(workflow[True]), {"push", "pull_request", "workflow_dispatch"})
        for job in workflow["jobs"].values():
            self.assertFalse(job.get("with", {}).get("download", False))
        native = self.workflow("ci-native")
        self.assertEqual(set(native[True]), {"workflow_call"})
        self.assertFalse(native[True]["workflow_call"]["inputs"]["download"]["default"])

    def test_no_detached_workflow_run_or_latest_release_download(self):
        for path in (ci_metadata.ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.safe_load(path.read_text())
            self.assertNotIn("workflow_run", workflow[True])
            self.assertNotIn("gh release download --pattern", path.read_text())


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "td").mkdir()
        (root / "td/CMakeLists.txt").write_text("project(TDLib VERSION 9.8.7 LANGUAGES CXX C)")
        for relative in (".github/native-build.json", ".github/workflows/build-tdlib.yml",
                         ".github/scripts/relocate_pkgconfig.py", "tdlib-rs/native-libraries.json"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ci_metadata.ROOT / relative).read_bytes())
        patched_root = patch.object(ci_metadata, "ROOT", root)
        patched_root.start()
        self.addCleanup(patched_root.stop)

    def test_cargo_controls_version_and_repository(self):
        workspace = {"target_directory": "/custom/target", "packages": [{"name": "tdlib-rs", "version": "9.0.1",
            "repository": "https://github.com/test/fork",
            "metadata": {"system-deps": {"tdjson": "9.8.7"}}}]}
        with patch.object(ci_metadata.subprocess, "check_output", side_effect=[json.dumps(workspace), "source-tree"]):
            values, _ = ci_metadata.metadata()
        self.assertEqual(values["RELEASE_TAG"], "v9.0.1")
        self.assertEqual(values["RELEASE_REPOSITORY"], "test/fork")
        self.assertEqual(values["CARGO_TARGET_DIR"], "/custom/target")

    def test_runner_image_is_part_of_native_cache_identity(self):
        workspace = {"target_directory": "/target", "packages": [{"name": "tdlib-rs", "version": "9.0.1",
            "repository": "https://github.com/test/fork",
            "metadata": {"system-deps": {"tdjson": "9.8.7"}}}]}
        images = []
        for image_version in ("build-1", "build-2"):
            with patch.dict(ci_metadata.os.environ, {"ImageOS": "ubuntu", "ImageVersion": image_version}):
                with patch.object(ci_metadata.subprocess, "check_output", side_effect=[json.dumps(workspace), "same-tree"]):
                    values, _ = ci_metadata.metadata()
                    images.append(values["RUNNER_IMAGE"])
        self.assertNotEqual(*images)
        workflow = yaml.safe_load((ci_metadata.ROOT / ".github/workflows/build-tdlib.yml").read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step.get("name") == "Restore cache TDLib":
                    self.assertIn("env.RUNNER_IMAGE", step["with"]["key"])

    def test_mismatched_native_version_is_rejected(self):
        workspace = {"packages": [{"name": "tdlib-rs", "version": "9.0.1",
            "metadata": {"system-deps": {"tdjson": "0.0.0"}}}]}
        with patch.object(ci_metadata.subprocess, "check_output", return_value=json.dumps(workspace)):
            with self.assertRaisesRegex(ValueError, "vendored TDLib"):
                ci_metadata.metadata()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "artifacts"
        self.source.mkdir()
        self.output = self.root / "assets"
        self.version = "9.8.7"
        self.config = json.loads((ci_metadata.ROOT / ".github/native-build.json").read_text())
        libraries = json.loads((ci_metadata.ROOT / "tdlib-rs/native-libraries.json").read_text())
        for os in (*self.config["runners"], "android"):
            arches = self.config["architectures"]
            for arch in arches:
                directory = self.source / f"tdlib-{self.version}-{os}-{arch}"
                files = ["include/td/telegram/td_json_client.h", "include/td/telegram/td_file_system.h",
                         "lib/pkgconfig/tdjson.pc"]
                platform = "windows" if os == "windows" else "unix"
                for name in libraries["tdlib"] + libraries[platform]:
                    files.append(f"lib/{name}.lib" if os == "windows" else f"lib/lib{name}.a")
                if os == "windows":
                    files += ["bin/tdjson.dll", "lib/tdjson.lib", "lib/tdjson_static.lib"]
                else:
                    shared = {"linux": f"libtdjson.so.{self.version}", "macos": f"libtdjson.{self.version}.dylib", "android": "libtdjson.so"}[os]
                    files += [f"lib/{shared}", "lib/libtdjson_static.a", "lib/pkgconfig/tdjson.pc"]
                for relative in files:
                    path = directory / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"fixture")

    def package(self):
        with patch.object(package_release, "metadata", return_value=({"TDLIB_VERSION": self.version}, {})):
            package_release.package(self.source, self.output)

    def test_complete_platform_set_has_expected_archive_root(self):
        self.package()
        archives = list(self.output.glob("*.zip"))
        self.assertEqual(len(archives), 8)
        for path in archives:
            with zipfile.ZipFile(path) as archive:
                self.assertTrue(all(name.startswith("tdlib/") for name in archive.namelist()))
                self.assertIsNone(archive.testzip())

    def test_missing_platform_blocks_all_archives(self):
        missing = next(self.source.iterdir())
        missing.rename(self.root / missing.name)
        with self.assertRaisesRegex(ValueError, "artifact mismatch"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_wrong_shared_library_version_blocks_all_archives(self):
        next(self.source.glob("*-macos-*/lib/*.dylib")).unlink()
        with self.assertRaisesRegex(ValueError, "native release file"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_missing_android_dependency_blocks_publication(self):
        next(self.source.glob("*-android-*/lib/libcrypto.a")).unlink()
        with self.assertRaisesRegex(ValueError, "native release file"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_missing_tdlib_static_component_blocks_publication(self):
        next(self.source.glob("*-linux-*/lib/libtde2e.a")).unlink()
        with self.assertRaisesRegex(ValueError, "native release file"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_empty_library_blocks_publication(self):
        next(self.source.glob("*-windows-*/bin/tdjson.dll")).write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "missing or empty"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_pkgconfig_survives_moving_installation(self):
        installed = self.root / "installation"
        pc = installed / "lib/pkgconfig/tdjson.pc"
        pc.parent.mkdir(parents=True)
        pc.write_text('prefix=/old/runner/install\nLibs: -L"${prefix}/lib" -ltdjson\nVersion: 9.8.7\n')
        relocate(installed)
        installed.rename(self.root / "moved")
        text = (self.root / "moved/lib/pkgconfig/tdjson.pc").read_text()
        self.assertIn("prefix=${pcfiledir}/../..", text)
        self.assertNotIn("/old/runner", text)
        self.assertIn("Version: 9.8.7", text)


if __name__ == "__main__":
    unittest.main()
