"""Recursive comparison of research processes (lane 6, #3218).

``process`` digests a research process spec, ``freshness`` decides which
opportunities are untouched, ``budget`` accounts an arm's spend in four
units, and ``arms`` is the seam through which a research process runs.
Modules import lazily where they touch Redis; nothing here loads at
package import.
"""
