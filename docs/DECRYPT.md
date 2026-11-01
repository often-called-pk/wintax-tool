# WinTAX cableData.ztx decryption

## Summary

- The container `cableData.ztx` uses traditional PKWARE encryption (ZipCrypto).
- The password is `K3z5GjD7`.
- The ZipCrypto internal keys are `fb54aea8 86091352 edcee112`.
- One key decrypts every lap in the archive.
- The decrypt tool is `src/decrypt_ztx.py`.

## The container

`cableData.ztx` is a standard ZIP archive. It holds 3 deflate members:

- `dltable.t04` (channel table, 13440 bytes),
- `lap.bin` (samples, 10860160 bytes),
- `lapheader.bin` (400 bytes).

Each entry carries a data descriptor (general purpose bit 3). For such an entry
the ZipCrypto check byte comes from the DOS time field, not the CRC. The tool
`zip2john` reports this as `TS_chk`.

## How we recovered the key

WinTAX stores the password with a light cipher, not as plain text. We recovered
it by static analysis of the application. The known plaintext attack could not
work here. See the next section for the reason.

Steps:

1. Build `bkcrack`. There is no apt package. Build it from source with cmake.
   The binary is at `/home/pk/opt/bkcrack-src/build/src/cli/bkcrack`.
2. Analyze `WT4FlashCard.exe`. This component writes `cableData.ztx`. It links
   zlib 1.2.3 and uses minizip-style ZipCrypto. It holds 3 C++ classes for the
   password: `CWTXZipPwdManager`, `CPlainTextZipPwdStore`, `CZtxPasswordCipher`.
3. Find the ciphered constant. The string `Ej9KK84|` sits in read-only data.
   `CZtxPasswordCipher` transforms it into the real password at run time.
4. Reverse the cipher. The transform is a byte permutation plus a per-index
   arithmetic step. The permutation seed is the byte sum modulo the length. The
   arithmetic step subtracts `index mod 5` from each byte.
5. Recover the password. A brute force over the 8 factorial byte permutations,
   with the arithmetic step, finds the one output that is valid. The check is:
   decrypt `dltable.t04`, inflate it, and compare its CRC32 to the stored CRC
   `0xE53AA412`. The result is `K3z5GjD7`. The script is `src/_crack_cipher.py`.
6. Derive the keys. Run `bkcrack --password "K3z5GjD7"`. It prints the internal
   representation `fb54aea8 86091352 edcee112`.

## Why the known plaintext attack could not work

The supplied file `artifacts/dltable_plaintext.bin` is not the true plaintext of
`dltable.t04`. Its CRC32 is `0x7BDB0A77`. The archive stores CRC `0xE53AA412`.
The two buffers match for the first 13312 bytes. They differ in 52 bytes near the
end (offsets 13312 and later). WinTAX changes a trailing region when it stores
the table.

A known plaintext attack on a deflate entry needs the exact compressed bytes.
The compressed bytes come from the exact uncompressed bytes through the exact
encoder. The supplied plaintext is wrong in its tail, so its compressed form
diverges from the stored stream. No encoder reproduced the stored 5263-byte
stream, which matches this conclusion. The encoder itself is zlib 1.2.3.

## Decrypt a lap

Use the Python tool (stdlib only, no attack needed):

```
python3 -I src/decrypt_ztx.py <path/to/cableData.ztx> <out_dir>
```

It writes `dltable.t04`, `lap.bin`, and `lapheader.bin` to `out_dir`. It checks
every member against the archive CRC and exits non-zero on a mismatch.

Or decrypt the whole archive with bkcrack:

```
bkcrack -C <path/to/cableData.ztx> -k fb54aea8 86091352 edcee112 -D out.zip
unzip out.zip
```

## One key for all laps

The password is a constant in WinTAX, so it is the same for every lap. A test of
12 laps across different runs, sessions, and car ids showed this. Each lap
decrypted, and each inflated `dltable.t04` matched its stored CRC. The archive
holds 897 laps.
