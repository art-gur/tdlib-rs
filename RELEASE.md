# Release and CI

`Cargo.toml` owns the crate version and repository. The TDLib requirement is
`package.metadata.system-deps.tdjson` in `tdlib-rs/Cargo.toml`. Build scripts and
CI read those fields; CI rejects a mismatch with `td/CMakeLists.txt`.
`.github/native-build.json` owns runner matrices and native toolchain pins.
Update intentional dependency/toolchain pins there instead of copying them into
workflows. Historical versions in `CHANGELOG.md` and old TL schemas are snapshots.
`tdlib-rs/native-libraries.json` declares required static libraries for both Rust
build paths and release validation.

Branch pushes and pull requests run `CI`:

1. Rust format, parser/generator tests, docs-feature build and Clippy on desktop runners.
2. Build the checked-out native TDLib sources and upload platform artifacts.
3. Test `local-tdlib`, static linking, and `pkg-config` using this run's artifacts.

Version tags run `CD` on that exact tagged commit:

1. Run the same Rust checks and validate that the tag matches the crate version.
2. Build TDLib, then run native artifact checks.
3. Validate and package the complete platform set. Create a draft release, upload
   every archive, then publish it.
4. Only after publication succeeds, test `download-tdlib` with dynamic and static
   linking. Its URL uses the tagged crate version, repository, and TDLib requirement.

The final job uses an explicit `needs: release_github` dependency. No independent
`workflow_run` or release-event trigger is needed. Failed native checks block
publication; failed publication blocks all download jobs. A branch with an
unreleased crate version never attempts a release download.

Native cache keys include the target, runner image version, TDLib source tree and build recipe.
pkg-config files use paths relative to their installation. Windows programs need
the selected installation's `bin` on `PATH`; build helpers do not modify Cargo's
global executable directory.

After updating versions, committing, and pushing the release commit, create its tag:

```bash
tag=$(python .github/scripts/ci_metadata.py | python -c 'import json,sys; print(json.load(sys.stdin)["RELEASE_TAG"])')
git tag "$tag"
git push origin "$tag"
```

Do not move an existing published version tag. If a release remains a draft after
an upload failure, inspect or remove that draft before rerunning publication.
If only the final download checks failed, rerun failed jobs against the existing
release.

To publish a successful branch CI run without rebuilding, open Actions →
**Release from CI artifacts** → **Run workflow** on `main`. Supply its `run_id`
and the release `tag` matching that source commit's Cargo version. For example,
run `42219251994` contains the checked artifacts for commit `20f1eacb0` and
version `v1.7.1`.

The workflow accepts only completed, successful runs of `.github/workflows/ci.yml`
from this repository's `main` branch. It checks out that run's exact commit,
downloads its retained artifacts, and validates the complete platform set before
changing any tag or creating a draft. Expired or incomplete artifacts block
publication. No native build or post-publication download build runs in this path;
the selected CI run supplies native validation, not published-download acceptance.
Repository checks use GitHub's stable numeric ID, so a repository rename and
redirects from the source commit's old repository URL remain valid.

If the tag points to an older failed commit and has no release, enable
`retarget_unpublished_tag`. Existing releases, including drafts, block publication;
inspect or remove an unfinished draft before retrying. Tag changes use the
workflow's `GITHUB_TOKEN`, so they do not trigger the tag-push CD or rebuild TDLib.
Do not push the replacement tag manually when using this path.

Local checks:

```bash
python -m pip install -r .github/requirements.txt
python -m unittest discover -s .github/scripts -p 'test_*.py'
cargo fmt --all -- --check
cargo test --workspace --exclude tdlib-rs
cargo check --all-targets --features docs
cargo clippy --all-targets --features docs -- -D warnings
```

These checks validate source behavior and controlled fixtures. Native packaging,
loading and published download acceptance require the corresponding CI runners.
