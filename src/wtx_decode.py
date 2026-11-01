#!/usr/bin/env python3
"""Decode a Marelli F4 stored lap (cableData.ztx) into per-channel samples.

Usage: python3 -I wtx_decode.py [path/to/cableData.ztx]

Output goes to the output folder (default tests/out/): lap_decoded.mat (main output), summary.csv, one CSV per channel.

Layout of lap.bin (solved on lap 2991 of run 713; all 897 laps of the archive decode with it):
  lap.bin is a chain of blocks. One block holds 50 ms of data. 20 blocks make one second.
  Block = 32 byte header + payload. Header fields are little endian:
    0: 66 AA "BD004" 10   magic
    8: u32 lap id, 12: u32 block counter (+1 per block), 16: u16 block in lap,
   18: u32 lap in run, 22: u16 slot (0..19, the block position inside its second),
   24: u16 payload length in 4 byte words, 26: 6 byte session tag
  Payload: the channel sections are packed with no padding, and the whole payload is
  padded to 4 bytes. Order of the sections:
    1. by sample width: 4 byte (dltable type 0x14 float32, 0x04 int32), then 2 byte
       (0x02 int16), then 1 byte (0x01 uint8). All big endian.
    2. inside one width: rate class from high to low.
    3. inside one rate class: dltable index from high to low.
  The rate class is byte 2 of the 8 byte record of dltable section 7 (not the
  descriptor field block_len). Classes 8..1 are 200, 100, 50, 20, 10, 5, 2, 1 Hz.
  A channel of rate R has ceil(R*(s+1)/20) - ceil(R*s/20) samples in slot s. Its sample
  instants are k/R seconds from the start of the second.
Integer channels hold value * 10^decimals. The decimals are the low nibble of the
descriptor unit code (byte 6), except for the channels in SCALE_FIX. Float channels are
in their own unit. Names longer than 16 chars come from dltable section 34. The two PBX
total-consumption byte pairs are combined into one 16-bit channel each (PAIRS).
Time: t = 0 at the start of the first block (block k = k * 0.05 s).
"""
import csv
import math
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

# python -I does not add the script folder to sys.path. Add it here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from decrypt_ztx import PASSWORD, _decrypt, _members
from wtx_fileformat import parse_dltable

try:
    from scipy.io import savemat
except ImportError:
    savemat = None

TEST_ZTX = ("/home/pk/Downloads/pk-backup-d-drive/Motorsports/WinTAX ORECA BASIC APPLICATION-7/"
            "Data/047 - Arjun/F4_047/Run_713_R1_047/Lap_00002991_00927/cableData.ztx")
# Output folder of a standalone run. wtx.py always passes its own outdir.
OUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "out"
SIG, HDR = b"\x66\xaa\x42\x44\x30\x30\x34", 32
DTYPE = {0x14: ">f4", 0x04: ">i4", 0x02: ">i2", 0x01: "u1"}
RATE_HZ = {8: 200, 7: 100, 6: 50, 5: 20, 4: 10, 3: 5, 2: 2, 1: 1}
# Scale overrides for channels where the unit nibble is wrong. dltable section 11 (the logger's ECU
# read list, records in field-id order: u32 address, float32 factor, 2 flag bytes) holds the factor
# the logger applied before storing each int16. It confirms 15 nibbles (CarSpeed x10, pOil x100,
# Vref x1000, ...) and gives pRail x1, nTurbo x50 (253 krpm at 1.7 bar boost; the Turbo.gra axis is
# 0..300), Lambda1Out x100, pBaro x1 (mbar: 967 at Kari, 400 m, and 1005 at MMRT), Vtank_Corr x1000
# (mV, it sits in the Vref array; no tank sensor, reads 0 V). It also confirms EngLoad_Req x10 and
# pHydr x10 (100.0 bar constant = no sensor). GPS_Speed = CarSpeed in km/h on six moving laps.
# PBX (Bosch PowerBox) bytes: Vbat 0.1 V (= VBatt - 0.45 V). Each 16 bit total is exactly the sum of its
# six output bytes with weights 1, 2 and 10 (residual 0 on 40 laps), so the outputs have three
# resolutions, one per channel class. The finest step is 0.05 A by physics: FIA ADR 50 mA, lambda
# heater 1.1-1.4 A warm-up then 0.5-0.9 A, ignition 1.2-1.5 A at 6500 rpm, lift pump 5.5-7.3 A,
# starter solenoid 5.5 A, shift actuator pulses 50-73 A (under 80 A of a paralleled PBX 90 channel).
# Weight 1 = 0.05 A (15 A class), 2 = 0.1 A (25 A), 10 = 0.5 A (40-80 A). Alternator and AUX never
# carry current in the archive, their weight 1 is assumed. Not checked against a Bosch CAN sheet.
SCALE_FIX = {"pRail": 1.0, "nTurbo": 0.02, "Lambda1Out": 0.01, "pBaro": 1.0, "Vtank_Corr": 0.001, "GPS_Speed": 1.0,
             "PBX_Vbat_R": 0.1, "PBX_Vbat_L": 0.1, "PBX_Upshift_Current": 0.5, "PBX_Downshift_Current": 0.5}
SCALE_FIX.update({f"PBX_{n}_Current": 0.1 for n in ("LP_Fuel", "Starter", "ECU", "IGN")})
SCALE_FIX.update({f"PBX_{n}_Current": 0.05 for n in ("ADR", "Alternator", "SteeringWheel", "LBD", "RAIN", "AUX")})
# int16 channels that are unsigned in the ECU: vFuelCons (fuel volume odometer in 0.01 L, wraps at
# 655.36 L, = mFuelCons1 / 0.74 kg/L on six cars) and tLap_sec (ms of the seconds part of the last
# lap time: 42402 = 42.402 s, plus 60 s x minutes = Logger_FinalTime).
UNSIGNED = {"vFuelCons", "tLap_sec"}
# PBX total consumption: 16 bit, MSB byte first, 0.05 A = the sum of the six outputs of that side.
PAIRS = {"PBX_Total_cons_R_Current": ("PBX_Total_cons_MSB_R_Current", "PBX_Total_cons_LSB_R_Current"),
         "PBX_Total_cons_L_Current": ("PBX_Total_cons_MSB_L_Current", "PBX_Total_cons_LSB_L_Current")}
PBX_SIDES = {"PBX_Total_cons_R_Current": ("ADR", "Alternator", "LP_Fuel", "Starter", "SteeringWheel", "Upshift"),
             "PBX_Total_cons_L_Current": ("AUX", "Downshift", "ECU", "IGN", "LBD", "RAIN")}
# Left raw, no physical scale exists: GPS_Altitude is the low byte of a 16 bit 0.1 m CAN signal (Kari,
# 414 m, reads 40..60; MMRT, 72 m, reads 204..217), the absolute value is lost. GPS_PosAccuracy and
# GPS_SpdAccuracy are 16 bit CAN signals logged as one byte: 0 with a fix, noise without.
# GPS_Stats_Number = satellites. CcanRx1..11 = raw CAN receive registers (dltable group "Receive").
# PBX_*_State = status bits (1 enabled, 2 output on, 4 no load current, 64 unknown). PBX_CPU_Temp_* in
# degC. BrakeBalance = float32 100 pBrakeF / (pBrakeF + pBrakeR), -1 off the brakes, wild near 0 bar.
# eWG1_Cmd in % (section 11 factor 100 on the ECU fraction), -100 closes, +100 opens.


def load_members(ztx_path):
    """Return the decrypted members as {name: bytes}. Check the CRC of each member."""
    # ponytail: loads whole .ztx and lap.bin, stream if > 200 MB
    data = Path(ztx_path).read_bytes()
    members = {}
    for name, method, stored, crc in _members(data):
        raw = _decrypt(stored, PASSWORD)
        if method == 8:
            raw = zlib.decompressobj(-15).decompress(raw)
        if zlib.crc32(raw) & 0xFFFFFFFF != crc:
            sys.exit(f"Error: CRC mismatch in {name}. Check the file and the password.")
        members[name] = raw
    return members


def section(dlt, sid):
    """Return the bytes of dltable section sid (directory at byte 16, 12 byte entries)."""
    for i in range(struct.unpack_from(">H", dlt, 2)[0]):
        s, _, size, off = struct.unpack_from(">HHII", dlt, 16 + 12 * i)
        if off + size > len(dlt):
            break  # the last directory entry is overwritten by the firmware string
        if s == sid:
            return dlt[off:off + size]
    sys.exit(f"Error: dltable section {sid} not found.")


def rate_classes(dlt, n_chan):
    """Return the rate class of each channel: byte 2 of the section 7 records."""
    rec = section(dlt, 7)
    if len(rec) != 8 * n_chan:
        sys.exit(f"Error: dltable section 7 has {len(rec)} B, expected {8 * n_chan}.")
    cls = [rec[8 * c + 2] for c in range(n_chan)]
    bad = [c for c in cls if c not in RATE_HZ]
    if bad:
        sys.exit(f"Error: unknown rate class {sorted(set(bad))} in dltable section 7.")
    return cls


def full_names(chans, dlt):
    """Restore the names longer than 16 chars: section 34 holds u16 length + chars per channel."""
    rec, p = section(dlt, 34), 0
    for c in chans:
        n = struct.unpack_from(">H", rec, p)[0]
        if n:
            c["name"] = rec[p + 2:p + 2 + n].decode("latin1")
        p += 2 + n


def scale_of(c):
    """Return the factor from the stored value to the physical value."""
    if c["name"] in SCALE_FIX:
        return SCALE_FIX[c["name"]]
    return 1.0 if c["type"] == 0x14 else 10.0 ** -(c["unit_code"] & 15)


def dtype_of(c):
    return np.dtype(">u2" if c["name"] in UNSIGNED else DTYPE[c["type"]])


def slot_layout(chans, cls, s):
    """Return ([(channel, byte offset, samples)], payload bytes) for slot s."""
    order = sorted(range(len(chans)), key=lambda i: (-dtype_of(chans[i]).itemsize, -cls[i], -i))
    lay, off = [], 0
    for i in order:
        r = RATE_HZ[cls[i]]
        n = math.ceil(r * (s + 1) / 20) - math.ceil(r * s / 20)
        if n:
            lay.append((i, off, n))
            off += n * dtype_of(chans[i]).itemsize
    return lay, off


def decode(chans, lap, dlt):
    """Return {"meta": {...}, "ch": {name: {...}}}. Samples are physical values in float64."""
    full_names(chans, dlt)
    cls = rate_classes(dlt, len(chans))
    pos, p = [], 0
    while p < len(lap):
        end = p + HDR + 4 * int.from_bytes(lap[p + 24:p + 26], "little")
        if len(lap) - p >= 7 and lap[p:p + 7] != SIG:
            sys.exit(f"Error: no block header at byte {p} of lap.bin.")
        if len(lap) - p < HDR or end > len(lap):
            print("note: the last block of lap.bin is cut off. The program drops it.")
            break
        pos.append(p)
        p = end
    u8 = np.frombuffer(lap, np.uint8)
    pos = np.array(pos)
    ctr = np.array([int.from_bytes(lap[q + 12:q + 16], "little") for q in pos])
    slot = np.array([int.from_bytes(lap[q + 22:q + 24], "little") for q in pos])
    u = ctr - ctr[0] + slot[0]  # block number counted in 50 ms slots
    if (u % 20 != slot).any():
        sys.exit("Error: block counter and slot field disagree.")
    sec = u // 20
    parts = {i: [] for i in range(len(chans))}
    for s in range(20):
        sel = np.flatnonzero(slot == s)
        if not sel.size:
            continue
        lay, size = slot_layout(chans, cls, s)
        plen = (size + 3) // 4 * 4
        words = np.array([int.from_bytes(lap[q + 24:q + 26], "little") for q in pos[sel]])
        if (words * 4 != plen).any():
            sys.exit(f"Error: slot {s} payload is {sorted(set(words * 4))} B but the dltable "
                     f"layout needs {plen} B. This lap does not fit the layout.")
        m = u8[(pos[sel] + HDR)[:, None] + np.arange(plen)]
        for i, off, n in lay:
            dt = dtype_of(chans[i])
            raw = np.ascontiguousarray(m[:, off:off + n * dt.itemsize]).view(dt).reshape(sel.size, n)
            r = RATE_HZ[cls[i]]
            k = sec[sel][:, None] * r + math.ceil(r * s / 20) + np.arange(n)  # sample instant index
            parts[i].append((k.ravel(), raw.ravel()))
    out = {}
    for i, c in enumerate(chans):
        k = np.concatenate([a for a, _ in parts[i]])
        v = np.concatenate([b for _, b in parts[i]]).astype(np.float64)
        k0 = k.min()
        dense = np.full(k.max() - k0 + 1, np.nan)  # a missing block shows as NaN
        dense[k - k0] = v * scale_of(c)
        out[c["name"]] = {"v": dense, "k0": int(k0), "rate_hz": RATE_HZ[cls[i]], "gap": int(dense.size - k.size),
                          "scale": scale_of(c), "unit_code": c["unit_code"], "type": c["type"]}
    for name, (hi, lo) in PAIRS.items():
        h, l = out.pop(hi), out.pop(lo)
        out[name] = {**l, "v": (h["v"] * 256 + l["v"]) * 0.05, "scale": 0.05, "type": 0x02}
    meta = {"n_blocks": len(pos), "duration_s": len(pos) * 0.05, "lap_id": int.from_bytes(lap[8:12], "little"),
            "lap_in_run": int.from_bytes(lap[18:22], "little"), "first_slot_s": slot[0] / 20}
    return {"meta": meta, "ch": out}


def times(ch, meta):
    """Return the sample times in s from the start of the first block."""
    return (ch["k0"] + np.arange(ch["v"].size)) / ch["rate_hz"] - meta["first_slot_s"]


KEY = ("VBatt", "IdleSpeed_CmdPID", "ECU_AccX", "ECU_AccY", "ECU_AccZ", "DistanceFull", "DistanceLap",
       "LambdaTgt_1", "RPM", "pInlet", "tWater", "CarSpeed", "LogLapTime")


def validate(lap):
    """Print the key channels and run the checks that must hold on any lap. Return True if all pass."""
    ch, meta = lap["ch"], lap["meta"]
    v = lambda n: ch[n]["v"]
    p99 = lambda a: float(np.percentile(np.abs(a), 99))
    inside = lambda lo, hi, *names: all(lo <= np.nanpercentile(v(n), 1) and np.nanpercentile(v(n), 99) <= hi
                                        for n in names)  # 1st..99th percentile, so a few zero samples do not count
    print(f"{'channel':<18}" + "".join(f"{h:>13}" for h in ("first", "median", "last", "min", "max")))
    for n in (k for k in KEY if k in ch):
        x = v(n)
        print(f"{n:<18}" + "".join(f"{y:>13.6g}" for y in (x[0], np.nanmedian(x), x[-1], np.nanmin(x), np.nanmax(x))))
    checks = [
        ("no gap in any channel (no missing block)", lambda: all(c["gap"] == 0 for c in ch.values())),
        ("LogLapTime advances 10 ms per 100 Hz sample", lambda: abs(np.median(np.diff(v("LogLapTime"))) - 0.01) < 1e-6),
        ("VVT1_Err = VVT1_ShiftTarget - VVT1_ShiftReal", lambda: np.abs(
            v("VVT1_Err") - (v("VVT1_ShiftTarget") - v("VVT1_ShiftReal"))).max() < 1e-3),
        ("VVT2_Err = VVT2_ShiftTarget - VVT2_ShiftReal", lambda: np.abs(
            v("VVT2_Err") - (v("VVT2_ShiftTarget") - v("VVT2_ShiftReal"))).max() < 1e-3),
        ("CdFp_PID = CdFp_P + CdFp_I + CdFp_D (99th percentile)", lambda: p99(
            v("CdFp_PID") - (v("CdFp_P") + v("CdFp_I") + v("CdFp_D"))) < 1e-3),
        ("VBatt inside 6..17 V", lambda: inside(6, 17, "VBatt")),
        ("ECU_AccX, ECU_AccY, ECU_AccZ inside +-20 g", lambda: inside(-20, 20, "ECU_AccX", "ECU_AccY", "ECU_AccZ")),
        ("LambdaTgt_1 median inside the WinTAX axis 0.5..1.5", lambda: 0.5 <= np.nanmedian(v("LambdaTgt_1")) <= 1.5),
        ("RPM inside 0..12000", lambda: inside(0, 12000, "RPM")),
        ("tWater inside -20..150 degC", lambda: inside(-20, 150, "tWater")),
        ("pBaro inside 850..1050 mbar, EngLoad_Req inside 0..110 %", lambda: inside(850, 1050, "pBaro") and inside(0, 110, "EngLoad_Req")),
        ("Logger_FinalTime - tLap_sec is a whole number of minutes", lambda: p99(
            (v("Logger_FinalTime") - v("tLap_sec") + 30) % 60 - 30) < 2e-3),
        ("vFuelCons (L) rises 1/0.74 x FuelConsumption (kg)", lambda: (
            lambda dl, dkg: dl < 0.05 or 0.70 < dkg / dl < 0.78)(  # a lap with no fuel burnt proves nothing
            v("vFuelCons")[-1] - v("vFuelCons")[0], v("FuelConsumption")[-1] - v("FuelConsumption")[0])),
        ("PBX_Total_cons_R/L = sum of the six output currents of that side", lambda: all(np.nanmax(np.abs(
            v(t) - sum(v(f"PBX_{n}_Current") for n in outs))) < 1e-6 for t, outs in PBX_SIDES.items())),
        ("PBX_Vbat_R, PBX_Vbat_L median within 1 V of VBatt", lambda: all(  # the box reads 0.45 V below the ECU
            abs(np.nanmedian(v(n)) - np.nanmedian(v("VBatt"))) < 1.0 for n in ("PBX_Vbat_R", "PBX_Vbat_L"))),
    ]
    ok = True
    for label, f in checks:
        try:
            good = bool(f())
        except KeyError as e:
            print(f"SKIP  {label} (no channel {e})")
            continue
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {label}")
    return ok


def safe(name):
    """Return a MATLAB field name for a channel name."""
    s = re.sub(r"\W", "_", name)
    return s if s[0].isalpha() else "ch" + s


def export(lap, out_dir):
    """Write the MAT file, the summary CSV and one CSV per channel into out_dir."""
    ch, meta = lap["ch"], lap["meta"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "rate_hz", "n_samples", "min", "mean", "max", "scale"])
        for n, c in ch.items():
            v = c["v"]
            w.writerow([n, c["rate_hz"], v.size, f"{np.nanmin(v):.7g}", f"{np.nanmean(v):.7g}",
                        f"{np.nanmax(v):.7g}", f"{c['scale']:g}"])
    for n, c in ch.items():
        t, v = times(c, meta), c["v"]
        with open(out_dir / f"{safe(n)}.csv", "w") as f:
            f.write("name,time_s,value\n")
            f.writelines(f"{n},{a:.4f},{b:.7g}\n" for a, b in zip(t.tolist(), v.tolist()))
    print(f"Wrote {len(ch)} channel CSV files and summary.csv in {out_dir}.")
    if savemat is None:
        print("note: scipy does not import. The script writes no .mat file.")
        return
    grids, span = {}, {}  # one time vector per rate: every channel of one rate has the same grid
    for n, c in ch.items():
        if span.setdefault(c["rate_hz"], (c["k0"], c["v"].size)) != (c["k0"], c["v"].size):
            sys.exit(f"Error: channel {n} has another sample grid than the other {c['rate_hz']} Hz channels.")
        grids.setdefault(c["rate_hz"], times(c, meta).reshape(-1, 1))
    mat = {"meta": {**meta, "note": "t_s of a channel = lap.t.(hzNNN); v = physical value"},
           "t": {f"hz{r}": g for r, g in grids.items()},
           "ch": {safe(n): {"name": n, "v": c["v"].reshape(-1, 1), "rate_hz": c["rate_hz"],
                            "tgrid": f"hz{c['rate_hz']}", "unit_code": c["unit_code"],
                            "scale": c["scale"]} for n, c in ch.items()}}
    savemat(str(out_dir / "lap_decoded.mat"), {"lap": mat}, do_compression=True)
    print(f"Wrote {out_dir / 'lap_decoded.mat'}.")


def main(ztx_path, out_dir=OUT_DIR):
    members = load_members(ztx_path)
    chans = parse_dltable(members["dltable.t04"])["channels"]
    lap = decode(chans, members["lap.bin"], members["dltable.t04"])
    m = lap["meta"]
    print(f"blocks {m['n_blocks']} x 50 ms = {m['duration_s']:.3f} s, channels {len(lap['ch'])}")
    ok = validate(lap)
    export(lap, out_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else TEST_ZTX))
