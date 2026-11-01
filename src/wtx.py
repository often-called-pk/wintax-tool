#!/usr/bin/env python3
"""WinTAX stored-lap tool: decrypt, decode, batch and download.

Run with: python3 -I src/wtx.py -h
"""
import argparse
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

# python -I does not add the script folder to sys.path. Add it here.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import decrypt_ztx  # noqa: E402
import wtx_client  # noqa: E402
import wtx_decode  # noqa: E402


def outdir_for(arg, tree):
    """Return the output folder: arg, or ./out. Exit if it lies inside tree. The tool never writes into the data."""
    out, tree = Path(arg or "out").resolve(), Path(tree).resolve()
    if out.is_relative_to(tree):
        sys.exit(f"ERROR: the output folder {out} is inside the data folder {tree}. Give an outdir outside the data.")
    return out


def cmd_decrypt(a):
    outdir = outdir_for(a.outdir, Path(a.ztx).parent)
    if not decrypt_ztx.decrypt_ztx(a.ztx, outdir, decrypt_ztx.PASSWORD):
        sys.exit("ERROR: a member failed the CRC check. Check the file and the password.")


def cmd_decode(a):
    """Return 1 if a check fails. The files are written anyway."""
    return wtx_decode.main(Path(a.ztx), outdir_for(a.outdir, Path(a.ztx).parent))


def cmd_batch(a):
    if not Path(a.data_dir).is_dir():
        sys.exit(f"ERROR: not a folder: {a.data_dir}. Check the path.")
    outdir = outdir_for(a.outdir, a.data_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    ztxs = sorted(Path(a.data_dir).rglob("cableData.ztx"))
    used, done, failed = set(), 0, 0
    # ponytail: one lap at a time. Add a process pool if a full archive run is too slow.
    with open(outdir / "batch_errors.log", "w") as log:
        for i, ztx in enumerate(ztxs, 1):
            lap = ztx.resolve().parent.name
            stem, k = lap, 1
            while stem in used:  # lap folder names repeat across the archive
                k += 1
                stem = f"{lap}_{k}"
            used.add(stem)
            buf = io.StringIO()  # keeps the per-lap check table out of the progress output
            try:
                # each lap decodes into its own temp folder; only the MAT is kept
                with tempfile.TemporaryDirectory(dir=outdir) as tmp, contextlib.redirect_stdout(buf):
                    rc = wtx_decode.main(ztx, tmp)
                    shutil.move(Path(tmp) / "lap_decoded.mat", outdir / f"{stem}.mat")
            except (Exception, SystemExit) as e:  # wtx_decode reports a bad lap with sys.exit
                failed += 1
                log.write(f"{ztx}: {type(e).__name__}: {e}\n")
                print(f"[{i}/{len(ztxs)}] FAILED {ztx}: {type(e).__name__}: {e}")
                continue
            done += 1
            if rc:  # a check failed, but the MAT is written: keep it and log the checks
                log.write(f"{ztx}: check FAIL\n{buf.getvalue()}")
            print(f"[{i}/{len(ztxs)}] {stem}.mat <- {ztx}")
    print(f"decoded {done}, failed {failed}")
    return 1 if failed else 0


def cmd_download(a):
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Note: download needs the ECU on the network at {wtx_client.ECU_IP_DEFAULT}.")
    print("It first runs the control session: open, set 5000 (one write to the ECU, as WinTAX does), get version, get port.")
    blob = outdir / "download_raw.bin"
    try:
        n = wtx_client.download(wtx_client.ECU_IP_DEFAULT, str(blob))
    except (OSError, wtx_client.ProtocolError) as e:  # no ECU: timeout or unreachable
        sys.exit(f"ERROR: download failed: {type(e).__name__}: {e}")
    print(f"wrote {n} bytes to {blob}")


def main(argv=None):
    p = argparse.ArgumentParser(description="WinTAX stored-lap tool.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("decrypt", help="write dltable.t04, lap.bin and lapheader.bin")
    s.add_argument("ztx")
    s.add_argument("outdir", nargs="?", help="default: ./out (never inside the ztx folder)")
    s.set_defaults(fn=cmd_decrypt)
    s = sub.add_parser("decode", help="decode a lap to a MAT file and a summary CSV")
    s.add_argument("ztx")
    s.add_argument("outdir", nargs="?", help="default: ./out (never inside the ztx folder)")
    s.set_defaults(fn=cmd_decode)
    s = sub.add_parser("batch", help="decode every cableData.ztx under data_dir to outdir/<lap>.mat (outdir outside data_dir)")
    s.add_argument("data_dir")
    s.add_argument("outdir")
    s.set_defaults(fn=cmd_batch)
    s = sub.add_parser("download", help="live download from the ECU (needs the ECU on the network, sends a SET to it)")
    s.add_argument("outdir")
    s.set_defaults(fn=cmd_download)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
