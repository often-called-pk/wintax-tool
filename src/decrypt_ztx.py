#!/usr/bin/env python3
"""Decrypt a WinTAX cableData.ztx (traditional PKWARE / ZipCrypto) into its
three members: dltable.t04, lap.bin, lapheader.bin.

The password is a constant baked into WinTAX (class CZtxPasswordCipher). It is
the same for every stored lap, so this script needs no attack: it just decrypts.
See docs/DECRYPT.md for how the password and keys were recovered.

  Password (ASCII):  K3z5GjD7
  ZipCrypto keys  :  fb54aea8 86091352 edcee112   (bkcrack internal representation)

Usage:
  python3 -I decrypt_ztx.py <cableData.ztx> [out_dir]
  python3 -I decrypt_ztx.py --password <pw> <cableData.ztx> [out_dir]

Pure Python (stdlib only). Verifies every member against the archive CRC and
exits non-zero on any mismatch.
"""
import struct
import sys
import zlib
from pathlib import Path

PASSWORD = b"K3z5GjD7"

_CRC = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _CRC.append(_c)


def _decrypt(stored, password):
    """ZipCrypto-decrypt stored bytes (12-byte header + ciphertext); return the
    plaintext stream with the 12-byte encryption header removed."""
    k0, k1, k2 = 0x12345678, 0x23456789, 0x34567890
    for b in password:
        k0 = (k0 >> 8) ^ _CRC[(k0 ^ b) & 0xFF]
        k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 0x08088405 + 1) & 0xFFFFFFFF
        k2 = (k2 >> 8) ^ _CRC[(k2 ^ (k1 >> 24)) & 0xFF]
    out = bytearray(len(stored))
    for i, c in enumerate(stored):
        t = (k2 | 2) & 0xFFFF
        p = c ^ (((t * (t ^ 1)) >> 8) & 0xFF)
        out[i] = p
        k0 = (k0 >> 8) ^ _CRC[(k0 ^ p) & 0xFF]
        k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 0x08088405 + 1) & 0xFFFFFFFF
        k2 = (k2 >> 8) ^ _CRC[(k2 ^ (k1 >> 24)) & 0xFF]
    return bytes(out[12:])


def _members(data):
    """Yield (name, compression_method, stored_bytes, crc32) per central-directory
    entry. The central directory is authoritative for size/crc (local headers of
    streamed entries can carry zeroed sizes)."""
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("not a zip: no end-of-central-directory record")
    count = struct.unpack("<H", data[eocd + 10:eocd + 12])[0]
    p = struct.unpack("<I", data[eocd + 16:eocd + 20])[0]
    for _ in range(count):
        if data[p:p + 4] != b"PK\x01\x02":
            raise ValueError("corrupt central directory")
        crc = struct.unpack("<I", data[p + 16:p + 20])[0]
        csize = struct.unpack("<I", data[p + 20:p + 24])[0]
        nlen, elen, clen = struct.unpack("<HHH", data[p + 28:p + 34])
        method = struct.unpack("<H", data[p + 10:p + 12])[0]
        lho = struct.unpack("<I", data[p + 42:p + 46])[0]
        name = data[p + 46:p + 46 + nlen].decode("latin1")
        ln, le = struct.unpack("<HH", data[lho + 26:lho + 30])
        start = lho + 30 + ln + le
        yield name, method, data[start:start + csize], crc
        p += 46 + nlen + elen + clen


def decrypt_ztx(ztx_path, out_dir, password):
    data = Path(ztx_path).read_bytes()  # ponytail: whole .ztx (a few MB); members are bounded
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, method, stored, crc in _members(data):
        raw = _decrypt(stored, password)
        if method == 8:
            raw = zlib.decompressobj(-15).decompress(raw)
        got = zlib.crc32(raw) & 0xFFFFFFFF
        good = got == crc
        ok &= good
        dst = out_dir / name
        dst.write_bytes(raw)
        print(f"{name:14s} {len(raw):>9d} B  crc=0x{got:08X}  {'OK' if good else 'CRC MISMATCH'}  -> {dst}")
    return ok


def main(argv):
    password = PASSWORD
    args = argv[1:]
    if args and args[0] == "--password":
        password = args[1].encode()
        args = args[2:]
    if not args:
        print(__doc__)
        return 2
    ztx = args[0]
    out_dir = args[1] if len(args) > 1 else "."
    ok = decrypt_ztx(ztx, out_dir, password)
    if not ok:
        print("ERROR: at least one member failed CRC (wrong password?)", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
