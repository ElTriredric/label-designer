"""Independent VC-500W XML/TCP implementation based on published wire examples.

Protocol reference: https://github.com/Sunburn-Schematics/brother-vc500w-driver
No source code from that project is included. The printer's reply consists of a status
XML document, optionally a NUL byte, then exactly <datasize> bytes of XML payload.
"""
from io import BytesIO
import re
import socket
import time
import xml.etree.ElementTree as ET

from PIL import Image

PORT = 9100
PREFIX = b'<?xml version="1.0" encoding="UTF-8"?>\n'
READ_CONFIG = b'<read>\n<path>/config.xml</path>\n</read>'
READ_STATUS = b'<read>\n<path>/status.xml</path>\n</read>'
WIDTHS = (9, 12, 19, 25, 50)
MAX_XML = 65536


class ProtocolError(Exception):
    pass


class PrinterError(Exception):
    pass


class Connection:
    def __init__(self, address: str, timeout: float = 6):
        self.address = address
        self.timeout = timeout
        self.sock = None
        self.buffer = bytearray()

    def __enter__(self):
        self.sock = socket.create_connection((self.address, PORT), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        return self

    def __exit__(self, *_):
        self.sock.close()  # Closing the connection triggers the printer's cut cycle.

    def _receive(self):
        block = self.sock.recv(4096)
        if not block:
            raise ProtocolError('Printer closed connection before its response was complete')
        self.buffer.extend(block)
        if len(self.buffer) > MAX_XML:
            raise ProtocolError('Printer response exceeds XML size limit')

    def _xml(self, root: str):
        closing = f'</{root}>'.encode()
        while True:
            # Replies can start with a NUL delimiter between XML documents.
            while self.buffer and self.buffer[0] in b'\x00\r\n\t ':
                del self.buffer[0]
            start = re.search(rb'<(?:\?xml\b|[a-zA-Z_])', self.buffer)
            if start and start.start():
                del self.buffer[:start.start()]
            end = self.buffer.find(closing)
            if end != -1:
                length = end + len(closing)
                raw = bytes(self.buffer[:length])
                del self.buffer[:length]
                try:
                    return ET.fromstring(raw)
                except ET.ParseError as exc:
                    raise ProtocolError('Invalid printer XML response') from exc
            self._receive()

    def _exact(self, count: int):
        if not 0 <= count <= MAX_XML:
            raise ProtocolError('Invalid printer payload length')
        # Firmware inserts a newline and NUL between header and payload.
        while not self.buffer:
            self._receive()
        while self.buffer and self.buffer[0] in b'\x00\r\n\t ':
            del self.buffer[0]
            if not self.buffer:
                self._receive()
        while len(self.buffer) < count:
            self._receive()
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def _reply(self):
        header = self._xml('status')
        if header.tag != 'status':
            raise ProtocolError('Unexpected printer reply')
        code = header.findtext('code')
        if code is None or not re.fullmatch(r'-?\d+', code):
            raise ProtocolError('Missing printer response code')
        if int(code) != 0:
            raise PrinterError(f'Printer error {code}: {header.findtext("comment") or "unknown error"}')
        size = header.findtext('datasize')
        if size is None:
            return None
        if not size.isdecimal():
            raise ProtocolError('Invalid printer datasize')
        payload = self._exact(int(size))
        # The response may use <status> or <config> as its second XML root.
        try:
            return ET.fromstring(payload.lstrip(b'\x00\r\n\t '))
        except ET.ParseError as exc:
            raise ProtocolError('Invalid printer payload') from exc

    def read(self, path: bytes):
        self.sock.sendall(PREFIX + path)
        result = self._reply()
        if result is None:
            raise ProtocolError('Printer sent no payload')
        return result

    def print_image(self, jpeg: bytes, cut: str, on_progress):
        if cut not in ('full', 'half', 'none'):
            raise ValueError('Invalid cut mode')
        command = ('<print>\n<mode>vivid</mode>\n<speed>0</speed>\n<lpi>317</lpi>\n'
                   '<width>0</width>\n<height>0</height>\n<dataformat>jpeg</dataformat>\n'
                   f'<autofit>1</autofit>\n<datasize>{len(jpeg)}</datasize>\n'
                   f'<cutmode>{cut}</cutmode>\n</print>').encode('ascii')
        self.sock.sendall(PREFIX + command)
        self._reply()  # Require explicit code 0 before sending a single image byte.
        on_progress('transmitting', 10)
        for offset in range(0, len(jpeg), 16384):
            self.sock.sendall(jpeg[offset:offset + 16384])
            on_progress('transmitting', 10 + round(65 * min(offset + 16384, len(jpeg)) / len(jpeg)))
        # Acknowledgment says data accepted, not that physical print/cut is complete.
        self.sock.settimeout(120)
        self._reply()
        on_progress('printing', 80)


def get_config(ip: str):
    with Connection(ip) as connection:
        root = connection.read(READ_CONFIG)
    if root.tag != 'config':
        raise ProtocolError('Unexpected config response')
    model = root.findtext('model_name')
    if model not in ('Wedge', 'VC-500W'):
        raise PrinterError('Connected device is not a VC-500W')
    media = root.find('media_features')
    if media is None:
        raise PrinterError('No cassette information available')
    width = media.findtext('width_inches') or root.findtext('width_inches')
    try:
        width_mm = float(width) * 25.4
    except (ValueError, TypeError) as exc:
        raise PrinterError('No cassette width available') from exc
    nearest = min(WIDTHS, key=lambda w: abs(w - width_mm))
    if abs(nearest - width_mm) > 2:
        raise PrinterError('Unsupported cassette width')
    cassette = media.findtext('cassette_type') or root.findtext('cassette_type')
    if cassette and ('CK-' in cassette.upper() or 'CLEAN' in cassette.upper()):
        tape_type = 'Cleaning Tape'
    elif cassette and cassette.upper().startswith('CZ-'):
        tape_type = 'Print Tape'
    elif (cassette and re.fullmatch(r'\d+(?:/\d+)?', cassette)
          and media.findtext('adhesive') and media.findtext('media_id')):
        # Observed on adhesive print rolls identified as '1' and '1/2'.
        # Other numeric widths must also carry adhesive and media ID attributes.
        tape_type = 'Print Tape'
    else:
        tape_type = 'Unknown'
    return {'model': model, 'firmware': root.findtext('fw_version_number'),
            'cassette_type': cassette, 'tape_type': tape_type,
            'tape_width_mm': nearest, 'reported_width_mm': round(width_mm, 2)}


def get_status(ip: str):
    with Connection(ip) as connection:
        root = connection.read(READ_STATUS)
    if root.tag != 'status':
        raise ProtocolError('Unexpected status response')
    state = (root.findtext('print_state') or 'UNKNOWN').upper()
    error = root.findtext('print_job_error') or 'UNKNOWN'
    stage = root.findtext('print_job_stage') or 'UNKNOWN'
    try:
        remaining = round(float(root.findtext('remain')) * 25.4, 1)
    except (TypeError, ValueError):
        remaining = None
    if error.upper() not in ('NONE', 'SUCCESS'):
        state = 'ERROR'
    return {'status': state, 'stage': stage, 'error': error, 'tape_remaining_mm': remaining}


def png_to_jpeg(data: bytes):
    with Image.open(BytesIO(data)) as original:
        # JPEG cannot carry transparency. Compose alpha on white, preserving RGB color.
        img = original.convert('RGBA')
        background = Image.new('RGBA', img.size, 'white')
        background.alpha_composite(img)
        result = BytesIO()
        # Browser canvas is length × tape width. Printer autofit expects tape
        # width across JPEG X and feed length along JPEG Y.
        background.convert('RGB').transpose(Image.Transpose.ROTATE_270).save(
            result, format='JPEG', quality=95, subsampling=0)
        return result.getvalue()


def execute_print(ip: str, png: bytes, width: int, cut: str, on_progress):
    config = get_config(ip)
    if config['tape_width_mm'] != width:
        raise PrinterError(f'Loaded tape is {config["tape_width_mm"]} mm, design is {width} mm')
    if config['tape_type'] != 'Print Tape':
        raise PrinterError('Cannot confirm a print tape is loaded')
    status = get_status(ip)
    if status['status'] not in ('IDLE', 'READY'):
        raise PrinterError(f'Printer not ready: {status["status"]} ({status["error"]})')
    on_progress('preparing', 5)
    jpeg = png_to_jpeg(png)
    with Connection(ip) as connection:
        connection.print_image(jpeg, cut, on_progress)
    # The socket MUST close before the cut starts. Poll with new connections.
    # The printer has acknowledged every JPEG byte. Transmission is finished;
    # IDLE is a separate physical readiness signal (often gated by output sensor).
    on_progress('verifying', 100)
    deadline = time.monotonic() + 40
    time.sleep(2)
    while time.monotonic() < deadline:
        status = get_status(ip)
        if status['status'] == 'IDLE':
            on_progress('complete', 100)
            return 'complete'
        if status['status'] == 'ERROR':
            raise PrinterError(f'Print failed: {status["error"]}')
        time.sleep(2)
    # The output-slot sensor can keep the printer non-idle until a person removes
    # the finished label. The successful image ACK is not proof of physical
    # completion, but lack of IDLE is not proof of a failed print either.
    on_progress('accepted', 100)
    return 'accepted'


def wait_for_next_segment(ip: str, on_wait, timeout: float = 180) -> bool:
    """Never submit another segment until the previous cut clears the output slot."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = get_status(ip)
        except (OSError, ProtocolError):
            on_wait()
            time.sleep(2.5)
            continue
        if status['status'] == 'IDLE':
            return True
        if status['status'] == 'ERROR':
            raise PrinterError(f'Printer error after cut: {status["error"]}')
        on_wait()
        time.sleep(2.5)
    return False
