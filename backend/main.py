"""Local VC-500W editor API with XML/TCP printer communication."""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from io import BytesIO
import ipaddress
import logging
from pathlib import Path
import socket
import threading
import uuid
from . import discovery, vc500w
from .segments import split_raster

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

log = logging.getLogger(__name__)
PORT = 9100
DPI = 313
WIDTHS = (9, 12, 19, 25, 50)
printers: dict[str, dict[str, str]] = {}
printer_lock = threading.Lock()
job_lock = threading.Lock()
jobs: dict[str, dict] = {}
active_printers: set[str] = set()


class PrinterListener(ServiceListener):
    """Resolve mDNS records into usable IP addresses, not raw address bytes."""

    def __init__(self, zc: Zeroconf):
        self.zc = zc

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=1500)
        if not info:
            return
        addresses = info.parsed_scoped_addresses()
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if ip.version == 4 and ip.is_private:
                with printer_lock:
                    printers[str(ip)] = {"ip": str(ip), "hostname": name, "source": "mdns",
                                         "verified": "vc-500w" in name.lower()}

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        with printer_lock:
            for address, item in list(printers.items()):
                if item["hostname"] == name and item.get("source") == "mdns":
                    del printers[address]


@asynccontextmanager
async def lifespan(app: FastAPI):
    zc = None
    try:
        zc = Zeroconf()
        listener = PrinterListener(zc)
        browsers = [ServiceBrowser(zc, service, listener) for service in
                    ("_ipp._tcp.local.", "_printer._tcp.local.")]
    except OSError as exc:
        log.warning("mDNS discovery unavailable: %s", exc)
    try:
        yield
    finally:
        if zc:
            zc.close()


app = FastAPI(lifespan=lifespan)


class Connection(BaseModel):
    address: str = Field(min_length=1, max_length=253)


class ScanRequest(BaseModel):
    subnets: list[str] = Field(default_factory=list, max_length=8)


class PrintJob(BaseModel):
    image: str
    tape_cut: str = 'full'
    width_mm: int
    length_mm: float
    cut_positions_mm: list[float] = Field(default_factory=list)


def resolve_lan(address: str) -> str:
    """Only permit private IPv4 LAN targets (not loopback, public or metadata IPs)."""
    if not address or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for ch in address):
        raise HTTPException(422, 'Invalid IPv4 address or hostname')
    try:
        answers = socket.getaddrinfo(address, PORT, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(422, 'Cannot resolve printer address') from exc
    addresses = {entry[4][0] for entry in answers}
    if not addresses or any(not ipaddress.ip_address(ip).is_private or
                            ipaddress.ip_address(ip).is_loopback or
                            ipaddress.ip_address(ip).is_link_local for ip in addresses):
        raise HTTPException(422, 'Printer must be on a private IPv4 LAN')
    return sorted(addresses)[0]


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.get('/api/printers')
def list_printers():
    with printer_lock:
        return {'printers': list(printers.values())}


@app.post('/api/printers/connect')
async def connect_printer(connection: Connection):
    ip = await asyncio.to_thread(resolve_lan, connection.address)
    try:
        config = await asyncio.to_thread(vc500w.get_config, ip)
    except (OSError, vc500w.ProtocolError, vc500w.PrinterError) as exc:
        raise HTTPException(503, f'Cannot identify VC-500W: {exc}') from exc
    with printer_lock:
        hostname = printers.get(ip, {}).get('hostname', connection.address)
        printers[ip] = {'ip': ip, 'hostname': hostname, 'source': 'manual', 'verified': True}
    return {'ip': ip, 'reachable': True, **config}


scan_lock = asyncio.Lock()
scan_cache: dict = {'key': None, 'at': 0.0, 'result': None}
SCAN_CACHE_SECONDS = 10


@app.get('/api/printers/scan/config')
def scan_config():
    subnets, source = discovery.configured_subnets()
    return {'subnets': [str(s) for s in subnets], 'source': source}


@app.post('/api/printers/scan')
async def scan_printers(request: ScanRequest | None = None):
    """Unicast scan of the LAN for VC-500Ws; works on a Docker bridge network."""
    if request and request.subnets:
        try:
            subnets = discovery.parse_subnets(request.subnets)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        source = 'request'
    else:
        subnets, source = await asyncio.to_thread(discovery.configured_subnets)
    if not subnets:
        with printer_lock:
            known = list(printers.values())
        return {'printers': known, 'subnets': [], 'source': source, 'scanned': 0, 'hint': 'set_subnets'}
    key = tuple(str(s) for s in subnets)
    async with scan_lock:  # one sweep at a time; concurrent clicks reuse its result
        loop = asyncio.get_running_loop()
        if scan_cache['key'] == key and loop.time() - scan_cache['at'] < SCAN_CACHE_SECONDS:
            found = scan_cache['result']
        else:
            with job_lock:
                busy = set(active_printers)  # never touch a printer mid-job
            found = await discovery.scan(subnets, skip=busy)
            scan_cache.update(key=key, at=loop.time(), result=found)
    with printer_lock:
        for item in found:
            existing = printers.get(item['ip'])
            if existing and existing.get('source') in ('manual', 'mdns'):
                existing['verified'] = existing.get('verified') or item['verified']
            else:
                printers[item['ip']] = dict(item)
        known = list(printers.values())
    return {'printers': known, 'subnets': list(key), 'source': source,
            'scanned': sum(s.num_addresses - 2 for s in subnets), 'hint': None}


@app.get('/api/printers/{address}/status')
async def printer_status(address: str):
    ip = await asyncio.to_thread(resolve_lan, address)
    try:
        status = await asyncio.to_thread(vc500w.get_status, ip)
        config = await asyncio.to_thread(vc500w.get_config, ip)
        return {'ip': ip, 'reachable': True, **status, **config}
    except (OSError, vc500w.ProtocolError, vc500w.PrinterError) as exc:
        raise HTTPException(503, f'Could not query VC-500W: {exc}') from exc


@app.get('/api/printers/{address}/tape')
async def tape_info(address: str):
    ip = await asyncio.to_thread(resolve_lan, address)
    try:
        return await asyncio.to_thread(vc500w.get_config, ip)
    except (OSError, vc500w.ProtocolError, vc500w.PrinterError) as exc:
        raise HTTPException(503, f'Could not query cassette: {exc}') from exc


def prepare_raster(job: PrintJob) -> bytes:
    """Validate exact 313-DPI raster dimensions, return PNG for JPEG conversion."""
    if job.width_mm not in WIDTHS or not 10 <= job.length_mm <= 432:
        raise HTTPException(422, 'Invalid label dimensions')
    if job.tape_cut not in ('full', 'half', 'none'):
        raise HTTPException(422, 'Invalid cut mode')
    if len(job.image) > 30_000_000:
        raise HTTPException(413, 'Image too large')
    try:
        data = base64.b64decode(job.image, validate=True)
        with Image.open(BytesIO(data)) as img:
            if img.format != 'PNG':
                raise HTTPException(422, 'Expected PNG image')
            expected = (round(job.length_mm * DPI / 25.4), round(job.width_mm * DPI / 25.4))
            if img.size != expected:
                raise HTTPException(422, f'Expected {expected[0]}x{expected[1]} pixels at 313 DPI')
            img.load()  # Reject corrupt or truncated image data.
            return data
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(422, 'Invalid image') from exc


def run_job(job_id: str, ip: str, pieces: list[bytes], width: int, cut: str):
    total = len(pieces)
    try:
        for index, piece in enumerate(pieces):
            def progress(stage: str, percent: int):
                # A cut between segments must never appear as final job completion
                # to clients that poll while the next segment is still pending.
                visible_stage = ('finishing_segment' if index < total - 1 and
                                 stage in ('complete', 'accepted') else stage)
                with job_lock:
                    jobs[job_id].update(stage=visible_stage,
                                        progress=round((index + percent / 100) * 100 / total))
                    if index == total - 1 and stage in ('complete', 'accepted'):
                        jobs[job_id]['sent'] = total
            result = vc500w.execute_print(ip, piece, width, cut, progress)
            with job_lock:
                jobs[job_id]['sent'] = index + 1
            if index < total - 1 and result not in ('complete', 'accepted'):
                raise vc500w.ProtocolError('Previous segment was not confirmed; stopped batch')
            if index < total - 1 and result == 'accepted':
                def waiting():
                    with job_lock:
                        jobs[job_id].update(stage='waiting_for_removal',
                                            progress=round((index + 1) * 100 / total))
                waiting()
                if not vc500w.wait_for_next_segment(ip, waiting):
                    with job_lock:
                        jobs[job_id].update(stage='partial',
                            error='Printer did not return to IDLE; remove printed label and check before retrying')
                    return
            if index < total - 1:
                with job_lock:
                    jobs[job_id].update(stage='queued', progress=round((index + 1) * 100 / total))
    except Exception as exc:
        log.exception('Print job %s stopped after %d of %d segments', job_id, jobs[job_id]['sent'], total)
        with job_lock:
            jobs[job_id].update(stage='partial' if jobs[job_id]['sent'] else 'error',
                                error=str(exc))
    finally:
        with job_lock:
            active_printers.discard(ip)


@app.post('/api/printers/{address}/print', status_code=202)
async def print_job(address: str, job: PrintJob):
    ip = await asyncio.to_thread(resolve_lan, address)
    png = await asyncio.to_thread(prepare_raster, job)
    pieces = await asyncio.to_thread(split_raster, png, job.length_mm, job.cut_positions_mm, job.tape_cut)
    with job_lock:
        if ip in active_printers:
            raise HTTPException(409, 'Another print job is already running on this printer')
        # Bound in-memory history; the browser polls jobs until completion.
        if len(jobs) >= 100:
            for key, value in list(jobs.items()):
                if value['stage'] in ('complete', 'accepted', 'error'):
                    del jobs[key]
                    break
            if len(jobs) >= 100:
                raise HTTPException(503, 'Too many outstanding print jobs')
        job_id = uuid.uuid4().hex
        jobs[job_id] = {'id': job_id, 'stage': 'queued', 'progress': 0, 'error': None,
                        'sent': 0, 'segments': len(pieces)}
        active_printers.add(ip)
    asyncio.create_task(asyncio.to_thread(run_job, job_id, ip, pieces, job.width_mm, job.tape_cut))
    return {'id': job_id}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    with job_lock:
        if job_id not in jobs:
            raise HTTPException(404, 'Unknown print job')
        return jobs[job_id].copy()


static_path = Path(__file__).resolve().parent.parent / 'static'
if static_path.is_dir():
    app.mount('/', StaticFiles(directory=str(static_path), html=True), name='static')
