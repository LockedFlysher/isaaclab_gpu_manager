# IsaacLab GPU Manager (Qt)

一个用 Python + Qt (PyQt6 + QtCharts) 实现的小工具，通过 SSH 每 5 秒轮询 `nvidia-smi` 来获取服务器的 GPU 利用率、显存占用，并根据用户汇总显存占用生成饼图。

## 功能
- SSH 到远程服务器（使用本机 `ssh` 命令，无需额外依赖 Paramiko）
- 每 5 秒轮询：
  - `nvidia-smi --query-gpu=...` 获取 GPU 列表、利用率、显存
  - `nvidia-smi --query-compute-apps=...` 获取占用显存的进程
  - `ps -o pid=,user= -p ...` 映射 PID 到用户名
- 左侧表格：GPU 指标（利用率、显存进度条、进程数量）
- 右侧饼图：按用户汇总的显存占用（MiB）

## 运行
1. 安装依赖（建议创建虚拟环境）：
   ```bash
   pip install -r requirements.txt  # 需要 PyQt6 与 PyQt6-Charts
   ```
2. 启动：
   ```bash
   python -m gpu_manager_gui.main
   ```

> 需要：
> - 远程服务器已安装 `nvidia-smi`（NVIDIA 驱动）
> - 本机可用 `ssh` 命令，且能免交互登录目标主机（已在 `~/.ssh/config` 配好或已将公钥放到服务器）
> - 首次连接的主机指纹请先用终端 `ssh user@host` 手动确认一次（本工具以非交互模式连接）

## 使用说明
- 登录页：输入 `Host`（`user@server` 或仅 `server`）、`Port`、可选 `Identity`、`Interval`，点击 Connect；连接验证通过后进入监控页。
- 监控页：仅显示 GPU 表格与用户饼图，右上角有 `Disconnect` 返回登录页。

## 说明与限制
- 为了减少依赖，本工具每个轮询周期会执行 2~3 次 `ssh` 命令，简单可靠；如需更低开销，可后续改为长连接/多路复用（ControlMaster）或 Paramiko。
- 用户显存饼图基于 `nvidia-smi --query-compute-apps` 列出的进程，并通过 `ps` 映射用户名；若进程瞬时退出或权限受限，可能显示为 `unknown`。
- “性能”当前展示为 `utilization.gpu`（GPU 核心利用率 %）。如需功耗、频率等指标可扩展查询字段。

## 后续可选增强
- 多服务器/多标签页管理
- 任务列表与一键终止（需要权限控制）
- 账号计费统计（需要启用 NVIDIA accounting）
- 自定义 `ssh` 选项（ProxyJump、跳板机等）
