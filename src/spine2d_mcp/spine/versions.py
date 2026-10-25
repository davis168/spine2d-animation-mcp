"""The Spine format versions this package reads and writes.

A skeleton's format version is the major.minor of its `skeleton.spine`
header (4.2, 4.3). A Spine runtime loads only data of its own format version
and never checks it, so each file is handled in the version it is written in.

Only stable releases are listed. A newer format version (and its editor and
runtime releases) is added here deliberately, once Esoteric Software
publishes it as stable, together with the checker and converter support.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FormatVersion:
    version: str  # "4.3"
    editor: str  # the editor release the tools are tested with
    runtime: str  # spine-core / spine-player release for the runtime check and preview
    constraint_kinds: tuple[str, ...]


FORMATS: dict[str, FormatVersion] = {
    "4.2": FormatVersion("4.2", editor="4.2.43", runtime="4.2.120",
                         constraint_kinds=("ik", "transform", "path", "physics")),
    "4.3": FormatVersion("4.3", editor="4.3.26", runtime="4.3.13",
                         constraint_kinds=("ik", "transform", "path", "physics", "slider")),
}
SUPPORTED = tuple(FORMATS)  # oldest first
NEWEST = SUPPORTED[-1]

_VERSION = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.\d+)?(?:[-+.][0-9A-Za-z.-]*)?\s*$")


class UnsupportedVersion(ValueError):
    """A format version outside SUPPORTED."""


def major_minor(text) -> str | None:
    """"4.3.26" -> "4.3"; None if `text` is not a version."""
    m = _VERSION.match(text) if isinstance(text, str) else None
    return f"{m.group(1)}.{m.group(2)}" if m else None


def of(doc: dict) -> str | None:
    """The format version a skeleton document is written in, from its header."""
    header = doc.get("skeleton")
    return major_minor(header.get("spine")) if isinstance(header, dict) else None


def is_supported(version: str | None) -> bool:
    return version in FORMATS


def supported_text() -> str:
    return ", ".join(SUPPORTED)


def get(version: str | None) -> FormatVersion:
    """The FormatVersion for a supported version string ("4.3" or "4.3.26")."""
    key = major_minor(version) if version else None
    if key not in FORMATS:
        raise UnsupportedVersion(
            f"format version {version!r} is not supported (supported: {supported_text()})")
    return FORMATS[key]


def layout(doc: dict) -> str:
    """Which reader's layout to read the document with: its own format version
    when supported, else the closest guess (a 4.3-style `constraints` array
    reads as 4.3, anything else as 4.2) so inspection can still make a best effort."""
    version = of(doc)
    if version in FORMATS:
        return version
    return "4.3" if "constraints" in doc else "4.2"
