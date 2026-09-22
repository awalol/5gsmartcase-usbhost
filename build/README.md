# UDX710 核心生成文件

本目录包含按设备整理的离线 U-Boot 与 USB 冷启动候选文件。

公共入口是 `build.py`，两个设备使用同一组 `input/` 和 `output/`：

```sh
python3 build.py --device mate50rs
python3 build.py --device p60
```

## 构建依赖

Python 代码只使用标准库，不需要安装第三方 Python 包。建议使用
Python 3.10 或更高版本。

构建过程还要求以下 AArch64 GNU binutils 命令位于 `PATH` 中：

```text
aarch64-linux-gnu-as
aarch64-linux-gnu-ld
aarch64-linux-gnu-objcopy
```

在 macOS 上，可以通过交叉编译 binutils 软件包或其他等效工具链提供这些命令。
它们是系统可执行文件，不属于 Python 依赖。

切换设备前，把对应设备的原始镜像放入公共 `input/`，文件名固定为
`boot.bin` 和 `uboot.bin`。构建器会按配置哈希拒绝错误型号，输出写入公共
`output/`，因此一次只保留当前构建结果。

设备参数保存在 `configs/`，共用实现保存在 `tools/`；两种设备都通过统一的
`build.py` 和 `tools/build_device.py` 构建。P60 的专用汇编和链接器脚本也放
在同一个工具目录中；Mate 50 RS 使用同一套流程中的固定、已审计指令区域。

- `configs/p60.json`：P60 参数；
- `configs/mate50rs.json`：Mate 50 RS 参数。

请阅读 `configs/*.json`。配置文件记录输入、输出哈希以及型号相关的关键补丁参数。

设备包中的平台校验用于确认输入镜像没有拿错，不改变其面向的设备型号。

所有候选均标记为尚未实机验证；生成成功不代表可以直接刷写。
