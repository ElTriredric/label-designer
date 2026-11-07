"""API and printer protocol tests; all print requests use mocks, never hardware."""
import base64
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import app
from backend import vc500w
from backend.segments import split_raster


def png(width=739, height=148):
    out = BytesIO()
    Image.new('RGBA', (width, height), 'red').save(out, format='PNG')
    return base64.b64encode(out.getvalue()).decode()


def test_health_and_discovery():
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        assert isinstance(client.get('/api/printers').json()['printers'], list)


def test_lan_validation():
    with TestClient(app) as client:
        for address in ('127.0.0.1', '8.8.8.8', '169.254.169.254', 'localhost', 'bad/name'):
            assert client.post('/api/printers/connect', json={'address': address}).status_code == 422


def test_manual_connect_and_status():
    config = {'model': 'Wedge', 'tape_width_mm': 25, 'tape_type': 'Print Tape'}
    with patch('backend.main.vc500w.get_config', return_value=config), patch(
        'backend.main.vc500w.get_status', return_value={'status': 'IDLE', 'error': 'NONE'}
    ):
        with TestClient(app) as client:
            assert client.post('/api/printers/connect', json={'address': '192.168.1.55'}).json()['reachable']
            assert client.get('/api/printers/192.168.1.55/status').json()['status'] == 'IDLE'
            assert client.get('/api/printers/192.168.1.55/tape').json()['tape_width_mm'] == 25


def test_print_validation_never_starts_job():
    with TestClient(app) as client, patch('backend.main.vc500w.execute_print') as execute:
        job = {'image': png(), 'width_mm': 12, 'length_mm': 60, 'tape_cut': 'full'}
        for bad in ({'tape_cut': 'invalid'}, {'image': png(10, 10)}, {'image': 'not base64'}):
            assert client.post('/api/printers/192.168.1.55/print', json={**job, **bad}).status_code == 422
        execute.assert_not_called()


def test_async_print_job_progress_without_hardware():
    import time
    def pretend_print(ip, image, width, cut, progress):
        assert ip == '192.168.1.55' and image.startswith(b'\x89PNG')
        assert width == 12 and cut == 'half'
        progress('transmitting', 50)
        progress('complete', 100)
    with patch('backend.main.vc500w.execute_print', side_effect=pretend_print):
        with TestClient(app) as client:
            response = client.post('/api/printers/192.168.1.55/print', json={
                'image': png(), 'width_mm': 12, 'length_mm': 60, 'tape_cut': 'half'
            })
            assert response.status_code == 202
            job_id = response.json()['id']
            for _ in range(50):
                result = client.get(f'/api/jobs/{job_id}').json()
                if result['stage'] == 'complete': break
                time.sleep(0.02)
            assert result['stage'] == 'complete' and result['progress'] == 100


def test_split_raster_exact_pixel_boundaries_and_original_unchanged():
    original = base64.b64decode(png(739, 148))
    pieces = split_raster(original, 60, [20, 40], 'full')
    sizes = [Image.open(BytesIO(piece)).size for piece in pieces]
    assert sizes == [(246, 148), (247, 148), (246, 148)]
    assert sum(width for width, _ in sizes) == 739
    assert split_raster(original, 60, [], 'none') == [original]


def test_invalid_cut_positions_rejected_before_printer_contact():
    with TestClient(app) as client, patch('backend.main.vc500w.execute_print') as execute:
        job = {'image': png(), 'width_mm': 12, 'length_mm': 60, 'tape_cut': 'full'}
        for cuts, mode in [([5], 'full'), ([55], 'half'), ([20, 21], 'full'),
                           ([30, 20], 'full'), ([20, 40], 'none'),
                           (list(range(10, 170, 10)), 'full')]:
            response = client.post('/api/printers/192.168.1.55/print',
                                   json={**job, 'cut_positions_mm': cuts, 'tape_cut': mode})
            assert response.status_code == 422, (cuts, response.text)
        execute.assert_not_called()


def test_segmented_job_sends_in_order_and_stops_if_output_blocked():
    import time
    job = {'image': png(), 'width_mm': 12, 'length_mm': 60,
           'tape_cut': 'full', 'cut_positions_mm': [20, 40]}
    sent = []
    def pretend(ip, part, width, cut, progress):
        with Image.open(BytesIO(part)) as img:
            sent.append(img.size)
        progress('complete', 100)
        return 'complete'
    with patch('backend.main.vc500w.execute_print', side_effect=pretend):
        with TestClient(app) as client:
            response = client.post('/api/printers/192.168.1.55/print', json=job)
            assert response.status_code == 202
            for _ in range(50):
                state = client.get('/api/jobs/' + response.json()['id']).json()
                if state['stage'] == 'complete': break
                time.sleep(0.02)
            assert state['stage'] == 'complete' and state['segments'] == 3 and state['sent'] == 3
    assert sent == [(246, 148), (247, 148), (246, 148)]

    sent.clear()
    def first_accepted(ip, part, width, cut, progress):
        sent.append(part)
        progress('accepted', 100)
        return 'accepted'
    with patch('backend.main.vc500w.execute_print', side_effect=first_accepted), \
         patch('backend.main.vc500w.wait_for_next_segment', return_value=False):
        with TestClient(app) as client:
            response = client.post('/api/printers/192.168.1.55/print', json=job)
            for _ in range(50):
                state = client.get('/api/jobs/' + response.json()['id']).json()
                if state['stage'] == 'partial': break
                time.sleep(0.02)
            assert state['stage'] == 'partial' and state['sent'] == 1 and state['segments'] == 3
    assert len(sent) == 1  # Never sends the next piece without IDLE.


def test_batch_waits_for_idle_before_second_segment():
    import time
    from backend import main
    seen = []
    def fake_print(ip, part, width, cut, progress):
        if not seen:
            progress('accepted', 100)
            with main.job_lock:
                seen.append(list(main.jobs.values())[-1]['stage'])
            return 'accepted'
        progress('complete', 100)
        seen.append('second')
        return 'complete'
    with patch('backend.main.vc500w.execute_print', side_effect=fake_print), \
         patch('backend.main.vc500w.wait_for_next_segment', return_value=True) as ready:
        with TestClient(app) as client:
            job = {'image': png(), 'width_mm': 12, 'length_mm': 60,
                   'cut_positions_mm': [30], 'tape_cut': 'full'}
            response = client.post('/api/printers/192.168.1.55/print', json=job)
            for _ in range(50):
                state = client.get('/api/jobs/' + response.json()['id']).json()
                if state['stage'] == 'complete': break
                time.sleep(0.02)
            assert state['stage'] == 'complete' and state['sent'] == 2
            assert ready.call_count == 1 and seen == ['finishing_segment', 'second']


def test_printer_reply_with_split_header_and_nul_delimited_payload():
    payload = b'<?xml version="1.0" encoding="UTF-8"?>\n<status><print_state>IDLE</print_state></status>\n'
    header = f'<?xml version="1.0" encoding="UTF-8"?>\n<status><code>0</code><datasize>{len(payload)}</datasize></status>\n'.encode()
    class FakeSocket:
        def __init__(self):
            self.pieces = iter([header[:11], header[11:] + b'\x00' + payload[:14], payload[14:]])
        def recv(self, _): return next(self.pieces)
        def sendall(self, value): self.sent = value
    connection = vc500w.Connection('192.168.1.55')
    connection.sock = FakeSocket()
    assert connection.read(vc500w.READ_STATUS).findtext('print_state') == 'IDLE'
    assert b'/status.xml' in connection.sock.sent


def test_print_sends_jpeg_only_after_success_and_correct_cut():
    class FakeSocket:
        def __init__(self, code):
            self.pieces = iter([f'<status><code>{code}</code></status>'.encode(), b'<status><code>0</code></status>'])
            self.sent = []
        def recv(self, _): return next(self.pieces)
        def sendall(self, value): self.sent.append(value)
        def settimeout(self, value): pass
    connection = vc500w.Connection('192.168.1.55')
    connection.sock = FakeSocket(0)
    jpeg = b'\xff\xd8' + b'\x22' * 100 + b'\xff\xd9'
    connection.print_image(jpeg, 'half', lambda *_: None)
    assert b'<cutmode>half</cutmode>' in connection.sock.sent[0]
    assert f'<datasize>{len(jpeg)}</datasize>'.encode() in connection.sock.sent[0]
    assert b'<dataformat>jpeg</dataformat>' in connection.sock.sent[0]
    assert connection.sock.sent[1] == jpeg
    connection.sock = FakeSocket(3)
    try:
        connection.print_image(jpeg, 'full', lambda *_: None)
    except vc500w.PrinterError:
        pass
    else:
        assert False, 'Printer error must prevent image transmission'
    assert len(connection.sock.sent) == 1


def test_print_refuses_unknown_tape_or_width_before_sending():
    with patch('backend.vc500w.get_config', return_value={
        'tape_width_mm': 25, 'tape_type': 'Unknown'
    }), patch('backend.vc500w.Connection') as connection:
        for width in (12, 25):
            try:
                vc500w.execute_print('192.168.1.55', base64.b64decode(png()), width, 'full', lambda *_: None)
            except vc500w.PrinterError:
                pass
            else:
                assert False, 'Unverified media must be rejected'
        connection.assert_not_called()


def test_fractional_cassette_with_adhesive_is_print_tape():
    import xml.etree.ElementTree as ET
    response = ET.fromstring('''<config><model_name>Wedge</model_name>
      <media_features><cassette_type>1/2</cassette_type><width_inches>0.535</width_inches>
      <adhesive>Permanent</adhesive><media_id>B2E2</media_id></media_features></config>''')
    with patch('backend.vc500w.Connection') as connection:
        connection.return_value.__enter__.return_value.read.return_value = response
        config = vc500w.get_config('192.168.1.55')
    assert config['tape_type'] == 'Print Tape' and config['tape_width_mm'] == 12


def test_print_ack_without_idle_is_accepted_not_reported_failed():
    stages = []
    config = {'tape_width_mm': 25, 'tape_type': 'Print Tape'}
    idle = {'status': 'IDLE', 'error': 'NONE'}
    busy = {'status': 'PRINTING', 'error': 'NONE'}
    with patch('backend.vc500w.get_config', return_value=config), \
         patch('backend.vc500w.get_status', side_effect=[idle, busy]), \
         patch('backend.vc500w.Connection') as connection, \
         patch('backend.vc500w.time.sleep'), \
         patch('backend.vc500w.time.monotonic', side_effect=[0, 1, 41]):
        vc500w.execute_print('192.168.1.55', base64.b64decode(png(739, 308)),
                             25, 'full', lambda stage, percent: stages.append((stage, percent)))
    connection.return_value.__enter__.return_value.print_image.assert_called_once()
    assert ('verifying', 100) in stages
    assert stages[-1] == ('accepted', 100)
    assert ('complete', 100) not in stages


def test_image_conversion_is_real_jpeg():
    converted = vc500w.png_to_jpeg(base64.b64decode(png(739, 148)))
    with Image.open(BytesIO(converted)) as img:
        assert img.format == 'JPEG' and img.size == (148, 739) and img.mode == 'RGB'
