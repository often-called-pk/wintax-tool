# WinTAX / Marelli F4 telemetry tool

Read the team's stored telemetry and download recordings off the Marelli F4 GEN2 ECU, into open formats, without WinTAX. This works on the user's own data, own ECU, and own licensed software.

## What it does

- Decrypts the stored encrypted lap files (`cableData.ztx`).
- Decodes the 188 channels into per-channel samples with correct rates and scaling. It writes 186 series, because the two power-box byte pairs are merged into combined currents.
- Exports to MATLAB `.mat` and CSV.
- Downloads a recording off the ECU over Ethernet (client coded to the captured protocol, validated offline).

## Layout

- `src/decrypt_ztx.py` : decrypt a `cableData.ztx` into its three members.
- `src/wtx_decode.py` : decode a lap into channels, export MAT and CSV.
- `src/wtx_client.py` : the ECU download client (beacon, control trigger, credited download).
- `src/wtx.py` : one CLI over all of the above.
- `src/wtx_fileformat.py` : the channel-table parser used by the decoder.
- `tests/test_wtx.py` : offline tests for the control frames, the download, and the output-path guard.
- `docs/FILE_FORMAT.md` : the lap file format.
- `docs/WIRE_PROTOCOL.md` : the ECU download protocol.
- `docs/DECRYPT.md` : the ZipCrypto key and method.
- `artifacts/` : the plaintext download reassembled from the packet capture.

## Usage

```
python3 -I src/wtx.py decode  <cableData.ztx> [outdir]     # one lap to MAT and CSV
python3 -I src/wtx.py batch   <Data dir> <outdir>          # decode the whole archive
python3 -I src/wtx.py decrypt <cableData.ztx> [outdir]     # just decrypt the members
python3 -I src/wtx.py download <outdir>                    # pull a recording off the ECU
```

If you omit the output directory, `decode` writes to `./out`. The tool refuses to write inside the data tree, so it never touches your archive.

## How it works, in brief

- A stored lap is `cableData.ztx`, a ZipCrypto ZIP (password `K3z5GjD7`, one key for all 897 laps) holding `dltable.t04` (the channel table), `lap.bin` (the samples), and `lapheader.bin`.
- `lap.bin` is a chain of 50 ms blocks, each a 32-byte header plus a packed payload. Types are float32, int32, int16, and uint8. Rates run from 200 Hz down to 1 Hz. See `docs/FILE_FORMAT.md`.
- The download is connected UDP. The ECU beacons its identity on port 30001. The client runs a short control session on port 30000, then receives the data on port 40000 with a lock-step credit per group. See `docs/WIRE_PROTOCOL.md`.

## Confidence

The decode is validated by 15 checks: cross-channel identities, rates that match the team's own export configs, GPS that decodes to real coordinates with plausible speed and distance on moving laps, and power-box currents that sum correctly. All 897 archived laps decode with an exact size fit.

## Limits

- A few channels are left raw, with code notes: the unconfigured CAN receive registers, some GPS auxiliaries that the logger stored truncated, and the power-box status bits.
- The live download is validated offline against the packet capture, and the control trigger is wired. A live run needs the ECU on the network and your go-ahead, because the trigger writes a setting to the ECU and stops its logger while the download runs. One small wire detail (a trailing credit) could not be tested without the ECU.
- The live path saves the raw download blob. Turning a live blob into a MAT still needs the blob-to-members split, which is proven only for the stored files. The stored-file path (decrypt then decode) is the fully working route today.
