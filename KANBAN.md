# WinTAX / Marelli F4 tool: kanban

Goal: a custom tool that downloads telemetry from the Marelli F4 ECU and decodes every channel, plus a decoder for the stored encrypted lap files.

Updated: 2026-11-01. Columns are Done, In progress, Backlog. Each card has an id.

## Done

- [x] **R0-R4** Download reassembled, container identified, transport decoded, channel table read.
- [x] **A3** Wire-protocol spec `docs/WIRE_PROTOCOL.md`.
- [x] **B1** ZipCrypto key recovered. Password `K3z5GjD7`, one key for all 897 laps. `src/decrypt_ztx.py`.
- [x] **A1/A2/A4** File format decoded. `docs/FILE_FORMAT.md`, `src/wtx_fileformat.py`.
- [x] **B2** Decoder `src/wtx_decode.py`. 50 ms block format, 188 channels, 4.17 M samples. Decodes 897 of 897 laps.
- [x] **B2b** Channel scaling: power-box currents and voltages, fuel-consumption volume, engine-load request. 15 validation checks pass.
- [x] **B3** ECU download client `src/wtx_client.py`. Control trigger wired. Offline pcap validation byte-identical.
- [x] **B4** CLI `src/wtx.py` (decrypt, decode, download, batch).
- [x] **B5** Whole-archive batch via `wtx.py batch`.
- [x] **V1** Finalization: output-path guard (never writes into the archive), download trigger wired, `wtx_fileformat.py` trimmed to the parser, `tests/test_wtx.py` added. Decoder 15 checks pass, batch clean.
- [x] **D1** `docs/FILE_FORMAT.md`, `README.md` refreshed to the final state.

## In progress: GUI app for worldwide distribution

Plan: `docs/APP_PLAN.md`. Ledger: `.sdd/progress.md`. Build is subagent-driven:
Haiku implements, Sonnet reviews and fixes, Fable escalates, Opus orchestrates.

- [x] **G1** Session core (headless): download, discover, decode, deep logs, `run.json` metrics, results zip. Reuses `src/`. Sonnet-verified, 11 tests pass.
- [x] **G2** Tkinter GUI: ECU IP + input and output folder fields, buttons, live log pane, progress, metrics summary, config memory. Sonnet-verified, 21 tests pass.
- [x] **G3** Packaging: PyInstaller one-file Windows exe, `build_windows.bat`, CI workflow. Sonnet-approved; Linux build and frozen-window launch verified.
- [x] **Docs** `USER_GUIDE.md` (layman steps) and `BUILD.md` (build the exe). STE-clean.
- [x] **Final review** whole-branch (Sonnet): code sound, safety verified, 24 tests pass. Verdict: pilot-ready, not mass-ready until gates.
- [ ] **Fixes** I1-I6 and cheap minors (Sonnet, in progress). USER_GUIDE fixes (orchestrator, in progress).
- [ ] **Gates before wide distribution**: owner bench-tests the real exe on a known ECU (the live download never ran here). Keep the repo PRIVATE (password, serial, driver name tracked).

## Backlog

- [ ] **B6** Live download off the ECU. Needs the ECU on the 192.168.111.0/24 network and the user's go-ahead, because the trigger writes a SET to the ECU and stops its logger. One wire detail (a trailing credit) is untested without the ECU. The live blob-to-members split is also not yet built (proven only for the stored files). The GUI download exercises this path for real.

## Channels left raw (with code notes)

- `CcanRx1` to `CcanRx11`: unconfigured CAN receive registers, zero on the test lap.
- `GPS_Altitude`, `GPS_PosAccuracy`, `GPS_SpdAccuracy`: the logger stored these truncated.
- `PBX_*_State` (12): status bits.
- GPS and wheel-speed scales are not exercised by the parked engine-test lap; moving laps confirm them.

## Notes

- Transport is UDP only, one active wrap-around recording, no per-lap selection on the wire.
- The stored-file path (decrypt then decode) is the fully working route. The batch run decodes the whole 897-lap archive.
