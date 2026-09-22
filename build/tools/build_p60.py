#!/usr/bin/env python3
"""Offline, hash-locked P60 port of strict-host + cold-start V1 and U-Boot."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import build_sprd_postverify_uboot_patch as uboot
import patch_boot_usb_cold_start as cold
import patch_dtb_force_host_inplace as dtb
import patch_dtb_strict_host_inplace as strict

ROOT = Path(__file__).resolve().parents[1]
BOOT_HASH = "e0db8ab6fa30cdd653a33dbf2f505f98300273c055b7a31b0efe0607ebd4b198"
UBOOT_HASH = "6dad2c6670a4055143c2a1e628f136580d1265947166f903408a26b9221306e3"
BOOT_SIZE = 0xD00000
WORKER_OFFSET = 0x3E4664
WORKER = bytes.fromhex("621b00d0200080524280079180ae00b977ffff17")
PLUGIN = bytes.fromhex(
    "fd7bbea9211a00d021400491fd030091f35301a9f40302aaf30303aa420080d2"
    "e00314aace610a94c0010034211a00d0620080d221800491e00314aac8610a94"
    "20020035212e00d03f20023999ffff97e00313aaf35341a9fd7bc2a8c0035fd6"
    "212e00d022008052200080522220023990ffff97e00313aaf35341a9fd7bc2a8"
    "c0035fd6a0028092f3ffff17"
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def assemble():
    with tempfile.TemporaryDirectory(prefix="p60-cold-") as tmp:
        obj, elf, binary = (Path(tmp) / n for n in ("fix.o", "fix.elf", "fix.bin"))
        for command in (
            ["aarch64-linux-gnu-as", "-o", str(obj), str(ROOT / "tools/usb_cold_boot_fix.S")],
            ["aarch64-linux-gnu-ld", "-T", str(ROOT / "tools/usb_cold_boot_fix_p60.ld"), "-o", str(elf), str(obj)],
            ["aarch64-linux-gnu-objcopy", "-O", "binary", str(elf), str(binary)],
        ):
            subprocess.run(command, check=True)
        return binary.read_bytes()


def boot_patch(source):
    require(len(source) == BOOT_SIZE and sha(source) == BOOT_HASH, "unsupported P60 boot input")
    targets = [t for f in dtb.candidate_fdts(source)
               if (t := strict.matching_target(source, f, False))]
    require(len(targets) == 1, "expected one original P60 USB DTB")
    target = targets[0]
    signature = dtb.find_sprd_signature(source, target["fdt"])
    require(signature is not None, "missing signature footer")
    require(source[WORKER_OFFSET:WORKER_OFFSET + len(WORKER)] == WORKER, "worker mismatch")
    require(source[0x750888:0x75088F] == b"extcon\0", "extcon address mismatch")
    result = bytearray(strict.patch_forward(source, target))
    result[WORKER_OFFSET + 12:WORKER_OFFSET + 16] = bytes.fromhex("80821529")
    rebuilt = assemble()
    regions = []
    for region in cold.REGIONS:
        before = PLUGIN if region.offset == 0x3E3968 else (
            bytes.fromhex("545f0294") if region.offset == 0x3E4280 else region.before)
        start = region.offset - cold.REGIONS[0].offset
        after = rebuilt[start:start + len(before)]
        require(source[region.offset:region.end] == before, f"instruction mismatch at {region.offset:#x}")
        require(len(after) == len(before), "assembly size mismatch")
        result[region.offset:region.end] = after
        regions.append({"offset": region.offset, "before": before.hex(), "after": after.hex(),
                        "purpose": region.purpose})
    result = bytes(result)
    updated = [t for f in dtb.candidate_fdts(result)
               if (t := strict.matching_target(result, f, True))]
    require(len(updated) == 1, "patched DTB validation failed")
    require(len(result) == len(source), "partition size changed")
    require(result[:0xA00] == source[:0xA00], "boot headers changed")
    require(result[signature["footer"]:] == source[signature["footer"]:], "signature suffix changed")
    restored = bytearray(strict.patch_reverse(result, updated[0]))
    restored[WORKER_OFFSET:WORKER_OFFSET + len(WORKER)] = WORKER
    for region in regions:
        before = bytes.fromhex(region["before"])
        restored[region["offset"]:region["offset"] + len(before)] = before
    require(bytes(restored) == source, "round-trip or unchanged-span verification failed")
    return result, {"regions": regions, "worker_file_offset": hex(WORKER_OFFSET + 12),
                    "dtb": target["fdt"], "signature": signature,
                    "changed_bytes": sum(a != b for a, b in zip(source, result)),
                    "exact_reverse_verified": True}


def uboot_patch(source):
    require(len(source) == 0x140000 and sha(source) == UBOOT_HASH, "unsupported P60 U-Boot input")
    require(source[0x63325:0x63335] == b"sprd_verify_img\0", "verification function identity mismatch")
    return uboot.make_image(source, 0x9F000000,
                            [(0x9F05D9EC, 0xA9B57BFD, 0x52800020),
                             (0x9F05D9F0, 0x910003FD, 0xD65F03C0)], True)


def build(output, input_dir):
    names = ("boot.img", "uboot.bin", "MANIFEST.json", "SHA256SUMS.txt")
    require(not any((output / n).exists() for n in names), "refusing to overwrite existing outputs")
    original_boot = (input_dir / "boot.bin").read_bytes()
    original_uboot = (input_dir / "uboot.bin").read_bytes()
    boot, boot_report = boot_patch(original_boot)
    loader, patches = uboot_patch(original_uboot)
    require(sha(boot) == "9ea4f27a835c89a1560ac5e40a4a9d705c36a224c1411ecc9a3576824f00db1a",
            "boot output differs from tested P60 candidate")
    require(sha(loader) == "fef82c56d7345c8da2e2fa7262782ff654565fe884455c2fb6d6bfe8c364fc42",
            "U-Boot output differs from tested P60 candidate")
    manifest = {
        "format": "p60-strict-host-cold-start-v1", "status": "UNTESTED_ON_P60_DEVICE",
        "input": {"boot.bin": BOOT_HASH, "uboot.bin": UBOOT_HASH},
        "output": {"boot.img": sha(boot), "uboot.bin": sha(loader)},
        "sizes": {"boot.img": len(boot), "uboot.bin": len(loader)},
        "boot": boot_report,
        "uboot": {"runtime_patches": patches, "original_layout": uboot.parse_layout(original_uboot),
                  "patched_layout": uboot.parse_layout(loader, allow_relocated=True),
                  "signed_payload_and_certificate_unchanged": True},
        "warnings": ["No device was flashed or tested.", "Boot payload signature is invalidated.",
                     "Requires a working U-Boot bypass and independently tested recovery path.",
                     "Never overwrite ubootbak. Cold-start V1 does not guarantee cold enumeration."]}
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("boot.img", boot), ("uboot.bin", loader)):
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "MANIFEST.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    with (output / "SHA256SUMS.txt").open("x") as stream:
        stream.write("".join(f"{value}  {name}\n" for name, value in manifest["output"].items()))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output_p60")
    parser.add_argument("--input-dir", type=Path, default=ROOT / "input")
    parser.add_argument("--allow-signed-image", action="store_true")
    args = parser.parse_args()
    if not args.allow_signed_image:
        parser.error("--allow-signed-image required; boot signatures are not repaired")
    print(json.dumps(build(args.output, args.input_dir), indent=2))
