"""Test bootstrap.

``custom_components/tplink_easysmart/__init__.py`` imports Home Assistant, which
is not needed to test page parsing. This loads the HA-free modules under a
stand-in package name so their relative imports resolve without executing the
integration's ``__init__``.

Result: ``python3 -m pytest tests/`` needs only ``pytest``.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

_COMPONENT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "tplink_easysmart"
)

PACKAGE = "tplink_easysmart_parsing"


def _bootstrap() -> None:
    if PACKAGE in sys.modules:
        return

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(_COMPONENT)]
    sys.modules[PACKAGE] = package

    # Order matters: parser, options and rates import const and models.
    for name in ("const", "models", "parser", "options", "rates"):
        path = _COMPONENT / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PACKAGE}.{name}"] = module
        spec.loader.exec_module(module)
        setattr(package, name, module)


_bootstrap()
