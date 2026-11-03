import pytest

from app import signatures
from app.signatures import SignatureError

SECRET = "whsec-current"
BODY = b'{"id":"evt_1","type":"payment.succeeded"}'
NOW = 1_800_000_000


def verify(header: str | None, body: bytes = BODY, secrets: tuple[str, ...] = (SECRET,)) -> None:
    signatures.verify(header, body, secrets, tolerance_seconds=300, now=NOW)


def test_valid_signature_passes() -> None:
    verify(signatures.build_header(SECRET, BODY, timestamp=NOW))


def test_tampered_body_is_rejected() -> None:
    header = signatures.build_header(SECRET, BODY, timestamp=NOW)
    with pytest.raises(SignatureError) as exc:
        verify(header, body=BODY.replace(b"evt_1", b"evt_2"))
    assert exc.value.reason == "mismatch"


def test_wrong_secret_is_rejected() -> None:
    header = signatures.build_header("someone-else", BODY, timestamp=NOW)
    with pytest.raises(SignatureError) as exc:
        verify(header)
    assert exc.value.reason == "mismatch"


@pytest.mark.parametrize("age", [301, -301])
def test_timestamps_outside_tolerance_are_rejected(age: int) -> None:
    header = signatures.build_header(SECRET, BODY, timestamp=NOW - age)
    with pytest.raises(SignatureError) as exc:
        verify(header)
    assert exc.value.reason == "stale"


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        (None, "missing"),
        ("", "missing"),
        ("v1=abc", "malformed"),
        (f"t={NOW}", "malformed"),
        ("t=yesterday,v1=abc", "malformed"),
    ],
)
def test_missing_or_malformed_headers_are_rejected(header: str | None, reason: str) -> None:
    with pytest.raises(SignatureError) as exc:
        verify(header)
    assert exc.value.reason == reason


def test_secret_rotation_accepts_old_and_new_secret() -> None:
    old_header = signatures.build_header("whsec-old", BODY, timestamp=NOW)
    new_header = signatures.build_header(SECRET, BODY, timestamp=NOW)
    for header in (old_header, new_header):
        verify(header, secrets=(SECRET, "whsec-old"))


def test_any_of_several_v1_signatures_may_match() -> None:
    good = signatures.compute(SECRET, NOW, BODY)
    verify(f"t={NOW},v1={'0' * 64},v1={good}")
