# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path.cwd()
TOOLCHAIN_BIN = ROOT / "vendor" / "export-tools" / "bin"
MODELS_DIR = ROOT / "vendor" / "models"


def existing_datas(items):
    return [(str(source), target) for source, target in items if Path(source).exists()]


def optional_tool_data(name):
    bundled = TOOLCHAIN_BIN / name
    path = str(bundled) if bundled.exists() else shutil.which(name)
    if not path:
        print(f"warning: {name} not found; run scripts/install_export_tools.py or install it on PATH")
        return []
    return [(path, "bin")]


def optional_tool_binary(name):
    bundled = TOOLCHAIN_BIN / name
    path = str(bundled) if bundled.exists() else shutil.which(name)
    if not path:
        print(f"warning: {name} not found; run scripts/install_export_tools.py or install it on PATH")
        return []
    return [(path, "bin")]


datas = existing_datas(
    [
        (ROOT / "vitamine" / "static", "vitamine/static"),
        (ROOT / "vitamine" / "logo", "vitamine/logo"),
        (ROOT / "vitamine" / "scripts", "vitamine/scripts"),
        (ROOT / "vitamine" / "onepage_tabular", "vitamine/onepage_tabular"),
        (ROOT / "vitamine" / "schema.sql", "vitamine"),
        (ROOT / "data" / "example.vitamine", "data"),
        (ROOT / "data" / "journal_metrics.csv", "data"),
        (MODELS_DIR, "models"),
        (ROOT / "vendor" / "export-tools" / "lib", "lib"),
    ]
) + optional_tool_data("typst") + optional_tool_data("pandoc")

binaries = optional_tool_binary("pdftotext") + optional_tool_binary("llama-server")

a = Analysis(
    ["vitamine/standalone.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "vitamine.app",
        "vitamine.paths",
        "vitamine.i18n",
        "vitamine.scripts.maintain_publications",
        "vitamine.scripts.import_uploaded_cv",
    ] + collect_submodules("docx"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VitaMine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VitaMine",
)
app = BUNDLE(
    coll,
    name="VitaMine.app",
    icon=str(ROOT / "vitamine" / "logo" / "vitamine_icon.icns"),
    bundle_identifier="de.netstim.vitamine",
    info_plist={
        "CFBundleName": "VitaMine",
        "CFBundleDisplayName": "VitaMine",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
    },
)
