# 集成接口预留

本项目提供一层通用集成框架，方便开源使用者后续接入 JDE、OA、财务系统、SaaS、REST Webhook 或 MCP。当前版本只内置通用 REST Webhook 推送；其他类型作为稳定扩展点保留，不包含任何厂商私有协议。

## 可选连接器

| 类型 | 当前状态 | 方向 |
| --- | --- | --- |
| JDE | 预留适配器 | 导入 / 推送 / 双向 |
| OA | 预留适配器 | 导入 / 推送 / 双向 |
| 财务系统 | 预留适配器 | 导入 / 推送 / 双向 |
| SaaS | 预留适配器 | 导入 / 推送 / 双向 |
| REST Webhook | 可用 | 推送 |
| MCP Server | 预留 | 后续可把系统服务包装为 MCP tools |
| MCP Client | 预留适配器 | 后续可调用外部 MCP 服务 |
| 自定义 | 预留适配器 | 导入 / 推送 / 双向 |

## REST Webhook

管理员在“集成接口”页面创建 `REST Webhook` 连接器后，合同、房间、PDF 等关键操作会先写入 Outbox，再由后台任务异步推送，避免外部系统故障影响主业务保存。

示例事件：

```json
{
  "id": 1,
  "event_type": "contract.created",
  "resource_type": "contract",
  "resource_id": 1001,
  "payload": {
    "contract_no": "LEASE-2026-001",
    "room_no": "A-1201",
    "tenant_name": "Example Tenant"
  },
  "created_at": "2026-04-30T10:00:00"
}
```

## Secret 处理

连接器密钥不会在 API 或前端列表中回显明文。保存 `bearer_token`、`api_key`、`client_secret` 等密钥前，必须启用：

```env
DATA_ENCRYPTION_ENABLED=true
DATA_ENCRYPTION_KEY=<32字节base64密钥>
```

如果未启用数据加密，后端会拒绝保存 secret。请长期保存 `DATA_ENCRYPTION_KEY`；丢失后无法解密已保存的连接器密钥、加密 PDF 和加密备份。

## 扩展适配器

新增厂商适配器时，建议只扩展 `app/integrations/adapters.py`：

```python
class MyAdapter(IntegrationAdapter):
    def test_connection(self, config, secret):
        ...

    def push_event(self, config, secret, event):
        ...

    def sync(self, config, secret, direction):
        ...
```

然后在 `adapter_for(provider)` 中返回该适配器。不要在合同或房间保存流程里同步调用外部系统；统一通过 Outbox 推送。
