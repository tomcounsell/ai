"""
Sandbox implementations for code execution.

This package provides different sandbox backends with varying levels of isolation:

1. RestrictedPythonSandbox (PROOF OF CONCEPT ONLY)
   - Pure Python sandboxing
   - NOT secure for production
   - Useful for development and testing
   - Fast, no external dependencies

2. E2BSandbox (RECOMMENDED FOR PRODUCTION)
   - Firecracker microVM isolation
   - Enterprise-grade security
   - Requires E2B API key
   - <200ms startup time

3. GVisorSandbox (SELF-HOSTED PRODUCTION)
   - gVisor container isolation
   - Requires Docker with runsc runtime
   - Good performance, strong isolation

Design Pattern:
    All sandboxes implement the BaseSandbox interface, enabling:
    - Easy switching between implementations
    - Testing with mock sandboxes
    - Progressive security enhancement (start with RestrictedPython, move to E2B)

Selection Strategy:
    - Development: RestrictedPythonSandbox
    - Production (< 1000 exec/day): E2BSandbox
    - Production (> 1000 exec/day): GVisorSandbox or Firecracker
"""

from .base import BaseSandbox, SandboxConfig, SandboxResult
from .restricted_python import RestrictedPythonSandbox

__all__ = [
    "BaseSandbox",
    "SandboxConfig",
    "SandboxResult",
    "RestrictedPythonSandbox",
]
