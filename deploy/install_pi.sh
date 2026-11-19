#!/usr/bin/env bash
# One-shot setup of the LensGuard AI service on a Raspberry Pi (64-bit Raspberry Pi OS, Python 3.11).
# Run from the repo root on the Pi as the NORMAL user (apt/systemd steps sudo themselves):
#   bash deploy/install_pi.sh
# Running it via sudo also works — files are still owned by the real user (SUDO_USER).
# Safe to run again after `git pull` (it only re-installs what changed and restarts the service).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
# The account the service will run as: plain run -> $USER; via sudo -> the human behind SUDO_USER.
OWNER="${SUDO_USER:-$USER}"

echo "== 1/5 system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-dev libopenblas-dev >/dev/null

echo "== 2/5 python virtualenv + packages"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r tools/requirements.txt

echo "== 3/5 credentials"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   .env created from .env.example -> fill in AI_NETPIE_* and TELEGRAM_* then run this script again"
  exit 1
fi
for k in AI_NETPIE_CLIENT_ID AI_NETPIE_TOKEN AI_NETPIE_SECRET; do
  grep -q "^$k=.\+" .env || { echo "   $k is empty in .env"; exit 1; }
done

echo "== 4/5 model"
if [ ! -f ai/model.joblib ]; then
  echo "   no ai/model.joblib -> training a starter model from synthetic data (replace with real data later)"
  .venv/bin/python ai/gen_synthetic.py --days 14 --out ai/data/synthetic.csv
  .venv/bin/python ai/train.py --data ai/data/synthetic.csv
fi
.venv/bin/python ai/service.py --selftest >/dev/null && echo "   selftest ok"

echo "== 5/5 systemd service"
# Installed files must belong to OWNER even when this script itself ran under sudo —
# otherwise the service (running as OWNER) cannot append live.jsonl or retrain the model.
if [ "$(id -u)" = "0" ]; then
  chown -R "$OWNER":"$OWNER" "$REPO"
fi
sed -e "s|__USER__|$OWNER|g" -e "s|__REPO__|$REPO|g" deploy/lensguard-ai.service | sudo tee /etc/systemd/system/lensguard-ai.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now lensguard-ai
sudo systemctl restart lensguard-ai
sleep 3
sudo systemctl --no-pager --lines=5 status lensguard-ai || true
IP=$(hostname -I | awk '{print $1}')
echo
echo "done. dashboard: http://$IP:8080   log: journalctl -u lensguard-ai -f"
