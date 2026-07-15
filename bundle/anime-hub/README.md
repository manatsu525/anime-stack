# Anime Hub

自用动漫资源整合面板：聚合 **动漫花园 / ACG.RIP / Nyaa** 搜索与近期更新，一键抓磁力/种子并通过 **aria2** 下载到 `/home/share`。

## 功能

- 三站并行搜索，结果统一列表展示
- 首页默认展示近期更新
- 点击条目直接添加 BT 下载
- 粘贴磁力链 / torrent 链接添加任务
- 下载任务管理：进度、暂停/继续、删除、清理已完成
- 无 TLS，纯 HTTP 自用

## 端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Web UI + API | **8765** | 浏览器访问 |
| aria2 JSON-RPC | **6800** | 仅本机，带 token |

## 启动

```bash
cd /home/share/anime-hub
./start.sh start      # 启动
./start.sh status     # 状态
./start.sh stop       # 停止
./start.sh restart    # 重启
```

浏览器打开：`http://<服务器IP>:8765`

下载文件目录：`/home/share`（与 filebrowser 共用）

## 配置

环境变量（可选，在启动前 export）：

- `DOWNLOAD_DIR` 默认 `/home/share`
- `WEB_PORT` 默认 `8765`
- `ARIA2_RPC_PORT` 默认 `6800`
- `ARIA2_RPC_SECRET` 默认 `animehub`

## API 速览

- `GET /api/search?q=关键词&source=dmhy,acgrip,nyaa`
- `GET /api/downloads`
- `POST /api/downloads` JSON: `{"uri":"magnet:?xt=..."}`
- `POST /api/downloads/{gid}/pause|resume`
- `DELETE /api/downloads/{gid}`

## 日志

- `logs/web.log` — Web 服务
- `logs/aria2.log` — aria2
