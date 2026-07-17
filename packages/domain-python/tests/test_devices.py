from __future__ import annotations

import pytest
from radcliffe_domain.devices import (
    DeviceEnrollmentConflictError,
    DeviceRegistry,
    credential_digest,
)
from radcliffe_domain.store import EventStore


@pytest.mark.asyncio
async def test_enrollment_is_idempotent_but_rejects_credential_replay(tmp_path) -> None:
    path = tmp_path / "events.sqlite3"
    await EventStore(path).migrate()
    registry = DeviceRegistry(path)
    digest = credential_digest("rdc_dev_" + "a" * 43)

    enrolled, created = await registry.enroll(
        owner_id="owner",
        installation_id="installation-123456",
        display_name="Pixel",
        credential_sha256=digest,
    )
    retry, retried_created = await registry.enroll(
        owner_id="owner",
        installation_id="installation-123456",
        display_name="Renaming cannot mutate the record",
        credential_sha256=digest,
    )

    assert created
    assert not retried_created
    assert retry == enrolled
    assert retry.display_name == "Pixel"
    with pytest.raises(DeviceEnrollmentConflictError, match="another credential"):
        await registry.enroll(
            owner_id="owner",
            installation_id="installation-123456",
            display_name="Pixel",
            credential_sha256=credential_digest("rdc_dev_" + "b" * 43),
        )
    with pytest.raises(DeviceEnrollmentConflictError, match="another installation"):
        await registry.enroll(
            owner_id="owner",
            installation_id="installation-654321",
            display_name="Another Pixel",
            credential_sha256=digest,
        )


@pytest.mark.asyncio
async def test_authentication_restart_revocation_and_content_free_audit(tmp_path) -> None:
    path = tmp_path / "events.sqlite3"
    await EventStore(path).migrate()
    registry = DeviceRegistry(path)
    raw_credential = "rdc_dev_" + "c" * 43
    enrolled, _ = await registry.enroll(
        owner_id="owner",
        installation_id="installation-123456",
        display_name="Pixel",
        credential_sha256=credential_digest(raw_credential),
    )
    await registry.audit(
        owner_id="owner",
        device_id=enrolled.device_id,
        event_type="enrolled",
        reason="new_installation",
    )

    restarted = DeviceRegistry(path)
    authenticated = await restarted.authenticate(credential_digest(raw_credential))
    assert authenticated is not None
    assert authenticated.device_id == enrolled.device_id
    assert authenticated.last_authenticated_at is not None
    assert await restarted.authenticate(credential_digest("rdc_dev_" + "x" * 43)) is None

    revoked = await restarted.revoke(owner_id="owner", device_id=enrolled.device_id)
    assert revoked is not None
    assert revoked.revoked_at is not None
    denied = await restarted.authenticate(credential_digest(raw_credential))
    assert denied is not None
    assert denied.revoked_at is not None

    events = await restarted.audit_events(owner_id="owner")
    assert [(event.event_type, event.reason) for event in events] == [
        ("enrolled", "new_installation")
    ]
    assert raw_credential not in repr(events)
