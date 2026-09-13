"""Plan B: recover the vendor's RPC IDL from the protobuf descriptor inside rpc.dll.

rpc.dll embeds the compiled `server.proto` (package `oime`) that defines the pipe protocol:
the header carries an op, the body is the protobuf message for that op. Decoding the
descriptor gives the complete method list and every message field, which is what a client
needs in order to speak the protocol without guessing.

    python dump_proto.py scratch/rpc.dll
"""
from __future__ import annotations

import sys

FIELD_NAMES = {1: "name", 2: "package", 3: "dependency", 4: "message_type", 5: "enum_type",
               6: "service", 7: "extension", 8: "options", 9: "source_code_info",
               10: "public_dependency", 11: "weak_dependency", 12: "syntax"}
FIELD_LABEL = {1: "optional", 2: "required", 3: "repeated"}
FIELD_TYPE = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 6: "fixed64",
              7: "fixed32", 8: "bool", 9: "string", 10: "group", 11: "message", 12: "bytes",
              13: "uint32", 14: "enum", 15: "sfixed32", 16: "sfixed64", 17: "sint32",
              18: "sint64"}


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def parse(buf: bytes, pos: int = 0, limit: int | None = None) -> tuple[dict, int]:
    """Parse a protobuf message into {field_number: [values]}; strings/bytes stay bytes."""
    out: dict[int, list] = {}
    end = len(buf) if limit is None else limit
    while pos < end:
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError(f"field 0 at {pos - 1}")
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            size, pos = read_varint(buf, pos)
            value = buf[pos:pos + size]
            pos += size
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"wire type {wire} at {pos - 1}")
        out.setdefault(field, []).append(value)
    return out, pos


def text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def describe_message(blob: bytes, indent: str = "  ") -> None:
    msg, _ = parse(blob)
    name = text(msg[1][0])
    print(f"{indent}message {name}")
    fields = []
    for f in msg.get(2, []):
        fm, _ = parse(f)
        fname = text(fm[1][0])
        number = fm[3][0]
        ftype = FIELD_TYPE.get(fm[5][0], "?")
        label = FIELD_LABEL.get(fm[4][0], "optional")
        type_name = text(fm[6][0]) if 6 in fm else ftype
        fields.append(f"{type_name} {fname} = {number};" + ("  (repeated)" if label == "repeated" else ""))
        if 10 in fm:  # json_name
            pass
    for line in fields:
        print(f"{indent}  {line}")
    for nested in msg.get(3, []):
        describe_message(nested, indent + "  ")
    for enum in msg.get(4, []):
        em, _ = parse(enum)
        print(f"{indent}  enum {text(em[1][0])}")
        for value in em.get(2, []):
            vm, _ = parse(value)
            print(f"{indent}    {text(vm[1][0])} = {vm[2][0]}")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "scratch/rpc.dll"
    blob = open(path, "rb").read()
    needle = b"\x0a\x0cserver.proto"
    start = blob.find(needle)
    if start < 0:
        print("[fail] server.proto descriptor not found")
        return 1
    # a FileDescriptorProto for a proto3 file ends with the syntax field
    marker = blob.find(b"\x62\x06proto3", start)
    if marker < 0:
        print("[fail] trailing syntax field not found")
        return 1
    size = marker + 8 - start
    try:
        fd, consumed = parse(blob[start:start + size])
    except (ValueError, IndexError) as exc:
        print(f"[fail] descriptor did not parse: {exc}")
        return 1
    if consumed != size:
        print(f"[warn] parsed {consumed} of {size} bytes")
    print(f"[info] FileDescriptorProto at file offset 0x{start:X}, {size} bytes")
    print(f"       name={text(fd[1][0])} package={text(fd[2][0])} syntax={text(fd[12][0]) if 12 in fd else '?'}")
    print()
    for msg in fd.get(4, []):
        describe_message(msg)
        print()
    for svc in fd.get(6, []):
        sm, _ = parse(svc)
        print(f"service {text(sm[1][0])}")
        for i, method in enumerate(sm.get(2, []), 1):
            mm, _ = parse(method)
            print(f"  [{i:2d}] {text(mm[1][0]):<28} {text(mm[2][0]):<28} -> {text(mm[3][0])}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
