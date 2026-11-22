"""Regression test: the core MCP server must build WITHOUT the audio extras.

numpy / librosa / sounddevice / soundfile are optional (the `audio` extra). A
user who installs only the base package must still get a working server for
transport / mixer / channels / plugins / piano roll. This guards against anyone
re-introducing a module-level `import numpy` into the import path of build_app.

Run in a subprocess so the check is not polluted by other tests that legitimately
import numpy in-process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_build_app_does_not_import_audio_deps():
    code = textwrap.dedent(
        """
        import os
        import sys
        from anyio import run
        from fl_studio_mcp.server import build_app
        os.environ.pop('FLMCP_TOOLSETS', None)
        os.environ.pop('FLMCP_DISABLE_TOOLS', None)
        app = build_app()
        heavy = [m for m in ('numpy', 'librosa', 'sounddevice', 'soundfile', 'scipy')
                 if m in sys.modules]
        assert not heavy, 'heavy deps imported at build time: %r' % heavy
        n = len(run(app.list_tools))
        assert n >= 150, 'expected >= 150 tools, got %d' % n
        print('OK', n)
        """
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert r.stdout.startswith("OK")
