#!/usr/bin/env python3
"""Offline checks of the wtx tool. They need no ECU, no tshark, no pcap and no archive.

1. Every control frame the client sends equals the PC->ECU line in the Phase 2.2 block of
   docs/WIRE_PROTOCOL.md. The client accepts the ECU->PC replies of that block.
2. download() runs the control trigger before it sends the first credit. A stand-in ECU on
   loopback records the order. No packet leaves this machine.
3. decrypt, decode and batch refuse an output folder inside the data folder. That includes the
   default ./out when the working folder is the lap folder.

Run with: python3 -I tests/test_wtx.py
"""
import os
import re
import select
import socket
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import wtx  # noqa: E402
import wtx_client as c  # noqa: E402

# Spec lines like "f85  6.6268  PC->ECU  open    10 01 01 22 00 00 00 00" become {85: bytes}.
DOC = {int(n): bytes.fromhex(h.replace(" ", "")) for n, h in re.findall(
    r"^f(\d+)\s+[\d.]+\s+(?:PC->ECU|ECU->PC)\s+\S+\s+([0-9a-f ]+?)\s*$",
    (ROOT / "docs" / "WIRE_PROTOCOL.md").read_text(), re.M)}
OPEN, SET, VERSION, PORT, CLOSE = 85, 87, 89, 91, 153           # the PC->ECU frames the client sends
ACK = {OPEN: 86, SET: 88, VERSION: 90, PORT: 92, CLOSE: 154}    # the ECU->PC reply to each
assert set(ACK) | set(ACK.values()) <= DOC.keys(), "Phase 2.2 block not found in docs/WIRE_PROTOCOL.md"
REPLY = {DOC[n]: DOC[a] for n, a in ACK.items()}                # request bytes -> reply bytes


class FakeSock:
    """Record the frames sent. Answer each receive with the next canned ECU frame."""

    def __init__(self, replies):
        self.sent, self.replies = [], list(replies)

    def sendto(self, frame, addr):
        self.sent.append(frame)

    def recvfrom(self, n):
        return self.replies.pop(0), None

    def close(self):
        pass


class NoBeacon:
    """Stand-in for BeaconWatcher: no listener on UDP 30001, never reports the ECU leaving Download."""
    left_download = False

    def start(self):
        pass

    def stop(self):
        pass


def test_control_frames():
    order = (OPEN, SET, VERSION, PORT, CLOSE)
    s = c.ControlSession("127.0.0.1")
    s._sock.close()
    s._sock = FakeSock(DOC[ACK[n]] for n in order)
    assert s.arm_download() == c.DOWNLOAD_PORT      # open, set, get version, get port
    s.close()
    assert s._sock.sent == [DOC[n] for n in order]
    assert int.from_bytes(c.SET_BODY[-2:], "big") == 5000
    print("PASS  control frames equal the Phase 2.2 templates")


def test_download_order():
    ctl, dat = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2))
    for s in (ctl, dat):
        s.bind(("127.0.0.1", 0))
    page = lambda o, body: bytes((o, 3, 0, 0x11)) + body
    trailer = lambda o: bytes((o, 3, 0, 0x12)) + bytes(8)
    groups = [[page(2, b"BB"), page(1, b"AA"), page(3, b"CC"), trailer(4)],     # pages out of order
              [page(1, b"DD"), trailer(2)]]                                      # short final group
    events = []

    def ecu():
        while True:
            ready = select.select([ctl, dat], [], [], 1.0)[0]
            if not ready:
                return  # the client is done
            for s in ready:
                pkt, peer = s.recvfrom(2048)
                if s is ctl:
                    events.append(("ctl", pkt))
                    ctl.sendto(REPLY[pkt], peer)
                else:
                    events.append(("credit", pkt))
                    for p in groups.pop(0) if groups else ():
                        dat.sendto(p, peer)

    saved = c.CONTROL_PORT, c.DOWNLOAD_PORT, c.BeaconWatcher
    c.CONTROL_PORT, c.DOWNLOAD_PORT, c.BeaconWatcher = ctl.getsockname()[1], dat.getsockname()[1], NoBeacon
    t = threading.Thread(target=ecu)
    t.start()
    try:
        with tempfile.TemporaryDirectory() as d:
            n = c.download("127.0.0.1", str(Path(d) / "blob.bin"), recv_timeout=0.2, max_idle=2)
            blob = (Path(d) / "blob.bin").read_bytes()
    finally:
        c.CONTROL_PORT, c.DOWNLOAD_PORT, c.BeaconWatcher = saved
        t.join()
        ctl.close()
        dat.close()
    assert blob == b"AABBCCDD" and n == len(blob)
    first = next(i for i, (kind, _) in enumerate(events) if kind == "credit")
    assert [p for _, p in events[:first]][:4] == [DOC[n] for n in (OPEN, SET, VERSION, PORT)]  # trigger first
    credits = [p for kind, p in events if kind == "credit"]
    assert credits[:2] == [c.credit_packet(0), c.credit_packet(1)]
    assert DOC[CLOSE] in [p for kind, p in events if kind == "ctl"]
    print("PASS  download() sends open, set 5000, get version, get port, then the first credit")


def test_outdir_guard():
    def refused(argv):
        try:
            wtx.main(argv)
        except SystemExit as e:
            return str(e.code).startswith("ERROR: the output folder")
        return False

    here = Path.cwd()
    with tempfile.TemporaryDirectory() as d:
        d = Path(d).resolve()
        data, lap, work = d / "data", d / "data" / "Lap_1", d / "work"
        lap.mkdir(parents=True)
        work.mkdir()
        ztx = str(lap / "cableData.ztx")  # absent on purpose: the guard must fire before any read
        for cmd in ("decode", "decrypt"):
            assert refused([cmd, ztx, str(lap)]) and refused([cmd, ztx, str(lap / "a" / "b")])
        assert refused(["batch", str(data), str(data / "out")]) and refused(["batch", str(data), str(data)])
        assert refused(["batch", str(data), str(lap / ".." / "o")])  # dotdot does not hide it
        try:
            os.chdir(lap)
            assert refused(["decode", "cableData.ztx"]) and refused(["decrypt", "cableData.ztx"])  # default ./out
        finally:
            os.chdir(here)
        assert sorted(p.name for p in d.rglob("*")) == ["Lap_1", "data", "work"]  # nothing was written
        assert wtx.outdir_for(str(work / "o"), lap) == work / "o"
        try:
            os.chdir(work)
            assert wtx.outdir_for(None, lap) == work / "out"  # the default is <cwd>/out
        finally:
            os.chdir(here)
    print("PASS  decrypt, decode and batch refuse an output folder inside the data")


if __name__ == "__main__":
    test_control_frames()
    test_download_order()
    test_outdir_guard()
