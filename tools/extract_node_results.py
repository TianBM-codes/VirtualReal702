"""Extract complete node histories from odb_node_history-style CSV files.

Uses Python 3 standard library only. Run --help for command-line options.
"""

import argparse
import csv
import json
from pathlib import Path
import sys


REQUIRED = (
    "step", "step_index", "frame_idx", "frame_value", "frame_desc",
    "instance", "node", "field", "section_point", "component", "value", "n_elems",
)
IDENTITY = REQUIRED[:7] + ("section_point",)


def names(text):
    return {item.strip() for item in (text or "").split(",") if item.strip()}


def read_rows(path, encoding):
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = set(REQUIRED) - set(headers)
        if missing:
            raise ValueError("Missing CSV columns: " + ", ".join(sorted(missing)))
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Invalid column count near CSV line {}".format(reader.line_num))
            try:
                int(row["node"])
                int(row["step_index"])
                int(row["frame_idx"])
            except ValueError as exc:
                raise ValueError("Invalid node/step/frame number near CSV line {}".format(
                    reader.line_num)) from exc
            rows.append(row)
    return headers, rows


def select_nodes(text, default_instance, available):
    selected = set()
    for token in sorted(names(text)):
        if "." in token:
            instance, label = token.rsplit(".", 1)
            key = (instance.upper(), int(label))
        else:
            label = int(token)
            matches = {key for key in available if key[1] == label}
            if default_instance:
                key = (default_instance.upper(), label)
            elif len(matches) == 1:
                key = next(iter(matches))
            elif not matches:
                raise ValueError("Node {} does not exist in the CSV".format(label))
            else:
                raise ValueError("Node {} belongs to multiple instances; use INSTANCE.node".format(label))
        if key not in available:
            raise ValueError("Node {}.{} does not exist in the CSV".format(*key))
        selected.add(key)
    return selected


def write_csv(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def wide_rows(rows):
    groups = {}
    columns = []
    for row in rows:
        key = tuple(row[name] for name in IDENTITY)
        group = groups.setdefault(key, dict(zip(IDENTITY, key)))
        # JSON array names are unambiguous even if fields contain punctuation.
        column = json.dumps([row["field"], row["component"]], ensure_ascii=False)
        count_column = column + ".n_elems"
        if column in group:
            raise ValueError("Duplicate result for {}: {}. Long CSV retains all rows.".format(
                key, column))
        if column not in columns:
            columns.extend((column, count_column))
        group[column] = row["value"]
        group[count_column] = row["n_elems"]
    return list(IDENTITY) + columns, list(groups.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).with_name("result.csv"))
    parser.add_argument("--nodes", help="Comma-separated INSTANCE.node or node labels")
    parser.add_argument("--instance", help="Default instance for bare node labels")
    parser.add_argument("--list", action="store_true", help="List available nodes and fields")
    parser.add_argument("--fields", help="Optional comma-separated fields; default: all")
    parser.add_argument("--steps", help="Optional comma-separated steps; default: all")
    parser.add_argument("--frames", help="Optional comma-separated frame indices; default: all")
    parser.add_argument("--out-dir", type=Path, help="Default: <input stem>_extracted beside input")
    parser.add_argument("--encoding", default="utf-8-sig")
    args = parser.parse_args(argv)
    if not args.list and not names(args.nodes):
        parser.error("Provide --nodes or --list")
    try:
        headers, rows = read_rows(args.input, args.encoding)
        available = {(row["instance"].upper(), int(row["node"])) for row in rows}
        if args.list:
            for instance, node in sorted(available):
                print("{}.{}".format(instance, node))
            print("Fields: " + ", ".join(sorted({row["field"] for row in rows})))
            print("Steps: " + ", ".join(dict.fromkeys(row["step"] for row in rows)))
            return 0
        selected = select_nodes(args.nodes, args.instance, available)
        fields, steps = names(args.fields), names(args.steps)
        frames = {int(value) for value in names(args.frames)}
        filtered = [row for row in rows
                    if (row["instance"].upper(), int(row["node"])) in selected
                    and (not fields or row["field"] in fields)
                    and (not steps or row["step"] in steps)
                    and (not frames or int(row["frame_idx"]) in frames)]
        if not filtered:
            raise ValueError("No results match the selected filters")
        output = args.out_dir or args.input.with_name(args.input.stem + "_extracted")
        long_path = output / "node_results_long.csv"
        wide_path = output / "node_results_wide.csv"
        if args.input.resolve() in (long_path.resolve(), wide_path.resolve()):
            raise ValueError("Output must not overwrite the input CSV")
        output.mkdir(parents=True, exist_ok=True)
        write_csv(long_path, headers, filtered)
        print("Long CSV ({} result rows): {}".format(len(filtered), long_path.resolve()))
        wide_headers, wide = wide_rows(filtered)
        write_csv(wide_path, wide_headers, wide)
        print("Wide CSV ({} frame/section rows): {}".format(len(wide), wide_path.resolve()))
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
