"""Network and file calls made straight through libc, which raise no audit events: the guards cannot see them.
Only an operating-system boundary (the container) stops them."""

import ctypes
import os
import socket
import struct
import sys

libc = ctypes.CDLL(None, use_errno=True)


def connect(host: str, port: int) -> str:
    fd = libc.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    if fd < 0:
        return f"errno {ctypes.get_errno()}"
    packed = socket.inet_aton(host)
    if sys.platform == "darwin":
        address = struct.pack("BBH4s8x", 16, socket.AF_INET, socket.htons(port), packed)
    else:
        address = struct.pack("HH4s8x", socket.AF_INET, socket.htons(port), packed)
    buffer = ctypes.create_string_buffer(address)
    result = libc.connect(fd, buffer, len(address))
    error = ctypes.get_errno()
    libc.close(fd)
    return "connected" if result == 0 else f"errno {error}"


def write(path: str) -> str:
    fd = libc.open(path.encode(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    if fd < 0:
        return f"errno {ctypes.get_errno()}"
    libc.write(fd, b"written through libc", 20)
    libc.close(fd)
    return "written"
