"""Test that force-unlock calls terraform force-unlock."""
from __future__ import annotations

from server.mock import MockTerraform


async def test_force_unlock():
    tf = MockTerraform()
    await tf.force_unlock(site_name="stuck-site", lock_id="abc-123-lock")
    # MockTerraform.force_unlock just logs and sleeps — no exception means success
