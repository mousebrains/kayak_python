"""Shim — the build command lives at kayak.web.build.deploy now."""

from kayak.web.build.deploy import addArgs, build

__all__ = ["addArgs", "build"]
