"""Static extraction of a code graph from a Python repo checkout.

Produces plain dicts (no DB access): files, symbols, call edges, and git history facts.
The call graph is a *static approximation*: names are resolved through imports,
re-exports, `self.` method lookup over the class hierarchy, and (capped) unique-name
matching for attribute calls. Ambiguous names get no edge rather than a wrong one.
"""
from __future__ import annotations
import ast, pathlib, re, subprocess
from collections import defaultdict
from itertools import combinations

AMBIGUITY_CAP = 3          # attribute call `x.name()` links only if <= this many candidates
# `x.encode()` on an unknown `x` is almost always a str/dict/list/file method, not our function of the same name
BUILTIN_METHODS = set("""encode decode get items keys values pop update add append extend remove insert index count sort reverse copy clear
join split rsplit strip lstrip rstrip replace format startswith endswith find lower upper title read write readline readlines
close flush seek open send recv connect bind listen accept run start stop wait put set setdefault discard union""".split())
MAX_FILES_PER_COMMIT = 15  # bulk commits (reformat, bump) say nothing about co-change


def _module_name(root: pathlib.Path, path: pathlib.Path) -> str:
    rel = path.relative_to(root)
    if rel.parts[0] == "src":
        rel = pathlib.Path(*rel.parts[1:])
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


_STOP = set("""self cls none true false return import from this that with have will would should could there their about which when what
else elif pass raise isinstance super args kwargs while yield lambda assert global del try except finally class def not and for the are was
were been being does done into than then them they your you its it's can may might must also only just like such some any all each other more most""".split())


def _split_ident(name: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower().split("_")
    return [p for p in parts if len(p) >= 3]


def symbol_words(node: ast.AST, include_body: bool = True) -> list[str]:
    """Lowercase lexical entry points for a symbol: its name, docstring, referenced identifiers and string constants."""
    out: dict[str, None] = {}

    def put(tok):
        tok = tok.lower()
        if len(tok) >= 3 and tok not in _STOP:
            out[tok] = None

    name = getattr(node, "name", "")
    put(name)
    for p in _split_ident(name):
        put(p)
    doc = ast.get_docstring(node) or ""
    for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", doc[:400]):
        put(w)
    if include_body:
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                put(n.id)
            elif isinstance(n, ast.Attribute):
                put(n.attr)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{2,40}", n.value):
                put(n.value)
    return list(out)[:160]


class Module:
    def __init__(self, name, path, tree, is_test):
        self.name, self.path, self.tree, self.is_test = name, path, tree, is_test
        self.defs: dict[str, str] = {}      # top-level name -> qname
        self.imports: dict[str, str] = {}   # alias -> dotted target (module or module.attr)


def extract_code(root: pathlib.Path) -> dict:
    root = pathlib.Path(root)
    mods: dict[str, Module] = {}
    for sub, is_test in (("src", False), ("tests", True)):
        base = root / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            name = _module_name(root, p)
            mods[name] = Module(name, str(p.relative_to(root)), tree, is_test)

    symbols: dict[str, dict] = {}
    class_bases: dict[str, list[str]] = {}      # class qname -> base simple names
    class_methods: dict[str, dict[str, str]] = defaultdict(dict)  # class qname -> {method: qname}
    fn_nodes: dict[str, tuple[Module, ast.AST, str | None]] = {}

    def add(qname, name, kind, mod, node, cls=None):
        symbols[qname] = {"qname": qname, "name": name, "kind": kind, "path": mod.path,
                          "start": node.lineno, "end": getattr(node, "end_lineno", node.lineno), "cls": cls or "",
                          "words": symbol_words(node, include_body=(kind != "class"))}

    for m in mods.values():
        for node in m.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{m.name}.{node.name}"
                kind = ("test" if node.name.startswith("test") and m.path.split("/")[-1].startswith("test_") else "helper") if m.is_test else "code"
                add(q, node.name, kind, m, node); m.defs[node.name] = q; fn_nodes[q] = (m, node, None)
            elif isinstance(node, ast.ClassDef):
                cq = f"{m.name}.{node.name}"
                add(cq, node.name, "class", m, node); m.defs[node.name] = cq
                class_bases[cq] = [b.id if isinstance(b, ast.Name) else b.attr for b in node.bases
                                   if isinstance(b, (ast.Name, ast.Attribute))]
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        q = f"{cq}.{sub.name}"
                        if m.is_test:
                            kind = "test" if sub.name.startswith("test") and m.path.split("/")[-1].startswith("test_") else "helper"
                        else:
                            kind = "code"
                        add(q, sub.name, kind, m, sub, cls=cq); class_methods[cq][sub.name] = q
                        fn_nodes[q] = (m, sub, cq)
        for node in ast.walk(m.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    m.imports[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                pkg = m.name.split(".")
                is_pkg_init = m.path.endswith("__init__.py")
                if node.level:
                    base = pkg if is_pkg_init else pkg[:-1]
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    target = ".".join(base + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                for a in node.names:
                    m.imports[a.asname or a.name] = f"{target}.{a.name}"

    # --- name resolution -------------------------------------------------
    by_name: dict[str, list[str]] = defaultdict(list)
    for q, s in symbols.items():
        if s["kind"] in ("code", "class"):
            by_name[s["name"]].append(q)

    def resolve_dotted(dotted: str, depth=0) -> str | None:
        if dotted in symbols:
            return dotted
        if depth > 4 or "." not in dotted:
            return None
        modname, attr = dotted.rsplit(".", 1)
        mod = mods.get(modname)
        if mod:
            if attr in mod.defs:
                return mod.defs[attr]
            if attr in mod.imports:                     # re-export
                return resolve_dotted(mod.imports[attr], depth + 1)
        return None

    def mro_lookup(cq: str, method: str, seen=None) -> str | None:
        seen = seen or set()
        if cq in seen:
            return None
        seen.add(cq)
        if method in class_methods.get(cq, {}):
            return class_methods[cq][method]
        mod = mods.get(cq.rsplit(".", 1)[0])
        for b in class_bases.get(cq, []):
            bq = mod.defs.get(b) if mod and b in mod.defs else None
            if bq is None and mod and b in mod.imports:
                bq = resolve_dotted(mod.imports[b])
            if bq:
                r = mro_lookup(bq, method, seen)
                if r:
                    return r
        return None

    def descendants(cq: str) -> list[str]:
        out = []
        for c, bases in class_bases.items():
            mod = mods.get(c.rsplit(".", 1)[0])
            for b in bases:
                bq = (mod.defs.get(b) if mod and b in mod.defs else None) or \
                     (resolve_dotted(mod.imports[b]) if mod and b in mod.imports else None)
                if bq == cq:
                    out.append(c); out.extend(descendants(c))
        return out

    calls: dict[tuple[str, str], int] = defaultdict(int)

    def link(src, dst):
        if dst and dst != src and dst in symbols:
            calls[(src, dst)] += 1
            if symbols[dst]["kind"] == "class":      # ClassName(...) -> __init__
                init = mro_lookup(dst, "__init__")
                if init:
                    calls[(src, init)] += 1

    for q, (m, fnode, cls) in fn_nodes.items():
        for node in ast.walk(fnode):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name):
                n = f.id
                if n in m.defs:
                    link(q, m.defs[n])
                elif n in m.imports:
                    link(q, resolve_dotted(m.imports[n]))
            elif isinstance(f, ast.Attribute):
                attr = f.attr
                base = f.value
                if isinstance(base, ast.Name) and base.id in ("self", "cls") and cls:
                    t = mro_lookup(cls, attr)
                    if t:
                        link(q, t)
                    for d in descendants(cls):        # overrides in subclasses
                        if attr in class_methods.get(d, {}):
                            link(q, class_methods[d][attr])
                    continue
                if isinstance(base, ast.Name) and base.id in m.imports:
                    t = resolve_dotted(f"{m.imports[base.id]}.{attr}")
                    if t:
                        link(q, t)
                        continue
                cands = [c for c in by_name.get(attr, []) if symbols[c]["kind"] != "class" or True]
                if 0 < len(cands) <= AMBIGUITY_CAP and not attr.startswith("__") and attr not in BUILTIN_METHODS:
                    for c in cands:
                        link(q, c)

    imports: set[tuple[str, str]] = set()
    for m in mods.values():
        for target in m.imports.values():
            t = target
            while t and t not in mods:                    # `pkg.mod.name` -> the module `pkg.mod`
                t = t.rsplit(".", 1)[0] if "." in t else ""
            if t and t != m.name:
                imports.add((m.path, mods[t].path))
    files = {m.path: {"path": m.path, "kind": "test" if m.is_test else "src"} for m in mods.values()}
    return {
        "imports": [{"src": a, "dst": b} for a, b in sorted(imports)],
        "files": list(files.values()),
        "symbols": list(symbols.values()),
        "calls": [{"src": a, "dst": b, "n": n} for (a, b), n in calls.items()],
    }


# ---------------------------------------------------------------- git history
def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def extract_history(root: pathlib.Path, ref: str = "HEAD", prefix: str = "") -> dict:
    """Commits reachable from `ref` (so a replay at a parent commit cannot see the fix).
    `prefix` = subfolder of the repo that holds src/ and tests/ (e.g. "demo-app/"); paths are returned relative to it."""
    out = _git(root, "log", ref, "--no-merges", "--name-only", "--date=short",
               "--format=@@%H\x1f%an\x1f%ad\x1f%s")
    commits, cochange, authored = [], defaultdict(int), defaultdict(int)
    subj_to_sha = {}
    for blk in out.split("@@")[1:]:
        lines = blk.strip("\n").split("\n")
        sha, author, at, subject = lines[0].split("\x1f", 3)
        fs = [f[len(prefix):] for f in lines[1:] if f.startswith(prefix) and f.endswith(".py")
              and (f[len(prefix):].startswith("src/") or f[len(prefix):].startswith("tests/"))]
        if not fs:
            continue
        commits.append({"sha": sha, "author": author, "at": at, "subject": subject, "files": fs, "reverted": False})
        subj_to_sha.setdefault(subject, sha)
        if len(fs) <= MAX_FILES_PER_COMMIT:
            for a, b in combinations(sorted(set(fs)), 2):
                cochange[(a, b)] += 1
        for f in set(fs):
            authored[(author, f)] += 1
    reverted = set()
    for c in commits:
        mm = re.match(r'Revert "(.*)"', c["subject"])
        if mm and mm.group(1) in subj_to_sha:
            reverted.add(subj_to_sha[mm.group(1)])
    for c in commits:
        c["reverted"] = c["sha"] in reverted
    return {"commits": commits,
            "cochange": [{"a": a, "b": b, "n": n} for (a, b), n in cochange.items() if n >= 2],
            "authored": [{"person": p, "file": f, "n": n} for (p, f), n in authored.items()]}
