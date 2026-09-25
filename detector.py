from dataclasses import dataclass, field
from typing import Optional, List, Tuple


@dataclass
class DetectionResult:
    encoding: Optional[str]
    confidence: float
    reason: str
    binary: bool = False
    candidates: List[Tuple[str, bool, float]] = field(default_factory=list)


ALIASES = {
    "UTF-8": "utf-8",
    "UTF-8 BOM": "utf-8-sig",
    "CP932": "cp932",
    "Shift_JIS": "shift_jis",
    "EUC-JP": "euc_jp",
    "ISO-2022-JP": "iso2022_jp",
}


def normalize_encoding_name(name):
    if name not in ALIASES:
        return name
    return ALIASES[name]


def looks_binary(data):
    if not data:
        return False

    sample = data[:65536]

    # NULはかなり強いバイナリ指標。ただしUTF-16も引っかかるので、
    # BOM付きUTF-16は別途「未対応テキスト」とする。
    if sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff"):
        return False

    if b"\x00" in sample:
        return True

    # ISO-2022-JPのESCは許可。
    bad = 0
    for b in sample:
        if b < 0x09:
            bad += 1
        elif 0x0E <= b < 0x20 and b != 0x1B:
            bad += 1

    return bad / max(1, len(sample)) > 0.015


def strict_decode(data, codec):
    try:
        return data.decode(codec, errors="strict")
    except UnicodeDecodeError:
        return None


def japanese_score(text):
    if text is None:
        return float("-inf")

    if not text:
        return 0.0

    jp = 0
    printable = 0
    weird = 0
    score = 0.0

    for ch in text:
        o = ord(ch)

        if ch in "\r\n\t":
            printable += 1
            continue

        if o < 32:
            # ESC残り等
            weird += 2
            continue

        printable += 1

        if (
            0x3040 <= o <= 0x309F or
            0x30A0 <= o <= 0x30FF or
            0x3400 <= o <= 0x4DBF or
            0x4E00 <= o <= 0x9FFF or
            0xFF61 <= o <= 0xFF9F
        ):
            jp += 1
            score += 2.0

        # ありがちな文字化け片を少し減点
        if ch in "�":
            weird += 20

    if printable:
        score += 25.0 * jp / printable

    return score - weird


def candidate_list(data):
    result = []
    for label, codec in [
        ("UTF-8", "utf-8"),
        ("CP932", "cp932"),
        ("Shift_JIS", "shift_jis"),
        ("EUC-JP", "euc_jp"),
        ("ISO-2022-JP", "iso2022_jp"),
    ]:
        text = strict_decode(data, codec)
        result.append((label, text is not None, japanese_score(text)))
    return result


def has_jis_escape(data):
    pats = (
        b"\x1b$B", b"\x1b$@", b"\x1b(B",
        b"\x1b(J", b"\x1b(I"
    )
    return any(p in data for p in pats)


def detect_encoding(data):
    if data.startswith(b"\xef\xbb\xbf"):
        cands = candidate_list(data)
        return DetectionResult("UTF-8 BOM", 1.0, "UTF-8 BOMを検出", False, cands)

    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return DetectionResult(None, 1.0, "UTF-16 BOMを検出（未対応）", False, [])

    if looks_binary(data):
        return DetectionResult(None, 0.0, "バイナリらしい", True, [])

    if not data:
        return DetectionResult("UTF-8", 1.0, "空ファイル", False, [])

    cands = candidate_list(data)

    if has_jis_escape(data):
        t = strict_decode(data, "iso2022_jp")
        if t is not None:
            return DetectionResult(
                "ISO-2022-JP", 0.99,
                "JISエスケープシーケンスを検出",
                False, cands
            )

    # ASCIIだけは元のエンコーディングを一意に決められないが、
    # UTF-8互換としてそのまま扱える。
    if all(b < 0x80 for b in data):
        return DetectionResult(
            "UTF-8", 0.70,
            "ASCIIのみ（UTF-8互換として扱う）",
            False, cands
        )

    utf8 = strict_decode(data, "utf-8")
    if utf8 is not None:
        return DetectionResult(
            "UTF-8", 0.98,
            "UTF-8 strict decode成功",
            False, cands
        )

    valid = [(name, score) for name, ok, score in cands if ok]
    if not valid:
        return DetectionResult(None, 0.0, "対応候補でdecodeできない", False, cands)

    # CP932とShift_JISが両方通る場合、Windows実用上はCP932を優先。
    rescored = []
    for name, score in valid:
        s = score
        if name == "CP932":
            s += 0.20
        rescored.append((name, s))

    rescored.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = rescored[0]
    second_score = rescored[1][1] if len(rescored) > 1 else best_score - 10
    margin = best_score - second_score

    if margin >= 5:
        confidence = 0.90
    elif margin >= 2:
        confidence = 0.78
    elif margin >= 0.5:
        confidence = 0.66
    else:
        confidence = 0.56

    reason = "複数候補を比較して推定"
    if confidence < 0.65:
        reason += "（曖昧）"

    return DetectionResult(best_name, confidence, reason, False, cands)


def decode_with_encoding(data, encoding):
    codec = normalize_encoding_name(encoding)
    return data.decode(codec, errors="strict")
