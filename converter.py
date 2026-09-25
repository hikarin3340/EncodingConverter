from pathlib import Path
import shutil

from detector import decode_with_encoding, normalize_encoding_name


def output_encoding_for_mode(mode, custom_output):
    if mode == "MATLAB":
        # ユーザー環境: MATLAB R2007b / feature('DefaultCharacterSet') = Shift_JIS
        # Windows日本語環境での実用互換性を考え、CP932を採用。
        return "CP932"
    if mode == "Unix C":
        return "UTF-8"
    return custom_output


def collect_all_files(roots, recursive=True, extensions=None):
    result = []
    seen = set()

    for root in roots:
        root = Path(root)

        if root.is_file():
            candidates = [root]
        elif root.is_dir():
            candidates = root.rglob("*") if recursive else root.glob("*")
        else:
            continue

        for p in candidates:
            if not p.is_file():
                continue

            if extensions is not None and p.suffix.lower() not in extensions:
                continue

            try:
                key = str(p.resolve()).lower()
            except Exception:
                key = str(p.absolute()).lower()

            if key in seen:
                continue
            seen.add(key)
            result.append(p)

    return sorted(result, key=lambda p: str(p).lower())


def detect_eol_bytes(data):
    crlf = data.count(b"\r\n")
    tmp = data.replace(b"\r\n", b"")
    lf = tmp.count(b"\n")
    cr = tmp.count(b"\r")

    if crlf > 0 and crlf >= lf and crlf >= cr:
        return "\r\n"
    if lf > 0 and lf >= cr:
        return "\n"
    if cr > 0:
        return "\r"
    return None


def apply_eol(text, eol):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", eol)


def convert_file_to_path(
    src,
    dst,
    input_encoding,
    output_encoding,
    preserve_eol=True,
    backup=False,
):
    src = Path(src)
    dst = Path(dst)

    data = src.read_bytes()
    text = decode_with_encoding(data, input_encoding)

    if preserve_eol:
        eol = detect_eol_bytes(data)
        if eol:
            text = apply_eol(text, eol)

    if backup and src.resolve() == dst.resolve():
        backup_path = src.with_suffix(src.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(str(src), str(backup_path))

    dst.parent.mkdir(parents=True, exist_ok=True)

    codec = normalize_encoding_name(output_encoding)
    encoded = text.encode(codec, errors="strict")
    dst.write_bytes(encoded)


def copy_file_to_path(src, dst):
    src = Path(src)
    dst = Path(dst)
    if src.resolve() == dst.resolve():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
