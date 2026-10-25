import argparse
import json
import sys

from . import __version__
from .core import InputError, analyze, load_entries


def positive(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Map clock-offset changes in journalctl JSON lines."
    )
    parser.add_argument("files", nargs="+", help="one or more journalctl -o json exports")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--threshold-us", type=positive, default=1_000_000)
    parser.add_argument(
        "--timeline", action="store_true", help="include sorted timestamp-only records"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument(
        "--fail-on-change", action="store_true", help="exit 1 if a change is detected"
    )
    parser.add_argument("--max-records", type=positive, default=200_000)
    parser.add_argument("--max-line-bytes", type=positive, default=1_048_576)
    parser.add_argument("--max-bytes", type=positive, default=268_435_456)
    args = parser.parse_args(argv)
    try:
        entries = load_entries(
            args.files,
            max_records=args.max_records,
            max_line_bytes=args.max_line_bytes,
            max_bytes=args.max_bytes,
        )
        report = analyze(entries, threshold_us=args.threshold_us, timeline=args.timeline)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print(
                f"{report['records']} records; {len(report['groups'])} machine/boot groups; "
                f"{report['discontinuity_count']} offset discontinuities"
            )
            for group in report["groups"]:
                print(
                    f"boot={group['boot_id']} machine={group['machine_id'] or 'unknown'} "
                    f"records={group['records']} ties={group['equal_monotonic_pairs']}"
                )
                for change in group["discontinuities"]:
                    before, after = change["before"], change["after"]
                    print(
                        f"  {change['offset_change_us']:+d} us offset: "
                        f"{ascii(before['file'])}:{before['line']} -> "
                        f"{ascii(after['file'])}:{after['line']} "
                        f"(monotonic gap {change['monotonic_elapsed_us']} us)"
                    )
                if args.timeline:
                    for record in group["timeline"]:
                        print(
                            f"  mono={record['monotonic_us']} wall={record['realtime_us']} "
                            f"{ascii(record['file'])}:{record['line']}"
                        )
            print(report["interpretation"])
        return int(args.fail_on_change and report["discontinuity_count"] > 0)
    except (InputError, OSError) as exc:
        print(f"journal-offset-map: {ascii(str(exc))}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
