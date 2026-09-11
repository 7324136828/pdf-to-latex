"""Readable, numbered setup output."""

from __future__ import annotations

import sys

RULE = "=" * 60


class Console:
    """Prints the ``[n/total] ...`` progression the setup log is made of."""

    def __init__(self, total: int, verbose: bool = False):
        self.total = total
        self.verbose = verbose
        self.step_number = 0
        self.warnings: list[str] = []

    def banner(self, title: str) -> None:
        print(RULE)
        print(title)
        print(RULE)
        print()

    def step(self, title: str) -> None:
        self.step_number += 1
        print(f"[{self.step_number}/{self.total}] {title}", flush=True)

    def info(self, message: str) -> None:
        for line in str(message).splitlines():
            print(f"  {line}", flush=True)

    def detail(self, message: str) -> None:
        """Diagnostics that only ``--verbose`` asks for."""
        if self.verbose:
            self.info(message)

    def ok(self, message: str = "OK") -> None:
        self.info(message)
        print(flush=True)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        for line in str(message).splitlines():
            print(f"  WARNING: {line}", file=sys.stderr, flush=True)

    def fail(self, message: str) -> None:
        for line in str(message).splitlines():
            print(f"  ERROR: {line}", file=sys.stderr, flush=True)

    def finish(self, message: str = "Setup complete.") -> None:
        print()
        if self.warnings:
            print(f"{message} {len(self.warnings)} warning"
                  f"{'' if len(self.warnings) == 1 else 's'}:")
            for warning in self.warnings:
                print(f"  - {warning.splitlines()[0]}")
        else:
            print(message)
