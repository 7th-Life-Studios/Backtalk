
import os
import struct
from pathlib import Path


def parse_chunks(blob: bytes, start: int = 0) -> list[tuple[bytes, bytes]]:
    chunks = []
    position = start
    while position < len(blob):
        if position + 8 > len(blob):
            raise ValueError("Truncated WAV chunk header")
        tag, size = struct.unpack_from("<4sI", blob, position)
        end = position + 8 + size
        if end + size % 2 > len(blob):
            raise ValueError("Truncated WAV chunk or padding")
        chunks.append((tag, blob[position + 8:end]))
        position = end + size % 2
    return chunks


def pack_chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def read_wav(blob: bytes) -> list[tuple[bytes, bytes]]:
    if len(blob) < 12 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError("Expected a RIFF/WAVE file")
    if struct.unpack_from("<I", blob, 4)[0] != len(blob) - 8:
        raise ValueError("WAV size does not match its header")
    chunks = parse_chunks(blob, 12)
    core = [(tag, payload) for tag, payload in chunks if tag in (b"fmt ", b"data")]
    if [tag for tag, _ in core] != [b"fmt ", b"data"]:
        raise ValueError("Expected one fmt chunk followed by one data chunk")
    if len(core[0][1]) != 16 or struct.unpack_from("<H", core[0][1])[0] != 1:
        raise ValueError("This reference supports basic PCM WAV exports only")
    for tag, payload in chunks:
        if tag in (b"fmt ", b"data"):
            continue
        if tag == b"LIST" and payload[:4] == b"INFO":
            parse_chunks(payload, 4)
            continue
        raise ValueError(f"Unsupported chunk {tag!r}; file left unchanged")
    return core


def stamp_in_place(path, title, version, year):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    try:
        original = path.read_bytes()
        chunks = parse_chunks(original, 12)
        core = []
        data_payload = None
        for tag, payload in chunks:
            if tag == b"LIST" and payload[:4] == b"INFO":
                continue
            core.append((tag, payload))
            if tag == b"data":
                data_payload = payload
        fields = [
            (b"INAM", title),
            (b"IART", "7th Life Studios"),
            (b"IPRD", f"Backtalk {version}"),
            (b"ICRD", str(year)),
            (b"ISFT", f"Backtalk {version}"),
            (b"ICMT", "Developer: 7th Life Studios\r\nLocation: Los Angeles, CA"),
        ]
        if not title.strip() or not version.strip() or not 1000 <= int(year) <= 9999:
            raise ValueError("Provide a title, app version, and four-digit year")
        tags = []
        for tag, value in fields:
            if "\0" in value:
                raise ValueError("Metadata cannot contain null characters")
            tags.append((tag, value.encode("ascii", "replace") + b"\0"))
        info = b"INFO" + b"".join(pack_chunk(tag, value) for tag, value in tags)
        body = b"WAVE" + b"".join(pack_chunk(tag, value) for tag, value in core)
        body += pack_chunk(b"LIST", info)
        output = b"RIFF" + struct.pack("<I", len(body)) + body
        saved_core = read_wav(output)
        if [payload for tag, payload in saved_core if tag == b"data"] != [data_payload]:
            return False
        with temp.open("wb") as stream:
            stream.write(output)
        os.replace(temp, path)
        return True
    except Exception:
        try:
            if temp.exists():
                temp.unlink()
        except Exception:
            pass
        return False
