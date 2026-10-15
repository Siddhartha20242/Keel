"""Build replay cases: (real GitHub issue text, parent commit, ground-truth files/tests of the real fix).

Public data only: pallets/flask issues + git history.
"""
import json, subprocess, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT / "targets" / "flask"
CASES = [  # (fix commit, GitHub issue number, split). "dev" cases were used while tuning; "heldout" were mined after and
    # only evaluated once the retrieval settings were frozen.
    ("4995a775", 5553, "dev"), ("53b8f082", 5786, "dev"), ("9822a035", 5774, "dev"), ("1af8f957", 5391, "dev"), ("e82db2ca", 5916, "dev"),
    ("6a649690", 5815, "heldout"), ("daf1510a", 5729, "heldout"), ("4f7156f2", 5636, "heldout"),
    ("2c316030", 5628, "heldout"), ("9efc1ebe", 5472, "heldout"), ("28d5a4d7", 5504, "heldout"), ("c7a53888", 5625, "heldout")
]

def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=True).stdout

def main():
    for sha, num, split in CASES:
        full = git("rev-parse", sha).strip()
        parent = git("rev-parse", full + "^").strip()
        files = [f for f in git("show", full, "--format=", "--name-only").split("\n") if f]
        raw = subprocess.run(["gh", "api", f"repos/pallets/flask/issues/{num}"], capture_output=True, text=True)
        issue = json.loads(raw.stdout) if raw.returncode == 0 else {"title": git("log", "-1", "--format=%s", full).strip(), "body": ""}
        case = {
            "id": sha[:8], "split": split, "fix_commit": full, "parent": parent, "issue_number": num,
            "title": issue.get("title", ""), "body": (issue.get("body") or "")[:4000],
            "truth_src": [f for f in files if f.startswith("src/") and f.endswith(".py")],
            "truth_tests": [f for f in files if f.startswith("tests/") and f.endswith(".py")],
        }
        (ROOT / "data" / "cases" / f"{sha[:8]}.json").write_text(json.dumps(case, indent=2))
        print(sha[:8], num, case["title"][:70], case["truth_src"], case["truth_tests"])

main()
