"""Run the built image, without networking or root, and check its real HTTP lifecycle."""

import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    subprocess.run(["docker", "info"], check=True, capture_output=True)
    subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--entrypoint", "python", args.image,
                    "/opt/release-tools/smoke_runtime.py", "--installed", "--require-non-root"], check=True, timeout=120)


if __name__ == "__main__":
    main()
