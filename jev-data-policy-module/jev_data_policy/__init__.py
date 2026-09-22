"""Data classification for outbound data. Stdlib only."""
from .policy import (ExternalLeakError, NotApprovedError, PolicyError,
                     approved_targets, assert_service, assert_target,
                     guarded_call, load, rank, require_targets)

__all__ = ["PolicyError", "NotApprovedError", "ExternalLeakError", "load",
           "rank", "approved_targets", "assert_target", "require_targets",
           "assert_service", "guarded_call"]
