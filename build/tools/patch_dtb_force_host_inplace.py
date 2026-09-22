#!/usr/bin/env python3
"""Patch the UDX710 USB wrapper to force Host without resizing its DTB.

The patch changes only the 32-bit nameoff field of the wrapper's `extcon`
property.  It reuses an existing, ignored property name from the same DTB, so
the FDT header, structure/data lengths, string table, following signature data,
and complete input-file length remain byte-for-byte in their original places.

This script accepts either the extracted kernel_dtb suffix or the complete
ubi0_boot image.  It deliberately refuses ambiguous or structurally unexpected
inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import struct
import tempfile


FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

EXPECTED_MODEL = b"Spreadtrum UDX710_4h10 Board\0"
EXPECTED_COMPATIBLES = (b"sprd,udx710_4h10", b"sprd,udx710")
USB_WRAPPER_PATH = "/soc/ipa/usb3@29100000"
ORIGINAL_NAME = "extcon"
NEUTRAL_NAME = "snps,host_suspend_capable"
EXPECTED_EXTCON_VALUE = struct.pack(">I", 0xC8)


class PatchError(RuntimeError):
    pass


def be32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def align4(value: int) -> int:
    return (value + 3) & ~3


def c_string(data: bytes | bytearray, start: int, limit: int) -> bytes:
    end = data.find(b"\0", start, limit)
    if end < 0:
        raise PatchError(f"unterminated FDT string at file offset {start:#x}")
    return bytes(data[start:end])


def candidate_fdts(data: bytes) -> list[dict[str, int]]:
    magic = struct.pack(">I", FDT_MAGIC)
    result: list[dict[str, int]] = []
    search_from = 0
    while True:
        base = data.find(magic, search_from)
        if base < 0:
            break
        search_from = base + 1
        if base + 40 > len(data):
            continue
        fields = struct.unpack_from(">10I", data, base)
        (_, total, off_struct, off_strings, off_mem_rsvmap, version,
         last_compatible, boot_cpuid, size_strings, size_struct) = fields
        if not (
            40 <= total <= len(data) - base
            and version >= 16
            and last_compatible <= version
            and off_mem_rsvmap < total
            and off_struct < total
            and off_strings < total
            and off_struct + size_struct <= total
            and off_strings + size_strings <= total
        ):
            continue
        result.append({
            "base": base,
            "total": total,
            "off_struct": off_struct,
            "off_strings": off_strings,
            "size_struct": size_struct,
            "size_strings": size_strings,
            "boot_cpuid": boot_cpuid,
        })
    return result


def parse_properties(data: bytes | bytearray, fdt: dict[str, int]) -> list[dict]:
    base = fdt["base"]
    pos = base + fdt["off_struct"]
    struct_end = pos + fdt["size_struct"]
    strings_start = base + fdt["off_strings"]
    strings_end = strings_start + fdt["size_strings"]
    stack: list[str] = []
    properties: list[dict] = []

    while pos < struct_end:
        token_offset = pos
        token = be32(data, pos)
        pos += 4
        if token == FDT_BEGIN_NODE:
            raw_name = c_string(data, pos, struct_end)
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as exc:
                raise PatchError(f"non-ASCII node name at {pos:#x}") from exc
            pos = align4(pos + len(raw_name) + 1)
            stack.append(name)
        elif token == FDT_END_NODE:
            if not stack:
                raise PatchError(f"unbalanced FDT_END_NODE at {token_offset:#x}")
            stack.pop()
        elif token == FDT_PROP:
            if pos + 8 > struct_end:
                raise PatchError(f"truncated FDT_PROP at {token_offset:#x}")
            length, nameoff = struct.unpack_from(">II", data, pos)
            nameoff_field = pos + 4
            pos += 8
            data_offset = pos
            if pos + length > struct_end or nameoff >= fdt["size_strings"]:
                raise PatchError(f"invalid FDT_PROP at {token_offset:#x}")
            raw_name = c_string(data, strings_start + nameoff, strings_end)
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError as exc:
                raise PatchError(f"non-ASCII property name at {token_offset:#x}") from exc
            path = "/" + "/".join(part for part in stack if part)
            properties.append({
                "path": path,
                "name": name,
                "nameoff": nameoff,
                "nameoff_field": nameoff_field,
                "data": bytes(data[data_offset:data_offset + length]),
                "data_offset": data_offset,
                "length": length,
            })
            pos = align4(pos + length)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            if stack:
                raise PatchError("FDT_END reached with open nodes")
            return properties
        else:
            raise PatchError(f"unknown FDT token {token:#x} at {token_offset:#x}")

    raise PatchError("FDT structure block has no FDT_END token")


def property_map(properties: list[dict]) -> dict[tuple[str, str], list[dict]]:
    result: dict[tuple[str, str], list[dict]] = {}
    for prop in properties:
        result.setdefault((prop["path"], prop["name"]), []).append(prop)
    return result


def matching_target(data: bytes, fdt: dict[str, int], reverse: bool) -> dict | None:
    properties = parse_properties(data, fdt)
    mapped = property_map(properties)

    model = mapped.get(("/", "model"), [])
    compatible = mapped.get(("/", "compatible"), [])
    if len(model) != 1 or model[0]["data"] != EXPECTED_MODEL or len(compatible) != 1:
        return None
    compat_values = tuple(part for part in compatible[0]["data"].rstrip(b"\0").split(b"\0"))
    if compat_values != EXPECTED_COMPATIBLES:
        return None

    current_name = NEUTRAL_NAME if reverse else ORIGINAL_NAME
    target = mapped.get((USB_WRAPPER_PATH, current_name), [])
    if len(target) != 1 or target[0]["data"] != EXPECTED_EXTCON_VALUE:
        return None

    conflicting_name = ORIGINAL_NAME if reverse else NEUTRAL_NAME
    if mapped.get((USB_WRAPPER_PATH, conflicting_name)):
        raise PatchError(
            f"target wrapper already has both {current_name!r} and {conflicting_name!r}"
        )

    desired_name = ORIGINAL_NAME if reverse else NEUTRAL_NAME
    strings_start = fdt["base"] + fdt["off_strings"]
    strings_end = strings_start + fdt["size_strings"]
    needle = desired_name.encode("ascii") + b"\0"
    matches: list[int] = []
    start = strings_start
    while True:
        found = data.find(needle, start, strings_end)
        if found < 0:
            break
        matches.append(found)
        start = found + 1
    # Require the name to begin at a normal string-table entry boundary.
    matches = [m for m in matches if m == strings_start or data[m - 1] == 0]
    if len(matches) != 1:
        raise PatchError(
            f"wanted exactly one complete {desired_name!r} string, found {len(matches)}"
        )

    return {
        "fdt": fdt,
        "property": target[0],
        "desired_name": desired_name,
        "desired_nameoff": matches[0] - strings_start,
    }


def find_sprd_signature(data: bytes, fdt: dict[str, int]) -> dict[str, int] | None:
    """Recognize a UNISOC BTHD payload footer, including in a split suffix.

    `magiskboot split` can place the FDT at file offset zero while retaining the
    footer and certificate that originally followed it.  cert_offset remains an
    absolute offset in the complete BTHD file, so infer the removed prefix from
    it and validate all relationships before reporting a signed image.
    """
    first_possible = align4(fdt["base"] + fdt["total"])
    for footer in range(first_possible, len(data) - 0x60 + 1, 4):
        major = struct.unpack_from("<I", data, footer + 0x08)[0]
        minor = struct.unpack_from("<I", data, footer + 0x0C)[0]
        payload_size, payload_offset, cert_size, cert_offset = struct.unpack_from(
            "<4Q", data, footer + 0x10
        )
        if major != 0 or minor != 0 or payload_offset not in (0, 0x200, 0x210) or cert_size == 0:
            continue
        local_cert = footer + 0x60
        if cert_offset < local_cert or local_cert + cert_size > len(data):
            continue
        removed_prefix = cert_offset - local_cert
        if removed_prefix + footer != 0x200 + payload_size:
            continue
        if not (removed_prefix <= fdt["base"] + removed_prefix < 0x200 + payload_size):
            continue
        return {
            "footer": footer,
            "cert_size": cert_size,
            "cert_offset": cert_offset,
            "payload_size": payload_size,
            "removed_prefix": removed_prefix,
        }
    return None


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--reverse",
        action="store_true",
        help=f"restore {NEUTRAL_NAME!r} to {ORIGINAL_NAME!r}",
    )
    parser.add_argument(
        "--allow-signed-image",
        action="store_true",
        help="create an output even though the UNISOC RSA certificate will be invalid",
    )
    args = parser.parse_args()

    source = args.input.read_bytes()
    if args.input.resolve() == args.output.resolve():
        raise PatchError("refusing in-place file overwrite; choose a separate output path")

    fdts = candidate_fdts(source)
    targets = [target for fdt in fdts if (target := matching_target(source, fdt, args.reverse))]
    if len(targets) != 1:
        raise PatchError(
            f"wanted exactly one matching UDX710_4h10 USB wrapper, found {len(targets)} "
            f"among {len(fdts)} structurally valid FDT(s)"
        )

    target = targets[0]
    signature = find_sprd_signature(source, target["fdt"])
    if signature and not args.allow_signed_image:
        raise PatchError(
            "UNISOC BTHD/RSA signature footer detected; this DTB is inside the signed "
            "payload. Refusing to create an unbootable-by-default image. Re-run with "
            "--allow-signed-image only for offline research or after arranging a verified "
            "signature-bypass/signing path"
        )
    field = target["property"]["nameoff_field"]
    before_field = source[field:field + 4]
    patched = bytearray(source)
    struct.pack_into(">I", patched, field, target["desired_nameoff"])
    patched_bytes = bytes(patched)

    differences = [index for index, (old, new) in enumerate(zip(source, patched_bytes)) if old != new]
    if not differences or any(index < field or index >= field + 4 for index in differences):
        raise PatchError("internal error: changes escaped the one 32-bit nameoff field")
    if len(source) != len(patched_bytes):
        raise PatchError("internal error: output size changed")

    # Parse the complete result again and verify the requested state is now unique.
    reverse_check = not args.reverse
    check_targets = [
        check for fdt in candidate_fdts(patched_bytes)
        if (check := matching_target(patched_bytes, fdt, reverse_check))
    ]
    if len(check_targets) != 1:
        raise PatchError("post-patch structural verification failed")

    atomic_write(args.output, patched_bytes, args.input.stat().st_mode & 0o777)

    action = "restored" if args.reverse else "patched"
    fdt_base = target["fdt"]["base"]
    print(f"{action}: {args.input} -> {args.output}")
    print(f"input size:  {len(source)} bytes")
    print(f"output size: {len(patched_bytes)} bytes (unchanged)")
    print(f"FDT base:    {fdt_base:#x}")
    print(f"nameoff:     file {field:#x}: {before_field.hex()} -> {patched_bytes[field:field + 4].hex()}")
    print(f"changed byte offsets: {', '.join(hex(index) for index in differences)}")
    print(f"input SHA-256:  {hashlib.sha256(source).hexdigest()}")
    print(f"output SHA-256: {hashlib.sha256(patched_bytes).hexdigest()}")
    if signature:
        print(
            "UNISOC signed payload: "
            f"footer={signature['footer']:#x}, payload_size={signature['payload_size']:#x}, "
            f"cert_offset={signature['cert_offset']:#x}, cert_size={signature['cert_size']:#x}"
        )
        print("WARNING: output preserves byte positions but INVALIDATES the UNISOC RSA certificate")
    else:
        print("warning: any external cryptographic signature covering the DTB must still be handled")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, PatchError) as exc:
        raise SystemExit(f"error: {exc}") from exc
