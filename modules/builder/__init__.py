"""
Module Builder - Autonomous Module Generation

The ModuleBuilderAgent takes requirements (natural language or structured)
and generates complete, tested, documented modules that conform to the
module standard framework.
"""

from modules.builder.agent import ModuleBuilderAgent
from modules.builder.templates import ModuleTemplate

__all__ = [
    "ModuleBuilderAgent",
    "ModuleTemplate",
]
