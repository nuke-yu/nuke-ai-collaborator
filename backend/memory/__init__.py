"""Independent Memory & Learning bounded context.

Callers should depend on :mod:`memory.contracts` and :mod:`memory.ports`, never
on persistence or algorithm adapters directly.
"""

from memory.module import MemoryModule

__all__ = ["MemoryModule"]
