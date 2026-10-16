"""Fail-closed final-release identity, protected-environment and exact-artifact gates."""

import argparse
import ast
import json
import os
import re
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from release_tools import ROOT, PYTHON_VERSION, check_python, digest, git


def validate_identity(version, runtime_version, tag, commit, tag_commit, dirty):
    if not re.fullmatch(r"[1-9]\d*\.\d+\.\d+", version):
        raise ValueError("Final release requires a qualified >=1.0.0 version")
    if runtime_version != version or tag != "v" + version:
        raise ValueError("Tag, project version and runtime version must match exactly")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or tag_commit != commit or dirty:
        raise ValueError("Final release must be the exact clean tagged revision")


def candidate():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    tree = ast.parse((ROOT / "src/kshetrajna/__init__.py").read_text(encoding="utf-8"))
    versions = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    if len(versions) != 1 or os.environ.get("GITHUB_REF_TYPE") != "tag":
        raise ValueError("Dispatch the final workflow on the exact version tag")
    commit = git("rev-parse", "HEAD")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Invalid final tag")
    validate_identity(project["version"], versions[0], tag, commit,
                      git("rev-parse", tag + "^{commit}"), bool(git("status", "--porcelain")))
    if os.environ.get("GITHUB_SHA") != commit:
        raise ValueError("Workflow SHA and candidate differ")
    return {"version": project["version"], "source_commit": commit, "tag": tag, "python": PYTHON_VERSION}


def validate_environment(data):
    reviewers = [r for r in data.get("protection_rules", []) if r.get("type") == "required_reviewers"]
    policy = data.get("deployment_branch_policy") or {}
    if (not reviewers or not reviewers[0].get("reviewers") or reviewers[0].get("prevent_self_review") is not True
            or policy.get("custom_branch_policies") is not True):
        raise ValueError("Release environment needs independent required reviewers and explicit tag policies")
    if not any(r.get("type") in {"User", "Team"} and type(r.get("reviewer", {}).get("id")) is int
               and r["reviewer"]["id"] > 0 for r in reviewers[0]["reviewers"]):
        raise ValueError("Release environment has no recognized required reviewer")


def protected_environment(name):
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    url = "https://api.github.com/repos/" + repository + "/environments/" + urllib.parse.quote(name, safe="")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    def fetch(address):
        with urllib.request.urlopen(urllib.request.Request(address, headers=headers), timeout=30) as response:
            return json.load(response)
    validate_environment(fetch(url))
    policies = fetch(url + "/deployment-branch-policies?per_page=100")
    # A tag-specific policy is required; a broad branch policy is insufficient.
    if not any(p.get("type") == "tag" and p.get("name") == "v*" for p in policies.get("branch_policies", [])):
        raise ValueError("Protected environment must explicitly allow version tags (v*)")
    print("Protected environment configuration verified: " + name)


def validate_native(report, commit, architecture, runtime):
    if (report.get("schema") != 1 or report.get("source_commit") != commit
            or report.get("architecture") != architecture or report.get("python") != PYTHON_VERSION
            or report.get("runtime") != runtime or report.get("status") != "passed"
            or type(report.get("tests")) is not int or report["tests"] <= 0
            or any(type(report.get(k)) is not int or report[k] != 0 for k in ("failures", "errors", "skips"))):
        raise ValueError("Missing, stale, skipped or failed native " + architecture + " " + runtime + " checks")


def exact_artifacts(directory, version):
    expected = {f"Kshetrajna-{version}-{arch}{suffix}" for arch in ("x64", "arm64")
                for suffix in (".msi", ".spdx.json", ".evidence.json")}
    paths = list(Path(directory).iterdir())
    if any(p.is_symlink() or not p.is_file() for p in paths) or {p.name for p in paths} != expected:
        raise ValueError("Final artifacts must be exactly the two signed MSIs, SPDX inventories and evidence files")
    return {p.name: digest(p) for p in sorted(paths)}


def manifest(directory, identity):
    hashes = exact_artifacts(directory, identity["version"])
    for architecture in ("x64", "arm64"):
        base = f"Kshetrajna-{identity['version']}-{architecture}"
        evidence = json.loads((directory / (base + ".evidence.json")).read_text(encoding="utf-8-sig"))
        for runtime in ("source", "packaged"):
            validate_native(evidence[runtime], identity["source_commit"], architecture, runtime)
        lifecycle = evidence["lifecycle"]
        if (lifecycle.get("source_commit") != identity["source_commit"] or lifecycle.get("architecture") != architecture
                or lifecycle.get("msi_sha256") != hashes[base + ".msi"] or lifecycle.get("standard_user") is not True
                or lifecycle.get("status") != "passed" or lifecycle.get("history_preserved") is not True
                or any(lifecycle.get(k) != "passed" for k in ("install", "upgrade", "uninstall"))):
            raise ValueError("Missing exact-artifact native lifecycle qualification")
        if (evidence.get("authenticode") != "verified" or evidence.get("reproducible_unsigned") is not True
                or evidence.get("content_scan") != "passed" or evidence.get("msi_sha256") != hashes[base + ".msi"]
                or not re.fullmatch(r"[0-9a-f]{64}", evidence.get("unsigned_payload_sha256", ""))):
            raise ValueError("Signing, scan or unsigned reproducibility evidence missing")
        inventory = json.loads((directory / (base + ".spdx.json")).read_text(encoding="utf-8"))
        if (inventory.get("spdxVersion") != "SPDX-2.3" or inventory.get("dataLicense") != "CC0-1.0"
                or not any(p.get("SPDXID") == "SPDXRef-Installer" and p.get("name") == base + ".msi"
                           and p.get("versionInfo") == identity["version"]
                           and {"algorithm": "SHA256", "checksumValue": hashes[base + ".msi"]} in p.get("checksums", [])
                           for p in inventory.get("packages", []))):
            raise ValueError("Known-component SPDX inventory must identify the exact signed MSI")
    document = {"schema": 1, **identity, "artifacts": hashes,
                "provenance": "Not asserted by this manifest; verify the CI-issued GitHub/Sigstore attestations separately."}
    (directory / "release-manifest.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    hashes["release-manifest.json"] = digest(directory / "release-manifest.json")
    (directory / "SHA256SUMS").write_text("".join(f"{value}  {name}\n" for name, value in sorted(hashes.items())), encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("candidate", "environment", "manifest"))
    parser.add_argument("--environment")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    check_python()
    if args.command == "environment":
        protected_environment(args.environment)
    else:
        identity = candidate()
        if args.command == "manifest":
            manifest(args.directory, identity)
        else:
            print(json.dumps(identity))


if __name__ == "__main__":
    main()
