import redis.asyncio as redis

class RedisLock:
    def __init__(self, redis_url, lock_key, ttl=60):
        self.redis_url = redis_url
        self.lock_key = lock_key
        self.ttl = ttl
        self.redis: redis.Redis | None = None
        self.lock_value: str | None = None

    async def __aenter__(self):
        self.redis = redis.from_url(self.redis_url, decode_responses=True)
        import uuid
        self.lock_value = str(uuid.uuid4())
        # NX with EX for TTL
        acquired = await self.redis.set(self.lock_key, self.lock_value, ex=self.ttl, nx=True)
        if not acquired:
            # Close connections
            await self.redis.close()
            await self.redis.connection_pool.disconnect()
            raise RuntimeError('Could not acquire dispatcher lock')
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self.redis is not None:
                value = await self.redis.get(self.lock_key)
                if value == self.lock_value:
                    await self.redis.delete(self.lock_key)
        finally:
            if self.redis is not None:
                await self.redis.close()
                await self.redis.connection_pool.disconnect()
