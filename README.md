# Anime Stack

一键部署 **File Browser**（文件管理）+ **Anime Hub**（动漫花园 / ACG.RIP / Nyaa 聚合搜索 + aria2 BT 下载）。

仓库：https://github.com/manatsu525/anime-stack

## 目录结构

```
anime-stack/
├── install.sh          # 安装 / 卸载 / 启停 脚本
├── README.md
└── bundle/
    ├── anime-hub/      # Anime Hub 应用源码
    ├── systemd/        # systemd 单元
    └── anime-hub.env.example
```

## 新服务器部署

### 方式一：clone 本仓库（推荐）

```bash
git clone https://github.com/manatsu525/anime-stack.git
cd anime-stack
chmod +x install.sh
sudo ./install.sh install
```

### 方式二：只下载打包源码（不需要 git）

```bash
curl -fsSL https://github.com/manatsu525/anime-stack/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1

chmod +x install.sh
sudo ./install.sh install
```

装到指定目录示例：

```bash
mkdir -p /root/anime-stack && cd /root/anime-stack
curl -fsSL https://github.com/manatsu525/anime-stack/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1
chmod +x install.sh && sudo ./install.sh install
```

也可用 scp 把本目录拷到服务器任意位置后再执行 `./install.sh install`。

脚本会自动：

- 安装缺失依赖（python3、venv、pip、aria2、curl 等）
- 下载安装 File Browser
- 部署 Anime Hub 到 `/opt/anime-hub`（venv + 依赖）
- 配置 systemd 开机自启
- 启动全部服务

## 卸载

```bash
sudo ./install.sh uninstall           # 卸服务与程序，保留 /home/share 下载数据
sudo ./install.sh uninstall --purge   # 连下载数据一起清（保留本安装包目录）
```

## 运维

```bash
sudo ./install.sh status
sudo ./install.sh restart
sudo ./install.sh stop
sudo ./install.sh start
```

或直接：

```bash
systemctl status filebrowser anime-hub anime-hub-aria2
journalctl -u anime-hub -f
```

## 默认端口

| 服务 | 端口 | 说明 |
|------|------|------|
| File Browser | 8080 | 文件管理，根目录 `/home/share` |
| Anime Hub | 8765 | 搜索 + 下载管理 Web UI |
| aria2 RPC | 6800 | 仅本机 |

均无 TLS，适合自用。

## 可选环境变量

安装前可 export：

```bash
export SHARE_DIR=/home/share
export APP_DIR=/opt/anime-hub
export WEB_PORT=8765
export FILEBROWSER_PORT=8080
export FILEBROWSER_USER=admin
export FILEBROWSER_PASSWORD='your-strong-password'   # 不设则自动生成
export ARIA2_RPC_SECRET=animehub

sudo -E ./install.sh install
```

凭据写入：`/root/anime-stack-credentials.txt`

## 访问

- File Browser: `http://服务器IP:8080`
- Anime Hub: `http://服务器IP:8765`
- 下载文件目录: `/home/share`（两个服务共用）

## 下载能力

- BT / 磁力 / `.torrent` 链接（支持种子内**部分文件选择**）
- 普通 HTTP(S) / FTP 直链下载
- 任务名完整显示（多行增高，不裁切）
