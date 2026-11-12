# Plan: Printer Discovery on the Docker Host's Network

## Problem
"Discover printers" only lists what the zeroconf `ServiceBrowser` heard over mDNS
(`backend/main.py:62-76`). mDNS is **multicast** (224.0.0.251:5353). With the default
Compose setup (bridge network, `ports: 8000:8000`) the container sits behind Docker's
NAT on a private bridge (e.g. `172.17.0.0/16`): multicast from the LAN never reaches it,
so the list stays empty. Host networking fixes this only on Linux; Docker Desktop
(macOS/Windows) runs containers in a VM and does not forward LAN multicast reliably.

What *does* work in bridge mode on every platform: **outbound unicast** TCP/UDP. The
container can already reach `192.168.x.y:9100` through NAT — that is why manual IP
connection works.

## Approach
Add an **active unicast subnet scan** next to the existing mDNS browser:

1. Determine which subnet(s) of the Docker host to scan.
2. Probe every address for TCP port 9100 (short timeout, bounded concurrency).
3. Confirm each open port is a VC-500W with the existing `vc500w.get_config()`.
4. Merge hits into the `printers` registry and return them to the UI.

mDNS stays as a passive bonus source (works with host networking on Linux).

### Rejected alternatives
| Option | Why not |
|---|---|
| `network_mode: host` only | Linux only; breaks the `ports:` mapping; unreliable on Docker Desktop. |
| macvlan/ipvlan network | Needs host NIC name + free IP per install; host can't reach the container; not supported on Docker Desktop. |
| Avahi reflector sidecar | Sidecar itself needs host networking → same Linux-only limitation. |
| Brother UDP broadcast discovery | Broadcast doesn't cross the NAT either. |

## Design

### 1. Subnet selection (`backend/discovery.py`, new)
Priority order:
1. **`PRINTER_SCAN_SUBNETS`** env var — comma-separated CIDRs, e.g. `192.168.1.0/24,10.0.5.0/24`.
   This is the required setting for bridge mode, because the container cannot see the host's LAN interfaces.
2. **Optional request body** `{"subnets": [...]}` on the scan endpoint (UI "advanced" field), so users
   who didn't set the env var can still scan.
3. **Auto-detect** from local interfaces using `ifaddr` (already a transitive dependency of
   `zeroconf`, add it explicitly to `requirements.txt`). Skip interfaces named `docker*`, `br-*`,
   `veth*`, `lo`. If running in a container (`/.dockerenv`) and the only remaining network is the
   container's own bridge (inside `172.16.0.0/12`, matches the default-gateway subnet), treat it as
   bridge mode: scan nothing and return a hint telling the user to set `PRINTER_SCAN_SUBNETS`.
   With host networking, this yields the real LAN subnet automatically.

Validation for every subnet (reuse the rules of `resolve_lan`): IPv4, private, not loopback /
link-local; cap prefix at `/22` (1022 hosts) per subnet and 4096 hosts total → 422 otherwise.

### 2. Probe
- `asyncio.open_connection(ip, 9100)` with ~0.4 s timeout, `asyncio.Semaphore(128)`; close immediately.
  A /24 finishes in ~1–2 s.
- For open hosts, run `vc500w.get_config` via `asyncio.to_thread` with a small semaphore (4) — this
  filters out other port-9100 devices (laser printers, etc.).
- Hostname: `socket.gethostbyaddr` (router DNS often knows it) with short timeout, falling back to
  `"VC-500W (<ip>)"`. Optional later: unicast mDNS query to `<ip>:5353` (RFC 6762 §6.7 legacy
  unicast replies are NAT-friendly) for the Bonjour name.
- Only one scan at a time (`asyncio.Lock`); a concurrent request waits for / reuses the running scan.
  Cache the last result for ~10 s to stop button-mashing from re-sweeping the LAN.

### 3. API (`backend/main.py`)
- `POST /api/printers/scan` — body optional `{"subnets": ["192.168.1.0/24"]}`.
  Response: `{"printers": [...], "subnets": ["192.168.1.0/24"], "scanned": 254, "hint": null | "set_subnets"}`.
- Registry entries gain `"source": "mdns" | "scan" | "manual"` (frontend ignores unknown fields).
- `GET /api/printers` unchanged (still returns mDNS + scan + manual results).
- Optional: `GET /api/printers/scan/config` returns the effective default subnets so the UI can
  pre-fill the advanced field.

### 4. Frontend (`frontend/src/app/canvas/`)
- "Discover printers" button calls `POST /api/printers/scan` then shows the merged list;
  disabled with a "Searching…" label while running. `ngOnInit` keeps the cheap `GET /api/printers`.
- If `hint === 'set_subnets'` or no printer found: show message with the scanned subnets and how to
  configure `PRINTER_SCAN_SUBNETS` / the advanced subnet field.
- Small collapsible "Network to search" input (CIDR), stored in localStorage.
- New i18n keys in `en.json` and `de.json` (`printer.searching`, `printer.noneFound`,
  `printer.setSubnets`, `printer.subnet`).

### 5. Docker / docs
- `docker-compose.yml`: add `PRINTER_SCAN_SUBNETS=${PRINTER_SCAN_SUBNETS:-}` with a comment;
  users put `PRINTER_SCAN_SUBNETS=192.168.1.0/24` in `.env`.
- New `docker-compose.host.yml` override (`network_mode: host`, `ports: !reset []`) for Linux users who
  want mDNS + auto-detected subnet: `docker compose -f docker-compose.yml -f docker-compose.host.yml up -d`.
- Update `README.md` (how to find your subnet: `ipconfig` / `ip route`), `missing.md`,
  `docker_compose_specification.md` (no longer *requires* host mode).

### 6. Production deployment (`ElTriredric/infrastructure.komodo`, `nodes/lenovo/komodo.label-designer.yaml`)
The app runs behind Traefik on the external `web_traffic` bridge network. **Do not switch to
`network_mode: host`**: Traefik reaches the container over `web_traffic`, and host mode would break that
routing. Bridge mode is fine for the scan because outbound connections from the container to the LAN
go through the Lenovo host's NAT. Changes required:
- Add `- PRINTER_SCAN_SUBNETS=<LAN CIDR of the Lenovo host>` to `environment` (find the CIDR on the host with
  `ip -4 route | grep -v docker`).
- Redeploy the stack in Komodo with a fresh pull of `ghcr.io/eltriredric/label-designer:latest` once CI has published the new image.
- If the printer is on a different VLAN than the Lenovo host, the router must allow TCP 9100 from the host to the printer.

## Tests (`backend/test_discovery.py`)
- Subnet parsing/validation: rejects public, loopback, link-local, too large, malformed; accepts env list.
- Interface auto-detect with mocked `ifaddr.get_adapters()`: host mode → LAN subnet;
  bridge mode (`/.dockerenv` + 172.17/16 only) → empty + hint; docker/veth interfaces skipped.
- Scan with patched TCP probe and `vc500w.get_config`: VC-500W added with `source: scan`; non-VC-500W
  on 9100 and `PrinterError` ignored; concurrent scans run once.
- Endpoint: `POST /api/printers/scan` with body, with env var, with nothing (hint), invalid subnet → 422.
- Frontend build stays green (CI already runs `npm run build`).

## Manual verification
1. Linux, default bridge compose + `PRINTER_SCAN_SUBNETS` → printer found.
2. Linux, host override, no env var → found via mDNS and auto-detected subnet.
3. Docker Desktop (macOS/Windows), bridge + env var → found.
4. Wrong subnet → "none found" message with hint; editing remains usable.

## Implementation order
1. `backend/discovery.py` + unit tests.
2. Scan endpoint + registry `source` field + endpoint tests.
3. Frontend button/flow + i18n.
4. Compose env var, host override file, docs.
