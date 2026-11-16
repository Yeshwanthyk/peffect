"""Test services: TestClock (time moves only when the test moves it) and TestConsole (records
what a program prints). Effect v4: src/testing/."""

from ._clock import TestClock, TestClockService
from ._console import TestConsole, TestConsoleService

__all__ = ["TestClock", "TestClockService", "TestConsole", "TestConsoleService"]
