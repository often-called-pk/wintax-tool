"""Recover the WinTAX ZipCrypto password from the ciphered constant "Ej9KK84|"
found in WT4FlashCard.exe (class CZtxPasswordCipher). The cipher = a byte
permutation + a per-position arithmetic step. We brute-force all 8! byte
permutations under several arithmetic hypotheses and verify each candidate by
decrypting dltable.t04 and checking CRC32 == the zip's stored CRC.

Usage: python3 -I _crack_cipher.py <cableData.ztx>
"""
import itertools, struct, sys, zlib
from pathlib import Path

ZTX = sys.argv[1]
TARGET_CRC = 0xE53AA412
CIPHERED = b"Ej9KK84|"

data = Path(ZTX).read_bytes()
off, stored = 0, None
while off + 4 <= len(data) and data[off:off + 4] == b"PK\x03\x04":
    n, m = struct.unpack("<HH", data[off + 26:off + 30])
    name = data[off + 30:off + 30 + n].decode("latin1")
    csize = struct.unpack("<I", data[off + 18:off + 22])[0]
    start = off + 30 + n + m
    if name == "dltable.t04":
        stored = data[start:start + csize]
        break
    off = start + csize

CRC = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    CRC.append(c)


CHECK_HI = (struct.unpack("<H", data[10:12])[0] >> 8) & 0xFF  # TS check byte (0x91)


def verify(pw):
    k0, k1, k2 = 0x12345678, 0x23456789, 0x34567890
    for b in pw:
        k0 = (k0 >> 8) ^ CRC[(k0 ^ b) & 0xFF]
        k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 0x08088405 + 1) & 0xFFFFFFFF
        k2 = (k2 >> 8) ^ CRC[(k2 ^ (k1 >> 24)) & 0xFF]
    out = bytearray()
    for i, c in enumerate(stored):
        t = (k2 | 2) & 0xFFFF
        p = c ^ (((t * (t ^ 1)) >> 8) & 0xFF)
        out.append(p)
        k0 = (k0 >> 8) ^ CRC[(k0 ^ p) & 0xFF]
        k1 = (((k1 + (k0 & 0xFF)) & 0xFFFFFFFF) * 0x08088405 + 1) & 0xFFFFFFFF
        k2 = (k2 >> 8) ^ CRC[(k2 ^ (k1 >> 24)) & 0xFF]
        if i == 11 and out[11] != CHECK_HI:
            return False
    try:
        raw = zlib.decompressobj(-15).decompress(bytes(out[12:]))
    except Exception:
        return False
    return (zlib.crc32(raw) & 0xFFFFFFFF) == TARGET_CRC


def ariths():
    yield "id", lambda c, j: c & 0xFF
    yield "sub5", lambda c, j: (c - (j % 5)) & 0xFF
    yield "add5", lambda c, j: (c + (j % 5)) & 0xFF
    yield "T1", lambda c, j: (159 - (c - (j % 5))) & 0xFF if (c - (j % 5)) < 32 else (c - (j % 5)) & 0xFF
    yield "T2f", lambda c, j: ((c + (j % 5) - 32) % 95) + 32
    yield "T2i", lambda c, j: ((c - (j % 5) - 32) % 95) + 32
    yield "subi", lambda c, j: (c - j) & 0xFF
    yield "addi", lambda c, j: (c + j) & 0xFF


cb = list(CIPHERED)
L = len(cb)
checked = 0
for name, f in ariths():
    for perm in itertools.permutations(range(L)):
        pw = bytes(f(cb[perm[j]], j) for j in range(L))
        checked += 1
        # fast reject on the first decrypted deflate byte range via full verify
        if verify(pw):
            print(f"FOUND password = {pw!r}  (arith={name})")
            print("hex:", pw.hex())
            sys.exit(0)
print(f"no match after {checked} candidates")
sys.exit(2)
