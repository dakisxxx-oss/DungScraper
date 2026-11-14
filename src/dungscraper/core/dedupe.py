"""Near-duplicate page detection with a 64-bit simhash."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_WORDS = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class Fingerprint:
    simhash: int
    exact: str
    words: int


def _simhash(words: list[str]) -> int:
    if len(words) < 3:
        blob = " ".join(words).encode()
        return int.from_bytes(hashlib.blake2b(blob, digest_size=8).digest(), "big")
    shingles = [" ".join(words[index : index + 3]) for index in range(len(words) - 2)]
    bits = [0] * 64
    for shingle in shingles:
        digest = hashlib.blake2b(shingle.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            bits[bit] += 1 if value & (1 << bit) else -1
    out = 0
    for bit, weight in enumerate(bits):
        if weight > 0:
            out |= 1 << bit
    return out


def fingerprint(text: str) -> Fingerprint:
    words = _WORDS.findall((text or "").lower())
    exact = hashlib.sha256(" ".join(words).encode()).hexdigest()
    return Fingerprint(simhash=_simhash(words), exact=exact, words=len(words))


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def is_near_duplicate(text: str, seen: list[Fingerprint], *, distance: int = 8) -> tuple[bool, Fingerprint]:
    """True when the page is the same text or a small edit of one already seen.

    Distance 8 on this 64-bit hash keeps a rewritten paragraph or a repeated
    template from collapsing unrelated articles, and still catches a copied
    page with a short banner added.
    """
    current = fingerprint(text)
    if current.words == 0:
        return False, current
    for other in seen:
        if current.exact == other.exact:
            return True, current
        if current.words >= 40 and other.words >= 40 and hamming(current.simhash, other.simhash) <= distance:
            return True, current
    return False, current
