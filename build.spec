# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MT5 Trade Notifier."""

import importlib
import os

block_cipher = None

ctk_path = os.path.dirname(importlib.import_module("customtkinter").__file__)

a = Analysis(
    ["run_notifier.py"],
    pathex=["."],
    binaries=[],
    datas=[
        (ctk_path, "customtkinter"),
        ("config.example.json", "."),
    ],
    hiddenimports=[
        "pystray._win32",
        "psutil",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright"],
    noarchive=False,
    optimize=0,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MT5 Trade Notifier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MT5 Trade Notifier",
)
