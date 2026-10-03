from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.db.models import Token, TokenStatus
from app.core.config import settings

async def sample_token_holders(mint: str, launched_at: datetime) -> Optional[int]:
    """
    Holder count at launch + 48h, counted onchain (app.services.live_holders). Sampled EXACTLY ONCE, at labeling.
    None when the chain cannot be read: never a made-up number. (This used to call Helius, a Solana API, with
    Robinhood Chain addresses and fell back to a random count, so earlier samples in the table are not real.)
    """
    from datetime import timedelta
    from app.services import desk_chain
    from app.services.live_holders import count_holders

    res = await count_holders(await desk_chain.log_reader(), mint, launched_at,
                              at=launched_at + timedelta(hours=48), transfers_r=await desk_chain.transfers_reader())
    return res[0] if res else None


async def run_label_worker_cycle(db: AsyncSession) -> dict:
    """
    Label Worker Cycle (runs every 15 minutes):
    1. Select tokens with status='pending' where launched_at <= NOW() - 48 hours.
    2. Sample holder count via RPC (sampled once).
    3. Assign label: 'passed' if peak_mc >= 30,000 else 'stalled'.
    4. Update status, labeled_at, holders, holders_sampled_at in database.
    """
    now = datetime.now(timezone.utc)
    cutoff = text("NOW() - INTERVAL '48 hours'")

    query = select(Token).where(
        Token.status == TokenStatus.pending,
        Token.launched_at <= cutoff
    ).limit(100)

    res = await db.execute(query)
    pending_tokens = res.scalars().all()

    labeled_passed = 0
    labeled_stalled = 0

    for token in pending_tokens:
        # 1. Sample holders ONCE at 48h mark
        holders_count = await sample_token_holders(token.mint, token.launched_at)
        token.holders = holders_count
        token.holders_sampled_at = now if holders_count is not None else None
        token.labeled_at = now

        # 2. Assign label based on $30K peak market cap rule
        if float(token.peak_mc) >= 30000.0:
            token.status = TokenStatus.passed
            labeled_passed += 1
        else:
            token.status = TokenStatus.stalled
            labeled_stalled += 1

    await db.commit()
    return {
        "processed": len(pending_tokens),
        "passed": labeled_passed,
        "stalled": labeled_stalled
    }
