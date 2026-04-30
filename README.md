# OpenLeaseOps

[中文](README.md) | [English](README.en.md)

OpenLeaseOps 是一个面向写字楼、园区、商业空间等租赁运营场景的轻量级合同管理系统。后端使用 `FastAPI + SQLite + Alembic`，前端使用 `Vue 3 + Vite`，默认通过单个 Docker Compose 服务部署。

项目默认不内置任何企业名称、楼塔、楼层、房间、合同、用户或 PDF 数据。首次启动后需要初始化第一个管理员账号。

## 功能概览

- 合同台账：合同 CRUD、分页、排序、筛选、Excel 导入导出、PDF 附件管理。
- 房间台账：房间 CRUD、办公/商业用途管理、楼栋、区域和楼层维度查看。
- 运营看板：出租率、在租面积、月租金、到期预警、状态分布、区域表现。
- 月度应收：按月份统计租金、物业费和合计金额。
- 系统工具：PDF 批量上传匹配、完整性检查、数据备份与恢复、用户管理、行为记录。
- 权限控制：管理员、编辑、只读三类角色。
- 加密存储：可选开启 PDF 文件和备份包加密。
- 集成接口：预留 JDE、OA、财务系统、SaaS、REST Webhook 和 MCP 接入层。

## 快速开始

### 1. 准备配置

复制示例配置：

```bash
cp .env.example .env
```

生成 `JWT_SECRET`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

把生成结果写入 `.env`：

```env
JWT_SECRET=替换成你生成的长随机字符串
```

如果需要加密 PDF 和备份，再生成 `DATA_ENCRYPTION_KEY`：

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

然后在 `.env` 中启用：

```env
DATA_ENCRYPTION_ENABLED=true
DATA_ENCRYPTION_KEY=替换成你生成的32字节base64密钥
```

### 2. Docker 部署

```bash
docker compose up -d --build
```

默认访问地址：

```text
http://127.0.0.1:8080
```

首次打开系统时，如果还没有用户，会进入管理员初始化流程。初始化只需要填写登录账号和密码。

## 个性化配置

所有通用部署参数都在 `.env` 中维护。改完 `.env` 后，一般执行下面命令重新构建和启动：

```bash
docker compose up -d --build
```

### 基础部署参数

| 变量 | 默认值 | 作用 | 如何定制 |
| --- | --- | --- | --- |
| `APP_PORT` | `8080` | 宿主机访问端口 | 改成空闲端口，例如 `APP_PORT=18080` |
| `CONTAINER_NAME` | `lease-manager` | Docker 容器名 | 同一台服务器部署多套时必须改名 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 应用时区 | 海外部署可改为对应 IANA 时区 |
| `DATA_DIR` | `./data` | 本地数据目录 | 通常保持默认；Docker 内固定挂载到 `/data` |
| `APP_VERSION` | `local` | 版本标识 | 可写入 git commit、发布号或公司内部版本号 |

### 品牌与界面

这些参数会在前端构建时生效，所以修改后需要重新执行 `docker compose up -d --build`。

| 变量 | 默认值 | 作用 | 如何定制 |
| --- | --- | --- | --- |
| `APP_TITLE` | `OpenLeaseOps` | 登录页、侧边栏和页面标题中的产品名 | 改成你的系统名称 |
| `APP_SUBTITLE` | `租赁运营后台` | 产品副标题 | 可改成公司内部系统说明 |
| `APP_MARK` | `LM` | 侧边栏文字标识 | 可改成公司简称或项目缩写 |

示例：

```env
APP_TITLE=企业租赁运营平台
APP_SUBTITLE=资产运营与合同管理
APP_MARK=LEASE
```

### 合同编号

| 变量 | 默认值 | 作用 | 如何定制 |
| --- | --- | --- | --- |
| `CONTRACT_NO_PREFIX` | `LEASE` | 自动生成合同编号的前缀 | 改成公司或项目缩写，例如 `OFFICE`、`PARK` |

系统不会预设楼栋、楼层或房间编号规则。楼栋、区域、楼层、房间类型都通过页面录入或 Excel 导入维护。

### 登录与安全

| 变量 | 默认值 | 作用 | 如何定制 |
| --- | --- | --- | --- |
| `JWT_SECRET` | 无 | 登录令牌签名密钥 | 必填，必须使用长随机字符串 |
| `TOKEN_EXPIRE_HOURS` | `24` | 登录有效期，单位小时 | 内网可适当放宽，公网建议更短 |
| `COOKIE_SECURE` | `false` | Cookie 是否仅 HTTPS 发送 | 正式 HTTPS 部署建议改为 `true` |

### 数据加密

| 变量 | 默认值 | 作用 | 如何定制 |
| --- | --- | --- | --- |
| `DATA_ENCRYPTION_ENABLED` | `false` | 是否加密 PDF 和备份包 | 生产环境建议开启 |
| `DATA_ENCRYPTION_KEY` | 空 | PDF 和备份加密密钥 | 开启加密时必填，必须长期妥善保存 |

开启加密后，新上传 PDF 会以加密文件落盘，新建备份会生成 `.zip.enc`。浏览器下载 PDF、恢复备份等业务操作保持不变。

密钥丢失后，已加密 PDF 和加密备份无法恢复。更多说明见 [docs/ENCRYPTION.md](docs/ENCRYPTION.md)。

## 数据目录

运行数据默认在 `data/`：

```text
data/
├── lease.db
├── lease.db-wal
├── lease.db-shm
├── pdfs/
└── backups/
```

仓库只保留空目录占位，不包含任何示例业务数据。公开发布前不要提交 `.env`、`data/lease.db`、PDF、备份包或任何真实合同文件。

## 权限说明

| 角色 | 权限 |
| --- | --- |
| `admin` | 用户管理、备份恢复、系统检查、行为记录、集成配置和全部业务操作 |
| `editor` | 合同、房间、PDF、导入导出等业务操作 |
| `viewer` | 首页、合同、房间和合同 PDF 的只读访问 |

## 集成接口

管理员可在“集成接口”页面配置连接器。当前可直接使用 `REST Webhook` 推送合同、房间、PDF 等关键事件；JDE、OA、财务系统、SaaS、MCP Server、MCP Client 和自定义连接器作为扩展点预留。

连接器 secret 不会回显明文。保存 API Key、Bearer Token 等密钥前必须启用 `DATA_ENCRYPTION_ENABLED=true`。更多说明见 [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)。

## 本地开发

安装后端依赖：

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

安装前端依赖并构建：

```bash
cd frontend
npm install
npm run build
```

执行迁移并启动：

```bash
python -m alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

运行测试：

```bash
python -m pytest -q
```

前端 E2E 测试默认关闭，避免误连正式环境。需要时：

```bash
python -m playwright install chromium
LEASE_E2E=1 python -m pytest tests/e2e -q
```

Windows PowerShell 可使用：

```powershell
$env:LEASE_E2E="1"
python -m pytest tests/e2e -q
```

## 开源版边界

- 默认不内置任何租户、合同、房间、楼栋或楼层样例数据。
- 默认使用 SQLite，适合单项目、小团队和轻量部署。
- PDF 和备份可加密；运行中的 SQLite 数据库仍是明文。如果需要数据库透明加密，应在部署层使用磁盘加密，或自行替换数据库方案。
- 真实 JDE、OA、财务系统、SaaS 和 MCP 协议需要按企业现场系统做适配，本项目只提供统一扩展框架。

## License

本项目采用 [MIT License](LICENSE)。
