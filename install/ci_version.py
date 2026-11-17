"""Version handling for the release workflow.

    python install/ci_version.py               decide the version (prepare job): writes
                                               version / numeric / release / tag to $GITHUB_OUTPUT
    python install/ci_version.py --write 1.2.3 write fv_version.py (build jobs)

Rules
    push of tag v1.2.3                      -> version 1.2.3, release
    Run workflow with version "1.2.3"      -> version 1.2.3, release, tag v1.2.3 is created
    Run workflow with the box empty         -> version 0.0.0-test.<sha>, build and test only
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALID = re.compile(r"^\d+\.\d+\.\d+([-+.][0-9A-Za-z.\-]+)?$")


def numeric(v):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    return ".".join(m.groups()) if m else "0.0.0"


def write_version_file(v):
    with open(os.path.join(ROOT, "fv_version.py"), "w", encoding="utf-8") as fh:
        fh.write("# Written by the release workflow from the version tag - do not edit.\n"
                 f'APP_VERSION = "{v}"\n')
    print(f"fv_version.py -> {v}")


def out(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kw.items()]
    print("\n".join(lines))
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def decide():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    wanted = os.environ.get("INPUT_VERSION", "").strip()
    sha = os.environ.get("GITHUB_SHA", "local")[:7]

    if event == "push" and ref_type == "tag":
        v, release = ref_name[1:] if ref_name.startswith("v") else ref_name, True
    elif wanted:
        v, release = wanted[1:] if wanted.lower().startswith("v") else wanted, True
    else:
        v, release = f"0.0.0-test.{sha}", False

    if not VALID.match(v):
        sys.exit(f"::error::'{v}' is not a version like 1.2.3 (tags must look like v1.2.3)")

    tag = f"v{v}"
    if release and event == "workflow_dispatch":
        r = subprocess.run(["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode == 0 and r.stdout.strip():
            sys.exit(f"::error::Tag {tag} already exists. Use a new version number, or re-run the "
                     f"workflow of that tag from the Actions tab.")
    out(version=v, numeric=numeric(v), release="true" if release else "false", tag=tag)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--write":
        write_version_file(sys.argv[2])
    else:
        decide()
