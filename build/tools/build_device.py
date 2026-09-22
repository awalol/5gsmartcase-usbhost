#!/usr/bin/env python3
"""Shared device builder for the audited boot and U-Boot candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "input"
OUTPUT = ROOT / "output"
TOOLS = ROOT / "tools"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, cwd=ROOT)


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def build(device: str) -> None:
    config = json.loads((ROOT / "configs" / f"{device}.json").read_text())
    boot = INPUT / "boot.bin"
    uboot = INPUT / "uboot.bin"
    actual = {"boot.bin": sha(boot.read_bytes()), "uboot.bin": sha(uboot.read_bytes())}
    if actual != config["input"]:
        raise RuntimeError(f"input hash mismatch for {device}: {actual}; expected {config['input']}")

    with tempfile.TemporaryDirectory(prefix=f"{device}-build-") as directory:
        temp = Path(directory)
        boot_out = temp / "boot.bin"
        uboot_out = temp / "uboot.bin"

        if device == "p60":
            p60_out = temp / "p60"
            run([
                sys.executable, str(TOOLS / "build_p60.py"),
                "--allow-signed-image", "--input-dir", str(INPUT),
                "--output", str(p60_out),
            ])
            copy_atomic(p60_out / "boot.img", boot_out)
            copy_atomic(p60_out / "uboot.bin", uboot_out)
        else:
            run([
                sys.executable, str(TOOLS / "build_sprd_postverify_uboot_patch.py"),
                str(uboot), str(uboot_out), "--load-address", "0x9f000000",
                "--patch-word", config["uboot"]["patches"][0],
                "--patch-word", config["uboot"]["patches"][1],
            ])
            strict = temp / "strict-host.bin"
            run([sys.executable, str(TOOLS / "patch_dtb_strict_host_inplace.py"), str(boot), str(strict), "--allow-signed-image"])
            run([sys.executable, str(TOOLS / "patch_boot_usb_cold_start.py"), str(strict), str(boot_out), "--allow-signed-image"])

        if sha(boot_out.read_bytes()) != config["output"]["boot.bin"]:
            raise RuntimeError("boot output hash mismatch")
        if sha(uboot_out.read_bytes()) != config["output"]["uboot.bin"]:
            raise RuntimeError("U-Boot output hash mismatch")
        copy_atomic(boot_out, OUTPUT / "boot.bin")
        copy_atomic(uboot_out, OUTPUT / "uboot.bin")

    (OUTPUT / "SHA256SUMS.txt").write_text(
        f"{config['output']['boot.bin']}  boot.bin\n"
        f"{config['output']['uboot.bin']}  uboot.bin\n",
        encoding="ascii",
    )
    (OUTPUT / "MANIFEST.json").write_text(json.dumps({
        "device": device,
        "status": "UNTESTED_ON_DEVICE",
        "input": config["input"],
        "output": config["output"],
        "offsets": config.get("offsets", {}),
        "warnings": ["Boot payload signature is invalidated.", "No device was flashed or boot-tested."],
    }, indent=2) + "\n", encoding="ascii")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("p60", "mate50rs"), required=True)
    args = parser.parse_args()
    build(args.device)


if __name__ == "__main__":
    main()
