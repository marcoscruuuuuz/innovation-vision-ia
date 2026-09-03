#!/usr/bin/env bash
set -Eeuo pipefail

# Moves one physical GPU from the old AI VM to the new Vision V12 Light VM.
# Does NOT stop Wine/T2U gateway VMs.

[[ ${EUID} -eq 0 ]] || { echo 'Execute como root no host Proxmox.' >&2; exit 1; }

: "${OLD_AI_VMID:?Informe OLD_AI_VMID}"
: "${NEW_AI_VMID:?Informe NEW_AI_VMID}"
: "${GPU_PCI:?Informe GPU_PCI, exemplo 0000:01:00.0}"
GPU_AUDIO_PCI="${GPU_AUDIO_PCI:-}"
WINE_VM_IDS="${WINE_VM_IDS:-}"
CONFIRM_GPU_CUTOVER="${CONFIRM_GPU_CUTOVER:-NO}"

[[ "$CONFIRM_GPU_CUTOVER" == "YES" ]] || {
  echo 'Defina CONFIRM_GPU_CUTOVER=YES após revisar o inventário.' >&2
  exit 2
}

for id in $WINE_VM_IDS; do
  [[ "$id" != "$OLD_AI_VMID" ]] || {
    echo "OLD_AI_VMID=$OLD_AI_VMID está listado como Wine VM. Abortado para não desligar os gateways." >&2
    exit 3
  }
done

qm config "$OLD_AI_VMID" >/dev/null
qm config "$NEW_AI_VMID" >/dev/null
lspci -nnk -s "$GPU_PCI"
[[ -z "$GPU_AUDIO_PCI" ]] || lspci -nnk -s "$GPU_AUDIO_PCI"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/root/visioniav12-gpu-cutover-${stamp}"
install -d -m 0700 "$backup_dir"
qm config "$OLD_AI_VMID" >"$backup_dir/old-${OLD_AI_VMID}.conf"
qm config "$NEW_AI_VMID" >"$backup_dir/new-${NEW_AI_VMID}.conf"
sha256sum "$backup_dir"/*.conf >"$backup_dir/SHA256SUMS"

# New VM must be stopped before attaching PCI devices.
if [[ "$(qm status "$NEW_AI_VMID" | awk '{print $2}')" != "stopped" ]]; then
  echo "A nova VM $NEW_AI_VMID precisa estar parada." >&2
  exit 4
fi

# Graceful shutdown only. No forced stop in this script.
if [[ "$(qm status "$OLD_AI_VMID" | awk '{print $2}')" == "running" ]]; then
  qm shutdown "$OLD_AI_VMID" --timeout 180
fi
[[ "$(qm status "$OLD_AI_VMID" | awk '{print $2}')" == "stopped" ]] || {
  echo 'A VM antiga não desligou de forma limpa. Abortado; não foi usado qm stop.' >&2
  exit 5
}

# Remove only the GPU assignments explicitly identified on the old VM.
OLD_GPU_KEYS="${OLD_GPU_KEYS:-hostpci0 hostpci1}"
for key in $OLD_GPU_KEYS; do
  if qm config "$OLD_AI_VMID" | grep -q "^${key}:"; then
    qm set "$OLD_AI_VMID" --delete "$key"
  fi
done

qm set "$NEW_AI_VMID" --hostpci0 "${GPU_PCI},pcie=1,x-vga=1"
if [[ -n "$GPU_AUDIO_PCI" ]]; then
  qm set "$NEW_AI_VMID" --hostpci1 "${GPU_AUDIO_PCI},pcie=1"
fi

cat >"$backup_dir/ROLLBACK.sh" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
qm shutdown ${NEW_AI_VMID} --timeout 180 || true
qm set ${NEW_AI_VMID} --delete hostpci0 || true
qm set ${NEW_AI_VMID} --delete hostpci1 || true
# Reattach using the exact original values shown in:
# $backup_dir/old-${OLD_AI_VMID}.conf
# Review before executing qm set on the old VM.
EOF
chmod 0700 "$backup_dir/ROLLBACK.sh"

qm config "$NEW_AI_VMID"
qm start "$NEW_AI_VMID"

echo "GPU movida para a nova VM. Backup/rollback: $backup_dir"
echo 'As VMs Wine/T2U não foram desligadas por este script.'
