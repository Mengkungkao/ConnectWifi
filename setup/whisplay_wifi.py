#!/usr/bin/env python3
"""Take Whisplay's own Wi-Fi entries off the HAT desktop, where Connect
WiFi takes their place, or put them back.

    python3 setup/whisplay_wifi.py status  WHISPLAY_DIR
    python3 setup/whisplay_wifi.py hide    WHISPLAY_DIR
    python3 setup/whisplay_wifi.py restore WHISPLAY_DIR

Only the menu entries go; Whisplay's code stays as PiSugar wrote it.

- The daemon's built-in "WiFi" (whisplay-wifi) is listed from code, in
  InternalAppManager.builtin_apps() in daemon/internal_apps/manager.py.
  That one list entry is removed. The app itself -- wifi_app.py, and its
  hooks into the daemon's keyboard and error handling -- stays; it is just
  never offered, so it never runs.
- The example "WiFi Config" (whisplay-wifi-config) is listed by a desktop
  entry that Whisplay's daemon installer seeds from daemon/default_apps/.
  The entry and the seed are removed, so re-running that installer does not
  list it again; example/wifi_config_app.py stays.

manager.py differs between Whisplay versions, so the edit is made and
checked on its syntax tree: the result must be the original minus that one
entry, and nothing else. Otherwise the original is put back and nothing
changes. Every file touched is first copied to
daemon/internal_apps/.connectwifi-backup/, which `restore` puts back. The
daemon reads manager.py only when it starts: restart whisplay-daemon
afterwards.
"""

from __future__ import annotations

import ast
import os
import py_compile
import re
import shutil
import sys
from pathlib import Path

BACKUP = Path("daemon/internal_apps/.connectwifi-backup")
MANAGER = Path("daemon/internal_apps/manager.py")
WIFI_APP = Path("daemon/internal_apps/wifi_app.py")   # only in backups from older versions of this tool
EXAMPLE_SEED = Path("daemon/default_apps/whisplay-wifi-config.json")
EXAMPLE_ENTRY = Path.home() / ".whisplay-daemon" / "app" / "whisplay-wifi-config.json"
ENTRY_BACKUP_NAME = "desktop-entry-whisplay-wifi-config.json"   # the seed has the same name


class Refused(Exception):
    pass


def _builtin_apps(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "builtin_apps":
            return node
    return None


def _is_wifi_entry(node: ast.AST) -> bool:
    """`self.wifi.builtin_app()`"""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "builtin_app" and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "wifi")


def _listed(node: ast.FunctionDef) -> list[ast.AST]:
    """The entries of the list builtin_apps() returns."""
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and isinstance(child.value, ast.List):
            return child.value.elts
    return []


def _mentions_wifi(function: ast.FunctionDef | None) -> bool:
    """Any `self.wifi.builtin_app()` in builtin_apps(), in whatever form --
    a plain list entry, or inside something a later version might write."""
    return function is not None and any(_is_wifi_entry(node) for node in ast.walk(function))


def wifi_listed(source: str) -> bool:
    return _mentions_wifi(_builtin_apps(ast.parse(source)))


def unlist(source: str) -> str:
    """manager.py with `self.wifi.builtin_app()` gone from builtin_apps(),
    whether each entry sits on a line of its own or they share one."""
    function = _builtin_apps(ast.parse(source))
    if function is None:
        raise Refused("manager.py has no builtin_apps(); this Whisplay version is unfamiliar")
    lines = source.splitlines(keepends=True)
    before = "".join(lines[:function.lineno - 1])
    body = "".join(lines[function.lineno - 1:function.end_lineno])
    after = "".join(lines[function.end_lineno:])
    entry = r"self\.wifi\.builtin_app\(\)"
    body = re.sub(rf"^[ \t]*{entry},?[ \t]*\n", "", body, count=1, flags=re.M)     # own line
    body = re.sub(rf"{entry},\s*", "", body, count=1)                                  # inline, not last
    body = re.sub(rf",\s*{entry}(?=\s*\])", "", body, count=1)                        # inline, last
    return before + body + after


def check(original: str, edited: str, path: Path):
    """The edit must be the original minus the Wi-Fi entry, and nothing else."""
    old_tree, new_tree = ast.parse(original), ast.parse(edited)
    old_function, new_function = _builtin_apps(old_tree), _builtin_apps(new_tree)
    if old_function is None or new_function is None:
        raise Refused("builtin_apps() did not survive the edit")
    if _mentions_wifi(new_function):
        raise Refused("this Whisplay version lists WiFi in a way this tool does not know; left as it was")
    expected = [ast.dump(entry) for entry in _listed(old_function) if not _is_wifi_entry(entry)]
    if [ast.dump(entry) for entry in _listed(new_function)] != expected:
        raise Refused("the desktop list did not come out as the original minus WiFi")
    # Everything outside builtin_apps() must be untouched.
    for tree, function in ((old_tree, old_function), (new_tree, new_function)):
        function.body = []
    if ast.dump(old_tree) != ast.dump(new_tree):
        raise Refused("the edit reached beyond the desktop list")
    py_compile.compile(str(path), doraise=True)


def status(root: Path) -> str:
    manager = root / MANAGER
    if not manager.is_file():
        raise Refused(f"{manager} not found: is {root} a Whisplay checkout?")
    built_in = "listed" if wifi_listed(manager.read_text()) else "hidden"
    example = "listed" if (root / EXAMPLE_SEED).exists() or EXAMPLE_ENTRY.exists() else "hidden"
    return f"built-in WiFi: {built_in}; example WiFi Config: {example}"


def _backup(root: Path, relative: Path):
    source = root / relative
    if source.exists():
        target = root / BACKUP / relative.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():            # never overwrite the true original
            shutil.copy2(source, target)


def hide(root: Path) -> list[str]:
    done = []
    manager = root / MANAGER
    original = manager.read_text()
    if wifi_listed(original):
        _backup(root, MANAGER)
        edited = unlist(original)
        manager.write_text(edited)
        try:
            check(original, edited, manager)
        except Exception:
            manager.write_text(original)
            raise
        done.append("took the built-in WiFi off the desktop list in daemon/internal_apps/manager.py "
                    "(its code stays)")
    if (root / EXAMPLE_SEED).exists():
        _backup(root, EXAMPLE_SEED)
        (root / EXAMPLE_SEED).unlink()
        done.append(f"removed the example's seed {EXAMPLE_SEED}")
    if EXAMPLE_ENTRY.exists():
        entry_backup = root / BACKUP / ENTRY_BACKUP_NAME
        entry_backup.parent.mkdir(parents=True, exist_ok=True)
        if not entry_backup.exists():
            shutil.copy2(EXAMPLE_ENTRY, entry_backup)
        EXAMPLE_ENTRY.unlink()
        done.append(f"removed the example's desktop entry {EXAMPLE_ENTRY}")
    return done


def restore(root: Path) -> list[str]:
    backup = root / BACKUP
    if not backup.is_dir():
        return []
    done = []
    for relative in (MANAGER, WIFI_APP, EXAMPLE_SEED):
        saved = backup / relative.name
        if saved.exists():
            shutil.copy2(saved, root / relative)
            done.append(f"restored {relative}")
    entry_backup = backup / ENTRY_BACKUP_NAME
    if entry_backup.exists():
        EXAMPLE_ENTRY.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry_backup, EXAMPLE_ENTRY)
        done.append(f"restored the desktop entry {EXAMPLE_ENTRY}")
    shutil.rmtree(backup)
    return done


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("status", "hide", "restore"):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    root = Path(argv[2]).expanduser().resolve()
    try:
        if argv[1] == "status":
            print(status(root))
        else:
            if not os.access(root / MANAGER, os.W_OK):
                raise Refused(f"cannot write {root / MANAGER}")
            done = (hide if argv[1] == "hide" else restore)(root)
            print("\n".join(done) if done else "nothing to do")
    except (Refused, OSError, SyntaxError, py_compile.PyCompileError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
