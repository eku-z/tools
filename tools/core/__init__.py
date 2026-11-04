"""Pure work: no Qt, no Resolve. Everything here is tested on its own."""


class Stopped(Exception):
    """The person pressed Stop."""

    def __init__(self):
        super().__init__("Stopped.")
