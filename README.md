# UI Prototype Manager v0.5.0

极简、本地可部署的交互 UI 页面管理器。使用 **uv + FastAPI + SQLite**，支持 HTML / 图片页面、页面跳转与真实返回交互、本地/S3资源存储和密钥访问控制。

## 功能

- 项目创建 / 删除
- HTML、ZIP 页面包、PNG/JPG/WebP/GIF 单个或批量上传
- 每次上传可选择 **本地** 或 **S3-compatible** 存储
- S3/OSS 图片、音视频通过私有对象的短期预签名 URL 由浏览器直读；HTML/CSS/JS 由应用返回以保持相对路径语义
- 上传前可逐个修改 HTML / 图片名称，创建后可继续重命名
- 同一项目内 **HTML + 图片名称唯一**，大小写不敏感
- HTML：点击 DOM 元素创建交互
- HTML 显示模式：默认自动识别固定画布，支持响应式 / 指定设计尺寸；固定画布在编辑器和 Preview 中等比完整显示
- 图片：拖拽框选 Hotspot 创建交互
- HTML 元素与图片 Hotspot 会在编辑器画布中常驻显示区域和交互名称
- 画布标注与右侧交互列表双向选择、高亮和定位，右侧可直接编辑完整交互配置
- 每个交互可选择 **跳转到指定页面**、**跳转到外部网页** 或 **返回上一页**
- 外部网页在 Preview 当前页面的全屏 iframe 中打开；右上角提供默认收起、悬停展开的返回控件
- “返回上一页”与 Preview 顶部“← 返回”调用同一访问历史栈，不绑定固定页面
- 每条交互创建时必须命名，创建后可继续重命名
- 同一项目内 **交互名称单独唯一**，大小写不敏感
- 删除页面时自动清理所有相关交互，并清理底层资源
- Preview 默认“返回”按钮
- Preview 左侧可折叠/展开全部页面，点击直接跳转
- **全站密钥登录**：通过后浏览器保存 HttpOnly Token Cookie 24 小时
- SQLite 元数据 + 本地持久化目录

> v0.5.0 不提供旧数据兼容迁移。请直接删除旧 `data/` 后重新启动。

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

## 4. S3-compatible / 阿里云 OSS 存储

不配置对象存储时，上传界面只提供“本地”。配置后，同一项目可混用本地和对象存储页面。

对象存储页面由项目 API 返回短期预签名 URL，浏览器直接从存储服务读取 HTML/图片；应用服务器不再中转资源内容。签名有效期内，同一对象复用同一 URL 以命中浏览器缓存，并在到期前自动刷新。HTML 在上传时完成一次性交互脚本注入，S3/OSS 中保存的就是最终可展示文件。

### 通用 S3-compatible

```bash
export UIPM_S3_PROVIDER=s3
export UIPM_S3_BUCKET=my-bucket
export UIPM_S3_REGION=us-east-1
export UIPM_S3_ACCESS_KEY_ID=xxx
export UIPM_S3_SECRET_ACCESS_KEY=xxx

# AWS S3 可不配置 endpoint；MinIO / R2 等按实际填写。
export UIPM_S3_ENDPOINT_URL=http://minio:9000
# 必须是用户浏览器可以访问的地址；与内部地址相同时可以不填。
export UIPM_S3_BROWSER_ENDPOINT_URL=https://objects.example.com
export UIPM_S3_PREFIX=uipm
export UIPM_S3_ADDRESSING_STYLE=auto
export UIPM_S3_SIGNATURE_VERSION=s3v4
export UIPM_S3_DIRECT_READ=true
export UIPM_S3_PRESIGN_TTL_SECONDS=3600
```

关闭 `UIPM_S3_DIRECT_READ` 后会回退为应用服务器读取对象并返回，可用于临时排障。

### 阿里云 OSS（当前部署）

OSS 使用原生 Python SDK 和 V4 签名。`cn-chengdu` 会自动推导公网 Endpoint `https://oss-cn-chengdu.aliyuncs.com`，无需配置 `UIPM_S3_ENDPOINT_URL` 或 `UIPM_S3_BROWSER_ENDPOINT_URL`：

```bash
export UIPM_S3_PROVIDER=oss
export UIPM_S3_BUCKET=uipm
export UIPM_S3_REGION=cn-chengdu
export UIPM_S3_ACCESS_KEY_ID=xxx
export UIPM_S3_SECRET_ACCESS_KEY=xxx
```

浏览器直读 OSS 还需要一个绑定到 `uipm` Bucket、已配置 HTTPS 证书的自定义域名：

```bash
export UIPM_OSS_CNAME=https://prototype.example.com
```

`UIPM_OSS_CNAME` 不是一个单独服务，不需要额外部署程序。它只是一个 DNS CNAME：把域名指向 `uipm.oss-cn-chengdu.aliyuncs.com`，再到 OSS 控制台完成域名绑定和 HTTPS 证书配置。成都属于中国内地地域，因此该域名还需要完成 ICP 备案。

这里把自定义域名作为 OSS 直读的必填项：阿里云默认 Bucket 域名会强制下载文件，无法可靠地在 iframe 中展示 HTML；2025 年 3 月 20 日起，中国内地新 OSS 用户通过默认公网 Endpoint 访问数据 API 还会收到 `PublicEndpointForbidden`。参见阿里云官方的[自定义域名说明](https://www.alibabacloud.com/help/en/oss/user-guide/access-buckets-via-custom-domain-names)和[访问域名与网络说明](https://www.alibabacloud.com/help/en/oss/user-guide/access-and-network-overview)。缺少 `UIPM_OSS_CNAME` 时应用会在启动阶段给出明确配置错误，避免生成实际不可用的直读 URL。

如果应用服务器也部署在阿里云成都地域，可选用内网 Endpoint 来节省上传流量；浏览器仍通过公网自定义域名读取：

```bash
export UIPM_S3_ENDPOINT_URL=https://oss-cn-chengdu-internal.aliyuncs.com
```

`UIPM_S3_PREFIX`、`UIPM_S3_DIRECT_READ`、`UIPM_S3_PRESIGN_TTL_SECONDS` 等均已有合理默认值，一般无需配置。`UIPM_S3_BROWSER_ENDPOINT_URL` 只保留给 MinIO、R2 等通用 S3-compatible 服务，OSS 不需要使用。

对象 Key 使用稳定 UUID 和页面内相对路径：

```text
<prefix>/<project-id>/<page-id>/index.html
<prefix>/<project-id>/<page-id>/css/app.css
<prefix>/<project-id>/<page-id>/images/logo.png
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
   - HTML 显示配置已有默认值：自动模式、1920×1080，通常无需修改。
   - 普通响应式网页可显式选择“响应式页面”；其他固定尺寸页面可填写自己的设计宽高。
6. HTML：点击元素；图片：拖拽框选区域。已保存交互会在画布中显示名称和区域。
7. 点击画布标注或右侧交互条目可以双向高亮；在右侧输入唯一交互名称，并选择动作：
   - **跳转到指定页面**：选择目标页面；
   - **跳转到外部网页**：输入绝对的 HTTP(S) 链接；
   - **返回上一页**：不选择目标页面，运行时使用真实访问历史。
8. 页面可通过 `✎` 重命名；交互可在右侧配置区修改名称、动作和目标页面。
9. 点击“预览”。
10. 左侧页面列表可折叠，点击页面会进入访问历史；顶部“← 返回”和页面内“返回上一页”交互完全一致。
11. 外部网页会覆盖当前 Preview；鼠标移到右上角边缘的返回图标后可展开并返回原型。外部网站禁止 iframe 或加载失败时，仍可正常返回。

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
 │   ├─ storage_prefix
 │   ├─ entry_path
 │   └─ PageAsset[]       相对路径、媒体类型、文件大小
 └─ Interaction
     ├─ name              项目内交互唯一
     ├─ source_page_id
     ├─ action            navigate / external / back
     ├─ target_page_id    navigate 时必填，其他动作为空
     ├─ target_url        external 时必填，其他动作为空
     ├─ kind              element / region
     └─ payload
```

删除 Page 时，SQLite `ON DELETE CASCADE` 会删除从该页面发出的所有 Interaction，以及以该页面为目标的 `navigate` Interaction；其他页面上的 `back` 和 `external` Interaction 因不依赖固定目标页而不会受影响。

## 9. 当前边界

ZIP 页面包必须在根目录或唯一的单层包装目录中包含 `index.html`，且一个 ZIP 只包含一个 HTML 文件；包内相对 CSS/JS/图片/字体/音视频资源会保留目录结构。暂不支持以 `/` 开头的站点根路径、多 HTML 文档页面包、HTML/CSS/JS 编辑、组件拖拽、页面排序、多人账号、复杂权限、版本控制、条件跳转、动画转场、跨项目跳转、S3 Bucket 自动创建。

上传 HTML 使用 `sandbox="allow-scripts"` iframe。如果未来允许不可信外部用户上传 HTML，建议增加独立资源域、HTML 清洗和更严格 CSP。外部网页是否能在 Preview 中显示由目标站点的 `X-Frame-Options`、CSP `frame-ancestors` 等安全策略决定；被阻止时应用不会绕过目标站点限制。
