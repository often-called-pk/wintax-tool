"""Parsers for two members of a Marelli F4 GEN2 stored lap (cableData.ztx). See docs/FILE_FORMAT.md.

parse_dltable reads dltable.t04, the channel table. wtx_decode.py uses it and reads the
lap.bin sample layout itself. parse_lapheader reads the 400 byte lapheader.bin.

A dltable record is a 16 byte name (null padded) and an 8 byte descriptor. The records start at
byte 0x200. The descriptor holds no sample rate. The rate is the class byte of dltable section 7.
The sample width follows from the type byte. wtx_decode.py reads both.

Convention: OBSERVED = read from the bytes and cross-checked. INFERRED = a reasonable reading,
not proven.
"""
import struct

REC_OFFSET = 0x200
REC_SIZE = 24


# ---------------------------------------------------------------- dltable.t04

def _cstr(data, off, maxlen=64):
    end = data.find(b"\x00", off, off + maxlen)
    if end < 0:
        end = off + maxlen
    return data[off:end].decode("latin1", "replace")


def parse_dltable(data):
    """Parse a dltable.t04 (bytes). Return a dict with header fields and channels."""
    if len(data) < REC_OFFSET + REC_SIZE:
        raise ValueError("dltable too short")
    n = struct.unpack_from("<H", data, 0xFB)[0]        # OBSERVED: 0x00bc = 188
    channels = []
    for i in range(n):
        off = REC_OFFSET + i * REC_SIZE
        name = data[off:off + 16].split(b"\x00")[0].decode("latin1", "replace")
        d = data[off + 16:off + 24]
        channels.append({
            "index": i,
            "name": name,
            "cls": d[0],                       # 0x12 normal, 0x22 computed / GPS
            "type": d[1],                      # 0x14 float32, 0x04 int32, 0x02 int16, 0x01 uint8, big endian
            "flag": d[2],                      # 0x00 / 0x01
            "field_id": d[3] | (d[4] << 8),    # 16-bit id (desc[4] is 0)
            "unit_code": d[6],                 # low nibble = decimals of an integer channel
            "fmt": d[7],                        # INFERRED: format / flags
            "descriptor": d.hex(),
        })
    return {
        "firmware": _cstr(data, 0xB0),
        "date": _cstr(data, 0xD8),
        "time": _cstr(data, 0xE8),
        "alias": _cstr(data, 0x16C),
        "n_channels": n,
        "channels": channels,
    }


# --------------------------------------------------------------- lapheader.bin

def parse_lapheader(data):
    """Parse a 400-byte lapheader.bin. Return the magic and the ASCII fields.

    The lapheader is not present in the raw download blob (it is a separate ZIP
    member). This reads the structure described for it: a 4-byte magic
    `12 00 90 01`, then null-terminated ASCII fields (logger name, alias,
    firmware string).
    """
    out = {"magic": data[:4].hex()}
    # pull the printable ASCII runs; the first few are logger name, alias, firmware
    strings, run, start = [], [], None
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if start is None:
                start = i
            run.append(chr(b))
        else:
            if run and len(run) >= 3:
                strings.append((start, "".join(run)))
            run, start = [], None
    if run and len(run) >= 3:
        strings.append((start, "".join(run)))
    out["strings"] = strings
    return out
