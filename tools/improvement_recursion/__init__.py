"""Recursive comparison of research processes (lane 6, #3218).

``process`` digests a research process spec, ``freshness`` decides which
opportunities are untouched, ``budget`` accounts an arm's spend in four
units, ``arms`` is the seam through which a research process runs,
``compare`` freezes and runs a two-arm comparison and writes its one
evaluation, and ``report`` answers the three claim levels. Modules import
lazily where they touch Redis; nothing here loads at package import.
"""
