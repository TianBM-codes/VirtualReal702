"""Run from repository root: python -m tools.simright --help."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import (SimRightClient, SimRightError, match_sensors, project_node_history,
               project_strain, project_stress, sensor_values)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="数巧 DATA-API 客户端、测点匹配与张量方向投影")
    parser.add_argument("--base-url", default=os.getenv("SIMRIGHT_BASE_URL"))
    parser.add_argument("--profile", choices=("markdown", "excel"), default="markdown")
    parser.add_argument("--query-path")
    parser.add_argument("--filename-key", choices=("filename", "fileName"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", help="UTF-8 JSON 输出文件；省略则输出到终端")
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("query", help="任意 DATA-API 操作")
    q.add_argument("name")
    q.add_argument("--filename")
    q.add_argument("--args", help="args JSON 文件；支持 modelDiff 不传 filename")
    m = sub.add_parser("match", help="测点坐标匹配；一测点一次请求")
    m.add_argument("--filename", required=True)
    m.add_argument("--sensors", required=True)
    m.add_argument("--max-distance", required=True, type=float)
    m.add_argument("--options", help="工况/帧参数 JSON 文件")
    v = sub.add_parser("values", help="获取匹配节点的结果，保留帧号和角点数组")
    v.add_argument("--filename", required=True)
    v.add_argument("--matches", required=True)
    v.add_argument("--var-name", required=True)
    v.add_argument("--parts", help="assemble 返回的 data 对象 JSON 文件，用于 partId -> oriId")
    v.add_argument("--options")
    p = sub.add_parser("project", help="离线投影输入 JSON 中的 components 和 direction")
    p.add_argument("--input", required=True)
    h = sub.add_parser("project-history", help="获取六分量曲线并计算方向投影")
    h.add_argument("--input", required=True, help="含 filename/node_ids/direction/options 等的 JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "project":
            spec = read_json(args.input)
            kind = spec.pop("kind", "stress")
            if kind not in ("stress", "strain"):
                raise ValueError("kind must be stress or strain")
            result = (project_stress if kind == "stress" else project_strain)(**spec)
        else:
            if not args.base_url:
                parser.error("网络命令需要 --base-url 或 SIMRIGHT_BASE_URL")
            headers = {}
            if os.getenv("SIMRIGHT_AUTHORIZATION"):
                headers["Authorization"] = os.environ["SIMRIGHT_AUTHORIZATION"]
            if os.getenv("SIMRIGHT_COOKIE"):
                headers["Cookie"] = os.environ["SIMRIGHT_COOKIE"]
            with SimRightClient(args.base_url, profile=args.profile,
                               query_path=args.query_path, filename_key=args.filename_key,
                               timeout=args.timeout, headers=headers) as client:
                if args.command == "query":
                    params = read_json(args.args) if args.args else {}
                    if args.filename:
                        params["filename"] = args.filename
                    result = client.query(args.name, **params)
                elif args.command == "match":
                    result = match_sensors(client, args.filename, read_json(args.sensors),
                                           max_distance=args.max_distance,
                                           **(read_json(args.options) if args.options else {}))
                elif args.command == "values":
                    parts = read_json(args.parts)["parts"] if args.parts else []
                    part_ids = {p["id"]: p["oriId"] for p in parts}
                    result = sensor_values(client, args.filename, read_json(args.matches),
                                           args.var_name, part_ori_ids=part_ids,
                                           **(read_json(args.options) if args.options else {}))
                else:
                    spec = read_json(args.input)
                    options = spec.pop("options", {})
                    result = project_node_history(client, **spec, **options)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0
    except (SimRightError, ValueError, TypeError, KeyError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
