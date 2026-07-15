#!/usr/bin/env bash
# =============================================================================
# Anime Stack installer
#   File Browser (文件管理) + Anime Hub (三站聚合搜索 + aria2 BT 下载)
#
# Usage:
#   sudo ./install.sh install              # 安装并启动
#   sudo ./install.sh uninstall            # 卸载服务与程序（保留下载数据）
#   sudo ./install.sh uninstall --purge    # 卸载并删除 /home/share 下载数据
#   sudo ./install.sh reinstall            # 先卸载再安装
#   sudo ./install.sh status               # 查看状态
#   sudo ./install.sh start|stop|restart   # 控制服务
#
# Optional env overrides before install:
#   SHARE_DIR=/home/share
#   APP_DIR=/opt/anime-hub
#   WEB_PORT=8765
#   FILEBROWSER_PORT=8080
#   FILEBROWSER_USER=admin
#   FILEBROWSER_PASSWORD=yourpass   # empty = auto generate
#   ARIA2_RPC_SECRET=animehub
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}/bundle"

# Defaults (overridable via env)
SHARE_DIR="${SHARE_DIR:-/home/share}"
APP_DIR="${APP_DIR:-/opt/anime-hub}"
WEB_PORT="${WEB_PORT:-8765}"
ARIA2_RPC_PORT="${ARIA2_RPC_PORT:-6800}"
ARIA2_RPC_SECRET="${ARIA2_RPC_SECRET:-animehub}"
FILEBROWSER_PORT="${FILEBROWSER_PORT:-8080}"
FILEBROWSER_USER="${FILEBROWSER_USER:-admin}"
FILEBROWSER_PASSWORD="${FILEBROWSER_PASSWORD:-}"
FILEBROWSER_ROOT="${FILEBROWSER_ROOT:-$SHARE_DIR}"
FILEBROWSER_BIN="${FILEBROWSER_BIN:-/usr/local/bin/filebrowser}"
FILEBROWSER_DB_DIR="${FILEBROWSER_DB_DIR:-/var/lib/filebrowser}"
FILEBROWSER_DB="${FILEBROWSER_DB:-$FILEBROWSER_DB_DIR/filebrowser.db}"
ENV_FILE="${ENV_FILE:-/etc/anime-hub.env}"
CRED_FILE="${CRED_FILE:-$SHARE_DIR/anime-stack-credentials.txt}"

SERVICES=(filebrowser anime-hub-aria2 anime-hub)

# Colors
if [[ -t 1 ]]; then
  C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_CYAN='\033[0;36m'; C_RESET='\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''; C_RESET=''
fi

log()  { echo -e "${C_GREEN}[+]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[!]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[x]${C_RESET} $*" >&2; }
die()  { err "$*"; exit 1; }
info() { echo -e "${C_CYAN}[=]${C_RESET} $*"; }

need_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "请使用 root 运行：sudo $0 $*"
  fi
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_LIKE="${ID_LIKE:-}"
  else
    OS_ID=unknown
    OS_LIKE=
  fi
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) FB_ARCH=amd64 ;;
    aarch64|arm64) FB_ARCH=arm64 ;;
    armv7l|armhf) FB_ARCH=armv7 ;;
    *) die "不支持的架构: $ARCH" ;;
  esac
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# Run command as another user without requiring sudo package
as_user() {
  local user="$1"; shift
  if have_cmd runuser; then
    runuser -u "$user" -- "$@"
  elif have_cmd sudo; then
    sudo -u "$user" -- "$@"
  else
    su -s /bin/sh "$user" -c "$(printf '%q ' "$@")"
  fi
}

install_system_packages() {
  log "安装系统依赖…"
  detect_os
  export DEBIAN_FRONTEND=noninteractive

  if have_cmd apt-get; then
    apt-get update -qq
    apt-get install -y -qq \
      ca-certificates curl wget tar gzip \
      python3 python3-pip python3-venv python3-dev \
      aria2 \
      >/dev/null
  elif have_cmd dnf; then
    dnf install -y ca-certificates curl wget tar gzip \
      python3 python3-pip python3-devel aria2
  elif have_cmd yum; then
    yum install -y ca-certificates curl wget tar gzip \
      python3 python3-pip python3-devel aria2
  elif have_cmd apk; then
    apk add --no-cache ca-certificates curl wget tar gzip \
      python3 py3-pip python3-dev aria2
  else
    die "无法识别包管理器，请手动安装: python3 python3-venv python3-pip aria2 curl"
  fi

  have_cmd python3 || die "python3 安装失败"
  have_cmd aria2c || die "aria2 安装失败"
  have_cmd curl || die "curl 安装失败"
  log "系统依赖就绪 (python3=$(python3 --version 2>&1), aria2=$(aria2c --version 2>&1 | head -1))"
}

# ---------- File Browser ----------
install_filebrowser_binary() {
  if [[ -x "$FILEBROWSER_BIN" ]]; then
    log "File Browser 已存在: $($FILEBROWSER_BIN version 2>/dev/null | head -1 || echo "$FILEBROWSER_BIN")"
    return 0
  fi

  log "下载 File Browser ($FB_ARCH)…"
  local tmp ver url
  tmp="$(mktemp -d)"
  # Resolve latest release tag; fallback to a known stable version
  ver="$(curl -fsSL https://api.github.com/repos/filebrowser/filebrowser/releases/latest \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1 || true)"
  if [[ -z "${ver:-}" ]]; then
    ver="v2.32.3"
    warn "无法获取最新版本，使用回退版本 $ver"
  fi
  url="https://github.com/filebrowser/filebrowser/releases/download/${ver}/linux-${FB_ARCH}-filebrowser.tar.gz"
  log "拉取 $url"
  if ! curl -fsSL "$url" -o "$tmp/fb.tgz"; then
    # older naming pattern
    url="https://github.com/filebrowser/filebrowser/releases/download/${ver}/linux-${FB_ARCH}-filebrowser.tar.gz"
    die "下载 File Browser 失败，请检查网络或手动安装到 $FILEBROWSER_BIN"
  fi
  tar -xzf "$tmp/fb.tgz" -C "$tmp"
  install -m 0755 "$tmp/filebrowser" "$FILEBROWSER_BIN"
  rm -rf "$tmp"
  log "File Browser 安装到 $FILEBROWSER_BIN ($($FILEBROWSER_BIN version 2>/dev/null | head -1))"
}

ensure_share_dir() {
  mkdir -p "$SHARE_DIR" "$FILEBROWSER_ROOT"
  # dedicated user for filebrowser
  if ! id filebrowser >/dev/null 2>&1; then
    log "创建系统用户 filebrowser"
    useradd --system --home-dir "$FILEBROWSER_DB_DIR" --shell /usr/sbin/nologin \
      --comment "File Browser" filebrowser 2>/dev/null \
      || useradd --system --home-dir "$FILEBROWSER_DB_DIR" --shell /sbin/nologin filebrowser
  fi
  mkdir -p "$FILEBROWSER_DB_DIR"
  chown -R filebrowser:filebrowser "$FILEBROWSER_DB_DIR"
  # share dir: group filebrowser can rwx; setgid so aria2 (Group=filebrowser) nests inherit
  # 2775: owner+group rwx, sticky setgid — FB can delete downloads created by aria2
  chown root:filebrowser "$SHARE_DIR" 2>/dev/null || chown filebrowser:filebrowser "$SHARE_DIR"
  chmod 2775 "$SHARE_DIR" || true
  # Fix already-downloaded trees so File Browser is not 403 on delete
  if id filebrowser >/dev/null 2>&1; then
    find "$SHARE_DIR" -mindepth 1 \
      \( -name 'anime-hub' -o -name 'anime-stack' -o -name 'anime-stack-credentials.txt' \) -prune -o \
      -exec chown root:filebrowser {} + 2>/dev/null || true
    find "$SHARE_DIR" -mindepth 1 \
      \( -name 'anime-hub' -o -name 'anime-stack' -o -name 'anime-stack-credentials.txt' \) -prune -o \
      -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$SHARE_DIR" -mindepth 1 \
      \( -name 'anime-hub' -o -name 'anime-stack' -o -name 'anime-stack-credentials.txt' \) -prune -o \
      -type f -exec chmod 664 {} + 2>/dev/null || true
  fi
}

init_filebrowser_db() {
  local pass gen_note="" password_line=""

  if [[ -f "$FILEBROWSER_DB" ]]; then
    log "File Browser 数据库已存在，跳过初始化 ($FILEBROWSER_DB)"
    # still try ensure address/root
    as_user filebrowser "$FILEBROWSER_BIN" config set \
      -d "$FILEBROWSER_DB" \
      --address 0.0.0.0 \
      --port "$FILEBROWSER_PORT" \
      --root "$FILEBROWSER_ROOT" \
      --auth.method json \
      >/dev/null 2>&1 || true
    if [[ -n "${FILEBROWSER_PASSWORD}" ]]; then
      # optional: reset password when explicitly provided
      as_user filebrowser "$FILEBROWSER_BIN" users update "$FILEBROWSER_USER" \
        -d "$FILEBROWSER_DB" --password "$FILEBROWSER_PASSWORD" --perm.admin >/dev/null 2>&1 \
        || as_user filebrowser "$FILEBROWSER_BIN" users add "$FILEBROWSER_USER" "$FILEBROWSER_PASSWORD" \
             -d "$FILEBROWSER_DB" --perm.admin >/dev/null 2>&1 || true
      password_line="密码: ${FILEBROWSER_PASSWORD} (已按环境变量更新)"
      log "File Browser 密码已按 FILEBROWSER_PASSWORD 更新"
    else
      password_line="密码: (沿用已有数据库，未修改；见旧凭据或自行重置)"
    fi
  else
    if [[ -z "${FILEBROWSER_PASSWORD}" ]]; then
      if have_cmd openssl; then
        pass="$(openssl rand -base64 12 | tr -d '/+=' | head -c 14)"
      else
        pass="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 14)"
      fi
      FILEBROWSER_PASSWORD="$pass"
      gen_note="(自动生成)"
    else
      pass="$FILEBROWSER_PASSWORD"
    fi

    log "初始化 File Browser 数据库…"
    install -d -o filebrowser -g filebrowser "$FILEBROWSER_DB_DIR"
    as_user filebrowser "$FILEBROWSER_BIN" config init -d "$FILEBROWSER_DB" >/dev/null
    as_user filebrowser "$FILEBROWSER_BIN" config set \
      -d "$FILEBROWSER_DB" \
      --address 0.0.0.0 \
      --port "$FILEBROWSER_PORT" \
      --root "$FILEBROWSER_ROOT" \
      --log stdout \
      --auth.method json \
      >/dev/null
    as_user filebrowser "$FILEBROWSER_BIN" config set \
      -d "$FILEBROWSER_DB" \
      --auth.tokenExpirationTime 8760h \
      >/dev/null 2>&1 || true

    if as_user filebrowser "$FILEBROWSER_BIN" users ls -d "$FILEBROWSER_DB" 2>/dev/null | grep -q "$FILEBROWSER_USER"; then
      as_user filebrowser "$FILEBROWSER_BIN" users update "$FILEBROWSER_USER" \
        -d "$FILEBROWSER_DB" --password "$pass" --perm.admin >/dev/null
    else
      as_user filebrowser "$FILEBROWSER_BIN" users add "$FILEBROWSER_USER" "$pass" \
        -d "$FILEBROWSER_DB" --perm.admin >/dev/null
    fi
    password_line="密码: ${pass} ${gen_note}"
    log "File Browser 用户: $FILEBROWSER_USER  密码: $pass $gen_note"
  fi

  # credentials file
  mkdir -p "$(dirname "$CRED_FILE")"
  cat > "$CRED_FILE" <<EOF
Anime Stack 登录信息
生成时间: $(date -Iseconds)

=== File Browser ===
地址: http://<服务器IP>:${FILEBROWSER_PORT}
用户: ${FILEBROWSER_USER}
${password_line}
根目录: ${FILEBROWSER_ROOT}

=== Anime Hub ===
地址: http://<服务器IP>:${WEB_PORT}
下载目录: ${SHARE_DIR}
aria2 RPC: 127.0.0.1:${ARIA2_RPC_PORT} (secret 见 /etc/anime-hub.env)

管理命令:
  systemctl status filebrowser anime-hub anime-hub-aria2
  $(basename "$0" 2>/dev/null || echo install.sh) status
EOF
  chmod 600 "$CRED_FILE"
  cp -f "$CRED_FILE" /root/anime-stack-credentials.txt 2>/dev/null || true
  chmod 600 /root/anime-stack-credentials.txt 2>/dev/null || true
}

# ---------- Anime Hub ----------
install_anime_hub_app() {
  [[ -d "$BUNDLE_DIR/anime-hub" ]] || die "缺少应用包: $BUNDLE_DIR/anime-hub"

  log "部署 Anime Hub 到 $APP_DIR"
  mkdir -p "$APP_DIR" "$APP_DIR/logs" "$APP_DIR/session"
  # sync app files (do not wipe venv if reinstalling lightly)
  rsync -a --delete \
    --exclude '.venv' \
    --exclude 'logs' \
    --exclude 'session' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$BUNDLE_DIR/anime-hub/" "$APP_DIR/" 2>/dev/null \
    || {
      # fallback without rsync
      find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name '.venv' ! -name 'logs' ! -name 'session' -exec rm -rf {} +
      cp -a "$BUNDLE_DIR/anime-hub/." "$APP_DIR/"
    }

  mkdir -p "$APP_DIR/logs" "$APP_DIR/session"
  touch "$APP_DIR/session/aria2.session"

  # ensure config DOWNLOAD_DIR points correctly via env (runtime) + patch if needed
  # Python config defaults to /home/share; env is for systemd/aria2

  log "创建 Python 虚拟环境并安装依赖…"
  if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/.venv"
  fi
  "$APP_DIR/.venv/bin/pip" install -q --upgrade pip wheel
  "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
  log "Python 依赖安装完成"
}

write_env_file() {
  log "写入环境配置 $ENV_FILE"
  cat > "$ENV_FILE" <<EOF
# Managed by anime-stack install.sh — $(date -Iseconds)
APP_DIR=${APP_DIR}
DOWNLOAD_DIR=${SHARE_DIR}
WEB_PORT=${WEB_PORT}
ARIA2_RPC_PORT=${ARIA2_RPC_PORT}
ARIA2_RPC_SECRET=${ARIA2_RPC_SECRET}
FILEBROWSER_PORT=${FILEBROWSER_PORT}
FILEBROWSER_ROOT=${FILEBROWSER_ROOT}
EOF
  chmod 644 "$ENV_FILE"
}

install_systemd_units() {
  have_cmd systemctl || die "需要 systemd"

  log "安装 systemd 单元…"
  # Process unit templates: substitute fixed paths that systemd won't expand from env in all fields
  local unit src dest
  for unit in filebrowser.service anime-hub-aria2.service anime-hub.service; do
    src="$BUNDLE_DIR/systemd/$unit"
    dest="/etc/systemd/system/$unit"
    [[ -f "$src" ]] || die "缺少 $src"
    # Replace hard-coded /opt/anime-hub if APP_DIR differs
    sed "s|/opt/anime-hub|${APP_DIR}|g" "$src" > "$dest"
    chmod 644 "$dest"
  done

  # aria2 unit: EnvironmentFile expansion for ExecStart args works on systemd >= 229
  systemctl daemon-reload
  systemctl enable filebrowser.service anime-hub-aria2.service anime-hub.service
  log "systemd 单元已启用"
}

stop_legacy_processes() {
  # Stop old start.sh style processes if any
  if [[ -x /home/share/anime-hub/start.sh ]]; then
    /home/share/anime-hub/start.sh stop >/dev/null 2>&1 || true
  fi
  if [[ -x "$APP_DIR/start.sh" ]]; then
    "$APP_DIR/start.sh" stop >/dev/null 2>&1 || true
  fi
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "aria2c.*--rpc-listen-port=${ARIA2_RPC_PORT}" 2>/dev/null || true
  sleep 0.5
}

start_services() {
  log "启动服务…"
  systemctl restart filebrowser.service
  systemctl restart anime-hub-aria2.service
  sleep 0.8
  systemctl restart anime-hub.service
  sleep 0.8
  local ok=1
  for s in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$s"; then
      log "$s: active"
    else
      err "$s: 未运行 — journalctl -u $s -n 40 --no-pager"
      ok=0
    fi
  done
  [[ $ok -eq 1 ]] || warn "部分服务启动失败，请查看日志"
}

# ---------- Uninstall ----------
uninstall_stack() {
  local purge="${1:-}"
  need_root
  log "停止并禁用服务…"
  for s in "${SERVICES[@]}"; do
    systemctl stop "$s" 2>/dev/null || true
    systemctl disable "$s" 2>/dev/null || true
  done
  stop_legacy_processes

  log "移除 systemd 单元…"
  rm -f /etc/systemd/system/filebrowser.service \
        /etc/systemd/system/anime-hub.service \
        /etc/systemd/system/anime-hub-aria2.service
  systemctl daemon-reload 2>/dev/null || true
  systemctl reset-failed 2>/dev/null || true

  log "移除 Anime Hub 程序 ($APP_DIR)…"
  rm -rf "$APP_DIR"

  log "移除 File Browser 二进制与数据库…"
  rm -f "$FILEBROWSER_BIN"
  rm -rf "$FILEBROWSER_DB_DIR"

  log "移除环境配置…"
  rm -f "$ENV_FILE"
  rm -f /root/anime-stack-credentials.txt
  rm -f "$SHARE_DIR/anime-stack-credentials.txt" 2>/dev/null || true

  # optional: remove system user
  if id filebrowser >/dev/null 2>&1; then
    # only remove if no processes
    userdel filebrowser 2>/dev/null || warn "无法删除用户 filebrowser（可能仍被占用）"
  fi

  if [[ "$purge" == "--purge" || "$purge" == "purge" ]]; then
    warn "将删除下载数据目录内容: $SHARE_DIR"
    # safety: only wipe contents if path looks like share dir
    if [[ "$SHARE_DIR" == "/home/share" || "$SHARE_DIR" == "/data/share" ]]; then
      find "$SHARE_DIR" -mindepth 1 -maxdepth 1 \
        ! -name 'anime-stack' \
        -exec rm -rf {} + 2>/dev/null || true
      # also remove package dir if inside share
      if [[ "$SCRIPT_DIR" == "$SHARE_DIR/anime-stack" ]]; then
        warn "安装包目录 $SCRIPT_DIR 保留（脚本自身），如需删除请手动 rm -rf"
      fi
      log "已清理 $SHARE_DIR 下的数据（保留 anime-stack 安装包目录若存在）"
    else
      warn "SHARE_DIR=$SHARE_DIR 非默认路径，跳过自动 purge，请手动清理"
    fi
  else
    info "已保留下载数据目录: $SHARE_DIR （如需一并删除请用: $0 uninstall --purge）"
  fi

  log "卸载完成"
}

# ---------- Status / control ----------
cmd_status() {
  echo "======== Anime Stack Status ========"
  for s in "${SERVICES[@]}"; do
    local st
    st="$(systemctl is-active "$s" 2>/dev/null || echo missing)"
    printf "  %-20s %s\n" "$s" "$st"
  done
  echo
  echo "Ports:"
  ss -tlnp 2>/dev/null | grep -E ":(${FILEBROWSER_PORT}|${WEB_PORT}|${ARIA2_RPC_PORT}) " || true
  echo
  if [[ -f "$ENV_FILE" ]]; then
    echo "Env file: $ENV_FILE"
    cat "$ENV_FILE"
  fi
  echo
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ip="${ip:-<服务器IP>}"
  echo "File Browser: http://${ip}:${FILEBROWSER_PORT}"
  echo "Anime Hub:    http://${ip}:${WEB_PORT}"
  if [[ -f /root/anime-stack-credentials.txt ]]; then
    echo "凭据文件: /root/anime-stack-credentials.txt"
  fi
  # quick health
  if have_cmd curl; then
    echo
    curl -fsS --max-time 3 "http://127.0.0.1:${WEB_PORT}/api/health" 2>/dev/null \
      && echo \
      || warn "Anime Hub health 检查失败"
  fi
}

cmd_control() {
  local action="$1"
  need_root
  case "$action" in
    start)
      systemctl start filebrowser anime-hub-aria2 anime-hub
      ;;
    stop)
      systemctl stop anime-hub anime-hub-aria2 filebrowser
      ;;
    restart)
      systemctl restart filebrowser anime-hub-aria2
      sleep 0.5
      systemctl restart anime-hub
      ;;
  esac
  cmd_status
}

do_install() {
  need_root
  [[ -d "$BUNDLE_DIR" ]] || die "找不到 bundle 目录: $BUNDLE_DIR（请保持 install.sh 与 bundle/ 同级）"

  info "SHARE_DIR=$SHARE_DIR  APP_DIR=$APP_DIR  FB_PORT=$FILEBROWSER_PORT  WEB_PORT=$WEB_PORT"
  detect_os
  install_system_packages
  # rsync optional
  if ! have_cmd rsync; then
    if have_cmd apt-get; then apt-get install -y -qq rsync >/dev/null || true; fi
  fi

  ensure_share_dir
  install_filebrowser_binary
  init_filebrowser_db
  install_anime_hub_app
  write_env_file
  stop_legacy_processes
  install_systemd_units
  start_services

  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ip="${ip:-<服务器IP>}"

  echo
  echo "=============================================="
  log "安装完成！"
  echo "----------------------------------------------"
  echo "  File Browser : http://${ip}:${FILEBROWSER_PORT}"
  echo "  Anime Hub    : http://${ip}:${WEB_PORT}"
  echo "  下载目录     : ${SHARE_DIR}"
  echo "  凭据文件     : /root/anime-stack-credentials.txt"
  if [[ -f /root/anime-stack-credentials.txt ]]; then
    echo "----------------------------------------------"
    grep -E '用户:|密码:|地址:' /root/anime-stack-credentials.txt || true
  fi
  echo "=============================================="
  echo "常用命令:"
  echo "  $0 status"
  echo "  $0 restart"
  echo "  $0 uninstall"
  echo "  $0 uninstall --purge   # 连下载数据一起删"
}

usage() {
  cat <<EOF
Anime Stack 一键安装/卸载脚本

用法:
  sudo $0 install                 安装 File Browser + Anime Hub + aria2
  sudo $0 uninstall               卸载程序（保留 ${SHARE_DIR} 数据）
  sudo $0 uninstall --purge       卸载并清理下载数据
  sudo $0 reinstall               卸载后重新安装（保留数据）
  sudo $0 status                  查看状态
  sudo $0 start|stop|restart      启停服务

环境变量（安装前可选）:
  SHARE_DIR=/home/share
  APP_DIR=/opt/anime-hub
  WEB_PORT=8765
  FILEBROWSER_PORT=8080
  FILEBROWSER_USER=admin
  FILEBROWSER_PASSWORD=        # 空则自动生成
  ARIA2_RPC_SECRET=animehub
EOF
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    install)
      do_install
      ;;
    uninstall|remove)
      uninstall_stack "${1:-}"
      ;;
    reinstall)
      uninstall_stack ""
      do_install
      ;;
    status)
      cmd_status
      ;;
    start|stop|restart)
      cmd_control "$cmd"
      ;;
    -h|--help|help|"")
      usage
      [[ -n "$cmd" ]] || exit 0
      ;;
    *)
      usage
      die "未知命令: $cmd"
      ;;
  esac
}

main "$@"
