"""Sidecar entry point: write the Docker host's LAN subnets to a shared file.

Run in a one-shot container with ``network_mode: host`` so the host's interfaces
are visible:  python -m backend.detect_subnets /shared/subnets
The app (on a bridge network) reads the file via PRINTER_SCAN_SUBNETS_FILE.
"""
import logging
import os
from pathlib import Path
import sys

from .discovery import ENV_SUBNETS, interface_subnets, parse_subnets

log = logging.getLogger('detect_subnets')


def detect() -> list[str]:
    override = os.environ.get(ENV_SUBNETS, '').strip()
    subnets = parse_subnets(override, strict=False) if override else interface_subnets()
    return [str(network) for network in subnets]


def write(path: Path, subnets: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text('# Written by backend.detect_subnets\n' + ''.join(f'{s}\n' for s in subnets),
                         encoding='utf-8')
    temporary.replace(path)  # atomic: the app never reads a half-written file


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
    if len(argv) != 2:
        print('usage: python -m backend.detect_subnets <output-file>', file=sys.stderr)
        return 2
    subnets = detect()
    write(Path(argv[1]), subnets)
    if subnets:
        log.info('Printer scan subnets: %s', ', '.join(subnets))
    else:
        # Exit 0 anyway so the app still starts; manual IP entry keeps working.
        log.warning('No private LAN subnet found. Is this container using network_mode: host?')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
