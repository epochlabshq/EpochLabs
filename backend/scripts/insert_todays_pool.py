"""
Insert candidate ideas for Today's pool into the GoForge registry database.
Tokens:
1. Spore Keeper (SPORE)
2. Lantern Frog (LFROG)
3. Clock Tower (TOWER)
4. Paper Crane (CRANE)
5. Moss Oracle (MOSSY)
6. Ember Fox (EMBFOX)
7. Tide Turtle (TIDEY)
8. Night Market Cat (NMCAT)
"""
import os
import shutil
import hashlib
from datetime import datetime, timezone, date
import asyncio

from sqlalchemy import text
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.services import gf_rounds, gf_validation

IDEAS_DATA = [
    {
        "slot": 1,
        "name": "Spore Keeper",
        "ticker": "SPORE",
        "lore": (
            "A tiny mushroom who remembers every holder by name and refuses to forget a single one of them, "
            "even when the market forgets the mushroom itself. Each night it counts its spores and whispers the names "
            "back into the soil. In the cold months the whole forest floor glows faintly, and travellers say it is only "
            "the mushroom keeping its promise to remember."
        ),
        "creator": {
            "x_user_id": "1489230182901239801",
            "x_handle": "waleswoosh",
            "x_followers": 176000,
            "x_verified": True,
            "wallet": "0x71a2e3895e047add645b2901ee0953e4b228e62",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Spore Keeper.png",
    },
    {
        "slot": 2,
        "name": "Lantern Frog",
        "ticker": "LFROG",
        "lore": (
            "The lantern frog sings the tide back every evening so that the fishing boats can find the harbour lights again "
            "after the long grey storms of autumn. Nobody has ever seen it sleep, and the harbour has never been dark since. "
            "When the lantern flickers the frogs of the whole coast answer in a low chorus, and the boats turn toward shore without a word."
        ),
        "creator": {
            "x_user_id": "1489230182901239802",
            "x_handle": "SOLBigBrain",
            "x_followers": 308000,
            "x_verified": True,
            "wallet": "0x82b3fcecc916f7f901c901ee0953e4b228e62573",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Lantern Frog.png",
    },
    {
        "slot": 3,
        "name": "Clock Tower",
        "ticker": "TOWER",
        "lore": (
            "Nobody remembers who built the clock tower, but every hour it rings one coin more than the hour before, "
            "and the town has stopped asking why. At midnight the bells ring twelve and the whole square holds its breath. "
            "Children in the square leave a coin on the lowest step each evening, and by morning it is always gone, "
            "and the bells are always a little warmer."
        ),
        "creator": {
            "x_user_id": "1489230182901239803",
            "x_handle": "DegenerateNews",
            "x_followers": 420000,
            "x_verified": True,
            "wallet": "0x93c4a249d36ea31ee0953e4b228e62573aeb9dd3",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Clock Tower.png",
    },
    {
        "slot": 4,
        "name": "Paper Crane",
        "ticker": "CRANE",
        "lore": (
            "A paper crane folded from the first receipt ever printed, still flying, still carrying the same small promise "
            "across every border it meets. It lands on one window each dawn and leaves a single fold of luck behind. "
            "Those who find the folded luck keep it in a pocket, and nobody has ever managed to unfold it twice without "
            "it flying away again."
        ),
        "creator": {
            "x_user_id": "1489230182901239804",
            "x_handle": "0xSunNFT",
            "x_followers": 211000,
            "x_verified": True,
            "wallet": "0xa4d5b228e62573aeb9dd3f554b61ee0953e4b228",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Paper Crane.png",
    },
    {
        "slot": 5,
        "name": "Moss Oracle",
        "ticker": "MOSSY",
        "lore": (
            "A boulder so old that moss has learned to speak on its behalf. Travellers ask it about the road ahead "
            "and the moss answers slowly, one patient sentence per season, and it has never once been wrong about the weather. "
            "The old folk say the best question is the shortest one, because the moss only has the patience to answer questions "
            "that fit in a breath."
        ),
        "creator": {
            "x_user_id": "1489230182901239805",
            "x_handle": "SqyH100",
            "x_followers": 50000,
            "x_verified": True,
            "wallet": "0xb5d5c228e62573aeb9dd3f554b61ee0953e4b229",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Moss Oracle.png",
    },
    {
        "slot": 6,
        "name": "Ember Fox",
        "ticker": "EMBFOX",
        "lore": (
            "The ember fox walks through burnt forests and wakes the seeds that fire could not kill. Wherever it sleeps, "
            "a ring of new green grows by morning, and the villagers leave it warm bread at the edge of the ash. "
            "Foresters now plant their first sapling where the fox slept, and swear that the ground there stays warm "
            "through the whole of winter."
        ),
        "creator": {
            "x_user_id": "1489230182901239806",
            "x_handle": "oSKNYo_dev",
            "x_followers": 65000,
            "x_verified": True,
            "wallet": "0xc6d5c228e62573aeb9dd3f554b61ee0953e4b230",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Ember Fox.png",
    },
    {
        "slot": 7,
        "name": "Tide Turtle",
        "ticker": "TIDEY",
        "lore": (
            "A turtle the size of an island who carries a small village on its shell and swims the same slow circle every year, "
            "so the villagers always know the season by which coast they can see from their windows. Visitors who stay for a season "
            "learn to sleep to the rhythm of its swimming, and leave unable to sleep anywhere that stands still."
        ),
        "creator": {
            "x_user_id": "1489230182901239807",
            "x_handle": "SatosheeshETH",
            "x_followers": 85000,
            "x_verified": True,
            "wallet": "0xd7d5c228e62573aeb9dd3f554b61ee0953e4b231",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Tide Turtle.png",
    },
    {
        "slot": 8,
        "name": "Night Market Cat",
        "ticker": "NMCAT",
        "lore": (
            "A black cat who runs the stall at the night market that only appears when it rains. It sells small jars of lucky silence, "
            "and the price is always whatever you can honestly afford to give away. Regulars bring jars of their own and swap them at "
            "the end of the night, and the cat keeps a quiet ledger of every fair trade."
        ),
        "creator": {
            "x_user_id": "1489230182901239808",
            "x_handle": "2147_Million",
            "x_followers": 95000,
            "x_verified": True,
            "wallet": "0xe8d5c228e62573aeb9dd3f554b61ee0953e4b232",
        },
        "logo_path": r"C:\Users\bimo\Downloads\Logo Night Market Cat.png",
    },
]

async def run():
    now = datetime.now(timezone.utc)
    sch = gf_rounds.Schedule.from_settings(settings)
    round_date = gf_rounds.round_date_at(now, sch)
    print(f"[*] Targeting round_date: {round_date}")

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_img_dir = os.path.join(backend_dir, "storage", "thumbnails", "goforge")
    os.makedirs(target_img_dir, exist_ok=True)

    frontend_img_dir = os.path.abspath(os.path.join(backend_dir, "..", "frontend", "public", "api", "goforge", "images"))
    os.makedirs(frontend_img_dir, exist_ok=True)

    async with AsyncSessionLocal() as db:
        # 1. Ensure gf_rounds row for today
        await db.execute(text("""
            INSERT INTO gf_rounds (round_date, status, n_ideas)
            VALUES (:d, 'submit', :n)
            ON CONFLICT (round_date) DO UPDATE SET n_ideas = GREATEST(gf_rounds.n_ideas, :n)
        """), {"d": round_date, "n": len(IDEAS_DATA)})

        for item in IDEAS_DATA:
            logo_src = item["logo_path"]
            if not os.path.exists(logo_src):
                print(f"[!] Logo not found at: {logo_src}")
                continue

            img_bytes = open(logo_src, "rb").read()
            img_sha256 = hashlib.sha256(img_bytes).hexdigest()
            ext = "png"
            filename = f"{img_sha256}.{ext}"

            # Copy to storage directories
            shutil.copy2(logo_src, os.path.join(target_img_dir, filename))
            shutil.copy2(logo_src, os.path.join(frontend_img_dir, filename))
            print(f"[+] Saved logo {item['ticker']} -> {filename}")

            # 2. Upsert Creator
            c = item["creator"]
            await db.execute(text("""
                INSERT INTO gf_creators (x_user_id, x_handle, x_verified, x_created_at, x_followers, wallet, updated_at)
                VALUES (:x_user_id, :x_handle, :x_verified, now() - interval '1 year', :x_followers, :wallet, now())
                ON CONFLICT (x_user_id) DO UPDATE SET
                    x_handle = EXCLUDED.x_handle,
                    x_followers = EXCLUDED.x_followers,
                    wallet = EXCLUDED.wallet,
                    updated_at = now()
            """), c)

            # 3. Generate Embedding
            embedding = gf_validation.hash_embedding(item["lore"])

            # 4. Upsert Idea
            idea_id = f"{round_date:%Y%m%d}-{item['ticker'].lower()}"
            image_url = f"/api/goforge/images/{filename}"
            fee_tx = f"seed:{round_date}:{item['ticker'].lower()}"

            await db.execute(text("""
                INSERT INTO gf_ideas (
                    idea_id, round_date, x_user_id, wallet, name, ticker, lore,
                    image_url, image_sha256, fee_tx, status, submitted_at, reviewed_at,
                    reviewed_by, lore_embedding
                ) VALUES (
                    :idea_id, :round_date, :x_user_id, :wallet, :name, :ticker, :lore,
                    :image_url, :image_sha256, :fee_tx, 'approved', now(), now(),
                    'team', CAST(:lore_embedding AS DOUBLE PRECISION[])
                )
                ON CONFLICT (idea_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    ticker = EXCLUDED.ticker,
                    lore = EXCLUDED.lore,
                    image_url = EXCLUDED.image_url,
                    image_sha256 = EXCLUDED.image_sha256,
                    status = 'approved',
                    reviewed_at = now(),
                    reviewed_by = 'team',
                    lore_embedding = EXCLUDED.lore_embedding
            """), {
                "idea_id": idea_id,
                "round_date": round_date,
                "x_user_id": c["x_user_id"],
                "wallet": c["wallet"],
                "name": item["name"],
                "ticker": item["ticker"],
                "lore": item["lore"],
                "image_url": image_url,
                "image_sha256": img_sha256,
                "fee_tx": fee_tx,
                "lore_embedding": embedding,
            })
            print(f"[+] Upserted idea: {item['name']} (${item['ticker']}) [ID: {idea_id}]")

        await db.commit()
        print("[OK] Finished successfully!")

if __name__ == "__main__":
    asyncio.run(run())
