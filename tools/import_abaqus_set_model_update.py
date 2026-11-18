"""Import final model-update percentages by ELSET into an existing L3 workspace."""
import argparse
from pathlib import Path

if __package__:
    from .import_abaqus_set_sensitivity import run
else:
    from import_abaqus_set_sensitivity import run


def main():
    parser = argparse.ArgumentParser(
        description="按 ELSET 导入最终参数修正百分比。CSV 中 5 表示 +5%，-2 表示 -2%。",
    )
    parser.add_argument("workspace", type=Path, help="包含 manifest.db 的 L3 工作区")
    parser.add_argument("csv", type=Path, help="列为 set_name,value,instance（instance 可选）")
    parser.add_argument("--name", required=True, help="本次修正结果的唯一标识")
    parser.add_argument("--display-name", help="结果组显示名")
    parser.add_argument("--parameter-name", help="参数名，例如 弹性模量 E")
    parser.add_argument("--dry-run", action="store_true", help="校验 CSV 和 ELSET，不写入结果")
    args = parser.parse_args()

    parameter = args.parameter_name
    field = f"PARAMETER_{parameter}_RELATIVE_DELTA_PERCENT" if parameter else "PARAMETER_RELATIVE_DELTA_PERCENT"
    try:
        if parameter is not None and not parameter.strip():
            raise ValueError("--parameter-name 不能为空")
        run(
            args.workspace.resolve(), args.csv, args.name, args.dry_run,
            args.display_name or "最终参数修正百分比", field,
            step="BayesianUpdate", group_prefix="bayesian_manual_", frame_value=1.0,
        )
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"导入失败：{exc}\n")


if __name__ == "__main__":
    main()
