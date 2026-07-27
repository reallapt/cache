# cache

[English](#english) | [中文](#中文)

## 中文

`cache` 是一个极简、自托管的 24 小时临时文件空间。上传的文件、文件夹、图片、视频和文字会保存在自由画布上，到期后自动删除。

### 功能

- 文件、多个文件与文件夹上传，支持拖放。
- `Ctrl+V` 可粘贴文件、截图；纯文字会保存为文本文件。
- 图片、视频和文字直接预览；文字可原生选中与复制。
- 文件夹、压缩包和普通文件使用图标展示；普通文件图标显示扩展名前三位，并使用稳定的随机颜色。
- 内容块可自由拖动、调整大小和层级；桌面与移动端布局分别保存。
- 单击下载；文件夹下载时自动打包为 ZIP。
- 每个内容从创建起保留 24 小时，显示实时倒计时；少于一小时变红，到期后自动删除。
- 移动端和桌面端响应式界面。

### 安装

需要 Docker 与 Docker Compose。

```bash
git clone https://github.com/reallapt/cache.git
cd cache
docker compose up -d --build
```

默认打开 <http://localhost:9178>。内容保存在项目目录的 `data/` 中；即使容器重建，布局和倒计时元数据也会保留。

### 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `2048` | 单次上传请求最大大小，单位 MB。 |

如部署在 Nginx、Caddy 或其他反向代理之后，请同步提高代理的请求体限制。`cache` 默认没有身份验证，若服务可被不受信任的网络访问，请在反向代理或防火墙中限制访问。

## English

`cache` is a minimal self-hosted 24-hour temporary file space. Files, folders, images, videos, and notes live on a freeform canvas and are removed automatically when they expire.

### Features

- Upload files, multiple files, and folders with drag and drop.
- Paste files or screenshots with `Ctrl+V`; plain text is saved as a text file.
- Preview images, videos, and text directly. Text can be selected and copied natively.
- Folder, archive, and generic file icons. Generic icons use the first three extension characters and a stable random color.
- Drag, resize, and layer every item freely. Desktop and mobile layouts are saved separately.
- Click to download. Folders are downloaded as ZIP archives automatically.
- Every item has a 24-hour lifetime with a live countdown. It turns red during the final hour and is deleted automatically on expiry.
- Responsive desktop and mobile interface.

### Install

Docker and Docker Compose are required.

```bash
git clone https://github.com/reallapt/cache.git
cd cache
docker compose up -d --build
```

Open <http://localhost:9178>. Content is stored in the local `data/` directory, so it survives container rebuilds together with layout and expiry metadata.

### Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `2048` | Maximum upload request size in MB. |

If you deploy behind Nginx, Caddy, or another reverse proxy, raise the proxy request-body limit as well. `cache` has no built-in authentication. Restrict access through a firewall or reverse proxy before exposing it to an untrusted network.
