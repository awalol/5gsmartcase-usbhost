#!/usr/bin/env python3
"""Apply/reverse the exact UDX710 strict-host cold-start binary patch.

Only the already-installed strict-host boot is needed, not vendor source or
the original factory image. This does not flash a device or repair signatures.
Assembly and linker inputs are usb_cold_boot_fix.S and usb_cold_boot_fix.ld.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


BOOT_SIZE = 13_713_408
IMAGE_OFFSET = 0xA00
BASE_SHA256 = "813df94b70d006ddacf450c20872223369f6ef2bfa519672c794982cceca5448"
PATCHED_SHA256 = "f844af720d45fc175da255c7a12686e4d6c93ee2e3f080f66ddd324737bf6020"
SIGNATURE_FOOTER = 0x9EAA00


class PatchError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    offset: int
    before: bytes
    after: bytes
    purpose: str

    @property
    def end(self) -> int:
        return self.offset + len(self.before)


REGIONS = (
    Region(
        0x3E3968,
        bytes.fromhex(
            "fd7bbea9211a00d021600491fd030091f35301a9f40302aaf30303aa420080d2"
            "e00314aaae610a94c0010034211a00d0620080d221a00491e00314aaa8610a94"
            "20020035212e00d03f20023999ffff97e00313aaf35341a9fd7bc2a8c0035fd6"
            "212e00d022008052200080522220023990ffff97e00313aaf35341a9fd7bc2a8"
            "c0035fd6a0028092f3ffff17"
        ),
        bytes.fromhex(
            "c00b8092c0035fd6fd7bbfa9fd030091e072683940020035e00316aa611b00b0"
            "21403a9117540294a00100b5802e40f9018440f920003fd6c001f837802e40f9"
            "033440f900a0019121008052020080d260003fd62000805280821529e00316aa"
            "010080d2020080d2e30313aa6d610294fd7bc1a8c0035fd63f070071c1810054"
            "c03240f9808100b512040014"
        ),
        "Disable legacy gadget plugin writes; install early PHY preparation and guarded wait gate",
    ),
    Region(
        0x3E3EE8,
        bytes.fromhex("f303002aa0000034"),
        bytes.fromhex("137c800aa000f836"),
        "Normalize child pm_runtime_get_sync nonnegative success, retain negative errno",
    ),
    Region(
        0x3E4280,
        bytes.fromhex("425f0294"),
        bytes.fromhex("bcfdff97"),
        "Initialize/reset PHY and select Host before DWC3 child population",
    ),
    Region(
        0x3E4A04,
        bytes.fromhex("c0000034"),
        bytes.fromhex("e07eff34"),
        "Bypass first-suspend wait only for no-extcon Host; preserve charging/Device paths",
    ),
    Region(
        0x3E4A8C,
        bytes.fromhex("c0150035"),
        bytes.fromhex("c015f837"),
        "Accept already-active wrapper PM result; branch to error only on negative return",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_regions() -> None:
    previous_end = IMAGE_OFFSET
    for region in REGIONS:
        if not region.before or len(region.before) != len(region.after):
            raise PatchError("invalid equal-length patch definition")
        if region.offset % 4 or len(region.before) % 4:
            raise PatchError("unaligned ARM64 patch definition")
        if region.offset < previous_end or region.end > SIGNATURE_FOOTER:
            raise PatchError("overlapping or out-of-bounds patch definition")
        previous_end = region.end


def patch_bytes(source: bytes, *, reverse: bool = False) -> bytes:
    validate_regions()
    if len(source) != BOOT_SIZE:
        raise PatchError(f"wrong image size: {len(source)}; expected {BOOT_SIZE}")
    expected = PATCHED_SHA256 if reverse else BASE_SHA256
    actual = sha256(source)
    if actual != expected:
        raise PatchError(f"unsupported image SHA-256: {actual}; expected {expected}")
    if source[:4] != b"DHTB" or source[0x200:0x208] != b"ANDROID!":
        raise PatchError("expected complete UNISOC-wrapped Android boot image")
    result = bytearray(source)
    for region in REGIONS:
        old, new = (region.after, region.before) if reverse else (region.before, region.after)
        if source[region.offset:region.end] != old:
            raise PatchError(f"unexpected instructions at {region.offset:#x}")
        result[region.offset:region.end] = new
    result = bytes(result)
    expected_output = BASE_SHA256 if reverse else PATCHED_SHA256
    if len(result) != BOOT_SIZE or sha256(result) != expected_output:
        raise PatchError("output does not match the audited patch definition")
    # Reconstruct from unchanged spans too, rather than trusting region bounds.
    cursor = 0
    for region in REGIONS:
        if result[cursor:region.offset] != source[cursor:region.offset]:
            raise PatchError("unexpected changes outside patch regions")
        cursor = region.end
    if result[cursor:] != source[cursor:]:
        raise PatchError("unexpected changes after patch regions")
    return result


def manifest(source: bytes, result: bytes, *, reverse: bool) -> dict:
    return {
        "format": "udx710-usb-cold-start-v1",
        "status": "RESTORED_KNOWN_BASELINE" if reverse else "UNTESTED_ON_DEVICE",
        "reverse": reverse,
        "input_sha256": sha256(source),
        "output_sha256": sha256(result),
        "size": len(result),
        "changed_bytes": sum(a != b for a, b in zip(source, result)),
        "file_offsets_not_runtime_addresses": True,
        "dtb_and_boot_headers_unchanged": True,
        "signature_footer_offset": hex(SIGNATURE_FOOTER),
        "signature_valid": False,
        "requires_existing_verified_boot_signature_bypass": True,
        "regions": [
            {
                "offset": hex(r.offset),
                "length": len(r.before),
                "before": (r.after if reverse else r.before).hex(),
                "after": (r.before if reverse else r.after).hex(),
                "purpose": r.purpose,
            }
            for r in REGIONS
        ],
    }


def write_patch(source: Path, destination: Path, *, reverse: bool = False) -> dict:
    if source.resolve() == destination.resolve():
        raise PatchError("source and destination must differ; in-place writes are forbidden")
    report_path = destination.with_suffix(destination.suffix + ".manifest.json")
    if destination.exists() or report_path.exists():
        raise PatchError("destination or manifest already exists; refusing to overwrite")
    original = source.read_bytes()
    result = patch_bytes(original, reverse=reverse)
    report = manifest(original, result, reverse=reverse)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(result)
    with report_path.open("x", encoding="ascii") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="the exact current strict-host boot image")
    parser.add_argument("destination", type=Path, help="new complete image; must not exist")
    parser.add_argument("--reverse", action="store_true", help="restore the known strict-host baseline")
    parser.add_argument(
        "--allow-signed-image", action="store_true",
        help="acknowledge that a previously verified signature-bypass bootloader is required",
    )
    args = parser.parse_args()
    if not args.allow_signed_image:
        parser.error("requires --allow-signed-image; original RSA signatures are not repaired")
    try:
        report = write_patch(args.source, args.destination, reverse=args.reverse)
    except (OSError, PatchError) as exc:
        parser.error(str(exc))
    print(json.dumps({k: v for k, v in report.items() if k != "regions"}, indent=2))
    print(f"Wrote {args.destination}; no device was flashed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
