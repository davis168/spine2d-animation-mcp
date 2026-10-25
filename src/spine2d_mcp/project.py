"""The format version a game project needs, and when to ask the user.

A skeleton belongs to the game project it sits in. Starting at the
skeleton's folder and walking up to the repository root (the first folder
with `.git`; the home folder, or the filesystem root, outside a repository),
the nearest of each counts:

- `package.json` with an `@esotericsoftware/spine-*` dependency: the version
  installed in `node_modules` if there is one, else the declared range when
  it pins one format version (`~4.2.0`, `4.2.x`, `4.2.95`). A range spanning
  several (`^4.2.0`, `*`, `latest`) is unclear: a fresh install of `^4.2.0`
  gets 4.3 today.
- `.spine-version`: a version declaration holding just the format version
  (`4.3`), for games whose runtime is not an npm dependency.

When both say different versions, or what they say is unclear, the version
is not guessed: tools raise `NeedsAnswer`, which the MCP tool returns as a
question for the agent to put to the user.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .spine import versions

PACKAGE_PREFIX = "@esotericsoftware/spine-"
DECLARATION = ".spine-version"
ENV = "SPINE_FORMAT_VERSION"


class NeedsAnswer(Exception):
    """A decision only the user can make. Nothing has been written."""

    def __init__(self, question: str, options: list[dict], recommended: str | None = None):
        super().__init__(question)
        self.question = question
        self.options = options
        self.recommended = recommended

    def result(self) -> dict:
        return {
            "needs_answer": True,
            "question": self.question,
            "options": self.options,
            "recommended": self.recommended,
            "written": False,
            "next": "Ask the user this question with these options (say which is recommended), wait for "
                    "their answer, then do what the chosen option says. Do not choose for them.",
        }


@dataclass
class Signal:
    """What one file in the game project says about its format version."""

    source: str  # e.g. "slot-games/package.json: @esotericsoftware/spine-phaser-v4 4.2.95 installed"
    version: str | None  # None when unclear
    problem: str | None = None
    candidates: list[str] | None = None  # format versions an unclear range allows, when bounded
    settleable: bool = True  # a declaration may settle it (an open range, not contradicting packages)


@dataclass
class ProjectVersion:
    version: str | None
    source: str | None
    repo_root: Path | None
    start: Path
    signals: list[Signal] = field(default_factory=list)
    problem: str | None = None  # why the version is unclear or contradictory
    candidates: list[str] | None = None  # the versions in question, when unclear
    package_version: str | None = None  # what package.json clearly says, if anything
    declaration_path: Path | None = None  # the nearest .spine-version, if any

    @property
    def unclear(self) -> bool:
        return self.version is None and self.problem is not None

    def describe(self, file_version: str | None = None) -> dict:
        """What inspect, validate and the editing tools report."""
        out: dict = {"file": file_version, "game_project": self.version, "from": self.source}
        if self.problem:
            out["problem"] = self.problem
        out["note"] = self.note(file_version)
        return out

    def note(self, file_version: str | None) -> str:
        head = f"{file_version} file" if file_version else "new file"
        if self.version is None:
            if self.problem:
                return f"{head}; the game project's version is unclear: {self.problem}"
            return f"{head}; no game project version found (no Spine runtime in package.json, no {DECLARATION})"
        if file_version and file_version != self.version:
            return (f"{head}, but the game project uses {self.version} ({self.source}): the game cannot load "
                    "it until it is converted")
        return f"{head}; the game project uses {self.version} ({self.source})"

    def declaration_file(self) -> Path:
        """Where a version declaration is saved: over the nearest existing one,
        else at the repository root, else beside the skeleton."""
        return self.declaration_path or (self.repo_root or self.start) / DECLARATION


# ── detection ───────────────────────────────────────────────────────────────

def detect(path: str | Path) -> ProjectVersion:
    """The project format version for a skeleton at `path` (a file or folder,
    which need not exist yet)."""
    start = Path(path).expanduser().resolve()
    if start.is_file() or (not start.exists() and start.suffix):
        start = start.parent
    while not start.exists() and start != start.parent:
        start = start.parent
    dirs, repo_root = _search_dirs(start)
    package = declaration = declaration_path = None
    for folder in dirs:
        if declaration is None and (folder / DECLARATION).is_file():
            declaration_path = folder / DECLARATION
            declaration = _read_declaration(declaration_path)
        if package is None and (folder / "package.json").is_file():
            package = _read_package(folder, dirs)
    project = _combine(start, repo_root, package, declaration)
    project.declaration_path = declaration_path
    return project


def _search_dirs(start: Path) -> tuple[list[Path], Path | None]:
    home = Path.home().resolve()
    out = []
    for folder in (start, *start.parents):
        out.append(folder)
        if (folder / ".git").exists():
            return out, folder
        if folder == home:
            break
    return out, None


def _display(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _read_declaration(path: Path) -> Signal:
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    except OSError as e:
        return Signal(_display(path), None, f"cannot read {_display(path)}: {e}")
    text = next((ln for ln in lines if ln and not ln.startswith("#")), "")
    version = versions.major_minor(text)
    if version is None:
        return Signal(_display(path), None, f"{_display(path)} holds {text!r}, not a format version like 4.3")
    return Signal(f"{_display(path)}: {text}", version)


def _read_package(folder: Path, dirs: list[Path]) -> Signal | None:
    path = folder / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        for name, spec in ((data.get(key) or {}) if isinstance(data, dict) else {}).items():
            if name.startswith(PACKAGE_PREFIX) and isinstance(spec, str):
                deps.setdefault(name, spec)
    if not deps:
        return None
    found: list[tuple[str, str | None, str, list[str] | None]] = []  # (package, version, description, candidates)
    for name, spec in sorted(deps.items()):
        installed = _installed(folder, dirs, name)
        if installed:
            found.append((name, versions.major_minor(installed), f"{name} {installed} installed", None))
            continue
        lines = format_lines(spec)
        if lines is not None and len(lines) == 1:
            found.append((name, lines[0], f"{name} {spec}", None))
        else:
            allows = f"allows {' and '.join(lines)}" if lines else "does not pin one format version"
            found.append((name, None, f"{name} {spec!r} ({allows}; pin it with ~, e.g. ~4.2.0)", lines))
    source = f"{_display(path)}: " + ", ".join(desc for _, _, desc, _ in found)
    unclear = [(desc, cand) for _, version, desc, cand in found if version is None]
    if unclear:
        candidates = unclear[0][1] if len(unclear) == 1 else None
        return Signal(source, None, f"{_display(path)}: " + "; ".join(desc for desc, _ in unclear), candidates)
    formats = sorted({version for _, version, _, _ in found})
    if len(formats) > 1:
        return Signal(source, None, f"{_display(path)} has Spine packages on different format versions "
                                    f"({', '.join(desc for _, _, desc, _ in found)})", formats, settleable=False)
    return Signal(source, formats[0])


def _installed(folder: Path, dirs: list[Path], name: str) -> str | None:
    """The version npm installed for `name`, looking where Node would (this
    folder's node_modules, then each parent's, within the search)."""
    for parent in dirs[dirs.index(folder):]:
        manifest = parent / "node_modules" / name / "package.json"
        if manifest.is_file():
            try:
                version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
            except (OSError, json.JSONDecodeError, AttributeError):
                return None
            return version if isinstance(version, str) else None
    return None


def _combine(start: Path, repo_root: Path | None, package: Signal | None, declaration: Signal | None) -> ProjectVersion:
    signals = [s for s in (package, declaration) if s is not None]
    project = ProjectVersion(None, None, repo_root, start, signals,
                             package_version=package.version if package else None)
    if not signals:
        return project
    if declaration is not None and declaration.version is None:
        project.problem = declaration.problem
        project.candidates = [package.version] if package and package.version else (package.candidates if package else None)
        return project
    if package is None or declaration is None:
        only = signals[0]
        project.version, project.source, project.problem = only.version, only.source, only.problem
        project.candidates = only.candidates
        return project
    if package.version == declaration.version:
        project.version, project.source = package.version, f"{package.source}; {declaration.source}"
    elif package.version is None and package.settleable and (
            package.candidates is None or declaration.version in package.candidates):
        # The declaration settles what an open package.json range leaves unclear.
        project.version, project.source = declaration.version, f"{declaration.source} ({package.problem})"
    else:
        says = package.version or f"one of {', '.join(package.candidates or [])}"
        project.problem = f"{package.source} says {says} but {declaration.source} says {declaration.version}"
        project.candidates = sorted({*(package.candidates or [package.version]), declaration.version})
    return project


# ── npm ranges ──────────────────────────────────────────────────────────────

_PART = r"(?:0|[1-9]\d*|[xX*])"
_COMPARATOR = re.compile(rf"^(\^|~>?|>=|<=|>|<|=)?v?({_PART})(?:\.({_PART}))?(?:\.({_PART}))?(?:[-+][0-9A-Za-z.-]+)?$")
Version = tuple[int, int, int]


def format_lines(spec: str) -> list[str] | None:
    """The format versions ("4.2") an npm range allows, or None when it is
    open-ended or not a version range (a tag, URL or path)."""
    spec = spec.strip()
    if not spec:
        return None
    lines: set[str] = set()
    for alternative in spec.split("||"):
        interval = _interval(alternative.strip())
        if interval is None:
            return None
        found = _lines_between(*interval)
        if found is None:
            return None
        lines |= found
    return sorted(lines, key=lambda v: tuple(map(int, v.split("."))))


def _interval(text: str) -> tuple[Version, Version | None] | None:
    if not text:
        return None
    hyphen = re.match(r"^(\S+)\s+-\s+(\S+)$", text)
    if hyphen:
        low, high = _comparator(hyphen.group(1)), _comparator("<=" + hyphen.group(2))
        if low is None or high is None:
            return None
        return low[0], high[1]
    text = re.sub(r"(>=|<=|>|<|=|~>?|\^)\s+", r"\1", text)
    low: Version = (0, 0, 0)
    high: Version | None = None
    for token in text.split():
        bounds = _comparator(token)
        if bounds is None:
            return None
        low = max(low, bounds[0])
        if bounds[1] is not None:
            high = bounds[1] if high is None else min(high, bounds[1])
    return low, high


def _comparator(token: str) -> tuple[Version, Version | None] | None:
    m = _COMPARATOR.match(token)
    if m is None:
        return None
    op = m.group(1) or "="
    parts = [None if p in (None, "x", "X", "*") else int(p) for p in m.group(2, 3, 4)]
    if parts[0] is None:
        parts = [None, None, None]
    elif parts[1] is None:
        parts[2] = None
    major, minor, patch = parts
    floor: Version = (major or 0, minor or 0, patch or 0)

    def after() -> Version:  # the first version above the partial version
        if major is None:
            return (0, 0, 0)
        if minor is None:
            return (major + 1, 0, 0)
        if patch is None:
            return (major, minor + 1, 0)
        return (major, minor, patch + 1)

    if op == "=":
        return (floor, None) if major is None else (floor, after())
    if op.startswith("~"):
        if major is None:
            return (floor, None)
        return floor, ((major + 1, 0, 0) if minor is None else (major, minor + 1, 0))
    if op == "^":
        if major is None:
            return (floor, None)
        if major > 0 or minor is None:
            return floor, (major + 1, 0, 0)
        if minor > 0 or patch is None:
            return floor, (0, minor + 1, 0)
        return floor, (0, 0, patch + 1)
    if op == ">=":
        return floor, None
    if op == ">":
        return after(), None
    if op == "<":
        return (0, 0, 0), floor
    return (0, 0, 0), after()  # <=


def _lines_between(low: Version, high: Version | None) -> set[str] | None:
    """Format lines holding some version in [low, high); None if unbounded."""
    if high is None:
        return None
    if high <= low:
        return set()
    if high[2] > 0:
        last = (high[0], high[1])
    elif high[1] > 0:
        last = (high[0], high[1] - 1)
    else:
        return None if high[0] - 1 >= low[0] else set()  # up to x.∞: open-ended within a major
    if last[0] != low[0]:
        return None
    return {f"{low[0]}.{minor}" for minor in range(low[1], last[1] + 1)}


# ── resolution ──────────────────────────────────────────────────────────────

def _version_options(candidates, recommended: str, package_version: str | None = None) -> list[dict]:
    """One option per version; choosing saves it as the project's declaration,
    except a version package.json contradicts (saving would not settle it)."""
    options = []
    for v in sorted(candidates, key=lambda v: tuple(map(int, v.split("."))), reverse=True):
        option = {"answer": f"{v}" + (" (recommended)" if v == recommended else ""),
                  "call_again_with": {"format_version": v}}
        if package_version in (None, v):
            option["call_again_with"]["save_project_version"] = True
        else:
            option["answer"] += f" (package.json still says {package_version}; change it to make {v} the project's version)"
        options.append(option)
    return options


def for_new_file(target: str | Path, requested: str | None = None, allow_version_mismatch: bool = False,
                 save_project_version: bool = False) -> tuple[str, ProjectVersion, list[str]]:
    """The format version to create a skeleton in at `target`, the game
    project it belongs to, and notes for the result. Raises NeedsAnswer.
    With save_project_version the caller saves the declaration after its work
    succeeds (save_declaration); a declaration that would contradict the
    project is refused here, before anything is written."""
    project = detect(target)
    notes: list[str] = []
    if requested is not None:
        version = versions.get(requested).version
        if project.version and project.version != version and not allow_version_mismatch:
            raise NeedsAnswer(
                f"The game project uses Spine {project.version} ({project.source}), but format version {version} "
                f"was requested. A {version} skeleton will not load in this game.",
                [{"answer": f"Use {project.version}, the game project's version", "call_again_with":
                  {"format_version": project.version}},
                 {"answer": f"Use {version} anyway", "call_again_with":
                  {"format_version": version, "allow_version_mismatch": True}}],
                recommended=f"Use {project.version}, the game project's version")
        if project.version != version and (project.version or project.unclear):
            notes.append(project.note(version))
    elif project.version:
        version = project.version
        if not versions.is_supported(version):
            raise versions.UnsupportedVersion(
                f"the game project uses Spine {version} ({project.source}), which spine2d-mcp does not support "
                f"(supported: {versions.supported_text()})")
    elif project.unclear:
        candidates = [v for v in (project.candidates or versions.SUPPORTED) if versions.is_supported(v)] \
            or list(versions.SUPPORTED)
        if project.package_version in candidates:
            recommended = project.package_version  # what the installed runtime loads
        else:
            recommended = versions.NEWEST if versions.NEWEST in candidates else candidates[-1]
        raise NeedsAnswer(
            f"Which Spine format version does this game project use? {project.problem}. Choosing saves it as "
            f"{_display(project.declaration_file())} so it is not asked again.",
            _version_options(candidates, recommended, project.package_version),
            recommended=recommended)
    elif os.environ.get(ENV):
        version = versions.get(os.environ[ENV]).version
        notes.append(f"format version {version} from ${ENV} (no game project version found)")
    else:
        raise NeedsAnswer(
            f"No game project version found for {_display(project.start)}: no Spine runtime in a package.json "
            f"and no {DECLARATION} up to {_display((project.repo_root or project.start))}, and ${ENV} is not "
            "set. Which Spine format version should the new skeleton use? Choosing saves it as "
            f"{_display(project.declaration_file())} so it is not asked again.",
            _version_options(versions.SUPPORTED, versions.NEWEST),
            recommended=versions.NEWEST)
    if save_project_version:
        check_declaration(project, version)
    return version, project, notes


def check_declaration(project: ProjectVersion, version: str) -> None:
    if project.version is not None and project.version != version:
        raise ValueError(f"the game project already uses {project.version} ({project.source}); "
                         f"not writing a {DECLARATION} that contradicts it")
    if project.package_version is not None and project.package_version != version:
        raise ValueError(f"package.json says {project.package_version}; a {DECLARATION} saying {version} would "
                         "contradict it (change the dependency instead)")


def save_declaration(project: ProjectVersion, version: str) -> str:
    """Write `.spine-version` for a game project whose version was unknown or unclear."""
    if project.version == version:
        return f"the game project already declares {version} ({project.source}); nothing saved"
    check_declaration(project, version)
    path = project.declaration_file()
    path.write_text(f"{version}\n", encoding="utf-8")
    return f"saved format version {version} for this game project in {_display(path)}"


def for_existing_file(where: str | Path, file_version: str, allow_version_mismatch: bool = False,
                      action: str = "edit", file: str | Path | None = None,
                      save_project_version: bool = False) -> ProjectVersion:
    """The game project at `where` (the skeleton, or where its output goes).
    NeedsAnswer when the skeleton `file`'s format version differs from the
    project's, or the project's version is unclear, and going ahead has not
    been allowed. With save_project_version the caller saves `file_version`
    as the project's declaration once its work succeeds."""
    project = detect(where)
    source = Path(file or where)
    if save_project_version:
        check_declaration(project, file_version)
        return project
    if allow_version_mismatch:
        return project
    if project.unclear:
        raise _which_version(project, source, file_version, action)
    if project.version and project.version != file_version:
        raise NeedsAnswer(
            f"{source.name} is a Spine {file_version} file, but the game project uses {project.version} "
            f"({project.source}), so the game cannot load it. Convert it to {project.version} first, or {action} "
            f"it as {file_version} anyway?",
            [{"answer": f"Convert it to {project.version} first, then {action} the converted file",
              "instead": f"convert_skeleton(project_path={str(source)!r}, to_version={project.version!r})"},
             {"answer": f"{action.capitalize()} it as {file_version} anyway",
              "call_again_with": {"allow_version_mismatch": True}}],
            recommended=f"Convert it to {project.version} first, then {action} the converted file")
    return project


def _which_version(project: ProjectVersion, source: Path, file_version: str, action: str) -> NeedsAnswer:
    """Ask which version an unclear game project uses, before working on a file."""
    candidates = [v for v in (project.candidates or versions.SUPPORTED) if versions.is_supported(v)]
    options = []
    if project.package_version in (None, file_version):
        options.append({"answer": f"{file_version}, like {source.name}: {action} it and save {file_version} as the "
                                  "project's version", "call_again_with": {"save_project_version": True}})
    for v in sorted(set(candidates) - {file_version}, reverse=True):
        save = ", save_project_version=True" if project.package_version in (None, v) else ""
        options.append({"answer": f"{v}: convert {source.name} to {v} first",
                        "instead": f"convert_skeleton(project_path={str(source)!r}, to_version={v!r}{save})"})
    options.append({"answer": f"Not sure: {action} it as {file_version} anyway",
                    "call_again_with": {"allow_version_mismatch": True}})
    recommended = next((o["answer"] for o in options if project.package_version
                        and o["answer"].startswith(project.package_version)), options[0]["answer"])
    return NeedsAnswer(
        f"Which Spine format version does this game project use? {project.problem}. {source.name} is "
        f"{file_version}; the answer can be saved as {_display(project.declaration_file())} so it is not asked "
        "again.", options, recommended=recommended)
