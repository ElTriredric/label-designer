import asyncio
import ipaddress
import socket
import threading
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from zeroconf import DNSIncoming, DNSOutgoing, DNSPointer, DNSText
from zeroconf.const import _CLASS_IN, _FLAGS_AA, _FLAGS_QR_RESPONSE, _TYPE_PTR, _TYPE_TXT

from backend import detect_subnets, discovery
from backend.main import app, printers, scan_cache


def adapter(name, ip, prefix):
    return SimpleNamespace(name=name, nice_name=name, ips=[SimpleNamespace(ip=ip, network_prefix=prefix)])


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv(discovery.ENV_SUBNETS, raising=False)
    monkeypatch.delenv(discovery.ENV_SUBNETS_FILE, raising=False)
    printers.clear()
    scan_cache.update(key=None, at=0.0, result=None)
    yield
    printers.clear()


@pytest.mark.parametrize('value', ['8.8.8.0/24', '127.0.0.0/24', '169.254.0.0/24', '10.0.0.0/16',
                                   'fd00::/64', 'nonsense', '192.168.1.0/33'])
def test_rejects_unsafe_or_invalid_subnets(value):
    with pytest.raises(ValueError):
        discovery.validate_subnet(value)


def test_parses_lists_files_and_comments():
    text = '# detected\n192.168.1.0/24, 10.0.5.7/24\n192.168.1.0/24\n'
    assert [str(n) for n in discovery.parse_subnets(text)] == ['192.168.1.0/24', '10.0.5.0/24']
    assert discovery.parse_subnets('bad 192.168.2.0/24', strict=False) == [ipaddress.ip_network('192.168.2.0/24')]
    with pytest.raises(ValueError):
        discovery.parse_subnets(['10.0.0.0/22'] * 1 + ['10.0.4.0/22', '10.0.8.0/22', '10.0.12.0/22', '10.0.16.0/22'])


def test_interface_detection_skips_docker_and_narrows_large_networks():
    adapters = [adapter('lo', '127.0.0.1', 8), adapter('docker0', '172.17.0.1', 16),
                adapter('br-abc', '172.18.0.1', 16), adapter('veth1', '10.9.9.9', 24),
                adapter('enp3s0', '192.168.1.20', 24), adapter('wlan0', '10.1.2.3', 16),
                adapter('eth9', '8.8.8.8', 24)]
    assert [str(n) for n in discovery.interface_subnets(adapters)] == ['192.168.1.0/24', '10.1.2.0/24']


def test_configured_subnets_priority(tmp_path, monkeypatch):
    lan = [adapter('docker0', '172.17.0.1', 16), adapter('enp3s0', '192.168.1.20', 24)]
    bridged = [adapter('lo', '127.0.0.1', 8), adapter('eth0', '172.18.0.5', 16)]
    with patch.object(discovery.Path, 'exists', return_value=True):  # pretend /.dockerenv
        assert discovery.configured_subnets(bridged) == ([], 'none')
        assert discovery.configured_subnets(lan)[1] == 'interfaces'
        subnet_file = tmp_path / 'subnets'
        subnet_file.write_text('10.0.5.0/24\n')
        monkeypatch.setenv(discovery.ENV_SUBNETS_FILE, str(subnet_file))
        assert discovery.configured_subnets(bridged) == ([ipaddress.ip_network('10.0.5.0/24')], 'file')
        monkeypatch.setenv(discovery.ENV_SUBNETS, '192.168.7.0/24')
        assert discovery.configured_subnets(bridged)[1] == 'env'
        monkeypatch.setenv(discovery.ENV_SUBNETS_FILE, str(tmp_path / 'missing'))
        monkeypatch.delenv(discovery.ENV_SUBNETS)
        assert discovery.configured_subnets(bridged) == ([], 'none')


def test_sidecar_writes_detected_subnets(tmp_path):
    target = tmp_path / 'shared' / 'subnets'
    with patch.object(detect_subnets, 'interface_subnets',
                      return_value=[ipaddress.ip_network('192.168.1.0/24')]):
        assert detect_subnets.main(['detect', str(target)]) == 0
    assert discovery.parse_subnets(target.read_text()) == [ipaddress.ip_network('192.168.1.0/24')]
    with patch.object(detect_subnets, 'interface_subnets', return_value=[]):
        assert detect_subnets.main(['detect', str(target)]) == 0
    assert discovery.parse_subnets(target.read_text()) == []


def test_port_open_sends_nothing():
    received = []
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen()
    port = server.getsockname()[1]

    def accept():
        conn, _ = server.accept()
        received.append(conn.recv(10))
        conn.close()
    thread = threading.Thread(target=accept)
    thread.start()
    assert asyncio.run(discovery.port_open('127.0.0.1', port))
    thread.join(2)
    server.close()
    assert received == [b'']
    assert not asyncio.run(discovery.port_open('127.0.0.1', port, timeout=0.2))


def test_unicast_mdns_identifies_vc500w(monkeypatch):
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(('127.0.0.1', 0))
    monkeypatch.setattr(discovery, 'MDNS_PORT', responder.getsockname()[1])

    def answer():
        data, source = responder.recvfrom(9000)
        query = DNSIncoming(data)
        reply = DNSOutgoing(_FLAGS_QR_RESPONSE | _FLAGS_AA, multicast=False)
        reply.id = query.id
        name = 'Brother VC-500W._ipp._tcp.local.'
        reply.add_answer_at_time(DNSPointer('_ipp._tcp.local.', _TYPE_PTR, _CLASS_IN, 120, name), 0)
        reply.add_additional_answer(DNSText(name, _TYPE_TXT, _CLASS_IN, 120, b'\x0fty=Brother VC-500W'))
        responder.sendto(reply.packets()[0], source)
    thread = threading.Thread(target=answer)
    thread.start()
    assert discovery.mdns_query('127.0.0.1') == {'name': 'Brother VC-500W', 'vc500w': True}
    thread.join(2)
    responder.close()
    assert discovery.mdns_query('127.0.0.1', timeout=0.2) is None


def test_scan_verifies_and_sorts():
    async def fake_open(ip, port=9100, timeout=0.5):
        return ip in ('192.168.1.9', '192.168.1.30', '192.168.1.40')
    infos = {'192.168.1.30': {'name': 'Brother VC-500W', 'vc500w': True},
             '192.168.1.9': {'name': 'Laser', 'vc500w': False}}
    with patch.object(discovery, 'port_open', fake_open), \
         patch.object(discovery, 'mdns_query', lambda ip: infos.get(ip)), \
         patch.object(discovery, 'reverse_name', lambda ip: None):
        found = asyncio.run(discovery.scan([ipaddress.ip_network('192.168.1.0/24')], skip={'192.168.1.40'}))
    assert found == [
        {'ip': '192.168.1.30', 'hostname': 'Brother VC-500W', 'source': 'scan', 'verified': True},
        {'ip': '192.168.1.9', 'hostname': 'Laser', 'source': 'scan', 'verified': False}]


def test_scan_endpoint(monkeypatch):
    calls = []

    async def fake_scan(subnets, skip=()):
        calls.append([str(s) for s in subnets])
        return [{'ip': '192.168.1.30', 'hostname': 'Brother VC-500W', 'source': 'scan', 'verified': True}]
    with patch.object(discovery, 'scan', fake_scan), \
         patch.object(discovery, 'configured_subnets', return_value=([], 'none')):
        with TestClient(app) as client:
            response = client.post('/api/printers/scan').json()
            assert response['hint'] == 'set_subnets' and calls == []
            assert client.post('/api/printers/scan', json={'subnets': ['8.8.8.0/24']}).status_code == 422
            response = client.post('/api/printers/scan', json={'subnets': ['192.168.1.0/24']}).json()
            assert response['subnets'] == ['192.168.1.0/24'] and response['source'] == 'request'
            assert response['printers'][0]['verified']
            client.post('/api/printers/scan', json={'subnets': ['192.168.1.0/24']})
            assert len(calls) == 1  # cached
            assert client.get('/api/printers').json()['printers'][0]['ip'] == '192.168.1.30'
            assert client.get('/api/printers/scan/config').json() == {'subnets': [], 'source': 'none'}
