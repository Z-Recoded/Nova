# HP Omen Headless Ubuntu Server — Setup Runbook (v1.2)

> Reconciles the "Nova Reference — HP Omen Headless Ubuntu Server Setup Workbook v1.2"
> (pasted in from Claude Chat, 2026-07-12 — Drive's create-file tool errored, so this local
> file is now the authoritative working copy) with what's actually been verified live via
> SSH during today's setup session. Companion to ClickUp `86baeyfm1` and CLAUDE.md Phase 4.
>
> **Role confirmed:** the Omen is a **service host only** (Chroma, `nova_state.db`,
> orchestration) — not a model-inference host. Its GPU (GTX 1050 Ti, 4GB, Pascal) can't run
> the planned dual-model routing. CUDA is optional here; skip it entirely unless something
> you install specifically needs it.
>
> **v1.2 change, discovered during actual setup:** Nova's codebase
> (`nova_query.py`/`graph_builder.py`/`ingest.py`) was built entirely around
> `chromadb.PersistentClient` (embedded mode, file-path access only — no network protocol,
> can't be queried across machines). Confirmed via full-repo grep: zero `HttpClient` usage
> prior to this fix. Resolved by (1) running Chroma as its own standalone systemd service
> (`nova-chroma`, port 8000), (2) migrating all three client files to `HttpClient`, (3)
> moving `nova_api.py` to port 8001 to avoid the resulting port conflict, (4) fixing a
> hardcoded Windows-only path in `nova_orchestrator.py`'s `load_dotenv()` call to resolve
> relative to the script's own location instead.

---

## ✓ Phase 0 — Before You Touch the Omen (done)
- Ubuntu Server 24.04 LTS ISO, flashed to a USB installer
- A static IP target outside your router's DHCP range
- The Aero's SSH public key ready to paste in later

**Correction:** the workbook text here says this "closes ClickUp `86bavtz06`" once the key's
pasted in — checked the actual task and it's really **"Onboard Nova server, Pi fleet, and
trading bot box via SSH,"** three targets. Onboarding the Omen alone doesn't close it; moved
to "in progress" on the board (2026-07-12), not "complete."

## ✓ Phase 1 — Install the OS (done)
Minimized install, OpenSSH enabled, no desktop environment. "Erase disk and install Ubuntu"
is fine for a dedicated box. **Found live:** a second physical disk still had the old Windows
install — wiped and reclaimed as part of this step.

## ✓ Phase 2 — Static IP via Netplan (done — `192.168.1.250` on `eno1`)

```bash
sudo nano /etc/netplan/00-installer-config.yaml
```

```yaml
network:
  version: 2
  ethernets:
    enp0s25:  # your actual interface name — check with `ip a`
      addresses:
        - 192.168.1.50/24
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```

```bash
sudo netplan apply
ip a
```

## ✓ Phase 3 — Base Packages (done)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git curl ufw
```

If `python3.11` isn't available:

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv
```

NVIDIA/CUDA — optional, skip unless needed (CUDA 12.x only for this GPU, never 13.x).

**Docker:**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Requires logout/reboot to take effect — confirm with `getent group docker` and `docker ps`.
**Found live:** group membership didn't take immediately after a stalled install — caught and
fixed by re-checking `getent group docker` rather than assuming success.

**Ollama** — skip, doesn't fit this machine's role.

## ✓ Phase 4 — Nova's Python Environment (done)

```bash
mkdir -p ~/nova && cd ~/nova
git clone <nova-repo-remote> .
python3.11 -m venv nova-env
source nova-env/bin/activate
pip install -r requirements.txt
```

**Known gotchas, all hit live:**
- Nested repo folder (e.g. `~/nova/Nova/...`) — flatten before continuing.
- Windows-generated `requirements.txt` was UTF-16 encoded (PowerShell's default output
  encoding) — fixed with `iconv -f utf-16 -t utf-8 requirements.txt -o requirements_utf8.txt
  && mv requirements_utf8.txt requirements.txt`.
- Stripped Windows-only packages (`pywin32`): `sed -i '/pywin32/d' requirements.txt`.
- `.env` is git-ignored — copied via `scp` from the Aero, then `chmod 600 ~/nova/.env`.

## ✓ Phase 5 — Transfer Chroma Data from the Aero (done)

```powershell
scp -r C:\Nova\memory <user>@192.168.1.250:~/nova/memory
```

```bash
ls ~/nova/memory
```

## ✓ Phase 6 — Confirm Chroma Is Reachable From the Aero (done, verified live 2026-07-12)

Gated on Phase 9 below — `PersistentClient` has no network protocol; Chroma must run as a
server (`HttpClient` mode) first.

```bash
curl http://192.168.1.250:8000/api/v2/heartbeat
```

Then confirm a real query against `nova_memory` returns results — use `get_collection()`, not
`get_or_create_collection()` (the latter masks mismatches by silently creating an empty
collection). Zero-result = hard fail.

**Verified live from the Aero, same fix pattern as the original `/context-budget` bug
(CLAUDE.md Section 5):** ran `nova_chroma_omen_check.py --host 192.168.1.250 --port 8000` —
TCP reachable, heartbeat OK, `nova_memory` collection found (479 chunks), a real query
(`"Tell me about Null"`) returned real `Null.md`/`Nullius.md` results. Full pass.

## ✓ Phase 7 — Lid-Close Behavior, Never Suspend (done)

```bash
sudo nano /etc/systemd/logind.conf
```

```ini
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
```

```bash
sudo systemctl restart systemd-logind
```

Confirmed SSH-able while the lid is closed.

## ✓ Phase 8 — SSH Access From the Aero (done)

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<Aero's public key>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Test: `ssh <user>@192.168.1.250` — confirmed working via key-based auth.

**Same correction as Phase 0:** this does not, by itself, close ClickUp `86bavtz06` — that
task also covers the Pi fleet and trading bot box, neither onboarded yet.

## ✓ Phase 9 — systemd Services for Chroma and nova_api.py (done — corrected in v1.2)

`/etc/systemd/system/nova-chroma.service`:

```ini
[Unit]
Description=Nova Chroma Vector DB
After=network.target

[Service]
User=<your-user>
WorkingDirectory=/home/<your-user>/nova
ExecStart=/home/<your-user>/nova/nova-env/bin/chroma run --path /home/<your-user>/nova/memory --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nova-chroma
sleep 5 && sudo systemctl status nova-chroma --no-pager
```

`/etc/systemd/system/nova-api.service` — **port 8001, not 8000** (real port conflict with
Chroma, both defaulted there):

```ini
[Unit]
Description=Nova API (FastAPI)
After=network.target nova-chroma.service
Requires=nova-chroma.service

[Service]
User=<your-user>
WorkingDirectory=/home/<your-user>/nova
ExecStart=/home/<your-user>/nova/nova-env/bin/python -m uvicorn nova_api:app --host 0.0.0.0 --port 8001
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nova-api
sleep 5 && sudo systemctl status nova-api --no-pager
```

Confirm `.env` exists with `chmod 600`, and `load_dotenv()` calls use
`Path(__file__).parent / ".env"`, not a hardcoded absolute path (fixed in `nova_orchestrator.py`,
commit `5146222`, after this exact bug surfaced live on the Omen).

Both units confirmed running as permanent systemd services, stability-checked.

## ✓ Phase 10 — Firewall (ufw) (done — corrected in v1.2)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp   # Chroma
sudo ufw allow 8001/tcp   # nova_api.py
sudo ufw enable
sudo ufw status
```

Tightened from "Anywhere" to LAN-subnet-only after initial verification.

## ⏳ Phase 11 — Tailscale on Ubuntu (not done — the one remaining step)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4   # note this — it's the Omen's tailnet address
```

Confirmed via `tailscale status` from the Aero (2026-07-12): no Omen peer yet. Remember the
Windows-side gotcha already hit on the Aero (CLAUDE.md Phase 4): Tailscale's adapter classifies
as Private, not Public — irrelevant on Ubuntu since `ufw` rules above are already scoped
correctly, but worth re-checking if anything unexpectedly can't reach the Omen over the tailnet.

**Aero-side half of the Ollama callback path already done and verified (2026-07-12):**
Ollama's `OLLAMA_HOST` set to `0.0.0.0` + restarted, and a `Nova Ollama (Omen callback)`
inbound firewall rule (TCP 11434, Private profile) added on the Aero — verified locally by
hitting the Aero's own Tailscale IP (`100.122.229.23:11434`) and getting back `200 Ollama is
running`. **Once `tailscale up` succeeds here, validate the actual cross-machine hop from the
Omen:**

```bash
curl http://100.122.229.23:11434/
```

A response of `Ollama is running` confirms the Omen can reach the Aero's Ollama instance over
the tailnet — this is what makes hosted-service-only inference on the Omen actually work day
to day.

## ⏳ Phase 12 — Validate End-to-End (partially done)

- [x] Chroma reachable on LAN IP (8000) — confirmed live, see Phase 6
- [ ] Chroma reachable on Tailscale IP (8000) — blocked on Phase 11
- [ ] `nova_api.py` reachable on LAN IP (8001)
- [ ] `nova_api.py` reachable on Tailscale IP (8001) — blocked on Phase 11

---

## Not covered here — separate tracked decisions

- **Dockerizing these services** (`86baf4e29`) — deliberately held until this runbook is
  fully done (Phase 11 remaining), not run in parallel
- **Inference while running standalone on the Omen** — split across the Aero's own Ollama
  (Phase 11 above), hosted chat-API fallback (`86baf4eah`), serverless/raw GPU rental
  (`86baw3010`), and a dedicated GPU machine purchase (`86baw3016`)
- **Re-running the Tailscale DERP relay reachability test** (`86bat0ue1`) against the Omen
  once it's live, instead of the Aero
- **Token-based auth for `nova_api.py`** (`86bawf2z2`) — new, separate, deliberately deferred
  scope; network-level controls (Tailscale/ufw above) come first, this is defense-in-depth on
  top, not instead of them

## Optional Appendix — Claude Code as Standby Maintenance Tool (unchanged from v1.1)

Native install, API-key auth for headless use, `claude doctor` to verify. Use only for
specific fixes, never left running.
