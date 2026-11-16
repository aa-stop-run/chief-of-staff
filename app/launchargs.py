"""Options the server must know before anything else is imported (the data folder decides what `chief` reads)."""


def home_arg(argv: list) -> str | None:
    """The value of `--home`, written as `--home <folder>` or `--home=<folder>`; None if it is absent or has no value."""
    for i, arg in enumerate(argv):
        if arg == "--home":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith("--home="):
            return arg.split("=", 1)[1] or None
    return None
