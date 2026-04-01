# Binary Payload Spec

## 目标

本文档定义 L3 二进制响应的统一封装格式。

目标是解决这几个问题：

- 一个接口返回多个数组时，前端怎么稳定解包
- `mesh/chunk` 和 `render/state` 的 payload 怎么统一
- section 的名字、类型、shape、offset 怎么表达
- 后续协议升级怎么兼容

本文档聚焦：

- 二进制布局
- section table 规范
- 常用 section 定义
- 前端解包约定

不讨论具体业务计算逻辑。

---

## 1. 为什么需要统一二进制协议

L3 返回的很多内容都不是普通 JSON，而是大数组。

比如：

- positions
- normals
- render_face_idx
- scalar_per_face
- positions_deformed

如果每个接口各自定义一套乱七八糟的拼接规则，后面很快就会出现问题：

- 前端解包逻辑很多套
- 接口一改，前端就跟着改
- 多数组返回时很难知道每段是什么意思
- 后续想加字段时容易破坏兼容性

所以需要一个统一的 envelope。

---

## 2. 协议名称与版本

建议协议名：

```text
L3 Binary Envelope
```

第一版简称：

```text
L3BE v1
```

---

## 3. 总体布局

L3BE v1 采用下面的整体布局：

```text
[Fixed Header][Section Table][Section Payloads...]
```

也就是：

1. 固定头
2. section 描述表
3. 多段原始数组数据

---

## 4. 字节序与对齐

### 4.1 字节序

统一使用：

- **little-endian**

原因：

- Python / NumPy / 常见服务器环境最常见
- JS 解包也方便

### 4.2 对齐

建议：

- 所有 section payload 起始 offset 按 **8 字节对齐**

原因：

- 更利于后续扩展
- 对多种 dtype 更稳妥

如果为了简单，第一版也可接受 4 字节对齐，但建议直接统一 8 字节。

---

## 5. Fixed Header 结构

Fixed Header 用于告诉前端：

- 这是不是合法的 L3BE payload
- 当前是什么版本
- 有多少 section
- section table 从哪里开始

### 5.1 Header 字段

建议固定头字段如下：

| 字段 | 类型 | 字节数 | 说明 |
|------|------|--------|------|
| magic | char[4] | 4 | 固定为 `L3BE` |
| version | uint16 | 2 | 当前协议版本，v1=`1` |
| flags | uint16 | 2 | 预留 |
| header_size | uint32 | 4 | Fixed Header 总长度 |
| section_count | uint32 | 4 | section 数量 |
| section_table_offset | uint32 | 4 | section table 起始偏移 |
| payload_offset | uint32 | 4 | 第一个 payload section 起始偏移 |
| reserved | uint32[4] | 16 | 保留字段 |

建议 v1 固定头总长：

- `40 bytes`

### 5.2 Header 示例

例如：

- `magic = "L3BE"`
- `version = 1`
- `section_count = 5`
- `section_table_offset = 40`
- `payload_offset = 40 + section_count * section_entry_size`

---

## 6. Section Table 结构

section table 描述每一个数组段。

前端真正解包时，主要依赖这张表。

### 6.1 Section Entry 字段

建议每个 section entry 固定长度，便于快速解析。

| 字段 | 类型 | 字节数 | 说明 |
|------|------|--------|------|
| name | char[32] | 32 | section 名，ASCII，末尾 0 填充 |
| dtype_code | uint16 | 2 | 数据类型编码 |
| ndim | uint16 | 2 | 维度数 |
| shape | uint32[4] | 16 | 最多支持 4 维，未用维填 0 |
| offset | uint64 | 8 | section payload 偏移 |
| nbytes | uint64 | 8 | section payload 字节长度 |
| flags | uint32 | 4 | 预留 |
| reserved | uint32 | 4 | 预留 |

建议每个 section entry 固定长度：

- `76 bytes`

为了对齐，可向上补到：

- `80 bytes`

### 6.2 为什么 shape 固定最多 4 维

因为你们当前主要数组已经够用了：

- `[R]`
- `[R, 3]`
- `[R, 3, 3]`
- `[R, 3, 4]`

第一版没必要把协议设计得过度复杂。

---

## 7. dtype 编码

建议 v1 使用固定 dtype code 表：

| code | dtype |
|------|------|
| 1 | int8 |
| 2 | uint8 |
| 3 | int16 |
| 4 | uint16 |
| 5 | int32 |
| 6 | uint32 |
| 7 | int64 |
| 8 | uint64 |
| 9 | float32 |
| 10 | float64 |

说明：

- 第一版不建议在二进制 section 中直接存字符串数组
- 像 `source_elem_type` 这种内容，优先用整型 code

---

## 8. section 命名规范

section 名应采用稳定的 snake_case。

建议第一版统一这些名字：

### 8.1 基础几何相关

- `positions`
- `normals`
- `render_face_idx`
- `source_elem_row`
- `source_node_rows`
- `source_elem_type`

### 8.2 渲染态相关

- `scalar_per_vertex`
- `scalar_per_face`
- `color_per_vertex`
- `color_per_face`
- `attribute_id_per_face`
- `positions_deformed`
- `focus_mask`
- `legend_range`

### 8.3 预留

- `reserved_*`

---

## 9. section shape 约定

### 9.1 基础几何

#### `positions`

- dtype: `float32`
- shape: `[R, 3, 3]`

含义：

- `R` 个三角面
- 每个面 3 个顶点
- 每个顶点 3 个坐标值

#### `normals`

- dtype: `float32`
- shape: `[R, 3, 3]`

#### `render_face_idx`

- dtype: `int32`
- shape: `[R]`

#### `source_elem_row`

- dtype: `int32`
- shape: `[R]`

#### `source_node_rows`

- dtype: `int32`
- shape: `[R, 3]`

#### `source_elem_type`

- dtype: `int16` 或 `int32`
- shape: `[R]`

建议：

- 若 instance 内可能存在混合单元类型，第一版就保留这个 section

### 9.2 渲染态

#### `scalar_per_vertex`

- dtype: `float32`
- shape: `[R, 3]`

#### `scalar_per_face`

- dtype: `float32`
- shape: `[R]`

#### `color_per_vertex`

- dtype: `uint8`
- shape: `[R, 3, 4]`

建议 RGBA。

#### `color_per_face`

- dtype: `uint8`
- shape: `[R, 4]`

#### `attribute_id_per_face`

- dtype: `int32`
- shape: `[R]`

#### `positions_deformed`

- dtype: `float32`
- shape: `[R, 3, 3]`

#### `focus_mask`

- dtype: `uint8`
- shape: `[R]`

建议语义：

- `0` = 非 focus
- `1` = focus

#### `legend_range`

- dtype: `float32`
- shape: `[2]`

含义：

- `[value_min, value_max]`

---

## 10. `mesh/chunk` payload 规范

### 10.1 必选 section

第一版建议 `mesh/chunk` 至少包含：

- `positions`
- `normals`
- `render_face_idx`
- `source_elem_row`

### 10.2 推荐 section

推荐补上：

- `source_node_rows`
- `source_elem_type`

### 10.3 示例

假设某个 chunk 有 `R = 1024` 个三角面，则可能包含：

- `positions`：`[1024, 3, 3] float32`
- `normals`：`[1024, 3, 3] float32`
- `render_face_idx`：`[1024] int32`
- `source_elem_row`：`[1024] int32`
- `source_node_rows`：`[1024, 3] int32`

### 10.4 响应头建议

- `X-Payload-Type: mesh_chunk_v1`
- `X-Layout-Version: 1`

---

## 11. `render/state` payload 规范

### 11.1 设计原则

`render/state` 不是固定只返回一种结构。

它的返回内容取决于：

- 渲染模式
- 是否有变形
- 是否有 focus

所以第一版建议：

- `render_face_idx` 尽量总是返回
- 其他 section 按模式返回

### 11.2 Smooth 模式

建议至少返回：

- `render_face_idx`
- `scalar_per_vertex`

可选返回：

- `positions_deformed`
- `focus_mask`
- `legend_range`

### 11.3 Flat 模式

建议至少返回：

- `render_face_idx`
- `scalar_per_face`

可选返回：

- `positions_deformed`
- `focus_mask`
- `legend_range`

### 11.4 Attribute 模式

建议至少返回：

- `render_face_idx`
- `attribute_id_per_face`

可选返回：

- `positions_deformed`
- `focus_mask`

### 11.5 如果后端直接回颜色

第一版虽然优先推荐传 scalar，但若后端后续决定直接回颜色，可替换为：

- `color_per_vertex`
或
- `color_per_face`

但仍建议保留：

- `render_face_idx`

---

## 12. 偏移计算规则

### 12.1 基本规则

每个 section 的：

- `offset`
  是相对于整个 payload 起始位置的字节偏移

- `nbytes`
  是该 section 的原始字节长度

### 12.2 排布规则

建议：

1. 所有 section payload 紧密排列
2. 每段开始前按 8 字节对齐
3. section table 中的顺序应和实际 payload 顺序一致

### 12.3 示例

如果：

- Header = 40 bytes
- Section Entry = 80 bytes
- `section_count = 5`

则：

- `section_table_offset = 40`
- `payload_offset = 40 + 5 * 80 = 440`

实际第一个 payload section 的 offset 从 `440` 开始，再按 8 字节对齐。

---

## 13. 前端解包流程

前端拿到 `ArrayBuffer` 后，建议统一按下面步骤处理：

### 13.1 读取 Fixed Header

检查：

- magic 是否为 `L3BE`
- version 是否支持

### 13.2 读取 Section Table

拿到每个 section 的：

- name
- dtype_code
- ndim
- shape
- offset
- nbytes

### 13.3 建立 section map

例如得到：

```text
{
  positions: {...},
  normals: {...},
  render_face_idx: {...}
}
```

### 13.4 根据 dtype 和 offset 构造 TypedArray

例如：

- `float32` -> `Float32Array`
- `int32` -> `Int32Array`
- `uint8` -> `Uint8Array`

### 13.5 按 shape 交给 Three.js 或业务层使用

比如：

- `positions` 转成 `BufferAttribute`
- `scalar_per_face` 交给着色逻辑

---

## 14. Three.js 侧使用建议

### 14.1 `positions`

虽然协议中 shape 是 `[R, 3, 3]`，前端通常会把它 reshape 成：

- `[R * 3, 3]`

然后灌给 `BufferGeometry.position`。

### 14.2 `normals`

同理转成：

- `[R * 3, 3]`

### 14.3 `scalar_per_vertex`

可转成：

- `[R * 3]`

或扩展成颜色数组。

### 14.4 `scalar_per_face`

若前端需要面片均色，可在 CPU 侧展开到 3 个顶点，或在 shader 中按 face 语义处理。

---

## 15. 兼容性策略

### 15.1 版本号

若 envelope 布局变化：

- 升 `version`

### 15.2 section 增量扩展

在不破坏 v1 基础字段的前提下：

- 允许新增 section

前端若不认识某个 section，可直接忽略。

### 15.3 section 删除或重命名

不建议在同一 version 内做。

若必须改：

- 升版本

---

## 16. 第一版实现建议

为了降低实现复杂度，第一版建议遵循：

- 统一 envelope
- 固定 header
- 固定 section entry 长度
- 只支持数值数组
- 最多 4 维 shape

不要第一版就做：

- 压缩块
- 字符串 section
- 嵌套 section
- 可变长元数据 JSON 内嵌

这些以后需要时再加。

---

## 17. 最终结论

L3 的二进制返回应统一采用：

- `L3 Binary Envelope v1`
- `Fixed Header + Section Table + Section Payloads`

对当前项目最关键的好处是：

- `mesh/chunk` 和 `render/state` 可以共用一套解包逻辑
- 前端和后端对 section 语义能稳定对齐
- 后续协议扩展不会立刻把所有接口搞乱

第一版最值得先稳定的 section 有：

- `positions`
- `normals`
- `render_face_idx`
- `source_elem_row`
- `source_node_rows`
- `scalar_per_vertex`
- `scalar_per_face`
- `positions_deformed`
- `focus_mask`
- `legend_range`

