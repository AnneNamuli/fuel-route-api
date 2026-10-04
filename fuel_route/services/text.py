"""Small text helpers shared by the services."""


def collapse_whitespace(text: str) -> str:
    """Trim and collapse runs of whitespace: '  Kansas City,  MO ' -> 'Kansas City, MO'."""
    return ' '.join(text.split())
