#!/usr/bin/env python3
"""Download a recorded run off a Magneti Marelli F4 GEN2 ECU over UDP.

This is a client for the user's own ECU. The wire protocol is documented in
docs/WIRE_PROTOCOL.md; the layout and byte values here follow that spec. Run
the module with no live ECU to execute the offline validation (see below).

Four parts, one per protocol phase:

1. Beacon listener (UDP :30001). Parse the 120-byte identity beacon and return
   the ECU IP, name, alias and live state (spec Phase 0.1).
2. Control session (ECU :30000). Frame is
   ``[10 01][op][tag][len: 4-byte BE][body]``. Run open, set, get-version and
   get-download-port with ack handling, as the trigger session in Phase 2.2.
3. Downloader (ECU :40000). Run the control session (part 2) first, then bind
   a UDP socket, send the first credit from it to start the stream and register
   the return port, receive data pages, reassemble
   by page ordinal with the 4-byte page header stripped, send one credit per
   group advancing the 2-bit sequence (0x20, 0x60, 0xa0, 0xe0), stop on the
   short final group and on the beacon leaving the Download state, and write the
   raw reassembled blob (spec Phases 3 and 4).
4. Offline validation (the test). There is no live ECU here, so read the
   reference pcap with tshark, run the same page reassembly over the captured
   data pages and check the result against the stored plaintext download.
   tests/test_wtx.py also pins every control frame to the spec bytes and
   runs download() against a stand-in ECU on loopback.

The stored artifact run713_download_plaintext.bin is WinTAX's ``lap.bin``: the
bulk channel data only. The spec (Phase 3.6) states the split of the stream into
dltable.t04 / lapheader.bin / lap.bin is structural and "cannot be proven from
the capture alone". Measured against the artifact, lap.bin is exactly the full
1024-byte channel-data pages, minus the single leading logger block-header page
(the first data page whose body starts ``00 01 00 00``). Every shorter page (the
33x400-byte directory records, the ``66 aa BD004`` checkpoint blocks, the final
short group) is logger metadata and is not channel data. See LapSelector; this
rule reproduces the artifact byte for byte. The raw reassembled blob (every data
page, no selection) is what download() writes; LapSelector is the documented
extra step that yields lap.bin.

Standard library only (plus tshark for the offline read). No scapy, no raw
AF_PACKET sockets: ordinary UDP sockets and struct are enough.
"""
from __future__ import annotations

import hashlib
import os
import socket
import struct
import subprocess
import sys
import threading
from collections import namedtuple

# --- protocol constants (spec: docs/WIRE_PROTOCOL.md) -----------------------
ECU_IP_DEFAULT = "192.168.111.254"
BEACON_PORT = 30001          # ECU identity beacon, broadcast
CONTROL_PORT = 30000         # control session, unicast
DOWNLOAD_PORT = 40000        # bulk stream + PC flow control, unicast

CONTROL_MAGIC = b"\x10\x01"  # control and beacon message magic
BEACON_MAGIC = b"\x10\x01\x03\x36"
BEACON_LEN = 120
BEACON_BODY_LEN = 112

OP_OPEN, OP_SET, OP_GET, OP_CLOSE, OP_ACK = 0x01, 0x02, 0x03, 0x04, 0x05
TAG_REQ = 0x22               # PC requests and ECU get-replies
TAG_ACK = 0x00               # ECU acks

# Exact control bodies from the Phase 2.2 trigger session (OBSERVED frames).
SET_BODY = bytes.fromhex("040000000000000000001388")      # value 0x1388 = 5000
GET_VERSION_BODY = bytes.fromhex("0400000801030000160a0606")
GET_PORT_BODY = bytes.fromhex("040000080400e2009c400000")  # 0x9c40 = port 40000

PAGE_KIND_DATA = 0x11        # page header byte 3: data page
PAGE_KIND_TRAILER = 0x12     # page header byte 3: group trailer
PAGE_HEADER_LEN = 4          # [ord][03][00][kind]

# PC flow-control credit: fixed header then the sequence byte 0x20|(seq<<6).
CREDIT_HEADER = b"\x00\x03\x03\xf2"
CREDIT_SEQ_BYTES = (0x20, 0x60, 0xa0, 0xe0)  # 2-bit sequence 0,1,2,3

DOWNLOAD_STATE = "Download"  # live-state substring while a transfer runs

# lap.bin selection (see module docstring).
LAP_PAGE_LEN = 1024
LAP_HEADER_SIG = b"\x00\x01\x00\x00"

# Offline test fixtures.
HERE = os.path.dirname(os.path.abspath(__file__))
_MOTOR = os.path.dirname(os.path.dirname(HERE))              # .../Motorsports
PCAP_DEFAULT = os.path.join(_MOTOR, "wintax-packet-capture.pcapng")
EXPECTED_DEFAULT = os.path.join(
    HERE, "..", "artifacts", "run713_download_plaintext.bin")


# === Part 1: identity beacon ================================================
Beacon = namedtuple("Beacon", "ip name alias state netmask")


def parse_beacon(data: bytes) -> Beacon:
    """Parse a 120-byte identity beacon payload (spec Phase 0.1).

    Raise ValueError if the magic or length is wrong.
    """
    if len(data) != BEACON_LEN or data[:4] != BEACON_MAGIC:
        raise ValueError("not an F4 GEN2 identity beacon")
    body_len = struct.unpack_from(">I", data, 4)[0]
    if body_len != BEACON_BODY_LEN:
        raise ValueError("unexpected beacon body length %d" % body_len)
    ip = socket.inet_ntoa(data[16:20])
    netmask = socket.inet_ntoa(data[20:24])
    field = lambda off: data[off:off + 32].split(b"\x00", 1)[0].decode("latin1")
    return Beacon(ip, field(24), field(56), field(88), netmask)


def _bind_udp(port: int, broadcast: bool = False) -> socket.socket:
    """UDP socket bound to every interface on ``port``, address-reuse on."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    if broadcast:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", port))
    return s


def listen_beacon(timeout: float = 5.0) -> Beacon:
    """Wait for one identity beacon and return the ECU identity.

    Raise socket.timeout if no beacon arrives within ``timeout`` seconds.
    """
    with _bind_udp(BEACON_PORT) as s:
        s.settimeout(timeout)
        while True:
            data, _ = s.recvfrom(2048)
            try:
                return parse_beacon(data)
            except ValueError:
                continue  # some other packet on the port; keep waiting


class BeaconWatcher(threading.Thread):
    """Background thread that tracks the ECU live state from the beacon.

    The downloader uses it to detect the ECU leaving the Download state, which
    is the cleanest end-of-transfer signal (spec Phase 3.5).
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.state = None
        self.ip = None
        self._was_download = False
        self.left_download = False
        self._stop = threading.Event()
        try:
            self._sock = _bind_udp(BEACON_PORT)
            self._sock.settimeout(0.5)
        except OSError:
            self._sock = None  # port busy; downloader falls back to timeouts

    def run(self):
        if self._sock is None:
            return
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                b = parse_beacon(data)
            except ValueError:
                continue
            self.state, self.ip = b.state, b.ip
            if DOWNLOAD_STATE in (b.state or ""):
                self._was_download = True
            elif self._was_download:
                self.left_download = True

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            self._sock.close()


# === Part 2: control session ================================================
def build_frame(op: int, tag: int, body: bytes = b"") -> bytes:
    """Build a control frame: magic, op, tag, 4-byte BE length, body."""
    return CONTROL_MAGIC + bytes((op, tag)) + struct.pack(">I", len(body)) + body


def parse_frame(data: bytes):
    """Split a control frame into (op, tag, body). Raise ValueError on bad magic."""
    if len(data) < 8 or data[:2] != CONTROL_MAGIC:
        raise ValueError("not a control frame")
    op, tag = data[2], data[3]
    length = struct.unpack_from(">I", data, 4)[0]
    return op, tag, data[8:8 + length]


class ControlSession:
    """One control session to ECU :30000 from an ephemeral source port.

    The ECU acks open/set/close with op 05 (body = the request's 8-byte header)
    and answers get with its own op 03 reply (spec Phase 2.1).
    """

    def __init__(self, ecu_ip: str, timeout: float = 5.0):
        self._addr = (ecu_ip, CONTROL_PORT)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", 0))  # fresh ephemeral port per session
        self._sock.settimeout(timeout)

    def _send(self, op: int, tag: int, body: bytes = b"") -> bytes:
        frame = build_frame(op, tag, body)
        self._sock.sendto(frame, self._addr)
        return frame

    def _recv(self):
        data, _ = self._sock.recvfrom(2048)
        return parse_frame(data)

    def _request_ack(self, op: int, body: bytes = b""):
        """Send open/set/close and verify the op 05 ack echoes the request header."""
        frame = self._send(op, TAG_REQ, body)
        rop, rtag, rbody = self._recv()
        if rop != OP_ACK or rtag != TAG_ACK or rbody != frame[:8]:
            raise ProtocolError("bad ack for op %#x" % op)

    def open(self):
        self._request_ack(OP_OPEN)

    def set(self):
        """Send the SET (value 5000). This writes to the ECU."""
        self._request_ack(OP_SET, SET_BODY)

    def close(self):
        self._request_ack(OP_CLOSE)

    def get(self, body: bytes) -> bytes:
        """Send a get (op 03) and return the ECU reply body (op 03, not an ack)."""
        self._send(OP_GET, TAG_REQ, body)
        rop, _rtag, rbody = self._recv()
        if rop != OP_GET:
            raise ProtocolError("get reply op was %#x, expected 0x03" % rop)
        return rbody

    def get_version(self) -> bytes:
        return self.get(GET_VERSION_BODY)

    def get_download_port(self) -> int:
        """Confirm the download port. The value 0x9C40 = 40000 is a constant in
        the request and is zeroed in the reply, so there is nothing to read back;
        this get is the last step before the stream starts (spec Phase 2.3)."""
        self.get(GET_PORT_BODY)
        return DOWNLOAD_PORT

    def arm_download(self) -> int:
        """Run the full trigger session and return the download port."""
        self.open()
        self.set()
        self.get_version()
        port = self.get_download_port()
        return port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.close()
        except (OSError, ProtocolError):
            pass
        self._sock.close()


class ProtocolError(Exception):
    pass


# === Part 3: reassembly core + downloader ===================================
def credit_packet(seq2bit: int) -> bytes:
    """Build the 12-byte PC flow-control credit for 2-bit sequence 0..3."""
    return CREDIT_HEADER + bytes((CREDIT_SEQ_BYTES[seq2bit & 3],)) + b"\x00" * 7


def iter_data_pages(payloads):
    """Reassemble per spec Phase 3.3.

    ``payloads`` is an iterable of ECU page UDP payloads (each at least 4 bytes:
    the page header ``[ord][03][00][kind]``). Yield each data page's bytes
    (header stripped) ordered by ordinal within its group; groups come in
    arrival order. Trailer pages (kind 0x12) only delimit groups and are not
    channel bytes, so they are not yielded.
    """
    group = []  # (ordinal, data_bytes) for the current group
    for p in payloads:
        if len(p) < PAGE_HEADER_LEN:
            continue
        kind = p[3]
        if kind == PAGE_KIND_DATA:
            group.append((p[0], p[PAGE_HEADER_LEN:]))
        elif kind == PAGE_KIND_TRAILER:
            group.sort(key=lambda t: t[0])
            for _, d in group:
                yield d
            group = []
    if group:  # defensive: a capture cut mid-group still flushes its pages
        group.sort(key=lambda t: t[0])
        for _, d in group:
            yield d


class LapSelector:
    """WinTAX lap.bin structural selection (spec Phase 3.6; see module docstring).

    Keep full 1024-byte channel-data pages. Drop the single leading logger
    block-header page (the first data page whose body starts ``00 01 00 00``).
    Drop every shorter page (directory records, ``66 aa BD004`` checkpoint
    blocks, the final short group): those are logger metadata, not channel data.
    Stateful, so use one instance per stream.
    """

    def __init__(self):
        self._header_dropped = False

    def take(self, data: bytes) -> bool:
        if len(data) != LAP_PAGE_LEN:
            return False
        if not self._header_dropped and data[:4] == LAP_HEADER_SIG:
            self._header_dropped = True
            return False
        return True


def download(ecu_ip: str, out_path: str, recv_timeout: float = 2.0,
             max_idle: int = 5) -> int:
    """Run the control trigger, then the live download. Write the raw reassembled blob.

    The trigger is the Phase 2.2 session: open, set 5000 (one write to the ECU),
    get version, get download port. The first credit follows at once and the
    session then closes. Not run on a real ECU here. The offline validation
    proves the reassembly core, and tests/test_wtx.py runs this whole
    function against a stand-in ECU on loopback. Return the byte count written.
    """
    watcher = BeaconWatcher()
    watcher.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))  # the source port here becomes the ECU's return port
    sock.settimeout(recv_timeout)
    addr = (ecu_ip, DOWNLOAD_PORT)

    seq = 0
    group = []
    written = 0
    idle = 0
    try:
        with ControlSession(ecu_ip) as ctl:
            ctl.arm_download()
            sock.sendto(credit_packet(seq), addr)  # first credit starts the stream
        with open(out_path, "wb") as f:
            while True:
                try:
                    pkt = sock.recv(2048)
                except socket.timeout:
                    # No data since the last credit. End if the ECU has left the
                    # Download state, else re-send the credit (INFERRED Phase 5.7).
                    idle += 1
                    if watcher.left_download or idle >= max_idle:
                        break
                    sock.sendto(credit_packet(seq), addr)
                    continue
                idle = 0
                if len(pkt) < PAGE_HEADER_LEN:
                    continue
                kind = pkt[3]
                if kind == PAGE_KIND_DATA:
                    group.append((pkt[0], pkt[PAGE_HEADER_LEN:]))
                elif kind == PAGE_KIND_TRAILER:
                    group.sort(key=lambda t: t[0])
                    for _, d in group:
                        f.write(d)
                        written += len(d)
                    group = []
                    # End of transfer. The ECU leaving the Download state is the
                    # reliable signal (spec Phase 3.5); stop crediting at once so
                    # the ECU returns to logging. A short final group (trailer
                    # ordinal pkt[0] < 4) corroborates it, but short groups also
                    # occur mid-stream, so it does not stop the stream on its own;
                    # the idle timeout above is the backstop when no beacon binds.
                    if watcher.left_download:
                        break
                    seq = (seq + 1) & 3
                    sock.sendto(credit_packet(seq), addr)  # credit the next group
    finally:
        sock.close()
        watcher.stop()
    return written


# === Part 4: offline validation =============================================
def iter_pcap_pages(pcap: str, ecu_ip: str, src_port: int = DOWNLOAD_PORT):
    """Stream ECU download-stream page payloads from a pcap via tshark.

    One UDP payload per yielded value, in capture (frame) order. Streamed, so a
    multi-GB capture never loads into memory.
    """
    cmd = ["tshark", "-r", pcap,
           "-Y", "udp.srcport==%d && ip.src==%s" % (src_port, ecu_ip),
           "-T", "fields", "-e", "udp.payload"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1 << 20,
                            text=True)
    try:
        for line in proc.stdout:
            line = line.strip()
            if line:
                yield bytes.fromhex(line)
    finally:
        proc.stdout.close()
        if proc.wait() != 0:
            raise RuntimeError("tshark failed on %s" % pcap)


def _sha256_file(path: str) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return size, h.hexdigest()


def validate_offline(pcap: str, expected: str,
                     ecu_ip: str = ECU_IP_DEFAULT) -> bool:
    """Reassemble the captured stream and compare lap.bin to the stored artifact.

    Print the raw-blob and lap.bin sizes and sha256, then PASS or FAIL. Return
    True on a byte-identical match. One streaming pass over the capture feeds
    both the raw-blob hash and the lap.bin selection.
    """
    raw = hashlib.sha256()
    lap = hashlib.sha256()
    raw_len = lap_len = raw_pages = lap_pages = 0
    selector = LapSelector()
    for d in iter_data_pages(iter_pcap_pages(pcap, ecu_ip)):
        raw.update(d)
        raw_len += len(d)
        raw_pages += 1
        if selector.take(d):
            lap.update(d)
            lap_len += len(d)
            lap_pages += 1

    exp_size, exp_sha = _sha256_file(expected)
    lap_sha = lap.hexdigest()
    ok = (lap_len == exp_size and lap_sha == exp_sha)

    print("raw reassembly (every data page, spec Phase 3.3):")
    print("  pages %d  bytes %d  sha256 %s" % (raw_pages, raw_len, raw.hexdigest()))
    print("lap.bin extract (1024-byte channel pages minus leading header):")
    print("  pages %d  bytes %d  sha256 %s" % (lap_pages, lap_len, lap_sha))
    print("expected run713_download_plaintext.bin:")
    print("  bytes %d  sha256 %s" % (exp_size, exp_sha))
    print("PASS" if ok else "FAIL")
    return ok


# === self-checks (pin the framing against the spec's OBSERVED bytes) ========
def _selftest():
    # Beacon round-trip (spec Phase 0.1 fields).
    b = (BEACON_MAGIC + struct.pack(">I", BEACON_BODY_LEN)
         + bytes.fromhex("0003020005000051")
         + socket.inet_aton("192.168.111.254") + socket.inet_aton("255.255.255.0")
         + b"SRG_927".ljust(32, b"\x00") + b"F4_106".ljust(32, b"\x00")
         + b"(ABS=3012,TrigOut)".ljust(32, b"\x00"))
    assert len(b) == BEACON_LEN
    bc = parse_beacon(b)
    assert bc.ip == "192.168.111.254" and bc.name == "SRG_927"
    assert bc.alias == "F4_106" and bc.state == "(ABS=3012,TrigOut)"

    # Control frames against the Phase 2.2 trigger-session bytes.
    assert build_frame(OP_OPEN, TAG_REQ) == bytes.fromhex("1001012200000000")
    assert build_frame(OP_SET, TAG_REQ, SET_BODY) == bytes.fromhex(
        "100102220000000c040000000000000000001388")
    assert build_frame(OP_GET, TAG_REQ, GET_PORT_BODY) == bytes.fromhex(
        "100103220000000c040000080400e2009c400000")
    assert parse_frame(bytes.fromhex("100105000000000810010122"
                                     "00000000"))[0] == OP_ACK

    # Credit packets (spec Phase 4).
    assert credit_packet(0) == bytes.fromhex("000303f22000000000000000")
    assert credit_packet(1) == bytes.fromhex("000303f26000000000000000")
    assert credit_packet(3) == bytes.fromhex("000303f2e000000000000000")

    # Reassembly core: two tiny groups, out-of-order ordinals, trailer dropped.
    pages = [b"\x02\x03\x00\x11BB", b"\x01\x03\x00\x11AA",
             b"\x03\x03\x00\x12____",            # trailer
             b"\x01\x03\x00\x11CC", b"\x02\x03\x00\x12__"]
    assert list(iter_data_pages(pages)) == [b"AA", b"BB", b"CC"]

    # LapSelector drops the leading header and short pages only.
    sel = LapSelector()
    assert sel.take(LAP_HEADER_SIG + b"\x00" * 1020) is False  # leading header
    assert sel.take(b"\xff" * 1024) is True                     # channel page
    assert sel.take(LAP_HEADER_SIG + b"\x00" * 1020) is True    # later header kept
    assert sel.take(b"\x00" * 400) is False                     # directory record


if __name__ == "__main__":
    _selftest()
    pcap = sys.argv[1] if len(sys.argv) > 1 else PCAP_DEFAULT
    expected = sys.argv[2] if len(sys.argv) > 2 else EXPECTED_DEFAULT
    sys.exit(0 if validate_offline(pcap, os.path.abspath(expected)) else 1)
