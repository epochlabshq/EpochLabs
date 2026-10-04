import ssl
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL_ASYNC
clean_url = DATABASE_URL.split("?")[0]

if "sqlite" in clean_url:
    engine = create_async_engine(
        clean_url,
        echo=False,
        future=True
    )
else:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(
        clean_url, 
        echo=False, 
        future=True,
        pool_size=3,
        max_overflow=2,
        pool_timeout=15,
        pool_pre_ping=False,
        pool_recycle=600,
        connect_args={
            "ssl": ctx,
            "statement_cache_size": 0
        }
    )
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

from sqlalchemy.orm import declarative_base
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
