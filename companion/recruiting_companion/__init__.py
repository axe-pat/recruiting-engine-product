"""Local-first companion backend for the Recruiting Engine product surface."""

from .config import Settings
from .service import CompanionService

__all__ = ["CompanionService", "Settings"]
__version__ = "0.2.0"
