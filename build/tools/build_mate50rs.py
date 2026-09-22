#!/usr/bin/env python3
"""Mate 50 RS boot and U-Boot candidate builder."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def build(input_dir: Path, output_dir: Path, config: dict) -> tuple[Path, Path]:
    """Build Mate 50 RS artifacts into output_dir and return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    boot = input_dir / "boot.bin"
    uboot = input_dir / "uboot.bin"
    uboot_out = output_dir / "uboot.bin"
    strict = output_dir / "strict-host.bin"
    boot_out = output_dir / "boot.bin"

    subprocess.run([
        sys.executable, str(ROOT / "tools/build_sprd_postverify_uboot_patch.py"),
        str(uboot), str(uboot_out), "--load-address", "0x9f000000",
        "--patch-word", config["uboot"]["patches"][0],
        "--patch-word", config["uboot"]["patches"][1],
    ], check=True, cwd=ROOT)
    subprocess.run([
        sys.executable, str(ROOT / "tools/patch_dtb_strict_host_inplace.py"),
        str(boot), str(strict), "--allow-signed-image",
    ], check=True, cwd=ROOT)
    subprocess.run([
        sys.executable, str(ROOT / "tools/patch_boot_usb_cold_start.py"),
        str(strict), str(boot_out), "--allow-signed-image",
    ], check=True, cwd=ROOT)
    return boot_out, uboot_out
