#!/usr/bin/env python3
"""Remove Whisplay's own Wi-Fi apps, which Connect WiFi replaces, or put
them back.

    python3 setup/whisplay_wifi.py status  WHISPLAY_DIR
    python3 setup/whisplay_wifi.py remove  WHISPLAY_DIR
    python3 setup/whisplay_wifi.py restore WHISPLAY_DIR

There are two:

- the daemon's built-in "WiFi" app (whisplay-wifi). It is wired into
  daemon/internal_apps/manager.py in code, so it is unhooked there and
  wifi_app.py is deleted;
- the example "WiFi Config" (whisplay-wifi-config), which Whisplay's
  daemon installer seeds from daemon/default_apps/. Its desktop entry and
  that seed are deleted, so re-running Whisplay's installer does not bring
  it back.

manager.py differs between Whisplay versions, so the edit works on its
syntax tree, not on a fixed patch. Every file touched is first copied to
daemon/internal_apps/.connectwifi-backup/, which `restore` puts back. A
removal is checked -- manager.py must compile and must no longer mention
the Wi-Fi app -- and undone on the spot if the check fails. The daemon
reads manager.py only when it starts: restart whisplay-daemon afterwards.
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
WIFI_APP = Path("daemon/internal_apps/wifi_app.py")
EXAMPLE_SEED = Path("daemon/default_apps/whisplay-wifi-config.json")
EXAMPLE_ENTRY = Path.home() / ".whisplay-daemon" / "app" / "whisplay-wifi-config.json"
ENTRY_BACKUP_NAME = "desktop-entry-whisplay-wifi-config.json"   # the seed has the same name

TEXT_INPUT = (
    "        # Wi-Fi setup, the one built-in app that took typed input, is the\n"
    "        # standalone Connect WiFi app now (ConnectWifi, a separate project).\n"
    '        return any(getattr(app, "text_input_active", lambda: False)() for app in self._apps.values())\n'
)


class Refused(Exception):
    pass


def wifi_hooked(source: str) -> bool:
    return "wifi_app" in source or "self.wifi" in source


def _line_spans(source: str) -> list[tuple[int, int]]:
    """1-based inclusive line spans of the statements that exist only for
    the Wi-Fi app: its import, `self.wifi = ...`, `self.wifi.set_error(...)`."""
    spans = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "wifi_app":
            spans.append((node.lineno, node.end_lineno))
        elif isinstance(node, (ast.Assign, ast.Expr)):
            text = ast.get_source_segment(source, node) or ""
            if re.match(r"self\.wifi\s*=", text) or re.match(r"self\.wifi\.set_error\(", text):
                spans.append((node.lineno, node.end_lineno))
    return spans


def unhook(source: str) -> str:
    """manager.py without the Wi-Fi app, whatever the Whisplay version."""
    drop = set()
    for first, last in _line_spans(source):
        drop.update(range(first, last + 1))
    lines = source.splitlines(keepends=True)
    kept = [line for number, line in enumerate(lines, 1) if number not in drop]
    text = "".join(kept)

    # Entries in the app table and the desktop list, whether each sits on a
    # line of its own or they share one.
    text = re.sub(r"^[ \t]*WIFI_APP_ID:\s*self\.wifi,[ \t]*\n", "", text, flags=re.M)
    text = re.sub(r"WIFI_APP_ID:\s*self\.wifi,\s*", "", text)
    text = re.sub(r"^[ \t]*self\.wifi\.builtin_app\(\),[ \t]*\n", "", text, flags=re.M)
    text = re.sub(r"self\.wifi\.builtin_app\(\),\s*", "", text)

    # The daemon asks this to route keys to a text field; the Wi-Fi app was
    # the only one with such a field.
    text = re.sub(r"^[ \t]*return self\.wifi\.text_input_active\(\)[ \t]*\n", TEXT_INPUT, text, flags=re.M)
    return text


def leftovers(source: str) -> list[str]:
    """Anything in manager.py that still reaches the Wi-Fi app: the
    `.wifi` attribute, its names, its module."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "wifi":
            found.append(node)
        elif isinstance(node, ast.Name) and node.id in ("WIFI_APP_ID", "WifiInternalApp"):
            found.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module == "wifi_app":
            found.append(node)
    return [source.splitlines()[node.lineno - 1].strip() for node in found]


def check(source: str, path: Path):
    """The unhooked manager.py must compile and must not reach the app."""
    left = leftovers(source)
    if left:
        raise Refused(f"this Whisplay version still refers to the Wi-Fi app after editing: {left}")
    py_compile.compile(str(path), doraise=True)


def status(root: Path) -> str:
    manager = root / MANAGER
    if not manager.is_file():
        raise Refused(f"{manager} not found: is {root} a Whisplay checkout?")
    built_in = "present" if wifi_hooked(manager.read_text()) else "removed"
    example = "present" if (root / EXAMPLE_SEED).exists() or EXAMPLE_ENTRY.exists() else "removed"
    return f"built-in WiFi: {built_in}; example WiFi Config: {example}"


def _backup(root: Path, relative: Path):
    source = root / relative
    if source.exists():
        target = root / BACKUP / relative.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():            # never overwrite the true original
            shutil.copy2(source, target)


def remove(root: Path) -> list[str]:
    done = []
    manager = root / MANAGER
    original = manager.read_text()
    if wifi_hooked(original):
        _backup(root, MANAGER)
        _backup(root, WIFI_APP)
        manager.write_text(unhook(original))
        try:
            check(manager.read_text(), manager)
        except Exception:
            manager.write_text(original)
            raise
        if (root / WIFI_APP).exists():
            (root / WIFI_APP).unlink()
        done.append("unhooked the built-in WiFi app from daemon/internal_apps/manager.py, deleted wifi_app.py")
    if (root / EXAMPLE_SEED).exists():
        _backup(root, EXAMPLE_SEED)
        (root / EXAMPLE_SEED).unlink()
        done.append(f"deleted the example's seed {EXAMPLE_SEED}")
    if EXAMPLE_ENTRY.exists():
        entry_backup = root / BACKUP / ENTRY_BACKUP_NAME
        entry_backup.parent.mkdir(parents=True, exist_ok=True)
        if not entry_backup.exists():
            shutil.copy2(EXAMPLE_ENTRY, entry_backup)
        EXAMPLE_ENTRY.unlink()
        done.append(f"deleted the example's desktop entry {EXAMPLE_ENTRY}")
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
    if len(argv) != 3 or argv[1] not in ("status", "remove", "restore"):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    root = Path(argv[2]).expanduser().resolve()
    try:
        if argv[1] == "status":
            print(status(root))
        else:
            if not os.access(root / MANAGER, os.W_OK):
                raise Refused(f"cannot write {root / MANAGER}")
            done = (remove if argv[1] == "remove" else restore)(root)
            print("\n".join(done) if done else "nothing to do")
    except (Refused, OSError, SyntaxError, py_compile.PyCompileError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
