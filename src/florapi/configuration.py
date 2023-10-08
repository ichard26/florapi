# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
from datetime import timedelta
from typing import Any, Callable, Final, TypeVar

T = TypeVar("T")
_MISSING: Final = object()


def TimeDelta(value: str) -> timedelta:
    kwargs = {}
    for argument_string in value.replace(" ", "").split(","):
        unit, value = argument_string.split("=")
        kwargs[unit] = int(value)
    return timedelta(**kwargs)


class Options:
    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix + "_" if prefix else ""
        self.errors = []

    def __call__(self, name: str, type: Callable[[Any], T], default: Any = _MISSING) -> T:
        """Read a value from the environment, after conversion.

        The environment variable read is the option name uppercased with dashes
        replaced with underscores with the configured prefix. Example:

            use-tls -> TMC_USE_TLS

        If the envvar is missing, then the default is used. If a default is not
        specified, the missing option is tracked and can be reported using the
        report_errors() method.
        """
        name = self.prefix + name.replace("-", "_").upper()
        raw_value = os.getenv(name, default)
        if raw_value is _MISSING:
            self.errors.append(f"Missing environment variable: {name}")
            return _MISSING  # type: ignore

        try:
            return type(raw_value)
        except (TypeError, ValueError) as error:
            context = f"|\n╰─> {error.__class__.__name__}: {error}"
            self.errors.append(f"Invalid environment variable: {name}\n{context}")
            return _MISSING  # type: ignore

    def report_errors(self) -> None:
        if self.errors:
            errors = "\n".join(self.errors)
            raise RuntimeError(f"Configuration incomplete, errors encountered:\n\n{errors}")
