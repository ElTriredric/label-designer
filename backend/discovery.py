"""Unicast LAN discovery that also works from a Docker bridge network.

mDNS multicast does not cross Docker's NAT, but outbound unicast does. A scan
therefore (1) picks the LAN subnet(s), (2) connects to TCP 9100 on every host
without sending data, and (3) asks each open host via *unicast* mDNS (RFC 6762
section 6.7 legacy unicast, NAT friendly) whether it is a VC-500W. No print
protocol bytes are sent during a scan: other raw port-9100 printers would print
them as text.
"""
import asyncio
import ipaddress
import logging
import os
from pathlib import Path
import random
import socket
from typing import Iterable

import ifaddr
from zeroconf import DNSIncoming, DNSOutgoing, DNSPointer, DNSQuestion, DNSText
from zeroconf.const import _CLASS_IN, _FLAGS_QR_QUERY, _TYPE_PTR

log = logging.getLogger(__name__)

PORT = 9100
MDNS_PORT = 5353
MIN_PREFIX = 22            # at most 1022 hosts per subnet
MAX_HOSTS = 4096           # across all subnets of one scan
SERVICE_TYPES = ('_ipp._tcp.local.', '_printer._tcp.local.', '_pdl-datastream._tcp.local.')
MODEL_MARKERS = ('vc-500w', 'vc500w')
# Interfaces that never carry the LAN printers live on.
SKIPPED_INTERFACES = ('lo', 'docker', 'br-', 'veth', 'virbr', 'cni', 'flannel', 'cali',
                      'vxlan', 'tailscale', 'wg', 'tun', 'tap', 'zt')
ENV_SUBNETS = 'PRINTER_SCAN_SUBNETS'
ENV_SUBNETS_FILE = 'PRINTER_SCAN_SUBNETS_FILE'


def validate_subnet(value: str) -> ipaddress.IPv4Network:
    """Parse one CIDR and reject anything that is not a small private IPv4 LAN."""
    try:
        network = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f'Invalid subnet: {value!r}') from exc
    if network.version != 4:
        raise ValueError(f'Only IPv4 subnets are supported: {value!r}')
    if not network.is_private or network.is_loopback or network.is_link_local:
        raise ValueError(f'Subnet must be a private LAN: {value!r}')
    if network.prefixlen < MIN_PREFIX:
        raise ValueError(f'Subnet too large (max /{MIN_PREFIX}): {value!r}')
    return network


def parse_subnets(text: str | Iterable[str], strict: bool = True) -> list[ipaddress.IPv4Network]:
    """Parse comma/whitespace separated CIDRs; '#' starts a comment line."""
    if isinstance(text, str):
        lines = [line.split('#', 1)[0] for line in text.splitlines()]
        items = [item for line in lines for item in line.replace(',', ' ').split()]
    else:
        items = [item for item in text if item and item.strip()]
    result: list[ipaddress.IPv4Network] = []
    for item in items:
        try:
            network = validate_subnet(item)
        except ValueError:
            if strict:
                raise
            log.warning('Ignoring invalid printer scan subnet %r', item)
            continue
        if network not in result:
            result.append(network)
    if sum(network.num_addresses - 2 for network in result) > MAX_HOSTS:
        if strict:
            raise ValueError(f'Too many addresses to scan (max {MAX_HOSTS})')
        log.warning('Printer scan subnets exceed %d hosts; ignoring them', MAX_HOSTS)
        return []
    return result


def interface_subnets(adapters=None) -> list[ipaddress.IPv4Network]:
    """LAN subnets of the local network interfaces (the host's, with host networking).

    Overly large networks are narrowed to the /24 around the interface address.
    """
    result: list[ipaddress.IPv4Network] = []
    for adapter in adapters if adapters is not None else ifaddr.get_adapters():
        name = (adapter.nice_name or adapter.name or '').lower()
        if name.startswith(SKIPPED_INTERFACES):
            continue
        for address in adapter.ips:
            if not isinstance(address.ip, str):
                continue  # IPv6 entries are (address, flowinfo, scope) tuples
            ip = ipaddress.ip_address(address.ip)
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            prefix = address.network_prefix if address.network_prefix >= MIN_PREFIX else 24
            network = ipaddress.ip_network(f'{ip}/{prefix}', strict=False)
            if network.prefixlen >= 31 or network in result:
                continue
            result.append(network)
    return result


def _in_bridged_container(adapters) -> bool:
    """A bridge-networked container only sees its own veth (eth*) and lo.

    With host networking the host's docker0/br-* bridges are visible too.
    """
    if not Path('/.dockerenv').exists():
        return False
    names = [(adapter.nice_name or adapter.name or '').lower() for adapter in adapters]
    return not any(name.startswith(('docker', 'br-')) for name in names)


def configured_subnets(adapters=None) -> tuple[list[ipaddress.IPv4Network], str]:
    """Return (subnets, source). Read on every call so the sidecar file can change."""
    env = os.environ.get(ENV_SUBNETS, '').strip()
    if env:
        return parse_subnets(env, strict=False), 'env'
    path = os.environ.get(ENV_SUBNETS_FILE, '').strip()
    if path:
        try:
            subnets = parse_subnets(Path(path).read_text(encoding='utf-8'), strict=False)
        except OSError as exc:
            log.warning('Cannot read %s=%s: %s', ENV_SUBNETS_FILE, path, exc)
            subnets = []
        if subnets:
            return subnets, 'file'
    adapters = list(ifaddr.get_adapters()) if adapters is None else adapters
    if _in_bridged_container(adapters):
        return [], 'none'  # our own Docker network is not the printer LAN
    subnets = interface_subnets(adapters)
    return subnets, 'interfaces' if subnets else 'none'


async def port_open(ip: str, port: int = PORT, timeout: float = 0.5) -> bool:
    """Plain TCP connect; closes without sending a byte."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def mdns_query(ip: str, timeout: float = 1.0) -> dict | None:
    """Ask one host directly (unicast) for its printer services.

    Returns {'name': instance name, 'vc500w': bool} or None without an answer.
    """
    query = DNSOutgoing(_FLAGS_QR_QUERY, multicast=False)
    query.id = random.randint(1, 0xFFFF)
    for service in SERVICE_TYPES:
        query.add_question(DNSQuestion(service, _TYPE_PTR, _CLASS_IN))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(query.packets()[0], (ip, MDNS_PORT))
            while True:
                data, (source, _) = sock.recvfrom(9000)
                if source == ip:
                    break
        except OSError:
            return None
    try:
        response = DNSIncoming(data)
    except Exception:  # malformed packet from an arbitrary LAN device
        return None
    if response.id != query.id or not response.is_response():
        return None
    names: list[str] = []
    texts: list[bytes] = []
    for record in [*response.answers()]:
        if isinstance(record, DNSPointer):
            names.append(record.alias)
        elif isinstance(record, DNSText):
            texts.append(record.text)
        names.append(record.name)
    haystack = ' '.join(names).lower() + ' ' + b' '.join(texts).decode('utf-8', 'ignore').lower()
    instance = next((alias.split('._', 1)[0] for alias in names
                     if any(alias.endswith(service) for service in SERVICE_TYPES)
                     and not alias.startswith('_')), None)
    return {'name': instance, 'vc500w': any(marker in haystack for marker in MODEL_MARKERS)}


def reverse_name(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


async def scan(subnets: list[ipaddress.IPv4Network], concurrency: int = 128,
               timeout: float = 0.5, skip: Iterable[str] = ()) -> list[dict]:
    """Find port-9100 devices and tell VC-500Ws (verified via mDNS) apart."""
    skipped = set(skip)
    hosts = [str(ip) for network in subnets for ip in network.hosts() if str(ip) not in skipped]
    if len(hosts) > MAX_HOSTS:
        raise ValueError(f'Too many addresses to scan (max {MAX_HOSTS})')
    gate = asyncio.Semaphore(concurrency)

    async def probe(ip: str) -> str | None:
        async with gate:
            return ip if await port_open(ip, timeout=timeout) else None

    candidates = [ip for ip in await asyncio.gather(*map(probe, hosts)) if ip]

    async def describe(ip: str) -> dict:
        info = await asyncio.to_thread(mdns_query, ip)
        name = (info or {}).get('name')
        if not name:
            try:
                name = await asyncio.wait_for(asyncio.to_thread(reverse_name, ip), 1.5)
            except asyncio.TimeoutError:
                name = None
        return {'ip': ip, 'hostname': name or ip, 'source': 'scan',
                'verified': bool(info and info['vc500w'])}

    found = await asyncio.gather(*map(describe, candidates))
    # Verified VC-500Ws first, then other port-9100 devices.
    return sorted(found, key=lambda item: (not item['verified'], ipaddress.ip_address(item['ip'])))
