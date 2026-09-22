#!/usr/bin/env python3
"""Build an AArch64 UNISOC post-verification runtime patch image.

This keeps the certificate-covered payload byte-identical, relocates it by
0x10 bytes, and inserts a tiny runtime patcher after the signed payload.  It is
intended for offline image preparation only; it never accesses a device.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


BTHD_HEADER_SIZE = 0x200
SIGNED_HEADER_SIZE = 0x60
SHELLCODE_SIZE = 0x70
PATCH_AREA_SIZE = 0x80
ADD_LENGTH = 0x100
BTHD_IMAGE_SIZE_OFFSET = 0x30
BTHD_MAGIC = b"DHTB"


class ImageError(RuntimeError):
    pass


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def u64(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def p32(value: int) -> bytes:
    return struct.pack("<I", value)


def parse_patch_word(spec: str) -> tuple[int, int, int]:
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "patch must be ADDRESS:EXPECTED_WORD:NEW_WORD"
        )
    try:
        values = tuple(int(part, 0) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid patch value: {spec}") from exc
    if any(value < 0 or value > 0xFFFFFFFF for value in values):
        raise argparse.ArgumentTypeError(f"patch value exceeds 32 bits: {spec}")
    if values[0] & 3:
        raise argparse.ArgumentTypeError(f"patch address is not aligned: {spec}")
    return values  # type: ignore[return-value]


def run_checked(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise ImageError(f"tool failed: {' '.join(command)}\n{detail}") from exc


def build_shellcode(payload_size: int) -> bytes:
    tools = {
        name: shutil.which(name)
        for name in (
            "aarch64-linux-gnu-as",
            "aarch64-linux-gnu-ld",
            "aarch64-linux-gnu-objcopy",
        )
    }
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise ImageError("missing AArch64 binutils: " + ", ".join(missing))

    if payload_size % 8:
        raise ImageError("payload size must be divisible by 8")

    copy_words = payload_size // 8
    copy_high = copy_words & 0xFFFF0000
    copy_low = copy_words & 0xFFFF
    shell_address = payload_size + 0x10
    patch_address = payload_size + 0x80

    assembly = f"""
.section .text
.globl _start
_start:
    adrp x9, label_init
    add x10, x9, #0x10
    mov x11, #{copy_high}
    movk x11, #{copy_low}
    mov x12, #0
copy_loop:
    subs x13, x12, x11
    b.cs copy_done
    ldr x13, [x10, x12, lsl #3]
    str x13, [x9, x12, lsl #3]
    ic ialluis
    isb
    add x12, x12, #1
    b copy_loop
copy_done:
    adrp x9, label_patch
    add x9, x9, #{patch_address & 0xFFF}
patch_next:
    ldr w10, [x9], #4
    cbz x10, patch_done
    ldr w11, [x9], #4
    mov x12, #0
patch_loop:
    subs x13, x12, x11
    b.cs patch_next
    ldr w13, [x9], #4
    str w13, [x10], #4
    ic ialluis
    isb
    add x12, x12, #1
    b patch_loop
patch_done:
    b label_init
"""
    linker = f"""
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
SECTIONS
{{
    . = 0x{shell_address:x};
    .text : {{ *(.text*) }}
    PROVIDE(label_init = 0);
    PROVIDE(label_patch = 0x{patch_address:x});
}}
"""

    with tempfile.TemporaryDirectory(prefix="sprd-postverify-") as temp_dir:
        temp = Path(temp_dir)
        source = temp / "patch.S"
        script = temp / "patch.lds"
        obj = temp / "patch.o"
        elf = temp / "patch.elf"
        binary = temp / "patch.bin"
        source.write_text(assembly, encoding="ascii")
        script.write_text(linker, encoding="ascii")
        run_checked([tools["aarch64-linux-gnu-as"], "-o", str(obj), str(source)])
        run_checked(
            [
                tools["aarch64-linux-gnu-ld"],
                "-o",
                str(elf),
                "-T",
                str(script),
                str(obj),
            ]
        )
        run_checked(
            [
                tools["aarch64-linux-gnu-objcopy"],
                "-O",
                "binary",
                str(elf),
                str(binary),
            ]
        )
        result = binary.read_bytes()

    if len(result) != SHELLCODE_SIZE:
        raise ImageError(
            f"unexpected runtime patcher size: {len(result):#x}, expected {SHELLCODE_SIZE:#x}"
        )
    return result


def parse_layout(image: bytes, *, allow_relocated: bool = False) -> dict[str, int]:
    if len(image) < BTHD_HEADER_SIZE + SIGNED_HEADER_SIZE:
        raise ImageError("input is too small")
    if image[:4] != BTHD_MAGIC:
        raise ImageError("input has no DHTB/BTHD header")

    payload_size = u32(image, BTHD_IMAGE_SIZE_OFFSET)
    footer_offset = BTHD_HEADER_SIZE + payload_size
    if footer_offset + SIGNED_HEADER_SIZE > len(image):
        raise ImageError("signed-image footer is outside the input")

    footer = image[footer_offset : footer_offset + SIGNED_HEADER_SIZE]
    signed_payload_size = u64(footer, 0x10)
    signed_payload_offset = u64(footer, 0x18)
    cert_size = u64(footer, 0x20)
    cert_offset = u64(footer, 0x28)

    if signed_payload_size > payload_size:
        raise ImageError("signed payload size exceeds the BTHD payload size")
    accepted_offsets = {BTHD_HEADER_SIZE}
    if allow_relocated:
        accepted_offsets.add(BTHD_HEADER_SIZE + 0x10)
    if signed_payload_offset not in accepted_offsets:
        raise ImageError(
            f"unsupported signed payload offset: {signed_payload_offset:#x}"
        )
    if cert_size == 0 or cert_offset + cert_size > len(image):
        raise ImageError("certificate is missing or outside the input")

    return {
        "payload_size": payload_size,
        "signed_payload_size": signed_payload_size,
        "signed_payload_offset": signed_payload_offset,
        "footer_offset": footer_offset,
        "cert_size": cert_size,
        "cert_offset": cert_offset,
    }


def build_patch_data(
    image: bytes,
    payload_size: int,
    load_address: int,
    patches: list[tuple[int, int, int]],
) -> tuple[bytes, list[str]]:
    result = bytearray()
    summaries: list[str] = []
    seen: set[int] = set()

    for address, expected, replacement in sorted(patches):
        if address in seen:
            raise ImageError(f"duplicate patch address: {address:#x}")
        seen.add(address)
        relative = address - load_address
        if relative < 0 or relative + 4 > payload_size:
            raise ImageError(f"patch address is outside the payload: {address:#x}")
        actual = u32(image, BTHD_HEADER_SIZE + relative)
        if actual != expected:
            raise ImageError(
                f"word mismatch at {address:#x}: found {actual:#010x}, "
                f"expected {expected:#010x}"
            )
        result += p32(address)
        result += p32(1)
        result += p32(replacement)
        summaries.append(f"{address:#010x}: {expected:#010x} -> {replacement:#010x}")

    result += p32(0)
    if len(result) > PATCH_AREA_SIZE:
        raise ImageError(
            f"patch table needs {len(result)} bytes, only {PATCH_AREA_SIZE} are available"
        )
    return bytes(result).ljust(PATCH_AREA_SIZE, b"\0"), summaries


def make_image(
    original: bytes,
    load_address: int,
    patches: list[tuple[int, int, int]],
    preserve_size: bool,
) -> tuple[bytes, list[str]]:
    layout = parse_layout(original)
    payload_size = layout["payload_size"]
    old_footer_offset = layout["footer_offset"]
    shellcode = build_shellcode(payload_size)
    patch_data, summaries = build_patch_data(
        original, payload_size, load_address, patches
    )

    branch_delta = payload_size + 0x10
    if branch_delta % 4 or branch_delta // 4 >= (1 << 25):
        raise ImageError("runtime patcher is outside the AArch64 branch range")
    jump = p32(0x14000000 | (branch_delta // 4)) + b"\0" * 12

    header = bytearray(original[:BTHD_HEADER_SIZE])
    struct.pack_into(
        "<I", header, BTHD_IMAGE_SIZE_OFFSET, payload_size + ADD_LENGTH
    )
    payload = original[BTHD_HEADER_SIZE:old_footer_offset]
    footer = bytearray(
        original[old_footer_offset : old_footer_offset + SIGNED_HEADER_SIZE]
    )
    struct.pack_into("<Q", footer, 0x18, BTHD_HEADER_SIZE + 0x10)
    for size_offset, address_offset in (
        (0x20, 0x28),
        (0x30, 0x38),
        (0x40, 0x48),
        (0x50, 0x58),
    ):
        if u64(footer, size_offset):
            struct.pack_into(
                "<Q", footer, address_offset, u64(footer, address_offset) + ADD_LENGTH
            )

    expanded = (
        bytes(header)
        + jump
        + payload
        + shellcode
        + patch_data
        + bytes(footer)
        + original[old_footer_offset + SIGNED_HEADER_SIZE :]
    )

    if preserve_size:
        discarded = expanded[len(original) :]
        if len(discarded) != ADD_LENGTH or set(discarded) != {0xFF}:
            raise ImageError(
                "cannot preserve partition size: inserted bytes would discard non-0xff data"
            )
        output = expanded[: len(original)]
    else:
        output = expanded

    new_layout = parse_layout(output, allow_relocated=True)
    signed_size = layout["signed_payload_size"]
    new_payload_offset = new_layout["signed_payload_offset"]
    if (
        output[new_payload_offset : new_payload_offset + signed_size]
        != payload[:signed_size]
    ):
        raise ImageError("internal validation failed: signed payload changed")
    old_cert = original[
        layout["cert_offset"] : layout["cert_offset"] + layout["cert_size"]
    ]
    new_cert = output[
        new_layout["cert_offset"] : new_layout["cert_offset"] + new_layout["cert_size"]
    ]
    if old_cert != new_cert:
        raise ImageError("internal validation failed: certificate changed")

    return output, summaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="original full U-Boot partition image")
    parser.add_argument("output", type=Path, help="new image; input is never overwritten")
    parser.add_argument(
        "--load-address", type=lambda value: int(value, 0), required=True
    )
    parser.add_argument(
        "--patch-word",
        type=parse_patch_word,
        action="append",
        required=True,
        metavar="ADDRESS:EXPECTED:NEW",
    )
    parser.add_argument(
        "--allow-grow",
        action="store_true",
        help="allow output to grow by 0x100 instead of retaining partition size",
    )
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        parser.error("output must not overwrite input")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

    original = args.input.read_bytes()
    try:
        output, summaries = make_image(
            original,
            args.load_address,
            args.patch_word,
            preserve_size=not args.allow_grow,
        )
    except ImageError as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_bytes(output)
    temporary.replace(args.output)

    old_layout = parse_layout(original)
    new_layout = parse_layout(output, allow_relocated=True)
    print(f"input sha256:  {hashlib.sha256(original).hexdigest()}")
    print(f"output sha256: {hashlib.sha256(output).hexdigest()}")
    print(f"output size:   {len(output):#x}")
    print(
        "signed payload: "
        f"{old_layout['signed_payload_size']:#x} bytes, relocated 0x200 -> 0x210, unchanged"
    )
    print(
        f"footer:         {old_layout['footer_offset']:#x} -> "
        f"{new_layout['footer_offset']:#x}"
    )
    print("runtime patches:")
    for summary in summaries:
        print(f"  {summary}")
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
