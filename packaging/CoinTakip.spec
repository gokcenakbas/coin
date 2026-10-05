# -*- mode: python ; coding: utf-8 -*-
# Mac uygulaması paketi. Doğrudan değil, packaging/build_mac.sh üzerinden çalıştırın.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / "packaging" / "CoinTakip.icns"
VERSION = "1.0.0"

a = Analysis(
    [str(ROOT / "packaging" / "app_entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "cointracker" / "web"), "cointracker/web")],
    hiddenimports=["cointracker.app"],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Coin Takip",
    console=False,
    argv_emulation=False,
    target_arch=os.environ.get("TARGET_ARCH") or None,
    codesign_identity=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Coin Takip", upx=False)
app = BUNDLE(
    coll,
    name="Coin Takip.app",
    icon=str(ICON) if ICON.exists() else None,
    bundle_identifier="com.gokcenakbas.cointakip",
    version=VERSION,
    info_plist={
        "CFBundleName": "Coin Takip",
        "CFBundleDisplayName": "Coin Takip",
        "CFBundleShortVersionString": VERSION,
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # Panel, uygulamanın içinde 127.0.0.1 üzerinden sunulur
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
