"""Publish native artifacts from an explicitly selected successful branch CI run."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def api(path, method="GET", data=None, missing_ok=False, repository=None):
    repository = repository or os.environ["GITHUB_REPOSITORY"]
    request = Request(
        f"https://api.github.com/repos/{repository}" + (f"/{path}" if path else ""),
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f'Bearer {os.environ["GH_TOKEN"]}',
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise


def resolve(run_id):
    if not re.fullmatch(r"[0-9]+", run_id):
        raise ValueError("run_id must be numeric")
    run = api(f"actions/runs/{run_id}")
    repository_id = os.environ["GITHUB_REPOSITORY_ID"]
    if (run["status"] != "completed" or run["conclusion"] != "success"
            or run["path"] != ".github/workflows/ci.yml"
            or run["event"] not in ("push", "workflow_dispatch")
            or run["head_branch"] != "main"
            or str(run["repository"]["id"]) != repository_id
            or str(run["head_repository"]["id"]) != repository_id):
        raise ValueError("source must be a successful completed CI run from this repository's main branch")
    sha = run["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid CI commit SHA")
    return sha


def validate_identity(tag):
    if tag != os.environ["RELEASE_TAG"]:
        raise ValueError("tag does not match source Cargo version")
    # GitHub redirects old repository names after a rename. Numeric identity
    # remains stable for both the source metadata URL and the workflow context.
    source = api("", repository=os.environ["RELEASE_REPOSITORY"])
    if str(source["id"]) != os.environ["GITHUB_REPOSITORY_ID"]:
        raise ValueError("source release repository mismatch")


def publish(tag, sha, assets, retarget=False):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("invalid release tag")
    # Recheck provenance before any mutation; the workflow already validated
    # source metadata and the complete native platform set before this point.
    if resolve(os.environ["SOURCE_RUN_ID"]) != sha:
        raise ValueError("CI source commit changed")
    validate_identity(tag)
    archives = sorted(str(path) for path in assets.glob("*.zip"))
    if not archives:
        raise ValueError("no validated release archives")
    release = api(f"releases/tags/{quote(tag, safe='')}", missing_ok=True)
    if release:
        raise ValueError("release already exists; inspect or remove an unpublished draft before retrying")
    commit = api(f"commits/{quote(tag, safe='')}", missing_ok=True)
    if commit and commit["sha"] != sha:
        if not retarget:
            raise ValueError("tag points to another commit; enable retarget_unpublished_tag explicitly")
        # GITHUB_TOKEN ref writes do not trigger tag-push CD, avoiding a rebuild.
        api(f"git/refs/tags/{tag}", "PATCH", {"sha": sha, "force": True})
    elif commit is None:
        api("git/refs", "POST", {"ref": f"refs/tags/{tag}", "sha": sha})
    repository = os.environ["GITHUB_REPOSITORY"]
    subprocess.run(["gh", "release", "create", tag, "--repo", repository,
                    "--target", sha, "--verify-tag", "--draft", "--generate-notes",
                    *archives], check=True)
    subprocess.run(["gh", "release", "edit", tag, "--repo", repository,
                    "--draft=false"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("stage", choices=("resolve", "identity", "publish"))
    args = parser.parse_args()
    if args.stage == "resolve":
        sha = resolve(os.environ["SOURCE_RUN_ID"])
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            print(f"sha={sha}", file=output)
        print(f"Validated CI commit: {sha}")
    elif args.stage == "identity":
        validate_identity(os.environ["REQUESTED_TAG"])
    else:
        publish(os.environ["REQUESTED_TAG"], os.environ["SOURCE_SHA"], Path("release-assets"),
                os.environ.get("RETARGET_UNPUBLISHED_TAG") == "true")
