# 数据加密配置说明

本系统支持对合同 PDF、未匹配 PDF 和备份包进行应用层加密。运行中的 SQLite 数据库仍保持明文，以保证筛选、排序、统计和 Excel 导出可正常工作。

## 环境变量

```env
DATA_ENCRYPTION_ENABLED=true
DATA_ENCRYPTION_KEY=替换为32字节base64密钥
```

生成密钥：

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## 行为说明

- `DATA_ENCRYPTION_ENABLED=true` 时，`DATA_ENCRYPTION_KEY` 必须配置，格式错误会导致应用启动失败。
- 新上传 PDF 会以 `.enc` 文件落盘，浏览器下载时仍返回正常 PDF。
- 新建备份会生成 `.zip.enc`，不能直接 unzip 读取。
- 历史 `.zip` 备份仍可恢复。
- 开启加密后，应用启动会自动扫描旧明文 PDF 并迁移为加密文件。

## 密钥风险

密钥不能提交到 Git，也不要写进日志或截图。密钥丢失后，已加密 PDF 和 `.zip.enc` 备份无法恢复。

这套方案主要降低共享目录误暴露、备份包泄露、普通文件系统访问带来的风险。它不能防拥有服务器 root 权限且能读取容器环境变量的人。
