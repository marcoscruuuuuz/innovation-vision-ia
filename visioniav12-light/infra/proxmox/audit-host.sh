#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo 'Execute como root no Proxmox.' >&2; exit 1; }
command -v pvesh >/dev/null
command -v qm >/dev/null

OUT_DIR="${OUT_DIR:-/root/visioniav12-audit-$(date -u +%Y%m%dT%H%M%SZ)}"
install -d -m 0700 "$OUT_DIR"

pveversion -v >"$OUT_DIR/pveversion.txt"
pvesh get /cluster/resources --type vm --output-format json-pretty >"$OUT_DIR/vms.json"
pvesm status >"$OUT_DIR/storage.txt"
ip -br link >"$OUT_DIR/network-links.txt"
bridge link >"$OUT_DIR/bridge-links.txt" 2>&1 || true
lspci -nnk >"$OUT_DIR/lspci.txt"
lspci -nnk | grep -iA4 -E 'vga|3d|audio.*nvidia' >"$OUT_DIR/gpu.txt" || true
find /sys/kernel/iommu_groups -maxdepth 2 -type l -printf '%h %f -> %l\n' 2>/dev/null | sort >"$OUT_DIR/iommu-groups.txt" || true
cat /proc/cmdline >"$OUT_DIR/kernel-cmdline.txt"
lsmod | grep -E 'vfio|nvidia|nouveau' >"$OUT_DIR/gpu-modules.txt" || true

: >"$OUT_DIR/hostpci-map.txt"
while read -r vmid; do
  qm config "$vmid" >"$OUT_DIR/vm-${vmid}.conf"
  if grep -q '^hostpci' "$OUT_DIR/vm-${vmid}.conf"; then
    printf '\nVMID=%s\n' "$vmid" >>"$OUT_DIR/hostpci-map.txt"
    grep '^hostpci' "$OUT_DIR/vm-${vmid}.conf" >>"$OUT_DIR/hostpci-map.txt"
  fi
done < <(qm list | awk 'NR>1 {print $1}')

cat >"$OUT_DIR/CHECKLIST.txt" <<'EOF'
Preencher antes de qualquer cutover:

OLD_AI_VMID=
NEW_AI_VMID=
GPU_PCI=
GPU_AUDIO_PCI=
WINE_VM_IDS=

Confirmar:
[ ] Qual VM possui a GPU hoje?
[ ] Quais VMs hospedam os 10 Wine/T2uBridge?
[ ] A VM antiga de IA NÃO hospeda Wine/T2U?
[ ] IOMMU ativo?
[ ] GPU e funções associadas estão em grupo isolável?
[ ] Above 4G Decoding habilitado no firmware?
[ ] Storage comporta a nova VM?
[ ] Bridge/IP planejados?
[ ] Backup das configs Proxmox criado?
[ ] Rollback físico e lógico revisado?
EOF

sha256sum "$OUT_DIR"/* >"$OUT_DIR/SHA256SUMS"
chmod 0600 "$OUT_DIR"/*
printf 'Auditoria somente leitura criada em %s\n' "$OUT_DIR"
printf 'Não execute gpu-cutover.sh antes de revisar CHECKLIST.txt e hostpci-map.txt.\n'
