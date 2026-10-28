"""What a Windows DLL says it is: its product name and file version, read from the PE file itself."""
import struct

MACHINE_X64 = 0x8664
IMAGE_FILE_DLL = 0x2000
FIXED_SIGNATURE = struct.pack("<I", 0xFEEF04BD)  # VS_FIXEDFILEINFO
FIXED_VERSION = struct.pack("<I", 0x00010000)


def _string(data, key):
    """A StringFileInfo value (UTF-16), or None. Values follow their key, aligned to 4 bytes."""
    needle = key.encode("utf-16-le") + b"\0\0"
    i = data.find(needle)
    if i < 0:
        return None
    i += len(needle)
    i += -i % 4
    end = i
    while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
        end += 2
    value = data[i:end].decode("utf-16-le", "replace").strip()
    return value or None


def info(data):
    """{"product", "version"} of a 64-bit DLL's bytes (either may be None); ValueError if it isn't one."""
    if data[:2] != b"MZ" or len(data) < 0x40:
        raise ValueError("not a Windows DLL")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0" or len(data) < pe + 24:
        raise ValueError("not a Windows DLL")
    machine, characteristics = struct.unpack_from("<H", data, pe + 4)[0], struct.unpack_from("<H", data, pe + 22)[0]
    if not characteristics & IMAGE_FILE_DLL:
        raise ValueError("not a Windows DLL")
    if machine != MACHINE_X64:
        raise ValueError("not a 64-bit DLL")
    version = None
    # The signature, then the structure's version (1.0): the same 4 bytes can show up in code first (NVIDIA's DLLs).
    i = data.find(FIXED_SIGNATURE + FIXED_VERSION)
    if 0 <= i <= len(data) - 16:
        ms, ls = struct.unpack_from("<II", data, i + 8)
        version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    return {"product": _string(data, "ProductName"), "version": version}
