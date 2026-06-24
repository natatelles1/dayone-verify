from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Usa DIRECT_URL (IPv6, porta 5432) enquanto o Supavisor pooler não reconhece o tenant.
# Trocar para DATABASE_URL (pooler 6543) assim que o dashboard Supabase confirmar
# as strings de conexão corretas. Para escala, o pooler é preferível.
engine = create_async_engine(
    settings.direct_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={"ssl": "require", "statement_cache_size": 0},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
