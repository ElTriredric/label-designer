# VC-500W label designer

Angular 22 / Fabric editor with a browser-local IndexedDB library and a FastAPI LAN printer service.

## Run

```sh
# Works on Docker Desktop (Windows/macOS) and Linux
# -d keeps it running after the command finishes
docker compose up -d --build --force-recreate
# open http://localhost:8000 (health check: http://localhost:8000/health)
```

Check `docker compose ps` and `docker compose logs --tail=80` if the page cannot be reached. Stop with `docker compose down`.

### Printer discovery in Docker

The app container uses normal bridge networking (port 8000 published, works behind a reverse proxy). mDNS multicast cannot reach a bridged container, so **Discover printers** scans the LAN by unicast instead:

1. On startup, the one-shot `netdetect` service runs with `network_mode: host`, reads the Docker host's LAN subnet(s) (Docker/VPN/loopback interfaces are skipped; networks larger than /22 are narrowed to the host's /24) and writes them to `/shared/subnets` on the `netinfo` volume, then exits. It runs again on every `docker compose up`.
2. The app reads that file via `PRINTER_SCAN_SUBNETS_FILE`. On **Discover printers**, it opens a TCP connection to port 9100 on every address (no data is sent, so other raw printers print nothing) and confirms VC-500Ws by a unicast mDNS query. A /24 takes about a second. Unconfirmed port 9100 devices are listed as "not confirmed as VC-500W".

Priority of the subnet source: the *Network to search* field in the UI → `PRINTER_SCAN_SUBNETS` (comma-separated CIDRs, e.g. `192.168.1.0/24`) → the sidecar file → the container's own interfaces (only with host networking). Only private IPv4 networks up to /22 (4096 addresses in total) are accepted. On **Docker Desktop** (macOS/Windows) host networking shows the VM rather than your LAN, so set `PRINTER_SCAN_SUBNETS` in `.env` or enter the network in the UI. Entering a printer IP manually always works.

For development, start `python -m uvicorn backend.main:app --reload` at repository root after installing `backend/requirements.txt`, then run `cd frontend && npm install && npm start`. Angular proxies `/api` to port 8000. `npm run build` produces the static site. Run backend checks using `python -m pytest backend -q` (install `pytest` and `httpx` for tests).

Select an object on the canvas to delete it with **Delete/Backspace** or copy/paste it with **Ctrl+C / Ctrl+V** (⌘ on macOS). These shortcuts do not intercept typing in fields or editing text on the canvas. The **Copy**, **Paste**, and **Clone** buttons under Objects provide the same actions on mobile/tablet; cloning does not overwrite your copied object. Clipboard operations are within the editor and do not read/write the operating-system clipboard. **Cut markers:** Under Objects, add one or more red cut markers and drag them across the label length (or enter their millimetre positions). These are non-printing guides saved with the label. Each segment must be at least 10 mm; up to 15 markers create 16 sequential print jobs. Full/Half Cut applies between segments and at the end; No Cut cannot be combined with markers. The printer cuts **across** the tape only, never lengthwise. If the printer's output sensor blocks the next segment, the job waits for you to remove the previous label; after a timeout it stops without automatically repeating the already-sent segments. Check the physical labels before retrying a partial job. Segment spacing and cutting have been tested with simulated printer responses but **not physically validated** on a multi-segment print.

The editor offers English and German through Transloco; its language selector remembers your choice in browser localStorage. UI translations are bundled in `frontend/src/app/i18n/en.json` and `de.json`. Saved label content and names are user data and are not rewritten when you switch languages. Labels and thumbnails are stored **only in this browser's IndexedDB**. Download a 313 DPI PNG from the editor for manual use. Limit access to the FastAPI host to a trusted LAN: printer discovery, connection, and printing endpoints have no authentication. **Printing consumes tape**. Always confirm the tape width, length and cut setting before starting a job. If it times out, inspect the printer before retrying to avoid duplicate prints.

## Protocol provenance and testing

The XML/TCP message format, JPEG transport, cut modes and socket-close behavior were documented by [Sunburn Schematics' VC-500W driver project](https://github.com/Sunburn-Schematics/brother-vc500w-driver) (PolyForm Noncommercial license). **This app does not import, bundle or copy that project's source code**; `backend/vc500w.py` is an independent Python implementation of the documented wire protocol. Credit for reverse engineering belongs to Sunburn Schematics and their cited predecessors. Review third-party terms before reusing their code or documentation in a commercial product.

Read-only config/status queries were verified against `192.168.5.225` (firmware `2022071322`, 25 mm tape). The user successfully printed a label, but the printer did not return to IDLE within 40 seconds. An acknowledged print that remains non-IDLE is now reported as `accepted` with a prompt to check the output, rather than incorrectly reported as a failed print. No USB support is included; see `missing.md` for further physical validation and unknown cassette/status codes.
