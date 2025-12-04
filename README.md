# IsaacLab GPU Manager (Qt)

一个用 Python + Qt (PyQt6 + QtCharts) 实现的小工具，通过 SSH 每 5 秒轮询 `nvidia-smi` 来获取服务器的 GPU 利用率、显存占用，并根据用户汇总显存占用生成饼图。内置“Runner + Console”，可一键在远端 Conda 环境或 Docker 容器内运行 Python 脚本，并通过交互式 SSH 控制台查看/分屏。

## 功能
- 远程监控
  - 通过本机 `ssh`（或 Paramiko 密码模式）每 5 秒轮询：
    - `nvidia-smi --query-gpu=...` GPU 列表、利用率、显存
    - `nvidia-smi --query-compute-apps=...` 进程显存占用 + `ps` 映射用户名
  - 左侧表格：GPU 指标（利用率、显存进度条、进程数量）
  - 右侧饼图：按用户汇总的显存占用（MiB），并把系统/其他和空闲显存区分显示
- Runner（运行器）
  - 选择运行方式：主机 Conda 环境 或 Docker 容器（二者互斥）
  - 指定脚本、`--key=value` 风格参数、`KEY=VALUE` 环境变量
  - “Preview” 显示将要执行的每一条命令；支持逐条复制/运行
  - 预设：保存/加载常用配置，和 Console 的预设下拉联动
  - Docker 支持输入模板（例如 `isaac-lab-$(whoami)`），运行时在远端展开
- Console（交互式 SSH 终端）
  - 一键打开远端交互 Shell；支持多分屏（水平/垂直拆分）、关闭、清屏
  - 在 Console 页也能预览/逐条运行 Runner 生成的命令
- 登录与历史
  - 支持密码或密钥登录：
    - 填写密码时使用 Paramiko 建立长连接（推荐）
    - 不填密码则走系统 `ssh`（BatchMode），也可指定私钥
  - YAML 保存连接历史与 Runner 预设，可设为启动时自动连接
  - 本机运行：将 `Host` 设置为 `127.0.0.1` 即可在本机通过 SSH 运行与监控（需本机开启 sshd 并可登录）

## 运行
1. 安装依赖（建议创建虚拟环境）：
   ```bash
   pip install -r requirements.txt  # 需要 PyQt6、PyQt6-Charts、PyYAML；如需密码登录需 Paramiko（已包含）
   ```
2. 启动：
   ```bash
   python -m gpu_manager_gui.main
   ```
3. 图标（可选）：
   - 应用内置 SVG 图标：gpu_manager_gui/assets/icon.svg，已在启动时自动加载。
   - 如需自定义，可替换该文件或在 `gpu_manager_gui/main.py` 中修改图标路径。

> 需要：
> - 远程服务器已安装 `nvidia-smi`（NVIDIA 驱动）
> - 本机可用 `ssh` 命令，且能免交互登录目标主机（已在 `~/.ssh/config` 配好或已将公钥放到服务器）
> - 首次连接的主机指纹请先用终端 `ssh user@host` 手动确认一次（本工具以非交互模式连接）

## 使用说明
- 登录页：
  - `Profile` 下拉：历史连接（位于 `~/.isaaclab_gpu_manager/connections.yaml`）
  - 输入 `Host`（`server` 或 `user@server`）、`Port`、可选 `User`、`Identity`、`Password`、`Interval`
  - 可选勾选 `Remember password (insecure)`：明文可逆（base64）写入 YAML，仅为便捷；生产环境建议不勾选
  - 点击 Connect；连接验证通过后进入监控页。
- 监控页：仅显示 GPU 表格与用户饼图，右上角有 `Disconnect` 返回登录页。

## 说明与限制
- 轮询方式：默认使用系统 `ssh` 子进程；填写密码时改用 Paramiko 长连接。
- 容器名模板：Runner 在 Docker 模式下不对容器名加引号，以便远端 `bash -lc` 能展开 `$(whoami)` 等模板；因此请勿在容器名中加入空格/未转义符号。
- 用户显存饼图基于 `nvidia-smi --query-compute-apps` 并通过 `ps` 映射用户名；若进程瞬时退出或权限受限，可能显示为 `unknown`。
- “性能”当前展示为 `utilization.gpu`（GPU 核心利用率 %）。如需功耗、频率等指标可扩展查询字段。

## 后续可选增强
- 多服务器/多标签页管理
- 任务列表与一键终止（需要权限控制）
- 账号计费统计（需要启用 NVIDIA accounting）
- 自定义 `ssh` 选项（ProxyJump、跳板机等）
