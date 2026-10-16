"""Domain errors shared by the ledger and its adapters."""


class Conflict(ValueError):
    """A stale revision or incompatible state needs a fresh user decision."""
