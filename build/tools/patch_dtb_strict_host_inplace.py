#!/usr/bin/env python3
"""Force the UDX710 DWC3 wrapper, child, and queued worker into Host mode.

This is the strict successor to ``patch_dtb_force_host_inplace.py``.  The
legacy two-byte patch only hid the wrapper's ``extcon`` property.  Because the
wrapper creates the DWC3 child before it selects the always-on role, the child
could still probe as OTG, initialize its gadget/UDC path, and remain a USB
Device if the later asynchronous Host start did not complete.

This patch performs all changes required for fixed Host operation:

* rename the wrapper's ``extcon`` property to an ignored existing name; and
* add ``dr_mode = "host"`` to the DWC3 child; and
* initialize the wrapper's queued-work mode to Host in the no-extcon branch.

The last change fixes a vendor-driver bug found by device testing.  The
no-extcon probe path sets ``sdwc->dr_mode`` to Host and then queues work, but
the worker reads the zero-initialized ``sdwc->wq_mode`` instead.  Since its
start path treats every non-Host value as Device, the controller was changed
back to Device shortly after logging ``DWC3 is always running as HOST``.

The kernel instruction patch is deliberately tied to the unique instruction
sequence in this device's complete ``ubi0_boot``.  A detached DTB is no longer
accepted because it cannot fix the later worker transition.

The new property consumes 20 bytes in the structure block and the new
``dr_mode`` name consumes 8 bytes in the strings block.  Both are inserted into
the DTB's existing zero padding.  The containing kernel suffix or complete
boot image therefore keeps exactly the same length, and bytes at and after the
UNISOC signature footer remain at their original file offsets.  The signed
payload still changes, so an already verified signing/bypass path is required.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct

import patch_dtb_force_host_inplace as legacy


CHILD_PATH = legacy.USB_WRAPPER_PATH + "/dwc3@29100000"
DR_MODE_NAME = "dr_mode"
DR_MODE_NAME_BYTES = b"dr_mode\0"
HOST_VALUE = b"host\0"
HOST_VALUE_PADDED = HOST_VALUE + b"\0" * (legacy.align4(len(HOST_VALUE)) - len(HOST_VALUE))
PROPERTY_RECORD_SIZE = 12 + len(HOST_VALUE_PADDED)
STRING_GROWTH = len(DR_MODE_NAME_BYTES)
TOTAL_GROWTH = PROPERTY_RECORD_SIZE + STRING_GROWTH

# Normal-boot side of the wrapper's no-extcon branch.  The original STR writes
# USB_DR_MODE_HOST (w0 == 1) only to dr_mode at +172.  STP writes that same
# value to both dr_mode (+172) and wq_mode (+176), using the same four bytes.
KERNEL_MODE_ORIGINAL = bytes.fromhex(
    "621b00d0"  # adrp x2, ...
    "20008052"  # mov  w0, #1 (USB_DR_MODE_HOST)
    "42a00791"  # add  x2, x2, #0x1e8
    "80ae00b9"  # str  w0, [x20, #172]
    "77ffff17"  # b    shared logging/registration path
)
KERNEL_MODE_PATCHED = bytes.fromhex(
    "621b00d0"
    "20008052"
    "42a00791"
    "80821529"  # stp  w0, w0, [x20, #172]
    "77ffff17"
)
KERNEL_STORE_DELTA = 12


class PatchError(RuntimeError):
    pass


def find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = data.find(needle, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + 1


def patch_kernel_work_mode(source: bytes, reverse: bool) -> tuple[bytes, int, bool]:
    """Patch the unique no-extcon Host store; return data, offset, changed."""
    original_offsets = find_all(source, KERNEL_MODE_ORIGINAL)
    patched_offsets = find_all(source, KERNEL_MODE_PATCHED)

    if reverse:
        if len(patched_offsets) == 1 and not original_offsets:
            start = patched_offsets[0]
            result = (
                source[:start]
                + KERNEL_MODE_ORIGINAL
                + source[start + len(KERNEL_MODE_PATCHED):]
            )
            return result, start + KERNEL_STORE_DELTA, True

        # Keep reverse compatibility with the earlier DTB-only strict image.
        if len(original_offsets) == 1 and not patched_offsets:
            return source, original_offsets[0] + KERNEL_STORE_DELTA, False
    elif len(original_offsets) == 1 and not patched_offsets:
        start = original_offsets[0]
        result = (
            source[:start]
            + KERNEL_MODE_PATCHED
            + source[start + len(KERNEL_MODE_ORIGINAL):]
        )
        return result, start + KERNEL_STORE_DELTA, True

    action = "reverse" if reverse else "apply"
    raise PatchError(
        f"cannot {action} queued-work Host patch: found "
        f"{len(original_offsets)} original and {len(patched_offsets)} patched "
        "kernel instruction sequence(s); a complete matching ubi0_boot is required"
    )


def complete_string_offsets(strings: bytes, needle: bytes) -> list[int]:
    matches: list[int] = []
    start = 0
    while True:
        found = strings.find(needle, start)
        if found < 0:
            return matches
        if found == 0 or strings[found - 1] == 0:
            matches.append(found)
        start = found + 1


def matching_target(data: bytes, fdt: dict[str, int], reverse: bool) -> dict | None:
    properties = legacy.parse_properties(data, fdt)
    mapped = legacy.property_map(properties)

    model = mapped.get(("/", "model"), [])
    compatible = mapped.get(("/", "compatible"), [])
    if len(model) != 1 or model[0]["data"] != legacy.EXPECTED_MODEL or len(compatible) != 1:
        return None
    compat_values = tuple(compatible[0]["data"].rstrip(b"\0").split(b"\0"))
    if compat_values != legacy.EXPECTED_COMPATIBLES:
        return None

    wrapper_name = legacy.NEUTRAL_NAME if reverse else legacy.ORIGINAL_NAME
    wrapper = mapped.get((legacy.USB_WRAPPER_PATH, wrapper_name), [])
    if len(wrapper) != 1 or wrapper[0]["data"] != legacy.EXPECTED_EXTCON_VALUE:
        return None

    conflicting_wrapper_name = legacy.ORIGINAL_NAME if reverse else legacy.NEUTRAL_NAME
    if mapped.get((legacy.USB_WRAPPER_PATH, conflicting_wrapper_name)):
        raise PatchError(
            f"wrapper unexpectedly has both {wrapper_name!r} and "
            f"{conflicting_wrapper_name!r}"
        )

    dr_mode = mapped.get((CHILD_PATH, DR_MODE_NAME), [])
    if reverse:
        if len(dr_mode) != 1 or dr_mode[0]["data"] != HOST_VALUE:
            return None
    elif dr_mode:
        raise PatchError(f"target child already has {DR_MODE_NAME!r}")

    child_properties = [prop for prop in properties if prop["path"] == CHILD_PATH]
    if not child_properties:
        return None
    child_insert = max(
        legacy.align4(prop["data_offset"] + prop["length"])
        for prop in child_properties
    )
    if legacy.be32(data, child_insert) != legacy.FDT_END_NODE:
        raise PatchError("target child is not a flat property-only node")

    strings_start = fdt["base"] + fdt["off_strings"]
    strings_end = strings_start + fdt["size_strings"]
    strings = data[strings_start:strings_end]

    desired_wrapper_name = legacy.ORIGINAL_NAME if reverse else legacy.NEUTRAL_NAME
    wrapper_offsets = complete_string_offsets(
        strings, desired_wrapper_name.encode("ascii") + b"\0"
    )
    if len(wrapper_offsets) != 1:
        raise PatchError(
            f"wanted one complete {desired_wrapper_name!r} string, "
            f"found {len(wrapper_offsets)}"
        )

    dr_name_offsets = complete_string_offsets(strings, DR_MODE_NAME_BYTES)
    if reverse:
        expected_nameoff = fdt["size_strings"] - STRING_GROWTH
        if dr_name_offsets != [expected_nameoff]:
            raise PatchError("strict dr_mode string is not the final strings-block entry")
        if dr_mode[0]["nameoff"] != expected_nameoff:
            raise PatchError("strict dr_mode property points at an unexpected string entry")
    elif dr_name_offsets:
        raise PatchError("original DTB already contains a dr_mode string entry")

    return {
        "fdt": fdt,
        "wrapper": wrapper[0],
        "desired_wrapper_nameoff": wrapper_offsets[0],
        "child_insert": child_insert,
        "dr_mode": dr_mode[0] if dr_mode else None,
    }


def update_header(
    fdt_bytes: bytearray,
    *,
    total: int,
    off_strings: int,
    size_strings: int,
    size_struct: int,
) -> None:
    struct.pack_into(">I", fdt_bytes, 4, total)
    struct.pack_into(">I", fdt_bytes, 12, off_strings)
    struct.pack_into(">I", fdt_bytes, 32, size_strings)
    struct.pack_into(">I", fdt_bytes, 36, size_struct)


def patch_forward(source: bytes, target: dict) -> bytes:
    fdt = target["fdt"]
    base = fdt["base"]
    old_total = fdt["total"]
    new_total = old_total + TOTAL_GROWTH
    available = source[base + old_total:base + new_total]
    if len(available) != TOTAL_GROWTH or any(available):
        raise PatchError(
            f"DTB needs {TOTAL_GROWTH} zero padding bytes after totalsize; "
            "the containing image does not provide them"
        )

    working = bytearray(source)
    struct.pack_into(
        ">I",
        working,
        target["wrapper"]["nameoff_field"],
        target["desired_wrapper_nameoff"],
    )

    insertion = target["child_insert"] - base
    strings_end = fdt["off_strings"] + fdt["size_strings"]
    old_fdt = bytes(working[base:base + old_total])
    new_dr_nameoff = fdt["size_strings"]
    prop_record = (
        struct.pack(">III", legacy.FDT_PROP, len(HOST_VALUE), new_dr_nameoff)
        + HOST_VALUE_PADDED
    )
    if len(prop_record) != PROPERTY_RECORD_SIZE:
        raise PatchError("internal property-record size mismatch")

    new_fdt = bytearray(
        old_fdt[:insertion]
        + prop_record
        + old_fdt[insertion:strings_end]
        + DR_MODE_NAME_BYTES
        + old_fdt[strings_end:]
    )
    if len(new_fdt) != new_total:
        raise PatchError("internal DTB growth mismatch")
    update_header(
        new_fdt,
        total=new_total,
        off_strings=fdt["off_strings"] + PROPERTY_RECORD_SIZE,
        size_strings=fdt["size_strings"] + STRING_GROWTH,
        size_struct=fdt["size_struct"] + PROPERTY_RECORD_SIZE,
    )

    return source[:base] + bytes(new_fdt) + source[base + new_total:]


def patch_reverse(source: bytes, target: dict) -> bytes:
    fdt = target["fdt"]
    base = fdt["base"]
    old_total = fdt["total"]
    if old_total <= TOTAL_GROWTH:
        raise PatchError("strict DTB totalsize is too small to reverse")

    working = bytearray(source)
    struct.pack_into(
        ">I",
        working,
        target["wrapper"]["nameoff_field"],
        target["desired_wrapper_nameoff"],
    )
    old_fdt = bytes(working[base:base + old_total])
    dr_mode = target["dr_mode"]
    prop_start = dr_mode["data_offset"] - base - 12
    prop_end = legacy.align4(dr_mode["data_offset"] - base + dr_mode["length"])
    if prop_end - prop_start != PROPERTY_RECORD_SIZE:
        raise PatchError("strict dr_mode property record has an unexpected size")
    if struct.unpack_from(">I", old_fdt, prop_start)[0] != legacy.FDT_PROP:
        raise PatchError("strict dr_mode property token is missing")

    strings_end = fdt["off_strings"] + fdt["size_strings"]
    if old_fdt[strings_end - STRING_GROWTH:strings_end] != DR_MODE_NAME_BYTES:
        raise PatchError("strict dr_mode string suffix is missing")

    new_total = old_total - TOTAL_GROWTH
    new_fdt = bytearray(
        old_fdt[:prop_start]
        + old_fdt[prop_end:strings_end - STRING_GROWTH]
        + old_fdt[strings_end:]
    )
    if len(new_fdt) != new_total:
        raise PatchError("internal DTB shrink mismatch")
    update_header(
        new_fdt,
        total=new_total,
        off_strings=fdt["off_strings"] - PROPERTY_RECORD_SIZE,
        size_strings=fdt["size_strings"] - STRING_GROWTH,
        size_struct=fdt["size_struct"] - PROPERTY_RECORD_SIZE,
    )

    return (
        source[:base]
        + bytes(new_fdt)
        + b"\0" * TOTAL_GROWTH
        + source[base + old_total:]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="restore the exact original wrapper and child DTB layout",
    )
    parser.add_argument(
        "--allow-signed-image",
        action="store_true",
        help="create an output even though the UNISOC RSA certificate will be invalid",
    )
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        raise PatchError("refusing in-place file overwrite; choose a separate output path")
    source = args.input.read_bytes()
    fdts = legacy.candidate_fdts(source)
    targets = [
        target
        for fdt in fdts
        if (target := matching_target(source, fdt, args.reverse))
    ]
    if len(targets) != 1:
        raise PatchError(
            f"wanted exactly one matching UDX710 strict-host target, found {len(targets)} "
            f"among {len(fdts)} structurally valid FDT(s)"
        )
    target = targets[0]
    signature = legacy.find_sprd_signature(source, target["fdt"])
    if signature and not args.allow_signed_image:
        raise PatchError(
            "UNISOC signed payload detected; strict Host changes invalidate its RSA "
            "certificate. Re-run with --allow-signed-image only after arranging a "
            "verified signing or boot-verification bypass path"
        )

    result = patch_reverse(source, target) if args.reverse else patch_forward(source, target)
    result, kernel_store_offset, kernel_changed = patch_kernel_work_mode(
        result, args.reverse
    )
    if len(result) != len(source):
        raise PatchError("output file length changed")

    reverse_check = not args.reverse
    checks = [
        check
        for fdt in legacy.candidate_fdts(result)
        if (check := matching_target(result, fdt, reverse_check))
    ]
    if len(checks) != 1:
        raise PatchError("post-patch strict Host structural verification failed")
    check = checks[0]

    if signature:
        result_signature = legacy.find_sprd_signature(result, check["fdt"])
        if result_signature != signature:
            raise PatchError("signature footer/certificate offsets changed")
        footer = signature["footer"]
        if result[footer:] != source[footer:]:
            raise PatchError("bytes at or after the UNISOC signature footer changed")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    legacy.atomic_write(args.output, result, args.input.stat().st_mode & 0o777)

    differences = [i for i, (before, after) in enumerate(zip(source, result)) if before != after]
    action = "restored" if args.reverse else "strict-host patched"
    fdt_before = target["fdt"]
    fdt_after = check["fdt"]
    print(f"{action}: {args.input} -> {args.output}")
    print(f"file size:        {len(result)} bytes (unchanged)")
    print(f"FDT base:         {fdt_before['base']:#x}")
    print(f"FDT totalsize:    {fdt_before['total']:#x} -> {fdt_after['total']:#x}")
    print(f"structure size:   {fdt_before['size_struct']:#x} -> {fdt_after['size_struct']:#x}")
    print(f"strings size:     {fdt_before['size_strings']:#x} -> {fdt_after['size_strings']:#x}")
    print(f"changed bytes:    {len(differences)} in {min(differences):#x}..{max(differences):#x}")
    print(f"input SHA-256:    {hashlib.sha256(source).hexdigest()}")
    print(f"output SHA-256:   {hashlib.sha256(result).hexdigest()}")
    print(f"wrapper extcon:   {'restored' if args.reverse else 'absent'}")
    print(f"child dr_mode:    {'absent' if args.reverse else 'host'}")
    print(
        "worker wq_mode:   "
        f"{'restored' if args.reverse else 'host'} at {kernel_store_offset:#x}"
        f"{' (legacy DTB-only input; already original)' if args.reverse and not kernel_changed else ''}"
    )
    if signature:
        print(
            "UNISOC signature: "
            f"footer={signature['footer']:#x}, cert_offset={signature['cert_offset']:#x}; "
            "offsets and bytes preserved, payload signature invalidated"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PatchError, legacy.PatchError) as exc:
        raise SystemExit(f"error: {exc}") from exc
