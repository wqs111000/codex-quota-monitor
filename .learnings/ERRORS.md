
## [ERR-20260831-001] quota-api-refresh

**Observed**: 长期运行的本地采样服务偶发显示“额度接口暂时不可用”，但同一用户会话中新进程重试可以成功。

**Cause**: 单次请求失败后按完整采样周期等待，且错误信息过于笼统，导致短暂网络或代理波动长时间停留在界面上。

**Fix**: 增加请求级重试与退避；失败后缩短后台重试周期；区分认证、限流、服务端、网络和响应解析错误。

## [ERR-20260831-002] launchagent-bootstrap

**Observed**: 安装脚本中的 `launchctl bootstrap` 偶发返回 macOS `Input/output error`；按当前用户会话手动重新加载同一 plist 可以成功。

**Resolution**: 保留现有安装流程，并在验证或故障恢复时使用当前 GUI 用户会话重新加载 LaunchAgent。

## [ERR-20260831-003] process-inspection-permission

**Observed**: 受限执行环境中的进程检查被 macOS 返回 `operation not permitted`。

**Resolution**: 使用服务状态、监听端口和接口响应进行验证；不将该权限限制误判为 Quota 服务故障。

## [ERR-20260902-001] official-docs-search

**Observed**: 首次调用官方文档搜索时因工具调用参数的 JavaScript 字符串转义错误失败。

**Resolution**: 改用安全的字符串写法重新搜索，随后成功读取官方 Sites 与 API 认证文档。

## [ERR-20260908-001] browser-visual-check

**Observed**: 通过浏览器获取本地额度页面的无障碍树和截图时超时，浏览器测试会话被重置。

**Resolution**: 改用本地 JavaScript 语法检查、隔离数据段测试和服务接口验证；不将测试工具超时误判为页面功能失败。

## [ERR-20260908-002] chart-test-fixture

**Observed**: 首次隔离曲线测试的模拟 DOM 未包含页面按钮节点，导致测试脚本在事件绑定阶段报错。

**Resolution**: 补齐通用模拟节点后重跑测试；不修改产品代码来适配测试夹具。
