"""Publish a verified draft only after all exact assets and CI attestations are present."""

import argparse
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from release_gate import candidate, protected_environment
from release_tools import check_python, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    check_python()
    identity = candidate()
    protected_environment("release-publish")
    repository = os.environ["GITHUB_REPOSITORY"]
    tag = identity["tag"]
    directory = args.directory.resolve()
    hashes = json.loads((directory / "release-manifest.json").read_text(encoding="utf-8"))["artifacts"]
    hashes.update({name: digest(directory / name) for name in ("release-manifest.json", "SHA256SUMS")})
    if {p.name for p in directory.iterdir()} != set(hashes):
        raise ValueError("Publication directory contains unexpected artifacts")
    for name, expected in hashes.items():
        if (directory / name).is_symlink() or digest(directory / name) != expected:
            raise ValueError("Publication content changed after qualification")
        subprocess.run(["gh", "attestation", "verify", str(directory / name), "--repo", repository,
                        "--signer-workflow", repository + "/.github/workflows/final-release.yml",
                        "--source-digest", identity["source_commit"], "--source-ref", "refs/tags/" + tag,
                        "--deny-self-hosted-runners"], check=True)
    url = "https://api.github.com/repos/" + repository + "/releases/tags/" + urllib.parse.quote(tag, safe="")
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                                  "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=30):
            raise ValueError("A release already exists for this tag; review it rather than overwriting")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    subprocess.run(["gh", "release", "create", tag, *[str(directory / n) for n in sorted(hashes)],
                    "--repo", repository, "--draft", "--verify-tag", "--title", "Kshetrajna " + tag,
                    "--notes", "Signed per-user x64/ARM64 MSIs. See SHA256SUMS, SPDX known-component inventories, exact-artifact evidence and independently verifiable CI provenance."], check=True)
    # Any error from here leaves a draft. It never publishes a partial final release.
    remote = json.loads(subprocess.check_output(["gh", "release", "view", tag, "--repo", repository,
                                                "--json", "isDraft,assets"], text=True))
    if remote.get("isDraft") is not True or {a["name"] for a in remote["assets"]} != set(hashes):
        raise ValueError("Draft release asset set differs from the exact qualified set")
    with tempfile.TemporaryDirectory(prefix="kshetrajna-release-download-") as name:
        downloaded = Path(name)
        subprocess.run(["gh", "release", "download", tag, "--repo", repository, "--dir", str(downloaded)], check=True)
        if {p.name for p in downloaded.iterdir()} != set(hashes) or any(digest(downloaded / n) != h for n, h in hashes.items()):
            raise ValueError("Downloaded draft assets do not match qualified bytes")
    subprocess.run(["gh", "release", "edit", tag, "--repo", repository, "--draft=false"], check=True)


if __name__ == "__main__":
    main()
