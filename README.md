# ChatGPT / Codex Quota Monitor

本地运行的 ChatGPT/Codex 额度曲线监控器。第一版只统计账号级额度，不做项目、设备或模型归因，也不接入其他项目目录。

## 运行

```bash
python3 app.py
```

打开 <http://127.0.0.1:5077>；同一局域网内的其他设备可访问 `http://192.168.31.100:5077`。立即采集一次可执行：

```bash
python3 app.py --collect
```

## macOS 菜单栏

先保持本地服务运行，再构建并启动菜单栏应用：

```bash
./menubar/build.command
open build/Quota.app
```

菜单栏会显示当前最紧迫额度窗口的剩余百分比，例如 `86%`；点击后可查看各额度窗口、重置倒计时、刷新时间，并打开完整仪表盘。应用每 60 秒从本地服务刷新一次。

要让菜单栏工具和本地服务保持联动，并在登录 macOS 后自动启动：

```bash
./menubar/install.command
```

菜单栏工具启动时会拉起本地服务；菜单栏工具正常退出时会关闭它自己启动的服务。服务异常退出时会自动重启。为兼容 macOS 的后台权限，运行时副本安装在 `~/Library/Application Support/Quota`，源码仍保留在项目目录。

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

## 手机重置通知

网页顶部的“开启提醒”会申请浏览器通知权限。仪表盘轮询时，如果检测到额度窗口的重置时间向后跳变或剩余额度明显回升，就会在当前设备发送一次“额度已重置”通知；通知状态和去重快照只保存在浏览器本地。

手机访问需要 HTTPS；iPhone 还需要先将网页添加到主屏幕，再从主屏幕打开并允许通知。当前版本依赖页面轮询，页面完全退出或系统挂起时不会后台推送；要实现真正的后台提醒，需要额外接入 Web Push 和一个受保护的推送服务。

## Bark 手机推送（最简方案）

可以使用 Bark 接收 iPhone 通知，不需要自建 NAS 服务。先在 iPhone 安装 Bark，复制 App 中显示的 Device Key。Bark 支持通过 HTTPS GET/POST 推送，也支持标题、分组和时效性通知。[Bark 官方文档](https://github.com/Finb/Bark)

```bash
CHATGPT_QUOTA_BARK_KEY=你的Bark_Key python3 app.py
```

如果通过菜单栏 App 启动服务，可在运行时目录 `~/Library/Application Support/Quota/notification.env` 写入同样的配置：

```bash
CHATGPT_QUOTA_BARK_KEY=你的Bark_Key
```

这个文件只保存在本机，不会被提交到 GitHub。

也可以设置 `CHATGPT_QUOTA_BARK_URL` 指向自建 Bark 服务。启动后可测试通知：

```bash
curl -X POST http://127.0.0.1:5077/api/notify/test
```

采集器会在成功采样时比较前后额度窗口：检测到重置时间向后移动或剩余额度明显回升后，只发送一次通知，并将去重状态保存在本地 `data/signals/bark-state.json`。通知内容不包含 ChatGPT OAuth Token。

## 安全边界

程序优先读取 macOS Keychain 的 `Codex Auth`，回退读取 `~/.codex/auth.json`。Access Token 只在采集进程内使用，不写入历史文件、前端接口或日志。
