"""Clock arithmetic for newline-delimited journalctl JSON, without changing timestamps."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ID = re.compile(r"[0-9a-fA-F]{32}\Z")
UINT = re.compile(r"[0-9]{1,20}\Z")
MAX_UINT64 = 2**64 - 1


class InputError(ValueError):
    """Input cannot be interpreted without guessing."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key")
        result[key] = value
    return result


def timestamp(value, field):
    if isinstance(value, str) and UINT.fullmatch(value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_UINT64:
        raise InputError(f"{field} must be an unsigned 64-bit microsecond timestamp")
    return value


@dataclass(frozen=True)
class Entry:
    machine: str | None
    boot: str
    realtime_us: int
    monotonic_us: int
    file: str
    line: int
    ordinal: int

    @property
    def offset_us(self):
        return self.realtime_us - self.monotonic_us

    def witness(self):
        return {
            "file": self.file,
            "line": self.line,
            "realtime_us": self.realtime_us,
            "monotonic_us": self.monotonic_us,
            "offset_us": self.offset_us,
        }


def load_entries(paths, *, max_records=200_000, max_line_bytes=1_048_576, max_bytes=268_435_456):
    """Read bounded binary lines so the line limit applies before decoding/JSON parsing."""
    if min(max_records, max_line_bytes, max_bytes) < 1:
        raise InputError("input limits must be positive")
    entries = []
    consumed = 0
    for filename in paths:
        path = Path(filename)
        if not path.is_file():
            raise InputError(f"not a regular file: {str(path)!r}")
        with path.open("rb") as stream:
            line_no = 0
            while raw := stream.readline(max_line_bytes + 1):
                line_no += 1
                consumed += len(raw)
                location = f"{str(path)!r}:{line_no}"
                if len(raw) > max_line_bytes:
                    raise InputError(f"{location}: line exceeds byte limit")
                if consumed > max_bytes:
                    raise InputError("input exceeds total byte limit")
                if not raw.strip():
                    continue
                if len(entries) >= max_records:
                    raise InputError("input exceeds record limit")
                try:
                    record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
                    if not isinstance(record, dict):
                        raise InputError("each nonblank line must be a JSON object")
                    boot = record.get("_BOOT_ID")
                    machine = record.get("_MACHINE_ID")
                    if not isinstance(boot, str) or not ID.fullmatch(boot):
                        raise InputError("_BOOT_ID must be 32 hexadecimal characters")
                    if machine is not None and (
                        not isinstance(machine, str) or not ID.fullmatch(machine)
                    ):
                        raise InputError("_MACHINE_ID must be absent or 32 hexadecimal characters")
                    entries.append(
                        Entry(
                            machine.lower() if machine is not None else None,
                            boot.lower(),
                            timestamp(record.get("__REALTIME_TIMESTAMP"), "__REALTIME_TIMESTAMP"),
                            timestamp(record.get("__MONOTONIC_TIMESTAMP"), "__MONOTONIC_TIMESTAMP"),
                            str(path),
                            line_no,
                            len(entries),
                        )
                    )
                except (ValueError, UnicodeError, RecursionError) as exc:
                    # Do not echo input messages or JSON text in errors.
                    detail = (
                        str(exc) if isinstance(exc, InputError) else "invalid UTF-8/JSON object"
                    )
                    raise InputError(f"{location}: {detail}") from exc
    if not entries:
        raise InputError("no journal entries found")
    return entries


def analyze(entries, *, threshold_us=1_000_000, timeline=False):
    if type(threshold_us) is not int or threshold_us < 1:
        raise InputError("threshold_us must be a positive integer")
    grouped = defaultdict(list)
    for entry in entries:
        grouped[(entry.machine, entry.boot)].append(entry)
    groups = []
    for (machine, boot), records in sorted(grouped.items(), key=lambda p: (p[0][0] or "", p[0][1])):
        ordered = sorted(records, key=lambda e: (e.monotonic_us, e.ordinal))
        changes = []
        segments = []
        current = []
        ties = 0
        for index, entry in enumerate(ordered):
            change = 0 if index == 0 else entry.offset_us - ordered[index - 1].offset_us
            if index and entry.monotonic_us == ordered[index - 1].monotonic_us:
                ties += 1
            if index and abs(change) >= threshold_us:
                previous = ordered[index - 1]
                changes.append(
                    {
                        "before": previous.witness(),
                        "after": entry.witness(),
                        "offset_change_us": change,
                        "wall_elapsed_us": entry.realtime_us - previous.realtime_us,
                        "monotonic_elapsed_us": entry.monotonic_us - previous.monotonic_us,
                        "direction": "forward" if change > 0 else "backward",
                        "equal_monotonic_timestamp": entry.monotonic_us == previous.monotonic_us,
                    }
                )
                segments.append(segment(current))
                current = []
            current.append(entry)
        if current:
            segments.append(segment(current))
        item = {
            "machine_id": machine,
            "boot_id": boot,
            "records": len(records),
            "input_order_changed": [e.ordinal for e in ordered] != [e.ordinal for e in records],
            "equal_monotonic_pairs": ties,
            "discontinuities": changes,
            "segments": segments,
        }
        if timeline:
            item["timeline"] = [entry.witness() for entry in ordered]
        groups.append(item)
    return {
        "schema_version": 1,
        "threshold_us": threshold_us,
        "records": len(entries),
        "groups": groups,
        "discontinuity_count": sum(len(g["discontinuities"]) for g in groups),
        "interpretation": "Offset changes can include clock adjustments, suspend or sampling effects. "
        "They are not corrected times or a causal diagnosis. Groups have no global order.",
    }


def segment(records):
    return {
        "records": len(records),
        "first": records[0].witness(),
        "last": records[-1].witness(),
        "min_offset_us": min(e.offset_us for e in records),
        "max_offset_us": max(e.offset_us for e in records),
    }
