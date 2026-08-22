"""
fpl/history/ — Phase G bitemporal archive (docs/superpowers/specs/
2026-08-22-history-layer-design.md).

Strict write/read split: archive.py writes, query.py only reads. Nothing
in this package imports from fpl/project/ or fpl/decide/ — it archives
their already-written outputs and never recomputes them.
"""
