# UI Prototype Manager v0.4.0

极简、本地可部署的交互 UI 页面管理器。使用 **uv + FastAPI + SQLite**，支持 HTML / 图片页面、页面跳转与真实返回交互、本地/S3资源存储和密钥访问控制。

## 功能

- 项目创建 / 删除
- HTML、PNG/JPG/WebP/GIF 单个或批量上传
- 每次上传可选择 **本地** 或 **S3-compatible** 存储
- 上传前可逐个修改 HTML / 图片名称，创建后可继续重命名
- 同一项目内 **HTML + 图片名称唯一**，大小写不敏感
- HTML：点击 DOM 元素创建交互
- 图片：拖拽框选 Hotspot 创建交互
- 每个交互可选择 **跳转到指定页面** 或 **返回上一页**
- “返回上一页”与 Preview 顶部“← 返回”调用同一访问历史栈，不绑定固定页面
- 每条交互创建时必须命名，创建后可继续重命名
- 同一项目内 **交互名称单独唯一**，大小写不敏感
- 删除页面时自动清理所有相关交互，并清理底层资源
- Preview 默认“返回”按钮
- Preview 左侧可折叠/展开全部页面，点击直接跳转
- **全站密钥登录**：通过后浏览器保存 HttpOnly Token Cookie 24 小时
- SQLite 元数据 + 本地持久化目录

> v0.4.0 不提供旧数据兼容迁移。请直接删除旧 `data/` 后重新启动。

## 1. 本地启动

要求：Python 3.11+、uv。

### 方式 A：在项目目录直接运行

```bash
export UIPM_ACCESS_KEY='your-secret-key'
uv run python -m app.main
```

默认持久化到**执行命令时当前目录**：

```text
./data/
├── app.db
└── assets/
```

### 方式 B：使用 start.sh

`start.sh` 同样以**调用脚本时所在目录**作为默认持久化目录，而不是脚本自身目录。

```bash
mkdir -p ~/uipm-runtime
cd ~/uipm-runtime
UIPM_ACCESS_KEY='your-secret-key' /path/to/ui-prototype-manager/start.sh
```

数据会写入：

```text
~/uipm-runtime/data/
```

需要显式指定时：

```bash
UIPM_ACCESS_KEY='your-secret-key' \
UIPM_DATA_DIR=/srv/uipm/data \
uv run python -m app.main
```

访问：

```text
http://localhost:8080
```

`UIPM_ACCESS_KEY` 为必填；未设置时服务不会启动。

## 2. 密钥登录

```bash
export UIPM_ACCESS_KEY='your-secret-key'
```

除登录页和静态资源外，业务页面/API 都要求有效 Token。

登录流程：

1. 输入访问密钥；
2. 服务端校验密钥并生成 HMAC 签名 Token；
3. Token 写入 `HttpOnly` Cookie；
4. Cookie `Max-Age=86400`，最长有效 24 小时；
5. Token 过期、签名错误或服务端密钥更换后，需要重新登录。

HTTPS 部署建议：

```bash
export UIPM_COOKIE_SECURE=true
```

## 3. Docker 部署

```bash
cp .env.example .env
```

至少修改：

```dotenv
UIPM_ACCESS_KEY=your-secret-key
```

然后：

```bash
docker compose up -d --build
```

默认宿主机持久化：

```text
./data/
```

Compose 映射：

```text
./data  →  /data
```

可以在 `.env` 中改成任意持久化目录：

```dotenv
UIPM_HOST_DATA_DIR=/srv/uipm/data
```

不用 Compose：

```bash
docker build -t uipm .
docker run --rm -p 8080:8080 \
  -e UIPM_ACCESS_KEY='your-secret-key' \
  -v "$PWD/data:/data" \
  uipm
```

## 4. S3-compatible 存储

不配置 S3 时，上传界面只提供“本地”。配置后，同一项目可混用本地和 S3。

```bash
export UIPM_S3_BUCKET=my-bucket
export UIPM_S3_REGION=us-east-1
export UIPM_S3_ACCESS_KEY_ID=xxx
export UIPM_S3_SECRET_ACCESS_KEY=xxx

# AWS S3 可不配置 endpoint；MinIO / R2 等按实际填写
export UIPM_S3_ENDPOINT_URL=http://127.0.0.1:9000
export UIPM_S3_PREFIX=uipm
export UIPM_S3_ADDRESSING_STYLE=path
```

对象 Key 使用稳定 UUID：

```text
<prefix>/<project-id>/<page-id>.html
<prefix>/<project-id>/<page-id>.png
```

页面重命名不会改变底层 Object Key。

## 5. 唯一命名规则

### 页面名称空间

同一个项目内，HTML 和图片共用一个名称空间：

```text
首页      ✅
企业详情  ✅
首页      ❌
home 与 HOME 视为重复
```

批量上传时，在确认窗口可以逐个修改名称；前端会先检查，SQLite 唯一索引再次兜底。

### 交互名称空间

交互使用独立名称空间：

```text
查看企业详情  ✅
返回首页      ✅
查看企业详情  ❌
```

页面名称与交互名称可以相同，因为两者分开校验。

## 6. 使用流程

1. 输入密钥登录。
2. 新建项目。
3. 选择上传存储位置：本地 / S3。
4. 选择一个或多个 HTML / 图片。
5. 在上传确认窗口为每个页面设置唯一名称。
6. HTML：点击元素；图片：拖拽框选区域。
7. 输入唯一交互名称，并选择动作：
   - **跳转到指定页面**：选择目标页面；
   - **返回上一页**：不选择目标页面，运行时使用真实访问历史。
8. 页面与每条交互均可通过 `✎` 重命名。
9. 点击“预览”。
10. 左侧页面列表可折叠，点击页面会进入访问历史；顶部“← 返回”和页面内“返回上一页”交互完全一致。

## 7. 返回交互语义

例如预览访问顺序为：

```text
首页 → 列表 → 详情
```

在“详情”页点击绑定为“返回上一页”的 HTML 元素或图片 Hotspot：

```text
详情 → 列表
```

再次触发返回：

```text
列表 → 首页
```

如果通过 Preview 左侧页面列表从“首页”直接进入“详情”，访问历史是 `首页 → 详情`，因此页面内返回区域会真实回到“首页”，而不是回到某个预先写死的页面。首次直接打开某页且没有上一页历史时，返回动作与顶部返回按钮一致，不执行跳转。

## 8. 数据结构

```text
Project
 ├─ Page
 │   ├─ name              项目内页面唯一
 │   ├─ type              html / image
 │   ├─ storage_backend   local / s3
 │   └─ storage_key
 └─ Interaction
     ├─ name              项目内交互唯一
     ├─ source_page_id
     ├─ action            navigate / back
     ├─ target_page_id    navigate 时必填，back 时为空
     ├─ kind              element / region
     └─ payload
```

删除 Page 时，SQLite `ON DELETE CASCADE` 会删除从该页面发出的所有 Interaction，以及以该页面为目标的 `navigate` Interaction；其他页面上的 `back` Interaction 因不依赖固定目标页而不会受影响。

## 9. 当前边界

当前版本定位为可信本地/内网工具，暂不实现 HTML/CSS/JS 编辑、组件拖拽、页面排序、多人账号、复杂权限、版本控制、条件跳转、动画转场、跨项目跳转、S3 Bucket 自动创建。

上传 HTML 使用 `sandbox="allow-scripts"` iframe。如果未来允许不可信外部用户上传 HTML，建议增加独立资源域、HTML 清洗和更严格 CSP。
