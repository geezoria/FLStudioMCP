"""Find installed plugins in FL Studio's Plugin database.

The Plugin Manager keeps one .fst preset per plugin under
`<user data>/FL Studio/Presets/Plugin database`: FL's curated categories in
`Effects/` and `Generators/`, and every scanned plugin by format under
`Installed/Effects/<format>/` and `Installed/Generators/<format>/`. Loading one
of those presets loads the plugin, which is how `mixer_load_effect` adds effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

KINDS = {"Effects": "effect", "Generators": "generator"}


@dataclass(frozen=True)
class PluginEntry:
    name: str
    kind: str  # "effect" or "generator"
    category: str  # curated category or plugin format ("VST3", "Fruity"...), "" if none
    path: Path

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind, "category": self.category, "path": str(self.path)}


def _display_name(stem: str) -> str:
    # The Plugin Manager suffixes some 64-bit builds: "Fruity Reeverb 2_x64", "Foo (x64)".
    return re.sub(r"[ _]?\(?x64\)?$", "", stem, flags=re.IGNORECASE).strip()


def list_plugins(root: Path | None = None) -> list[PluginEntry]:
    """Every plugin in the database, one entry per (kind, name)."""
    root = root or paths.plugin_database_dir()
    entries: dict[tuple[str, str], PluginEntry] = {}
    for folder, kind in KINDS.items():
        # Curated categories first: their names are FL's own. The Installed tree
        # adds every scanned third-party plugin.
        for base in (root / folder, root / "Installed" / folder):
            if not base.is_dir():
                continue
            for fst in sorted(base.rglob("*.fst")):
                rel = fst.relative_to(base).parts
                entry = PluginEntry(_display_name(fst.stem), kind, rel[0] if len(rel) > 1 else "", fst)
                entries.setdefault((kind, entry.name.lower()), entry)
    return sorted(entries.values(), key=lambda e: (e.kind, e.name.lower()))


def search(query: str = "", kind: str | None = None, root: Path | None = None) -> list[PluginEntry]:
    """Plugins whose name or category contains every word of `query` (case-insensitive)."""
    words = query.lower().split()
    found = []
    for entry in list_plugins(root):
        if kind and entry.kind != kind:
            continue
        haystack = f"{entry.name} {entry.category}".lower()
        if all(w in haystack for w in words):
            found.append(entry)
    return found


def resolve(name: str, kind: str = "effect", root: Path | None = None) -> PluginEntry:
    """The one plugin `name` refers to: an exact name, else a unique partial match."""
    if name.lower().endswith(".fst"):
        path = Path(name).expanduser()
        if not path.is_file():
            raise ValueError(f"preset not found: {path}")
        return PluginEntry(_display_name(path.stem), kind, "", path)
    candidates = search(name, kind, root)
    exact = [e for e in candidates if e.name.lower() == name.strip().lower()]
    if exact:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"no {kind} named {name!r} in FL's Plugin database "
                         f"({root or paths.plugin_database_dir()}). Use plugin_search to list them; "
                         "FL's Plugin Manager adds third-party plugins after a scan.")
    names = ", ".join(e.name for e in candidates[:12])
    raise ValueError(f"{name!r} matches {len(candidates)} {kind}s: {names}. Use the exact name.")
