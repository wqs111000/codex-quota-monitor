
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
