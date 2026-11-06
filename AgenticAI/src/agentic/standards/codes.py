"""Standard-code helpers shared by production and the lab."""


def concept_of(code: str) -> str:
    """The concept a standard belongs to: the first four dot segments (`3.R.1.B.b` -> `3.R.1.B`)."""
    return ".".join(code.split(".")[:4])
