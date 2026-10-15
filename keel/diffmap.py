"""Pure helpers: unified diff -> hunks -> the symbols (from the base graph) the change lands in. No I/O."""
from __future__ import annotations
import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "modified"}


def parse_hunks(diff_text: str, strip_prefix: str = "") -> dict[str, list[tuple[int, int]]]:
    """path (relative to strip_prefix) -> [(old_start, old_count)] for every hunk of `git diff -U0`.
    Files that are new (--- /dev/null) have no old side and are skipped; use changed_paths to find them."""
    out: dict[str, list[tuple[int, int]]] = {}
    path = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            if path and strip_prefix and path.startswith(strip_prefix):
                path = path[len(strip_prefix):]
        elif line.startswith("--- ") and line.strip() == "--- /dev/null":
            path = None
        elif (m := HUNK.match(line)) and path:
            out.setdefault(path, []).append((int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)))
    return out


def changed_paths(name_status: str, strip_prefix: str = "") -> dict[str, list[str]]:
    """`git diff --name-status` -> {'added': [...], 'modified': [...], 'deleted': [...]} (paths relative to strip_prefix)."""
    res: dict[str, list[str]] = {"added": [], "modified": [], "deleted": []}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0][0], parts[-1]
        if strip_prefix:
            if not path.startswith(strip_prefix):
                continue
            path = path[len(strip_prefix):]
        res[STATUS.get(status, "modified")].append(path)
    return res


def symbols_from_hunks(hunks: dict[str, list[tuple[int, int]]], symbols: list[dict]) -> list[str]:
    """Qualified names of the code symbols (in the base checkout) whose lines the change touches.
    A pure insertion (old_count 0, after line `start`) counts for a symbol only if it lands strictly inside it."""
    by_file: dict[str, list[dict]] = {}
    for s in symbols:
        if s["kind"] == "code":
            by_file.setdefault(s["path"], []).append(s)
    out: list[str] = []
    for path, hs in hunks.items():
        syms = sorted(by_file.get(path, []), key=lambda s: s["end"] - s["start"])
        for start, count in hs:
            if count:
                owners = [next((s for s in syms if s["start"] <= line <= s["end"]), None) for line in range(start, start + count)]
            else:
                owners = [next((s for s in syms if s["start"] <= start and start + 1 <= s["end"]), None)]
            for s in owners:
                if s and s["qname"] not in out:
                    out.append(s["qname"])
    return out
