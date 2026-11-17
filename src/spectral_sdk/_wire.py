from __future__ import annotations

import re
from datetime import datetime

_DURATION_UNITS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "μs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

_DURATION_PART = re.compile(r"(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|μs|ms|s|m|h)")

_RFC3339 = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def format_duration(seconds: float) -> str:
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{round(seconds * 1000)}ms"


def parse_duration(text: str) -> float:
    rest = text.strip()
    sign = 1.0
    if rest[:1] in ("+", "-"):
        if rest[0] == "-":
            sign = -1.0
        rest = rest[1:]

    if rest == "0":
        return 0.0
    if not rest:
        raise ValueError(f"invalid duration {text!r}")

    total = 0.0
    pos = 0
    while pos < len(rest):
        match = _DURATION_PART.match(rest, pos)
        if match is None:
            raise ValueError(f"invalid duration {text!r}")
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        pos = match.end()

    return sign * total


def parse_rfc3339(text: str) -> datetime:
    match = _RFC3339.match(text)
    if match is None:
        raise ValueError(f"invalid RFC 3339 timestamp {text!r}")

    base, fraction, offset = match.groups()
    if offset == "Z":
        offset = "+00:00"

    microsecond = 0
    if fraction:
        microsecond = int((fraction[1:] + "000000")[:6])

    return datetime.fromisoformat(base + offset).replace(microsecond=microsecond)
