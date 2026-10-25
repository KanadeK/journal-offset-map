# journal-offset-map

Explain wall-clock discontinuities in saved `journalctl -o json` exports, on any machine with Python 3.11+. No systemd installation is needed for analysis. No runtime dependencies or network access.

When a clock changes during an incident, wall-time sorting can put a later log entry before an earlier one. This tool groups entries by machine and boot, orders each group by monotonic timestamp, and reports where `realtime - monotonic` changes. Each finding names the two original file/line locations and both elapsed times.

## Install and try

```sh
git clone https://github.com/KanadeK/journal-offset-map.git
cd journal-offset-map
python -m venv .venv
# POSIX: . .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
journal-offset-map examples/clock-change.jsonl --timeline
journal-offset-map examples/clock-change.jsonl --json --fail-on-change
```

The synthetic example contains three out-of-order records and one **+20,000,000 µs** offset change. The second command deliberately exits **1**. Module form is also available: `python -m journal_offset_map ...`.

On the source Linux machine, export a bounded incident interval:

```sh
journalctl -b -o json --no-pager > incident.jsonl
```

Copy the file to an analysis machine, then:

```sh
journal-offset-map incident.jsonl --threshold-us 500000 --json --timeline > report.json
```

Multiple input files are accepted. Records from overlapping exports are retained; no timestamp-based deduplication occurs. Files must be newline-delimited UTF-8 JSON objects (`-o json`, not `json-pretty`, `json-seq`, or binary journal files). Standard `__REALTIME_TIMESTAMP`, `__MONOTONIC_TIMESTAMP`, and `_BOOT_ID` are required. Missing `_MACHINE_ID` forms its own explicit unknown-machine group. Invalid or missing clock fields abort the report rather than silently dropping evidence.

## Reading the report

- `offset_change_us = (wall_after - wall_before) - (mono_after - mono_before)`; the default inclusive threshold is one second.
- Positive changes mean wall time advanced more than monotonic time. Suspend can produce this too: **a finding is not proof of NTP adjustment or a broken clock**.
- Findings bracket a change between observed entries. Sparse logs cannot locate its exact instant.
- Segments show min/max observed offsets. Gradual subthreshold drift may accumulate without triggering a pairwise finding.
- Groups are listed deterministically, without claiming chronological order between boots or machines.
- Equal monotonic timestamps preserve input order, are counted, and do not establish causal order. Duplicate entries remain present.
- The optional timeline contains only timestamps and provenance. Message bodies and arbitrary journal fields are never included. Machine IDs, boot IDs and file paths remain identifying information.

Exit codes: **0** successful analysis; **1** detected changes with `--fail-on-change`; **2** invalid input, limits or I/O failure. Default bounds: 200,000 records, 1 MiB per line, 256 MiB total input. Override with `--max-records`, `--max-line-bytes`, and `--max-bytes`. Processing uses O(n) memory and O(n log n) sorting; split very large exports by boot or incident interval.

## Scope and alternatives

`journalctl -o short-monotonic` and `short-delta` are preferable for live viewing. This project produces a portable structured evidence report from saved exports. It does not repair timestamps, prove clock accuracy, diagnose suspend, correlate clocks across machines, or edit source files. It does not handle monotonic-clock corruption within a boot.

The input contract follows [systemd's Journal JSON Format](https://systemd.io/JOURNAL_EXPORT_FORMATS/). The distinction between observed offsets and corrected time matters in [systemd #41267](https://github.com/systemd/systemd/issues/41267). All example data is synthetic; no third-party implementation code is included.

## Development and cleanup

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests, lint and builds. Unit tests cover clock arithmetic, random offset sequences, group boundaries, ordering ties and hostile/malformed input; subprocess tests exercise installed commands and exit codes. CI runs Python 3.11 on Linux and 3.13 on Windows.

Uninstall with `python -m pip uninstall journal-offset-map`. Delete your reports, checkout and virtual environment when no longer needed. The program creates no background service or persistent state. MIT licensed.

## 中文说明

离线分析 `journalctl -o json` 日志：按机器与启动 ID 分组，使用单调时钟排序，给出墙上时钟与单调时钟偏移变化及原文件行号。默认不输出日志消息正文，也不修改输入。

这是一份时间证据报告，并非自动校时工具。休眠、时钟调整、日志稀疏都可能影响结果；不会假装知道跨启动的真实时间顺序。示例为合成数据，运行 `--fail-on-change` 时示例预期退出码为 1。
