# ChatGPT / Codex Quota Monitor

本地运行的 ChatGPT/Codex 额度曲线监控器。第一版只统计账号级额度，不做项目、设备或模型归因，也不接入其他项目目录。

## 运行

```bash
python3 app.py
```

打开 <http://127.0.0.1:5077>。立即采集一次可执行：

```bash
python3 app.py --collect
```

## macOS 菜单栏

先保持本地服务运行，再构建并启动菜单栏应用：

```bash
./menubar/build.command
open build/CodexQuotaMenuBar.app
```

菜单栏会显示当前最紧迫额度窗口的剩余百分比，例如 `86%`；点击后可查看各额度窗口、重置倒计时、刷新时间，并打开完整仪表盘。应用每 60 秒从本地服务刷新一次。

默认数据保存在当前项目的 `data/` 下。可用环境变量切换位置：

```bash
CHATGPT_QUOTA_DATA_DIR=/path/to/data python3 app.py
```

## 预测信号

如果未来需要接入“Codex 重置提醒”，在本地数据目录写入 `signals/reset-forecast.json`，包含 `reset_type`、`probability_24h` 和 `forecast_updated_at`；超过 6 小时的信号会自动失效。

## 安全边界

程序优先读取 macOS Keychain 的 `Codex Auth`，回退读取 `~/.codex/auth.json`。Access Token 只在采集进程内使用，不写入历史文件、前端接口或日志。
