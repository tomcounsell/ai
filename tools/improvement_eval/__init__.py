"""Frozen-input evaluation harness for the improvement controller (#3216).

Runs a paired, blinded comparison of two retrieval arms over one frozen
memory corpus, each arm isolated on its own private Redis process. Every
claim the harness produces is checkable from the frozen artifacts alone.
"""

from __future__ import annotations
