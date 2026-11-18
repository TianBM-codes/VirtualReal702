"""Import measured Abaqus sensitivity scalars into an existing L3 workspace."""
import argparse
import csv
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.l3.infra.manifest_repo import ManifestRepo
from src.l3.services.external_result_writer import ExternalResultWriter

STEP = "Sensitivity"
FIELD = "ABAQUS_MANUAL_SENSITIVITY"


def run(workspace, csv_path, name, dry_run, response_name=None, parameter_name=FIELD,
        *, step=STEP, group_prefix="sensitivity_manual_", frame_value=0.0):
    if not re.fullmatch(r"[\w -]+", parameter_name) or not parameter_name.strip():
        raise ValueError("--parameter-name 只能包含文字、数字、空格、下划线和连字符")
    response_name = (response_name or name).strip() or name
    if not (workspace / "manifest.db").is_file():
        raise ValueError("工作区缺少 manifest.db；需要已导入的网格和 ELSET")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("--name 只能包含字母、数字、下划线和连字符")
    group = f"{group_prefix}{name}"
    target = workspace / "l1" / "results" / group
    if target.exists():
        raise ValueError(f"结果组已存在，请换一个 --name：{group}")

    repo = ManifestRepo(str(workspace))
    sets = {}
    for row in repo.list_element_sets():
        sets.setdefault(row["set_name"].casefold(), []).append(row["instance_name"])

    by_instance = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"set_name", "value"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV 必须包含 set_name,value 列；可选 instance")
        for row in reader:
            try:
                set_name = (row["set_name"] or "").strip()
                requested = (row.get("instance") or "").strip().casefold()
                value = float(row["value"] or "")
                if not math.isfinite(value) or abs(value) > 3.4028234663852886e38:
                    raise ValueError("数值必须在 float32 的有限范围内")
                instances = sorted(set(sets.get(set_name.casefold(), [])))
                if requested:
                    instances = [i for i in instances if i.casefold() == requested]
                if len(instances) != 1:
                    raise ValueError(f"ELSET {set_name!r} 无法唯一定位，匹配实例：{instances}；请核对或填写 instance")
                instance = instances[0]
                labels = repo.get_element_set_labels(set_name, instance)
                if labels is None or not len(labels):
                    raise ValueError(f"ELSET {instance}/{set_name} 为空或无法读取")
                values = by_instance.setdefault(instance, {})
                for label in map(int, labels):
                    if values.setdefault(label, value) != value:
                        raise ValueError(f"重叠 ELSET 对 {instance} 单元 {label} 给出了不同值")
                print(f"{instance}/{set_name}: {len(labels)} 个单元，值 {value:g}")
            except ValueError as exc:
                raise ValueError(f"CSV 第 {reader.line_num} 行：{exc}") from exc
    if not by_instance:
        raise ValueError("CSV 没有数据行")

    if not dry_run:
        target.mkdir(parents=True, exist_ok=False)
        writer = ExternalResultWriter(str(workspace), group)
        for instance, values in sorted(by_instance.items()):
            writer.write_element(
                instance=instance, step=step, field=parameter_name, components=["value"],
                frames=[{"frame_idx": 0, "frame_value": frame_value,
                         "description": response_name,
                         "data": [{"label": k, "values": [v]} for k, v in sorted(values.items())]}],
            )
        repo.update_result_group_meta_display_name(group, response_name)
    print(f"{'仅校验' if dry_run else '已导入'}：{group} / {step} / {parameter_name}，"
          f"{sum(map(len, by_instance.values()))} 个单元")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="包含 manifest.db 的 L3 工作区")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--name", required=True, help="本次结果名称，例如频率阶次或响应分量")
    parser.add_argument("--response-name", help="响应显示名")
    parser.add_argument("--parameter-name", default=FIELD, help="参数字段名")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        run(args.workspace.resolve(), args.csv, args.name, args.dry_run,
            args.response_name, args.parameter_name)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"导入失败：{exc}\n")


if __name__ == "__main__":
    main()
