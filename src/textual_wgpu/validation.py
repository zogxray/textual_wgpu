"""Validators — Textual ``textual.validation`` parity."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Failure:
    validator: Validator | None = None
    value: str = ""
    description: str = ""


@dataclass
class ValidationResult:
    """Result returned by ``Validator.validate``."""

    failures: list[Any] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.failures

    @property
    def failure_descriptions(self) -> list[str]:
        out: list[str] = []
        for f in self.failures:
            if isinstance(f, Failure):
                out.append(f.description)
            else:
                out.append(str(f))
        return out

    @classmethod
    def success(cls) -> ValidationResult:
        return cls([])

    @classmethod
    def failure(
        cls,
        description: str = "",
        validator: Validator | None = None,
        value: str = "",
    ) -> ValidationResult:
        if validator is not None:
            return cls([Failure(validator=validator, value=value, description=description)])
        return cls([description or "Validation failed"])

    @classmethod
    def merge(cls, results: Iterable[ValidationResult]) -> ValidationResult:
        merged: list[Any] = []
        for r in results:
            merged.extend(r.failures)
        return cls(merged)


class Validator(ABC):
    """Base validator class."""

    def __init__(self, failure_description: str | None = None) -> None:
        self.failure_description = failure_description

    @abstractmethod
    def validate(self, value: str) -> ValidationResult:
        """Validate ``value`` and return a ``ValidationResult``."""

    def success(self) -> ValidationResult:
        return ValidationResult.success()

    def failure(self, description: str | None = None, value: str = "") -> ValidationResult:
        return ValidationResult.failure(
            description or self.failure_description or "Validation failed",
            validator=self,
            value=value,
        )


class Number(Validator):
    """Validate floating-point or integer numbers within optional bounds."""

    def __init__(
        self,
        minimum: float | None = None,
        maximum: float | None = None,
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: str) -> ValidationResult:
        try:
            num = float(value)
        except (TypeError, ValueError):
            return self.failure(f"{value!r} is not a number", value)
        if self.minimum is not None and num < self.minimum:
            return self.failure(f"Must be >= {self.minimum}", value)
        if self.maximum is not None and num > self.maximum:
            return self.failure(f"Must be <= {self.maximum}", value)
        return self.success()


class Integer(Number):
    """Validate integer numbers within optional bounds."""

    def validate(self, value: str) -> ValidationResult:
        try:
            num = int(value)
        except (TypeError, ValueError):
            return self.failure(f"{value!r} is not an integer", value)
        if self.minimum is not None and num < self.minimum:
            return self.failure(f"Must be >= {self.minimum}", value)
        if self.maximum is not None and num > self.maximum:
            return self.failure(f"Must be <= {self.maximum}", value)
        return self.success()


class Length(Validator):
    """Validate string length bounds."""

    def __init__(
        self,
        minimum: int | None = None,
        maximum: int | None = None,
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: str) -> ValidationResult:
        n = len(value)
        if self.minimum is not None and n < self.minimum:
            return self.failure(f"Length must be >= {self.minimum}", value)
        if self.maximum is not None and n > self.maximum:
            return self.failure(f"Length must be <= {self.maximum}", value)
        return self.success()


class Regex(Validator):
    """Validate string against a regular expression."""

    def __init__(
        self,
        regex: str | re.Pattern[str],
        flags: int = 0,
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.regex = re.compile(regex, flags) if isinstance(regex, str) else regex

    def validate(self, value: str) -> ValidationResult:
        if self.regex.fullmatch(value) is None:
            return self.failure("Value does not match pattern", value)
        return self.success()


class Function(Validator):
    """Validate using a predicate ``func(value) -> bool``."""

    def __init__(
        self,
        function: Callable[[str], bool],
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.function = function

    def validate(self, value: str) -> ValidationResult:
        if not self.function(value):
            return self.failure(value=value)
        return self.success()


class URL(Regex):
    """Validate basic HTTP/HTTPS URLs."""

    def __init__(self, failure_description: str | None = None) -> None:
        super().__init__(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE, failure_description)
