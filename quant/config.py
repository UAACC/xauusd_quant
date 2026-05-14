"""Environment-specific configuration resolution.

Per-machine paths and credentials live here so scripts stay portable across
the user's machines (and don't leak local paths into git diffs every time
someone switches box).

Resolution priority for the demo MT5 terminal:
    1. ``XAUUSD_DEMO_MT5_PATH`` environment variable.
    2. First existing path in ``_KNOWN_DEMO_MT5_PATHS``.
    3. Raise ``FileNotFoundError``.

The ``trade_mode == 2`` REAL-account abort guard in
``scripts.mt5_connect.init_mt5`` is the secondary defense in case auto-detect
picks an existing-but-wrong terminal. See [[mt5-dual-install-discipline]].
"""

from __future__ import annotations

import os
from pathlib import Path


_ENV_VAR_DEMO_MT5 = "XAUUSD_DEMO_MT5_PATH"

# Known-good demo MT5 install paths across the user's machines, in
# preference order. To support a new machine, prepend its path here OR
# set the env var; do not delete entries — the user switches between boxes.
_KNOWN_DEMO_MT5_PATHS: tuple[str, ...] = (
    r"F:\demo-mt5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
)


# On this machine (Administrator's), C:\Program Files\MetaTrader 5\... is the
# user's LIVE terminal binary -- but it supports multiple accounts (demo + live)
# and currently shows demo. The "live" classification depends on which account
# is logged in inside the terminal, not the binary path. See [[reference-python-env]]
# memory entry for the project-discretionary-analyst project notes.
_ENV_VAR_LIVE_MT5 = "XAUUSD_LIVE_MT5_PATH"
_KNOWN_LIVE_MT5_PATHS: tuple[str, ...] = (
    r"C:\Program Files\MetaTrader 5\terminal64.exe",  # user-confirmed 2026-05-14
)


def get_demo_mt5_path() -> str:
    """Return the demo MT5 ``terminal64.exe`` path for the current machine.

    Raises ``FileNotFoundError`` if nothing resolves, with a message that
    tells the user how to fix it.
    """
    env_path = os.environ.get(_ENV_VAR_DEMO_MT5)
    if env_path:
        if not Path(env_path).is_file():
            raise FileNotFoundError(
                f"{_ENV_VAR_DEMO_MT5}={env_path!r} is set but the file does not exist."
            )
        return env_path

    for candidate in _KNOWN_DEMO_MT5_PATHS:
        if Path(candidate).is_file():
            return candidate

    raise FileNotFoundError(
        f"No demo MT5 terminal64.exe found. Set {_ENV_VAR_DEMO_MT5}=<absolute path> "
        f"or add the path to quant.config._KNOWN_DEMO_MT5_PATHS. "
        f"Tried (in order): {list(_KNOWN_DEMO_MT5_PATHS)}"
    )


def get_live_mt5_path() -> str:
    """Return the LIVE MT5 ``terminal64.exe`` path. May be the same binary as
    demo if the user runs both accounts inside one terminal — the active
    account inside the terminal determines what Python sees on connect.
    """
    env_path = os.environ.get(_ENV_VAR_LIVE_MT5)
    if env_path:
        if not Path(env_path).is_file():
            raise FileNotFoundError(
                f"{_ENV_VAR_LIVE_MT5}={env_path!r} is set but the file does not exist."
            )
        return env_path

    for candidate in _KNOWN_LIVE_MT5_PATHS:
        if Path(candidate).is_file():
            return candidate

    raise FileNotFoundError(
        f"No live MT5 terminal64.exe found. Set {_ENV_VAR_LIVE_MT5}=<absolute path> "
        f"or add the path to quant.config._KNOWN_LIVE_MT5_PATHS. "
        f"Tried (in order): {list(_KNOWN_LIVE_MT5_PATHS)}"
    )
