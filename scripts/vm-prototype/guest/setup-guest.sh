#!/bin/bash
# Runs once in the guest as root, from the seed disk. Installs the desktop and bare, then powers off.
set -eux
S=/mnt/seed
pacman-key --init || true
pacman -Syu --noconfirm --needed sway foot grim wtype nano qemu-guest-agent curl ttf-dejavu
install -m755 $S/bare-mcp /usr/local/bin/bare-mcp
install -m755 $S/bare-session $S/bare-rpc $S/as-deck /usr/local/bin/
install -m755 $S/deck_executor.py /usr/local/bin/deck-executor
install -m644 $S/deck-executor.service /etc/systemd/system/
install -m644 $S/70-deck-executor.rules /etc/udev/rules.d/
id deck || useradd -m -s /bin/bash deck
loginctl enable-linger deck || true
mkdir -p /etc/systemd/system/getty@tty1.service.d /home/deck/.config/sway
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<'EOT'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin deck --noclear %I $TERM
EOT
cat > /home/deck/.bash_profile <<'EOT'
if [ "$(tty)" = /dev/tty1 ]; then
  export WLR_RENDERER=pixman WLR_NO_HARDWARE_CURSORS=1
  exec sway
fi
EOT
install -m644 $S/sway-config /home/deck/.config/sway/config
chown -R deck:deck /home/deck
systemctl enable qemu-guest-agent.service deck-executor.service || true
# The desktop uses no managed home directories, no user-namespace allocation and no user database service.
# Masking them saves guest memory. Login, networking, time sync and the guest agent stay. See docs/vm-optimization-results.md.
systemctl mask systemd-homed.service systemd-homed-activate.service systemd-nsresourced.service systemd-nsresourced.socket systemd-userdbd.service systemd-userdbd.socket
touch /etc/cloud/cloud-init.disabled
printf 'deck-stage1 setup done\n' > /var/lib/stage1-setup-done
pacman -Scc --noconfirm
sync
fstrim -av || true
poweroff
