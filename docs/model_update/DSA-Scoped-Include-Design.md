# DSA Scoped Include 设计说明

这次 DSA `inp` 生成器不再把所有内容都塞进一个总 `include.inp`，而是按 Abaqus 关键字允许出现的层级拆开。

## 为什么要拆

以前的做法会把下面这些内容一起塞进文件头的 `include.inp`：

- `*PARAMETER`
- `*DESIGN PARAMETER`
- 新的 `*ELSET`
- 新的 `*SHELL SECTION`

这在“扁平 inp”里还能勉强工作，但在带 `*Part/*Assembly/*Instance` 的模型里会出错，因为 `*SHELL SECTION` 不能放在顶层，只能属于 `part` 或 `instance`。

## 现在的输出结构

### 1. 扁平 inp（没有 `*Part/*Instance`）

继续保持单文件 include：

- `include.inp`
  - `*PARAMETER`
  - `*DESIGN PARAMETER`
  - 根作用域下的新 `*ELSET`
  - 根作用域下的新 `*SHELL SECTION`

主文件仍然只在文件头 include 这一份。

### 2. 分层 inp（有 `*Part/*Assembly/*Instance`）

生成器会拆成几类 include：

- `include.inp`
  - 只放全局参数声明
  - `*PARAMETER`
  - `*DESIGN PARAMETER`

- `include_part_<part>.inp`
  - 只放对应 `part` 的
  - 新 `*ELSET`
  - 新 `*SHELL SECTION`
  - remainder `*ELSET`

- `include_assembly.inp`
  - 只在需要时生成
  - 目前用于 assembly 级新建集合，比如响应集

主文件的插入位置：

- 文件头：include `include.inp`
- 每个 `*End Part` 前：include 对应的 `include_part_<part>.inp`
- `*End Assembly` 前：include `include_assembly.inp`

## 内部作用域模型

为了同时兼容扁平 inp 和分层 inp，生成器内部统一把目标映射到作用域：

- `ROOT`
- `PART`
- `ASSEMBLY`

对没有显式 `part` 的扁平 inp，程序内部把它看成 `ROOT` 作用域，而不是强行改写原始文件结构。

## 多 instance 的当前边界

现在的壳厚度生成仍然最终落在 `part` 级 section 上。

这意味着：

- 如果某个 `part` 被多个 `instance` 共用
- 而参数来源又声明成某个 assembly/instance 作用域

程序会先把它解析回对应 `part`，并给出 warning：这类 section 变更会影响所有复用该 `part` 的 instance。

这不是程序偷懒，而是 Abaqus `section` 本身就是偏模板定义的。

如果以后要做到“同一个 part 的不同 instance 使用不同厚度/材料”，就需要进一步设计：

- 自动复制 part 变体
- 或增加更严格的参数作用域约束

## 调试辅助

生成器在自动插入的局部块里增加了统一注释，例如：

- `DSA_AUTO_GLOBAL_BEGIN`
- `DSA_AUTO_SCOPE_BEGIN PART:P1`
- `DSA_AUTO_TASK_BEGIN`
- `DSA_AUTO_REMAINDER`

这样后续排查时，直接全文搜索 `DSA_AUTO_` 就能快速定位自动生成区域。
