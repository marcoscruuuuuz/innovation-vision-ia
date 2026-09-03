#!/usr/bin/env bash
set -Eeuo pipefail

# Run on the Proxmox VE host as root.
# Creates the VM WITHOUT attaching the GPU. GPU cutover is a separate guarded step.

[[ ${EUID} -eq 0 ]] || { echo 'Execute como root no host Proxmox.' >&2; exit 1; }
command -v qm >/dev/null
command -v pvesh >/dev/null

VMID="${VMID:-$(pvesh get /cluster/nextid --output-format json | tr -d '"')}"
VM_NAME="${VM_NAME:-visioniav12-light}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
CORES="${CORES:-24}"
MEMORY_MB="${MEMORY_MB:-32768}"
DISK_GB="${DISK_GB:-200}"
IPCONFIG0="${IPCONFIG0:-ip=dhcp}"
NAMESERVER="${NAMESERVER:-1.1.1.1}"
CIUSER="${CIUSER:-innovation}"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-/root/.ssh/authorized_keys}"
IMAGE_URL="${IMAGE_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}"
IMAGE_PATH="${IMAGE_PATH:-/var/lib/vz/template/iso/noble-server-cloudimg-amd64.img}"
MANIFEST="/root/${VM_NAME}-${VMID}-manifest.txt"

qm status "$VMID" >/dev/null 2>&1 && { echo "VMID $VMID já existe." >&2; exit 1; }
pvesm status --storage "$STORAGE" >/dev/null 2>&1 || { echo "Storage $STORAGE não existe." >&2; exit 1; }
[[ -f "$SSH_PUBLIC_KEY_FILE" ]] || { echo "Chave SSH ausente: $SSH_PUBLIC_KEY_FILE" >&2; exit 1; }

if [[ ! -s "$IMAGE_PATH" ]]; then
  install -d -m 0755 "$(dirname "$IMAGE_PATH")"
  tmp="${IMAGE_PATH}.part"
  curl -fL --retry 5 --retry-delay 3 "$IMAGE_URL" -o "$tmp"
  mv "$tmp" "$IMAGE_PATH"
fi

IMAGE_SHA256="$(sha256sum "$IMAGE_PATH" | awk '{print $1}')"

qm create "$VMID" \
  --name "$VM_NAME" \
  --ostype l26 \
  --machine q35 \
  --bios ovmf \
  --cpu host \
  --sockets 1 \
  --cores "$CORES" \
  --memory "$MEMORY_MB" \
  --balloon 0 \
  --numa 1 \
  --scsihw virtio-scsi-single \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --agent enabled=1 \
  --onboot 0 \
  --startup order=50,up=30,down=120 \
  --serial0 socket \
  --vga serial0 \
  --tags 'visionia;v12-light;gpu'

qm set "$VMID" --efidisk0 "${STORAGE}:0,efitype=4m,pre-enrolled-keys=1"
qm importdisk "$VMID" "$IMAGE_PATH" "$STORAGE"
qm set "$VMID" --scsi0 "${STORAGE}:vm-${VMID}-disk-1,discard=on,iothread=1,ssd=1"
qm resize "$VMID" scsi0 "${DISK_GB}G"
qm set "$VMID" --ide2 "${STORAGE}:cloudinit"
qm set "$VMID" --boot order=scsi0
qm set "$VMID" --ciuser "$CIUSER"
qm set "$VMID" --sshkeys "$SSH_PUBLIC_KEY_FILE"
qm set "$VMID" --ipconfig0 "$IPCONFIG0"
qm set "$VMID" --nameserver "$NAMESERVER"
qm set "$VMID" --ciupgrade 1

cat >"$MANIFEST" <<EOF
created_at=$(date -Is)
vmid=$VMID
name=$VM_NAME
storage=$STORAGE
bridge=$BRIDGE
cores=$CORES
memory_mb=$MEMORY_MB
disk_gb=$DISK_GB
ipconfig0=$IPCONFIG0
cloud_image=$IMAGE_PATH
cloud_image_sha256=$IMAGE_SHA256
gpu_attached=no
EOF
chmod 0600 "$MANIFEST"

qm config "$VMID"
echo
printf 'VM criada sem GPU. Manifesto: %s\n' "$MANIFEST"
printf 'Próximo passo seguro: iniciar sem GPU, concluir cloud-init/rede e desligar a VM antes do cutover da GPU.\n'
