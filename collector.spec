# -*- mode: python ; coding: utf-8 -*-
"""AI Ops Agent Collector — PyInstaller spec.
Usage: pyinstaller collector.spec --clean
"""

import os
import sys

# Since this spec is evaluated from repo root, use cwd
_REPO_ROOT = os.getcwd()
_SHARED = os.path.join(_REPO_ROOT, 'shared')

a = Analysis(
    ['collector/run.py'],
    pathex=[_REPO_ROOT, _SHARED],
    binaries=[],
    datas=[],
    hiddenimports=[
        'ws_client',
        'connectors',
        'connectors.base',
        'connectors.http',
        'connectors.tcp',
        'connectors.ssh',
        'connectors.local',
        'connectors.prometheus',
        'connectors.k8s',
        'websockets',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest',  'http.server', 'asyncio.test_utils', 'test'],
    noarchive=False,
    module_collection_mode={
        'connectors': 'pyz',
        'connectors.*': 'pyz',
    },
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='aiops-collector',
    debug=False,
    console=True,
)
