"""The one kind of failure that is reported to the user as a message."""

from __future__ import annotations


class UserError(RuntimeError):
    """Something stopped the command that the user can act on; the message
    says what, and how to fix it if there is a way.

    Code that finds the problem raises it and does not log it: the program
    reports it once, where it ends the command (see `application.main`).
    """
