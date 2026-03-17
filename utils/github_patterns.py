"""Shared regex patterns for extracting data from GitHub URLs."""

import re

ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)")
PR_NUMBER_RE = re.compile(r"/pull/(\d+)")
