network_env="var/network.env"
if [[ -f "$network_env" ]]; then
  network_mode="$(stat -f '%Lp' "$network_env" 2>/dev/null || stat -c '%a' "$network_env")"
  if [[ "$network_mode" != "600" ]]; then
    echo "Radcliffe network environment must have mode 0600" >&2
    exit 1
  fi
  source "$network_env"
fi
unset network_env network_mode
