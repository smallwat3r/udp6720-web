#!/usr/bin/env bash
# Install or remove a systemd service that starts the dashboard at boot.
# Run it on the machine wired to the power supply, from the project directory.
# It asks for sudo, the dashboard itself keeps running as your own user.
set -euo pipefail

name="udp6720"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="$root/.venv/bin/udp6720-web"
unit="/etc/systemd/system/$name.service"
# An earlier version of this script installed a per-user service here
old_unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$name.service"

# Maps the alias to this machine, so the browser here can open http://udp6720.local
alias_name="udp6720.local"
hosts_line="127.0.0.1 $alias_name # udp6720 dashboard"

usage() {
    echo "usage: $0 install [dashboard flags] | remove | status | logs" >&2
    exit 2
}

remove_hosts_alias() {
    sudo sed -i "/ # udp6720 dashboard\$/d" /etc/hosts
}

remove_old_user_service() {
    [[ -f "$old_unit" ]] || return 0
    systemctl --user disable --now "$name.service" 2>/dev/null || true
    rm -f "$old_unit"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "removed the earlier per-user service"
}

install() {
    # Port 80 by default, so the address needs no port. Any flags given replace this.
    local flags=("$@")
    [[ ${#flags[@]} -gt 0 ]] || flags=(--http-port 80)
    if [[ ! -x "$bin" ]]; then
        echo "error: $bin not found, run 'make install' first" >&2
        exit 1
    fi
    if ! id -nG | tr ' ' '\n' | grep -qx dialout; then
        echo "warning: $USER is not in the dialout group, the serial port will be refused" >&2
        echo "         fix with: sudo usermod -aG dialout $USER, then log out and in" >&2
    fi
    remove_old_user_service
    # Other settings come from ~/.config/udp6720/config.toml, flags on this line win over it.
    # The dashboard starts whether or not the supply is on, and connects when it appears.
    # CAP_NET_BIND_SERVICE is the one privilege it gets, so that it may listen on port 80.
    sudo tee "$unit" > /dev/null <<EOF
[Unit]
Description=UDP6720 power supply web dashboard
Wants=network-online.target
After=network-online.target

[Service]
User=$USER
ExecStart="$bin" ${flags[*]}
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF
    remove_hosts_alias
    echo "$hosts_line" | sudo tee -a /etc/hosts > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable "$name.service"
    sudo systemctl restart "$name.service"
    echo "installed $unit"
    systemctl --no-pager --lines=5 status "$name.service" || true
}

remove() {
    remove_old_user_service
    sudo systemctl disable --now "$name.service" 2>/dev/null || true
    sudo rm -f "$unit"
    remove_hosts_alias
    sudo systemctl daemon-reload
    echo "removed $unit"
}

case "${1:-}" in
    install) shift; install "$@" ;;
    remove) remove ;;
    status) systemctl --no-pager status "$name.service" ;;
    logs) journalctl --unit "$name.service" --follow ;;
    *) usage ;;
esac
