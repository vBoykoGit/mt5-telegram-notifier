"""Detect running MetaTrader 5 terminals and install EA files."""

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

EA_EXPERT_REL = "MQL5/Experts/AO_Pattern_Bot.ex5"
EA_INDICATOR_REL = "MQL5/Indicators/AO_Pattern_Watcher.ex5"

_COPY_MAP: list[tuple[str, str]] = [
    ("Experts/AO_Pattern_Bot.mq5", "MQL5/Experts/AO_Pattern_Bot.mq5"),
    ("Experts/AO_Pattern_Bot.ex5", "MQL5/Experts/AO_Pattern_Bot.ex5"),
    ("Indicators/AO_Pattern_Watcher.mq5", "MQL5/Indicators/AO_Pattern_Watcher.mq5"),
    ("Indicators/AO_Pattern_Watcher.ex5", "MQL5/Indicators/AO_Pattern_Watcher.ex5"),
]

_COPY_DIR_MAP: list[tuple[str, str]] = [
    ("Experts/AO_Bot", "MQL5/Experts/AO_Bot"),
]


@dataclass
class TerminalInfo:
    pid: int
    hash: str
    data_dir: str
    install_path: str
    name: str
    ea_installed: bool = False
    indicator_installed: bool = False


def _get_terminals_base() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"


def _build_origin_map() -> dict[str, Path]:
    """Map normalised install_path -> data_dir for all known terminals."""
    base = _get_terminals_base()
    if not base.is_dir():
        return {}
    result: dict[str, Path] = {}
    for sub in base.iterdir():
        if not sub.is_dir() or len(sub.name) != 32:
            continue
        origin = sub / "origin.txt"
        if not origin.is_file():
            continue
        try:
            raw = origin.read_bytes()
            if raw[:2] == b"\xff\xfe":
                install_path = raw.decode("utf-16-le").strip().lstrip("\ufeff")
            elif raw[:2] == b"\xfe\xff":
                install_path = raw.decode("utf-16-be").strip().lstrip("\ufeff")
            else:
                install_path = raw.decode("utf-8").strip().lstrip("\ufeff")
        except Exception:
            continue
        result[os.path.normcase(install_path)] = sub
    return result


def discover_terminals() -> list[TerminalInfo]:
    """Find running MT5 terminals and check EA/indicator presence.

    Uses psutil to locate terminal64.exe processes, then maps each to its
    data directory via origin.txt files under %APPDATA%/MetaQuotes/Terminal/.
    """
    try:
        import psutil
    except ImportError:
        raise ImportError("psutil не установлен. Выполните: pip install psutil")

    origin_map = _build_origin_map()
    if not origin_map:
        log.info("No terminal data directories found under %s", _get_terminals_base())
        return []

    terminals: list[TerminalInfo] = []
    seen_pids: set[int] = set()

    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pname = (proc.info.get("name") or "").lower()
            if pname != "terminal64.exe":
                continue
            pid = proc.info["pid"]
            if pid in seen_pids:
                continue
            seen_pids.add(pid)

            exe_path = proc.info.get("exe") or ""
            if not exe_path:
                continue
            install_dir = os.path.normcase(str(Path(exe_path).parent))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

        data_dir_path = origin_map.get(install_dir)
        if data_dir_path is None:
            log.debug("No origin.txt mapping for %s", install_dir)
            continue

        data_dir_str = str(data_dir_path)
        ea_ok = (data_dir_path / EA_EXPERT_REL).is_file()
        ind_ok = (data_dir_path / EA_INDICATOR_REL).is_file()

        raw_name = Path(exe_path).parent.name
        terminals.append(TerminalInfo(
            pid=pid,
            hash=data_dir_path.name,
            data_dir=data_dir_str,
            install_path=str(Path(exe_path).parent),
            name=raw_name,
            ea_installed=ea_ok,
            indicator_installed=ind_ok,
        ))

    terminals.sort(key=lambda t: t.name.lower())
    return terminals


def install_ea(data_dir: str, source_dir: str) -> tuple[bool, str]:
    """Copy EA and indicator files from source_dir into the terminal's data_dir.

    source_dir should point to the trading-bot-mt5 repo root containing
    Experts/ and Indicators/ subdirectories.

    Returns (True, "") on success, (False, "error") on failure.
    """
    src = Path(source_dir)
    dst = Path(data_dir)

    if not src.is_dir():
        return False, f"Исходная папка не найдена: {source_dir}"
    if not dst.is_dir():
        return False, f"Папка данных терминала не найдена: {data_dir}"

    errors: list[str] = []

    for src_rel, dst_rel in _COPY_MAP:
        src_file = src / src_rel
        dst_file = dst / dst_rel
        if not src_file.is_file():
            log.warning("Source file missing, skipping: %s", src_file)
            continue
        try:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_file), str(dst_file))
        except Exception as exc:
            errors.append(f"{src_rel}: {exc}")

    for src_rel, dst_rel in _COPY_DIR_MAP:
        src_subdir = src / src_rel
        dst_subdir = dst / dst_rel
        if not src_subdir.is_dir():
            log.warning("Source dir missing, skipping: %s", src_subdir)
            continue
        dst_subdir.mkdir(parents=True, exist_ok=True)
        for f in src_subdir.iterdir():
            if f.is_file():
                try:
                    shutil.copy2(str(f), str(dst_subdir / f.name))
                except Exception as exc:
                    errors.append(f"{src_rel}/{f.name}: {exc}")

    if errors:
        return False, "; ".join(errors)
    return True, ""
