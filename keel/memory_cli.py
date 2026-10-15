"""Record what happened to a PR (merged / rejected) into the memory file, from the Reporter's own comment.
    python -m keel.memory_cli record --repo owner/name --pr 7 --outcome merged --file .keel-memory/memory.jsonl"""
from __future__ import annotations
import argparse, json, pathlib, re
from .ghio import MARK, latest_trusted, list_comments
from .memory_io import append_record

DATA = re.compile(r"<!-- keel:data (\{.*?\}) -->", re.S)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.memory_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--repo", required=True); r.add_argument("--pr", required=True)
    r.add_argument("--outcome", required=True, choices=["merged", "rejected"]); r.add_argument("--file", required=True)
    r.add_argument("--comments-file")
    a = ap.parse_args(argv)
    comments = json.loads(pathlib.Path(a.comments_file).read_text()) if a.comments_file else list_comments(a.repo, a.pr)
    c = latest_trusted(comments, MARK["report"])
    m = DATA.search(c["body"]) if c else None
    if not m:
        print("no Reporter data on this PR; nothing recorded")
        return
    rec = json.loads(m.group(1))
    rec["outcome"] = a.outcome
    pathlib.Path(a.file).parent.mkdir(parents=True, exist_ok=True)
    append_record(pathlib.Path(a.file), rec)
    print(f"recorded {rec['id']}: {a.outcome} ({len(rec.get('symbols', []))} symbols)")


if __name__ == "__main__":
    main()
