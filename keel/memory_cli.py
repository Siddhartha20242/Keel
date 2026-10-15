"""Record what happened to a PR (merged / rejected) into the memory file, from the Reporter's own comment.
    python -m keel.memory_cli record --repo owner/name --pr 7 --outcome merged --file .keel-memory/memory.jsonl"""
from __future__ import annotations
import argparse, json, pathlib, re
from .ghio import MARK, latest_trusted, list_comments
from . import ledger
from .memory_io import append_record

DATA = re.compile(r"<!-- keel:data (\{.*?\}) -->", re.S)
FINDINGS = re.compile(r"<!-- keel:findings (\[.*?\]) -->", re.S)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keel.memory_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--repo", required=True); r.add_argument("--pr", required=True)
    r.add_argument("--outcome", required=True, choices=["merged", "rejected"]); r.add_argument("--file", required=True)
    r.add_argument("--comments-file"); r.add_argument("--ledger")
    a = ap.parse_args(argv)
    comments = json.loads(pathlib.Path(a.comments_file).read_text()) if a.comments_file else list_comments(a.repo, a.pr)
    c = latest_trusted(comments, MARK["report"])
    m = DATA.search(c["body"]) if c else None
    if not m:
        print("no Reporter data on this PR; nothing recorded")
        return
    rec = json.loads(m.group(1))
    rec["outcome"] = a.outcome
    sk = latest_trusted(comments, MARK["skeptic"])        # findings only from the bot's own Skeptic comment
    fm = FINDINGS.search(sk["body"]) if sk else None
    rec["findings"] = json.loads(fm.group(1)) if fm else []
    pathlib.Path(a.file).parent.mkdir(parents=True, exist_ok=True)
    append_record(pathlib.Path(a.file), rec)
    print(f"recorded {rec['id']}: {a.outcome} ({len(rec.get('symbols', []))} symbols)")
    if a.ledger:
        pathlib.Path(a.ledger).parent.mkdir(parents=True, exist_ok=True)
        ok, msg = ledger.verify(ledger.read(a.ledger))
        if not ok:
            raise SystemExit(f"refusing to extend a broken ledger: {msg}")
        added = ledger.append(a.ledger, ledger.stage_entries(rec, a.outcome))
        print(f"ledger: appended {len(added)} records, head {added[-1]['hash'][:16]}")


if __name__ == "__main__":
    main()
