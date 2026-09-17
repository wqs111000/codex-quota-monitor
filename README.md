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

采集器会在成功采样时比较前后额度窗口：重置时间向后移动超过 60 秒，或剩余额度回升超过 20 个百分点时，先将事件写入本地 `data/signals/bark-state.json` 待发队列，再更新采样历史。独立通知线程负责发送，网页关闭、下一次额度采样没有变化或额度接口暂时失败，都不会取消已经入队的事件。首次采样只建立比较基线，不补推配置 Bark 之前的事件。

失败后按指数退避重试，起始间隔沿用 `CHATGPT_QUOTA_RETRY_SECONDS`（默认 30 秒，受采集器现有上下限约束），最长退避 1 小时；线程按重试检查间隔检查，到期后才会再次投递。待发事件、重试时间和最近 100 个已确认事件都持久化，服务重启后会继续处理，旧版仅有 `notified` 的文件可直接读取。状态文件采用原子替换；如果无法安全保存事件，不会推进对应采样的历史检查点，以便恢复后重新检测，并通过采集器错误状态提示。

只有 HTTP 成功且响应 JSON 的 `code` 为 `200`，才会从待发队列移除事件；自建 Bark 服务也需要返回这一确认格式。推送包含原始采样时间，便于识别延迟到达的提醒。通知正文、历史记录和状态接口均不包含 ChatGPT OAuth Token；状态接口也不返回 Bark Key、服务地址或原始上游错误内容。

### 查看推送状态

`GET /api/status` 的 `notifications.bark` 返回是否配置、待发数量、未确认投递的尝试次数、最近尝试/确认时间、下次重试时间和脱敏错误：

```bash
curl -s http://127.0.0.1:5077/api/status | python3 -c \
  'import json,sys; print(json.dumps(json.load(sys.stdin)["notifications"]["bark"], ensure_ascii=False, indent=2))'
```

`pending_count` 大于 0 表示仍有待处理事件；状态文件无法读取时该值为 `null`，请检查 `last_error`，不要将其当作空队列。`last_success_at` 仅表示 Bark 返回了成功确认，并非手机展示或用户阅读回执。测试接口 `/api/notify/test` 仍是立即尝试一次，不进入自动重试队列，也不会清除真正的重置事件。

### 投递边界

正常收到确认并保存状态后，同一事件不会重复发送。但如果服务端已接收请求、本地却超时，或进程在保存成功状态前退出，重试可能产生重复通知；这里提供的是“至少一次尝试投递”，不是严格的 exactly-once 或手机必达保证。检测仍沿用额度变化启发式，不等于确认官方全局重置。

持续重试需要监控服务保持运行；`--collect` 会尝试一次到期投递并退出，未成功的事件留给后续调用或常驻服务。每个数据目录仅支持一个监控进程；不要让多个实例共享同一个待发状态文件。

## 安全边界

程序优先读取 macOS Keychain 的 `Codex Auth`，回退读取 `~/.codex/auth.json`。Access Token 只在采集进程内使用，不写入历史文件、前端接口或日志。

## 测试

无需额外安装依赖：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app.py tests/test_notifications.py
```

测试使用临时数据目录、模拟凭据和本机 HTTP 服务，不读取真实 OAuth 登录态，也不会向真实 Bark 设备发送通知。覆盖重置检测、失败补发、重启恢复、指数退避、并发去重、原子写入失败、旧状态兼容、推送状态接口和测试推送接口。
