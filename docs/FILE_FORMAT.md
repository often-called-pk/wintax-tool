# Marelli F4 GEN2 lap file format

This document describes the stored lap container and the telemetry file format, as decoded from the user's own data. It supersedes the earlier draft, which assumed fixed 0.4 second cycles. The real format uses 50 millisecond blocks.

## Container: `cableData.ztx`

A lap folder holds XML metadata and one `cableData.ztx`. The `.ztx` is a ZIP archive with traditional ZipCrypto encryption. The password is `K3z5GjD7`, a WinTAX constant, the same for every lap. The archive has three members:

| Member | Size | Content |
|---|---|---|
| `dltable.t04` | 13440 B | The channel table. |
| `lap.bin` | variable | The telemetry samples. |
| `lapheader.bin` | 400 B | A short header. |

Note: the `dltable` captured on the download wire is not byte-identical to the stored one. WinTAX rewrites the last 52 bytes when it saves. Use the decrypted stored `dltable.t04` as authoritative.

## `dltable.t04`: the channel table

Header fields:

- Offset 0x00: magic `8c 7e`.
- Offset 0x04: table size, u32 big-endian, `0x3480` = 13440.
- Offset 0xb0: firmware string, for example `14.3.3.0_F4-GEN2-20230726-V25-CA`.
- Offset 0xd8: date. Offset 0xe8: time.
- Offset 0xfb: channel count, u16 little-endian, `0x00bc` = 188.
- Offset 0x16c: alias, for example `F4_106`.

Channel records start at offset 0x200. There are 188 records, each 24 bytes: a 16-byte name, ASCII, null-padded, not null-terminated when the name is exactly 16 characters, then an 8-byte descriptor.

Descriptor bytes:

- byte 0 class: `0x12` ECU-native, `0x22` computed or external (GPS, power box).
- byte 1 type: `0x14` float32, `0x04` int32, `0x02` int16, `0x01` uint8. All big-endian.
- byte 2 flag (meaning not confirmed).
- bytes 3 to 4 field id, u16 little-endian.
- byte 5 block-length field.
- byte 6 unit code. The low nibble is a decimal shift for integer channels.
- byte 7 format flags (not confirmed).

A second directory inside the table (entry id 7) holds one 8-byte record per channel, `[u16 index][class][b][ff ff ff ff]`. The class byte gives the sample rate: classes 8, 7, 6, 5, 4, 3, 2, 1 mean 200, 100, 50, 20, 10, 5, 2, 1 Hz.

## `lap.bin`: the samples

`lap.bin` is a chain of fixed-period blocks. One block is 50 ms. Twenty blocks make one second. Each block is a 32-byte header plus a packed payload.

Block header, little-endian:

- Offset 0: magic `66 AA` then ASCII `BD004` then `10`.
- Offset 8: lap id, u32.
- Offset 12: block counter, u32, increments by one per block.
- Offset 16: block index within the lap, u16.
- Offset 18: lap index within the run, u32.
- Offset 22: slot, u16, cycles 0 to 19 (the block position within its one-second frame).
- Offset 24: payload length in words, u16.
- Offset 26: a 6-byte tag.

Payload packing, within each block:

- Sections are ordered by sample width: the 4-byte channels first, then the 2-byte channels, then the 1-byte channels.
- Within a width, channels are ordered by rate class from high to low.
- Within a rate class, channels are ordered by table index from high to low.
- The whole payload is padded to a 4-byte boundary. There is no padding between sections.

The number of samples a channel contributes to slot `s` is `ceil(R * (s + 1) / 20) - ceil(R * s / 20)`, where `R` is the channel rate in Hz. A 200 Hz channel gives 10 samples per block. A 20 Hz channel gives one sample per block. A 1 Hz channel gives one sample in one block of the twenty.

Time: a block starts at `block_counter * 0.05` seconds. Within a block, sample `k` of a channel sits at `second_start + k / R`.

## `lapheader.bin`

400 bytes. Magic `12 00 90 01`, then null-terminated ASCII: the logger name, the alias, and the firmware string.

## Scaling

- Float32 and int32 channels are used as stored.
- Integer channels are scaled by `10` to the power of the negative low nibble of the unit code.
- Specific overrides live in `SCALE_FIX` in `src/wtx_decode.py`, each with its evidence. Battery voltage is a float32 in volts. A few power-box and GPS channels are still being pinned.

## Validation evidence

The decode is confirmed, not just plausible:

- Cross-channel identities hold. A PID output equals its P plus I plus D terms. Target minus actual error channels are near zero. A fuel-consumption int32 equals its int16 twin.
- The decoded sample rates match the team's own WinTAX export configs on every channel checked.
- GPS decodes to real coordinates near Coimbatore and Madras, with speed up to 199 km/h on moving laps, and distance that rises in step with the integral of speed.
- All 897 archived laps decode with an exact per-block size fit.
