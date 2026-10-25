"""Read back a synthetic trace; an exporter flush alone is never a receipt."""

import time
from datetime import datetime, timedelta


def _check_receipt(response, expected):
    observations = response.get("data", [])
    for row in observations:
        if (
            row.get("public") is not False
            or any(row.get(key) is not None for key in ("input", "output"))
            or any(
                row.get(key) not in (None, "")
                for key in ("status_message", "user_id", "session_id")
            )
        ):
            return "privacy_failed"
        if row.get("trace_id") != expected[0]["trace_id"]:
            return "trace_mismatch"
    if (response.get("meta") or {}).get("cursor"):
        return "pagination_unverified"
    by_id = {row["id"]: row for row in observations}
    expected_ids = {row["span_id"] for row in expected}
    if len(by_id) != len(observations) or set(by_id) - expected_ids:
        return "unexpected_observations"
    if expected_ids - set(by_id):
        return "observations_pending"
    for span in expected:
        row = by_id[span["span_id"]]
        parent = span["parent_span_id"]
        # SDK 4.18 can create a remote parent for a new, explicitly supplied trace ID.
        # Some servers normalize that unrecorded parent to null. Recorded children
        # must always retain their exact parent ID.
        parents = {parent} if parent in expected_ids else {None, parent}
        if (
            row.get("trace_id") != span["trace_id"]
            or row.get("name") != span["name"]
            or row.get("parent_observation_id") not in parents
            or row.get("type", "").upper() != "SPAN"
        ):
            return "observation_mismatch"
        metadata = row.get("metadata") or {}
        expected_metadata = {**span["metadata"], **span.get("sdk_metadata", {})}
        if set(metadata) - set(expected_metadata):
            return "privacy_failed"
        if {k: str(v) for k, v in metadata.items()} != expected_metadata:
            return "metadata_pending"
        if row.get("end_time") is None:
            return "observations_pending"
    return "matched"


def verify_trace_receipt(client, expected, *, attempts=10, delay_seconds=3):
    """Bounded readback. Never returns raw server data, credentials or exception text."""
    if not expected or len({row["trace_id"] for row in expected}) != 1:
        raise ValueError("expected spans must describe one nonempty trace")
    if len({row["span_id"] for row in expected}) != len(expected):
        raise ValueError("expected span IDs must be unique")
    if not 1 <= attempts <= 10 or not 0 <= delay_seconds <= 5:
        raise ValueError("readback budget out of range")
    starts = [datetime.fromisoformat(row["start_time"]) for row in expected]
    reason = "not_attempted"
    for attempt in range(1, attempts + 1):
        try:
            response = client.api.observations.get_many(
                trace_id=expected[0]["trace_id"],
                from_start_time=min(starts) - timedelta(seconds=1),
                to_start_time=max(starts) + timedelta(minutes=1),
                fields="core,basic,io,metadata",
                limit=1000,
                request_options={"max_retries": 0, "timeout_in_seconds": 2},
            )
            reason = _check_receipt(response.model_dump(mode="python"), expected)
        except Exception as exc:
            reason = "readback_unavailable"
            http_status = getattr(exc, "status_code", None)
            error_type = type(exc).__name__
        if reason == "matched" or reason in {
            "privacy_failed",
            "trace_mismatch",
            "unexpected_observations",
            "observation_mismatch",
        }:
            break
        if attempt < attempts:
            time.sleep(delay_seconds)
    return {
        "status": "receipt_verified" if reason == "matched" else "receipt_unverified",
        "server_receipt_verified": reason == "matched",
        "reason": reason,
        "readback_attempts": attempt,
        "trace_id": expected[0]["trace_id"],
        "expected_observations": len(expected),
        **({"readback_error_type": error_type} if reason == "readback_unavailable" else {}),
        **(
            {"readback_http_status": http_status}
            if reason == "readback_unavailable" and http_status
            else {}
        ),
    }
