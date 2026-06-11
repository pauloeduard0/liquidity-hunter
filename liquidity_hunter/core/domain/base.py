"""Shared base class for all domain entities."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base class for immutable, strictly-validated domain entities.

    - `frozen=True`: domain entities are value objects/observations and
      must not be mutated after construction.
    - `extra="forbid"`: catches typos and schema drift early.
    - `validate_assignment=True`: keeps invariants intact even though
      assignment is disallowed by `frozen`, for safety if subclasses
      relax immutability.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )
