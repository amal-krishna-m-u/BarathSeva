"""Evidence authenticity engine — the controls that decide whether a report
is trusted enough to dispatch a crew on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.authenticity import assess_evidence
from app.core.enums import AuthenticityOutcome, EvidenceSource
from app.core.hashing import dhash, hamming_distance, sha256_bytes
from app.core.security import (
    consume_capture_token,
    issue_capture_token,
    verify_capture_token,
)
from tests.conftest import INDIRANAGAR, KORAMANGALA, MUMBAI


def codes(assessment) -> set[str]:
    return {signal.code for signal in assessment.signals}


# ------------------------------------------------------------------ Layer 1
class TestCaptureTokens:
    def test_fresh_token_verifies(self, db, citizen):
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        check = verify_capture_token(db, issued.token)
        assert check.valid and check.reason == "ok"

    def test_tampered_signature_rejected(self, db, citizen):
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        body, signature = issued.token.split(".")
        forged = f"{body}.{'0' * len(signature)}"
        assert verify_capture_token(db, forged).reason == "token_bad_signature"

    def test_tampered_payload_rejected(self, db, citizen):
        """Editing the payload must invalidate the signature."""
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        body, signature = issued.token.split(".")
        mutated = ("A" if body[0] != "A" else "B") + body[1:]
        assert verify_capture_token(db, f"{mutated}.{signature}").valid is False

    def test_consumed_token_is_a_replay(self, db, citizen):
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        consume_capture_token(db, issued.token_id)
        db.commit()
        check = verify_capture_token(db, issued.token)
        assert check.is_replay

    def test_expired_token_rejected(self, db, citizen):
        from app.models import CaptureToken, utcnow

        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        record = db.query(CaptureToken).filter_by(token_id=issued.token_id).one()
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert verify_capture_token(db, issued.token).reason == "token_expired"

    def test_missing_token(self, db):
        assert verify_capture_token(db, None).reason == "token_missing"


# ------------------------------------------------------------------ Layer 4
class TestHashing:
    def test_sha256_changes_with_one_byte(self, photo):
        data = photo(*KORAMANGALA, seed=1)
        assert sha256_bytes(data) != sha256_bytes(data + b"\x00")

    def test_dhash_survives_rescale_and_recompress(self, photo):
        import io

        from PIL import Image

        original = photo(*KORAMANGALA, seed=42)
        image = Image.open(io.BytesIO(original)).resize((640, 480))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=55)
        assert hamming_distance(dhash(original), dhash(buffer.getvalue())) <= 2

    def test_dhash_separates_distinct_scenes(self, photo):
        a = dhash(photo(*KORAMANGALA, seed=11))
        b = dhash(photo(*KORAMANGALA, seed=77))
        assert hamming_distance(a, b) > 10

    def test_hamming_rejects_mismatched_lengths(self):
        assert hamming_distance("abcd", "abcdef") is None
        assert hamming_distance(None, "abcd") is None


# ---------------------------------------------------------------- scoring
class TestEvidenceAssessment:
    def _clean(self, db, citizen, photo, token=None):
        if token is None:
            issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
            db.commit()
            token = issued.token
        return assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            channel="web",
            declared_source="camera",
            capture_token=token,
            image_bytes=photo(*KORAMANGALA, seed=5),
            image_mime="image/jpeg",
            gps_accuracy_meters=8.0,
            reporter=citizen,
        )

    def test_clean_capture_auto_accepts(self, db, citizen, photo):
        result = self._clean(db, citizen, photo)
        assert result.outcome is AuthenticityOutcome.AUTO_ACCEPT
        assert result.score >= 0.9
        assert result.capture_token_valid
        assert {"capture_token_valid", "exif_time_consistent", "ward_exact"} <= codes(result)

    def test_camera_claim_without_token_hard_fails(self, db, citizen, photo):
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            declared_source="camera",
            capture_token=None,
            image_bytes=photo(*KORAMANGALA, seed=6),
            image_mime="image/jpeg",
            reporter=citizen,
        )
        assert result.outcome is AuthenticityOutcome.HARD_FAIL
        assert "capture_token_invalid" in codes(result)

    def test_outside_city_hard_fails(self, db, citizen, photo):
        issued = issue_capture_token(db, citizen.id, "dev-1", *MUMBAI)
        db.commit()
        result = assess_evidence(
            db,
            latitude=MUMBAI[0],
            longitude=MUMBAI[1],
            capture_token=issued.token,
            image_bytes=photo(*MUMBAI, seed=7),
            image_mime="image/jpeg",
            reporter=citizen,
        )
        assert result.outcome is AuthenticityOutcome.HARD_FAIL
        assert "outside_service_area" in codes(result)
        assert result.inside_serviced_ward is False

    def test_all_hard_failures_are_recorded_not_overwritten(self, db, citizen, photo):
        """A replayed token AND an out-of-area coordinate are both worth knowing."""
        issued = issue_capture_token(db, citizen.id, "dev-1", *MUMBAI)
        db.commit()
        consume_capture_token(db, issued.token_id)
        db.commit()
        result = assess_evidence(
            db,
            latitude=MUMBAI[0],
            longitude=MUMBAI[1],
            capture_token=issued.token,
            image_bytes=photo(*MUMBAI, seed=8),
            image_mime="image/jpeg",
            reporter=citizen,
        )
        assert len(result.hard_fail_reasons) == 2
        assert {"token_replayed", "outside_service_area"} <= codes(result)

    def test_gallery_upload_is_accepted_but_weighted_lower(self, db, citizen, photo):
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            declared_source="gallery",
            capture_token=None,
            image_bytes=photo(*KORAMANGALA, seed=9),
            image_mime="image/jpeg",
            gps_accuracy_meters=20.0,
            reporter=citizen,
        )
        assert result.outcome is not AuthenticityOutcome.HARD_FAIL
        assert result.source is EvidenceSource.GALLERY
        assert {"capture_unbound", "source_gallery"} <= codes(result)

    def test_stale_and_edited_photo_degrades_score(self, db, citizen, photo):
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            declared_source="gallery",
            capture_token=None,
            image_bytes=photo(
                *KORAMANGALA,
                when=datetime.now(timezone.utc) - timedelta(days=3),
                seed=10,
                software="Adobe Photoshop 25.0",
            ),
            image_mime="image/jpeg",
            gps_accuracy_meters=400.0,
            reporter=citizen,
        )
        assert {"exif_time_mismatch", "editing_software", "gps_accuracy_poor"} <= codes(result)
        assert result.outcome is AuthenticityOutcome.HUMAN_REVIEW

    def test_exif_gps_mismatch_detected(self, db, citizen, photo):
        """Photo geotagged in Indiranagar, reported from Koramangala."""
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            capture_token=issued.token,
            image_bytes=photo(*INDIRANAGAR, seed=12),
            image_mime="image/jpeg",
            gps_accuracy_meters=8.0,
            reporter=citizen,
        )
        assert "exif_gps_mismatch" in codes(result)
        assert result.exif_gps_distance_meters > 1000

    def test_mock_location_forces_review(self, db, citizen, photo):
        """Routing derives from the coordinate, so a spoofed fix never auto-dispatches."""
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            capture_token=issued.token,
            image_bytes=photo(*KORAMANGALA, seed=13),
            image_mime="image/jpeg",
            gps_accuracy_meters=8.0,
            mock_location=True,
            reporter=citizen,
        )
        assert result.force_review
        assert result.outcome is AuthenticityOutcome.HUMAN_REVIEW

    def test_text_only_report_is_allowed_but_penalised(self, db, citizen):
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            capture_token=issued.token,
            image_bytes=None,
            gps_accuracy_meters=8.0,
            reporter=citizen,
        )
        assert "no_photo" in codes(result)
        assert result.outcome is not AuthenticityOutcome.HARD_FAIL

    def test_unreadable_media_hard_fails(self, db, citizen):
        issued = issue_capture_token(db, citizen.id, "dev-1", *KORAMANGALA)
        db.commit()
        result = assess_evidence(
            db,
            latitude=KORAMANGALA[0],
            longitude=KORAMANGALA[1],
            capture_token=issued.token,
            image_bytes=b"this is definitely not an image",
            image_mime="image/jpeg",
            reporter=citizen,
        )
        assert result.outcome is AuthenticityOutcome.HARD_FAIL
        assert "media_unreadable" in codes(result)
