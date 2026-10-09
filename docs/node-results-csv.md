# 节点结果 CSV 提取

`tools/extract_node_results.py` 使用普通 Python 3 和标准库读取
`tools/odb_node_history.py` 导出的长表 CSV，无需安装 Abaqus、pandas 或启动服务。
默认输入是脚本旁的 `tools/result.csv`。

先查看文件包含哪些节点：

```powershell
python tools/extract_node_results.py --list
```

提取节点 461 和 1377 的全部结果：

```powershell
python tools/extract_node_results.py --nodes PART-2-1.461,PART-2-1.1377
```

也可以指定默认实例、其他输入文件和输出目录：

```powershell
python tools/extract_node_results.py --input tools/result.csv --instance PART-2-1 --nodes 461,1377 --out-dir tools/node_exports
```

可选筛选：`--fields U,S,LE`、`--steps Step-1`、`--frames 0,1,2`。
不提供筛选时保留全部分析步、帧、场、分量和截面点。帧号在每个分析步内独立解释。
同号节点存在于多个实例时，必须指定实例。任一指定节点不存在时报告错误。

默认输出位于输入文件旁的 `result_extracted/`：

- `node_results_long.csv`：保留选中记录的所有原始列和原始数值文本，按分析步及 increment 数值排序。
- `node_results_wide.csv`：每行一个节点、分析步、帧和截面点；结果列例如
  `["U", "U1"]`、`["S", "Mises"]`。对应 `.n_elems` 列保存参与计算的单元数。
  某帧缺少某个结果时，该单元格留空，不补零。输出带 UTF-8 BOM，方便 Excel 打开。

脚本保留 CSV 已有数值，不重新平均、计算不变量或改变单位。原始 ODB 导出时若已经
将周围单元结果平均，该 CSV 无法恢复平均前的逐单元结果。壳的不同截面点始终分开。
宽表遇到同一节点/步/帧/截面点/场/分量重复记录时报告错误，完整长表仍会保存所有记录，
不会静默覆盖数据。重复运行会覆盖同一输出目录中的两份导出文件。

## 绘制 increment 曲线

绘图需要安装 matplotlib：

```powershell
python -m pip install matplotlib
python tools/extract_node_results.py --nodes PART-2-1.461,PART-2-1.1377 --plot
```

输出目录中 `curves/` 保存每张 PNG 曲线图，`node_results_curves.pdf` 汇总全部图。
同一分析步、结果场、分量和截面点的多个节点画在一张图上，方便比较。
不同分析步、不同截面点以及应力/应变/位移分量分开绘图，纵轴沿用 CSV 数值和单位。

只画应力 S11 和 Mises：

```powershell
python tools/extract_node_results.py --nodes 461,1377 --fields S --components S11,Mises --plot
```

increment 优先读取可选的 `increment` 列，其次从 `frame_desc` 中提取
`Increment 6` 这样的编号。两者都没有时回退到 `frame_idx`（输出帧号）。
帧号可能与求解增量号不同，例如 ODB 只保存部分增量时；脚本不会把文本排序的
`1,10,2` 当作正确顺序。曲线的空数值作为缺失值处理，不补零。
