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

要让菜单栏工具和本地服务保持联动，并在登录 macOS 后自动启动：

```bash
./menubar/install.command
```

菜单栏工具启动时会拉起本地服务；菜单栏工具正常退出时会关闭它自己启动的服务。服务异常退出时会自动重启。

默认数据保存在当前项目的 `data/` 下。可用环境变量切换位置：

```bash
CHATGPT_QUOTA_DATA_DIR=/path/to/data python3 app.py
```

## 预测信号

仪表盘的“重置预测”面板支持手动输入预计重置时间和未来 24 小时概率，保存后会写入本地 `signals/reset-forecast.json`，并立即影响推荐额度。也可以由外部提醒脚本按同样格式写入：

```json
{
  "reset_type": "global_hard_reset",
  "probability_24h": 0.75,
  "forecast_reset_at": "2026-08-29T07:30:00.000Z",
  "forecast_updated_at": "2026-08-26T12:00:00+00:00"
}
```

预测信号超过 6 小时会自动失效；清除按钮会删除当前手动预测。

## 安全边界

程序优先读取 macOS Keychain 的 `Codex Auth`，回退读取 `~/.codex/auth.json`。Access Token 只在采集进程内使用，不写入历史文件、前端接口或日志。
