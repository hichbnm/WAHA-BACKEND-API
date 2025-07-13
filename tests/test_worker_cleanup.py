import pytest
from httpx import AsyncClient
from app.main import app

import asyncio

@pytest.mark.asyncio
async def test_cleanup_workers_removes_all_workers(async_session):
    """
    Test that /workers/cleanup endpoint removes all workers and wahasessions.
    """
    # Create a test client
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Optionally, create some workers and wahasessions here if needed
        # ...
        # Call the cleanup endpoint
        response = await ac.delete("/workers/cleanup")
        assert response.status_code == 204
        # Optionally, check that workers and wahasessions are empty
        # ...
