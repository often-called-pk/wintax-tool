# WinTAX / Magneti Marelli F4 GEN2 download wire protocol

Client specification for downloading a recorded run off a Magneti Marelli F4 GEN2
ECU/data logger over Ethernet (UDP). It is written to be enough to implement a
downloader.

## Source and method

- Capture: `wintax-packet-capture.pcapng` (UDP only, 65.1 s, one complete download
  session, 147272 frames).
- Hosts:
  - PC (WinTAX): `192.168.111.75`
  - ECU / logger: `192.168.111.254`
  - Broadcast: `192.168.111.255` (and `255.255.255.255`)
- ECU identity: serial `H43001J58F200923`, name `SRG_927`, alias `F4_106`,
  ABS id `3012`, firmware `14.3.3.0_F4-GEN2-20230726-V25-CA v.1`, protocol/app
  version `22.10.6.6`.
- Decoded with `tshark` only. No captured payload was executed. All payload bytes
  were treated as data.

### Convention in this document

- `OBSERVED` = read directly from the capture and cross-checked.
- `INFERRED` = a reasonable reading of field meaning, not proven by the capture.
- Hex bytes are shown space-separated. `[name]` marks a variable field.
- Multi-byte integers: the control header length field is big-endian (BE). The
  download trailer byte-count field is big-endian. Some control-body and
  data-body values appear little-endian (LE); each is labelled where it matters.

## Ports and roles (OBSERVED)

| Port | Transport | Direction | Role |
|---|---|---|---|
| 60000 | UDP broadcast | PC -> 255.255.255.255 | Discovery probe (find device by serial) |
| 30001 | UDP broadcast | ECU:40001 -> .255 | ECU identity beacon (binary), ~1 Hz |
| 6969 | UDP broadcast | ECU:6969 -> .255 | ECU console log (ASCII) |
| 30000 | UDP unicast | PC:ephemeral <-> ECU:30000 | Control session (open/set/get/close) |
| 40000 | UDP unicast | ECU:40000 <-> PC:ephemeral | Bulk download stream + PC flow-control |

The PC receive port for the download in this capture is `54529`. It is ephemeral:
the ECU learns it from the source port of the PC flow-control packet (see Phase 3
and Phase 4). A client may use any UDP source port for that packet.

---

## Phase 0: ECU autonomous beacons (OBSERVED)

The ECU broadcasts without being asked. These start before any PC traffic
(first beacon at t=0.720 s, frame 9). Use them to find the ECU and to watch its
state. They are not requests and need no reply.

### 0.1 Identity beacon, UDP `192.168.111.254:40001 -> 192.168.111.255:30001`

120-byte payload, fixed layout:

```
offset  bytes                         meaning
0       10 01 03 36                   magic / message type
4       00 00 00 70                   body length = 112 (BE)
--- body (112 bytes) ---
8       00 03 02 00                   unknown A (INFERRED: subtype/flags)
12      05 00 00 51                   unknown B (INFERRED: counter/id)
16      c0 a8 6f fe                   ECU IPv4 = 192.168.111.254
20      ff ff ff 00                   netmask = 255.255.255.0
24      [32 bytes]                    name, NUL-padded       "SRG_927"
56      [32 bytes]                    alias, NUL-padded      "F4_106"
88      [32 bytes]                    live state, NUL-padded "(ABS=3012,TrigOut)"
```

Full example (frame 9):

```
10 01 03 36 00 00 00 70 00 03 02 00 05 00 00 51
c0 a8 6f fe ff ff ff 00 53 52 47 5f 39 32 37 00 ...(SRG_927)
46 34 5f 31 30 36 00 ...                          (F4_106)
28 41 42 53 3d 33 30 31 32 2c 54 72 69 67 4f 75 74 29 00 ... ((ABS=3012,TrigOut))
```

The live-state field changes with ECU mode (OBSERVED, three distinct values):

| State string | Beacons | When |
|---|---|---|
| `(ABS=3012,TrigOut)` | 9 | idle, before and after the transfer |
| `(ABS=3012,Download)` | 69 | during the transfer (first at t=6.720 s frame 96, last at t=62.425 s frame 146496) |
| `(ABS=3012,Init)` with alias `Alias undefined` | 1 | ECU re-initialising logging after the transfer (t=62.824 s frame 147251) |

A client should watch this field. The ECU enters `Download` about 0.09 s before the
first data page and leaves it when the transfer ends. This is the cleanest external
signal of transfer start and end.

### 0.2 Console log, UDP `192.168.111.254:6969 -> 192.168.111.255:6969`

ASCII text, one line per packet, NUL-terminated. Informational only, not required to
drive the download, but useful to confirm ECU actions. Format:
`HH:MM:SS.mmm (Tsk:NN) <SOURCE> <level> : <message>`.

Key lines (OBSERVED):

```
frame 94  t=6.658  00:00:16.533 (Tsk:0f) LOG-CTXT Info+ : Saving partial times, main file 8001
frame 95  t=6.707  00:00:16.547 (Tsk:0f)  LOGGER  Error : LOG_STOPPED
frame 147252 t=62.836 ... LOG_INIT Info+ : Disk usage is Wrap-around, default lap time is 0 seconds
frame 147255 t=63.016 ... INTERFTL Info+ : 188 valid channels out of 188 expected ( Table : 0 )
frame 147265 t=63.110 ... LOG-CTXT Info+ : Found valid partial times at file 0x8001 with cnt 0x027a
```

The logger stops recording (`LOG_STOPPED`) at the start of the transfer and
re-initialises at the end. The recording is a single wrap-around (circular) memory,
`main file 8001`. There is no per-lap file selection on the wire (see Phase 2).

---

## Phase 1: Discovery probe (OBSERVED)

The PC broadcasts a 37-byte probe to `255.255.255.255:60000`. It is repeated from a
fresh ephemeral source port each time (frames 53, 54, 55 at t~4.37 s; frames 177, 178
at t~8.96 s).

```
offset  bytes            meaning
0       00 64            magic = 0x0064
2       00 21            ASCII length = 0x21 = 33
4       [33 bytes ASCII] "SN=<serial>&MAC=undefined"
```

Example (frame 53), the ASCII is `SN=H43001J58F200923&MAC=undefined`:

```
00 64 00 21 53 4e 3d 48 34 33 30 30 31 4a 35 38 46 32 30 30 39 32 33
26 4d 41 43 3d 75 6e 64 65 66 69 6e 65 64
```

How the ECU is then addressed (OBSERVED):

- No reply to the probe was seen on port 60000 (no unicast to or from
  `192.168.111.254:60000` anywhere in the capture).
- The ECU is already beaconing its IPv4 address on `:30001` (Phase 0.1). The PC
  learns `192.168.111.254` from that beacon body, then talks unicast to
  `192.168.111.254:30000` (control) and receives the stream from
  `192.168.111.254:40000`.

So the probe is a fire-and-forget "is the device with this serial present" broadcast.
A client can skip it and read the `:30001` beacon directly to find the ECU and
confirm the serial/name. INFERRED: on networks where the beacon is absent, the probe
may prompt a reply; not observed here.

---

## Phase 2: Control session, UDP `PC:ephemeral <-> 192.168.111.254:30000` (OBSERVED)

Short request/response sessions. The PC opens a new ephemeral source port per session.
One session (the first, frame 85 onward) triggers the download. The others repeat
through the transfer (roughly every 1.4 s in the later half) and are status polls;
they are not required for the stream to continue.

### 2.1 Common frame format

```
offset  bytes        meaning
0       10 01        magic
2       [op]         opcode: 01 open, 02 set, 03 get, 04 close, 05 ack
3       [tag]        0x22 for PC requests and ECU get-replies; 0x00 for ECU acks
4       [len]        body length, 4 bytes BE
8       [body]       len bytes
```

- `op 01` open, `op 02` set, `op 04` close: the ECU replies with `op 05` ack.
- `op 03` get: the ECU replies with its own `op 03` message (not an ack).
- `op 05` ack: `tag=00`, `len=8`, body = the exact 8-byte header of the request being
  acknowledged.

### 2.2 The trigger sequence (frames 85-92, 151-154; session src port 57998)

This is the full session that starts the download. Times in seconds.

```
f85  6.6268  PC->ECU  open    10 01 01 22 00 00 00 00
f86  6.6276  ECU->PC  ack     10 01 05 00 00 00 00 08  10 01 01 22 00 00 00 00
f87  6.6278  PC->ECU  set     10 01 02 22 00 00 00 0c  04 00 00 00 00 00 00 00 00 00 13 88
f88  6.6284  ECU->PC  ack     10 01 05 00 00 00 00 08  10 01 02 22 00 00 00 0c
f89  6.6286  PC->ECU  get     10 01 03 22 00 00 00 0c  04 00 00 08 01 03 00 00 16 0a 06 06
f90  6.6293  ECU->PC  get-rep 10 01 03 22 00 00 00 0c  11 00 00 08 07 03 00 00 16 0a 06 06
f91  6.6294  PC->ECU  get     10 01 03 22 00 00 00 0c  04 00 00 08 04 00 e2 00 9c 40 00 00
f92  6.6338  ECU->PC  get-rep 10 01 03 22 00 00 00 0c  11 00 00 08 09 00 e2 00 00 00 00 00
   >>> PC sends first flow-control packet to ECU:40000 at t=6.6342 (frame 93) <<<
   >>> first data page at t=6.7263 (frame 97) <<<
f151 6.7680  PC->ECU  get     10 01 03 22 00 00 00 0c  04 00 00 08 02 03 00 00 16 0a 06 06
f152 6.7693  ECU->PC  get-rep 10 01 03 22 00 00 00 0c  11 00 00 08 08 03 00 00 16 0a 06 06
f153 6.7695  PC->ECU  close   10 01 04 22 00 00 00 00
f154 6.7701  ECU->PC  ack     10 01 05 00 00 00 00 08  10 01 04 22 00 00 00 00
```

The session closes at t=6.770 s, but the download runs until t=62.6 s. So the control
session only arms and starts the transfer; it does not need to stay open.

### 2.3 Decoded bodies

op 02 SET (frame 87), 12-byte body:

```
04 00 00 00 00 00 00 00 00 00 13 88
^^                            ^^^^^
param/selector 0x04           value 0x1388 = 5000 (BE 16-bit in the last 2 bytes)
```

- The value 5000 is byte-identical in the SET of every session (trigger and all polls).
- OBSERVED: 5000 is a constant session parameter, not a per-run selector.
- INFERRED: likely a timeout or transfer parameter in milliseconds. Exact meaning not
  proven.

op 03 GET, 12-byte body, request/reply pairs. The body is
`[dir] 00 00 [sel] [8-byte item]`, where `dir`=0x04 in a PC request and 0x11 in an ECU
reply (INFERRED), and `sel` selects what is read (INFERRED). Observed items:

1. Firmware/app version (frames 89/90):
   - req item `01 03 00 00 16 0a 06 06`, reply item `07 03 00 00 16 0a 06 06`.
   - Value `16 0a 06 06` = version `22.10.6.6` (0x16=22, 0x0a=10, 0x06=6, 0x06=6).
   - The byte before `03` is a transaction counter (01 in the first version GET, 02 in
     the later one; reply 07 then 08).

2. Download port (frames 91/92):
   - req item `04 00 e2 00 9c 40 00 00`, reply item `09 00 e2 00 00 00 00 00`.
   - `9c 40` = 0x9C40 = 40000, the ECU download port, present in the request and
     zeroed in the reply. INFERRED: the PC confirms/claims the download port; this GET
     immediately precedes stream start (frame 92 at t=6.6338, stream at t=6.6342).
   - `e2 00` (also `e4 00` in other sessions) is a transaction counter.

3. Progress / logger block counter (poll sessions only, e.g. frame 18010):
   - req body `04 00 00 28 01 00 00 0b b0 00 00 00`.
   - The value `0b b0` = 0x0BB0 = 2992. It rises monotonically with time across
     sessions: 2991 at t=23.7 s, 2992 at t=28.3 s, 2995 at t=33.2 s, 2997 at t=39.5 s.
   - OBSERVED: the same counter appears inside the 628-byte checkpoint data blocks of
     the download stream (Phase 3.6). INFERRED: it is the logger memory block index,
     usable as a coarse progress indicator.

4. Status GET (poll sessions, e.g. frame 18011/18012):
   - req item `06 01 00 05 00 00 00 05`, reply item `0b 01 e4 00 00 00 00 00`.
   - INFERRED: a download-active or buffer-status read. Not required to implement the
     download.

### 2.4 Run / lap selection (OBSERVED)

There is no lap name, lap index, or run name anywhere on the wire. The strings
`dltable`, `lap.bin`, `lapheader`, `.t04`, `.bin` do not appear in any frame.

The ECU holds one active recording (`main file 8001`, a wrap-around buffer). The
download transfers that whole recording. "Selecting the run" in this capture means:

1. Select the ECU by serial (Phase 1) or by reading its beacon (Phase 0.1).
2. Run the trigger session (Phase 2.2). The SET (5000) and the GETs arm the dump.

The request that actually triggers the `:40000` stream is the PC flow-control packet
to `ECU:40000` (frame 93, Phase 4), sent right after the port GET (frame 91/92).

---

## Phase 3: Download stream, UDP `192.168.111.254:40000 -> PC:54529` (OBSERVED)

The ECU streams fixed-structure pages. The PC paces the stream with one small
flow-control packet per group (Phase 4). 87928 data pages and 29316 trailer pages were
sent; the reassembled payload is about 90 MB.

### 3.1 Page header (4 bytes, every ECU page)

```
offset  byte     meaning
0       [ord]    ordinal within the group: 01, 02, 03 for data; next value for the trailer
1       03       stream id (constant; INFERRED)
2       00       reserved (constant)
3       [kind]   0x11 = data page, 0x12 = group trailer
```

Data length of a page = `udp.length - 12` (8 UDP header + 4 page header). It varies by
page: 1024 bytes for a full data page (`udp.length` 1036), 400 bytes for a directory
record (`udp.length` 412), and smaller for the last page of a logical block.

### 3.2 Groups and the trailer

The stream is sent in groups. A group = up to three data pages (ordinals 01, 02, 03,
kind 0x11) followed by one trailer (kind 0x12). The trailer ordinal =
(number of data pages in the group) + 1.

Trailer body (12 bytes total):

```
offset  bytes      meaning
0       [ord]      04 for a full 3-page group; 03 for a 2-page group; 02 for a 1-page group
1       03 00 12   stream id, reserved, kind=trailer
4       [cnt]      2-byte BE = total DATA bytes in this group's data pages
6       [seq]      sequence byte, normally (seq2bit << 6): 00, 40, 80, c0
7       00
8       00 00 00 00
```

`cnt` verification (OBSERVED): directory group 3 x 400 = 1200 = 0x04B0; full data group
3 x 1024 = 3072 = 0x0C00; final group 936 + 32 = 968 = 0x03C8.

Trailer ordinal distribution over the whole transfer (OBSERVED): ord 04 x 29301
(full groups), ord 03 x 10 (two-page groups), ord 02 x 5 (one-page groups). Totals are
exact: 29301*3 + 10*2 + 5*1 = 87928 data pages, 29316 trailers, 29316 PC flow-control
packets.

### 3.3 Reassembly rule

1. For each group, read data pages in ascending ordinal order (01, then 02, then 03).
2. Append each page's data bytes (`udp.length - 12`, strip the 4-byte page header).
3. Append groups in arrival order.
4. Stop at end-of-transfer (Phase 3.5).

The three ordinals in a group are distinct, sequential pages, not repeats (OBSERVED:
the 33 leading 400-byte pages are 33 distinct records; consecutive 1024-byte groups
differ). There is no checksum or CRC in the page or trailer.

### 3.4 Flow control (summary; bytes in Phase 4)

Lock-step, one PC flow-control packet per group:

```
PC credit (seq N) -> ECU sends data pages 01..03 + trailer (seq N) -> PC credit (seq N+1) -> ...
```

- The PC sends the first credit to start (frame 93), before any data.
- After that the PC sends the next credit when it has received the group (OBSERVED
  mid-stream: trailer, then credit ~0.15 ms later, then the next group ~0.7 ms later).
- The 2-bit sequence lets each side detect a dropped group. Counts are clean in this
  capture (no loss), so retransmission behaviour was not observed. INFERRED: on loss,
  the side that sees a sequence gap re-requests or re-sends; a client should at least
  time out and re-send the last credit.

### 3.5 End of transfer (OBSERVED)

The transfer ends with a short final group (frames 147232-147234):

```
f147232  ECU->PC  len 948  01 03 00 11 ... (data page, ordinal 01, 936 data bytes)
f147233  ECU->PC  len 44   02 03 00 11 66 aa 42 44 30 30 34 ff ff ... (ordinal 02, 32 data bytes)
f147234  ECU->PC  len 20   03 03 00 12 03 c8 e0 00 00 00 00 00 (trailer, ordinal 03)
```

End-of-transfer signals, in order of reliability:

1. The ECU leaves the `Download` beacon state (last `Download` beacon t=62.425 s) and
   re-initialises logging (console `LOG_INIT` from t=62.836 s). Watch the `:30001`
   beacon state field.
2. A short final group: the trailer ordinal is `03` (or `02`), meaning fewer than
   three data pages. The PC then sends no further credit, so the stream stops.
3. The final trailer `seq` byte is `e0` (the full credit byte) rather than the masked
   `c0` expected for sequence 3. INFERRED: a quirk/marker of the final short group; do
   not rely on it alone.

INFERRED: the PC also knows the expected total size from the leading directory
(Phase 3.6) and stops crediting when the total is reached. A robust client should treat
either (a) a short group at the expected total, or (b) the beacon leaving `Download`,
or (c) a receive timeout after the last trailer, as end-of-transfer, and must stop
sending credits to let the ECU return to normal logging.

### 3.6 How the bytes map to dltable.t04 / lapheader.bin / lap.bin

The filenames are never sent on the wire. They are local names WinTAX gives to
structural segments of the reassembled byte stream. The segmentation is by position and
block type, not by any transmitted name. OBSERVED structure of the stream, in order:

1. Leading directory: 33 x 400-byte records (frames 97-149, 11 groups, `udp.length`
   412). Each record starts with `12 00 90 01` (`90 01` = 0x0190 = 400 = the record
   size), then ASCII `Marelli logger`, the alias `F4_106`, a per-record timestamp, the
   firmware string `14.3.3.0_F4-GEN2-...`, and binary config fields (one field is
   `9f 03` = 927, the `SRG_927` id). The last record is padded with `0xCD`. This
   13200-byte block is the download directory.
   INFERRED: this is `dltable.t04` (a table of fixed 400-byte records; `t04` = table
   type/version). It is the natural source of total size and layout.

2. Bulk channel data: a long run of 1024-byte data pages (`udp.length` 1036), broken
   into 3-page groups. This is the logged channel data.
   INFERRED: this is `lap.bin`.

3. Checkpoint blocks: every ~1.3 s a 628-byte block appears (`udp.length` 636), content
   `66 aa 42 44 30 30 34 ...` = magic `0xAA66` + ASCII `BD004` + a block counter
   (`af 0b` = 0x0BAF = 2991, rising to ~3011), often followed by a 32-byte block
   `66 aa BD004 ff ff ...` (`udp.length` 44). The counter matches the control-channel
   progress GET (Phase 2.3 item 3).
   INFERRED: these are lap/partial-time markers or buffer headers. The directory record
   plus these headers are the likely source of `lapheader.bin`.

Because the names are not on the wire, the exact byte ranges that WinTAX writes to each
of the three files cannot be proven from the capture alone. A downloader can safely do
one of:

- Save the whole reassembled stream to one file and split it in a later step using the
  directory in segment 1, or
- Treat segment 1 (the 400-byte directory records) as `dltable.t04` and the remaining
  stream as the data, and refine the split once the `dltable.t04` record format is
  decoded.

---

## Phase 4: PC back-channel, UDP `PC:54529 -> 192.168.111.254:40000` (OBSERVED)

The only bytes the PC sends on the download channel. One 12-byte packet per group.

```
offset  bytes      meaning
0       00 03 03 f2  fixed header (stream id 3, kind 0xf2 = credit)
4       [seq]        0x20 | (seq2bit << 6):  20, 60, a0, e0
5       00 00 00 00 00 00 00   (7 zero bytes)
```

Exactly four distinct packets exist in the whole capture (OBSERVED), cycling the 2-bit
sequence 0,1,2,3:

```
00 03 03 f2 20 00 00 00 00 00 00 00   seq 0   (count 7336)
00 03 03 f2 60 00 00 00 00 00 00 00   seq 1   (count 7334)
00 03 03 f2 a0 00 00 00 00 00 00 00   seq 2   (count 7326)
00 03 03 f2 e0 00 00 00 00 00 00 00   seq 3   (count 7320)
```

Cadence (OBSERVED):

- First credit (frame 93, seq 0) at t=6.6342 s, right after the control port GET, and
  before any data. This is what starts the stream. Its UDP source port (54529) is the
  address the ECU streams to.
- Then one credit per received group. At steady state the group cycle is about 1.1 to
  1.2 ms (credit about 0.15 ms after the trailer, next group about 0.7 ms after the
  credit). Average group rate about 524 groups/s; average data rate about 1570 data
  pages/s (about 2500/s peak).
- The sequence increments by 1 (mod 4) on each credit. The trailer of the group that a
  credit triggers carries the same 2-bit sequence in its `seq` byte (credit
  `0x20|seq<<6` -> trailer `seq<<6`).
- After the final trailer the PC sends no credit. Stopping credits is how the transfer
  ends cleanly.

---

## Phase 5: Timing, timeouts, and what a client must replicate

Observed timing (one session, lossless local capture):

| Event | Time (s) | Frame |
|---|---|---|
| First ECU identity beacon | 0.720 | 9 |
| First discovery probe | 4.370 | 53 |
| Trigger control session open | 6.627 | 85 |
| Port GET reply (stream about to start) | 6.634 | 92 |
| First PC credit to :40000 | 6.634 | 93 |
| ECU beacon enters `Download` | 6.720 | 96 |
| First data page | 6.726 | 97 |
| Logger `LOG_STOPPED` (console) | 6.707 | 95 |
| Last data/trailer (end of transfer) | 62.601 | 147234 |
| ECU beacon leaves `Download`, re-init | 62.4 to 62.8 | 146496+ |

There is an about 0.09 s gap between the first credit and the first data page: the ECU
stops logging and prepares the dump. A client must tolerate this initial delay.

What a client must replicate to be accepted by the ECU:

1. Learn the ECU IPv4 from the `:30001` beacon (or send the `:60000` serial probe
   first; a reply was not observed, so do not depend on one).
2. Open a control session to `ECU:30000` from an ephemeral port and run the sequence:
   `open (op01)`, `set (op02)` with the 12-byte body ending `13 88`, `get (op03)`
   version, `get (op03)` with `9c 40` (download port 40000). Use magic `10 01`, tag
   `0x22`, 4-byte BE length, and expect `op05` acks (for open/set/close) and `op03`
   replies (for get).
3. Immediately send the first flow-control packet `00 03 03 f2 20 00 ...` from the UDP
   port you want the data on, to `ECU:40000`. This both starts the stream and tells the
   ECU where to send it.
4. For each group received (data pages until a trailer `.. .. .. 12`), send the next
   flow-control packet with the sequence byte advanced (`20 -> 60 -> a0 -> e0 -> 20`).
   Keep the 2-bit sequence in step with the trailer.
5. Reassemble pages by ordinal, append groups in order (Phase 3.3).
6. Detect end-of-transfer (Phase 3.5): short final group and/or the beacon leaving
   `Download`. Then stop sending credits so the ECU returns to logging.
7. INFERRED but advisable: if no data arrives within a short timeout after a credit,
   re-send the same credit. The capture shows no loss, so the exact retransmission
   contract is not proven.

Inferred/open items a client should treat with care:

- SET value 5000 (0x1388): constant here; meaning not proven.
- Control GET `sel` bytes and transaction counters: field meaning inferred.
- The exact byte ranges for `dltable.t04`, `lapheader.bin`, `lap.bin`: the split is
  structural and not named on the wire (Phase 3.6).
- Retransmission and loss recovery: not exercised in this capture.
