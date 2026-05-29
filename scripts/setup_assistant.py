"""
One-time setup script for the RAG training assistant.

Run this after adding GEMINI_API_KEY to your .env.
It will:
  1. Check your API key is configured
  2. Enable the pgvector Postgres extension and create the activity_embeddings table
     (handles the 1536→768 dimension change automatically if needed)
  3. Embed all your existing activities so /assistant/ask can find them
  4. Tell you how to test it

Usage:
    ./.venv/bin/python -m scripts.setup_assistant
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("setup_assistant")

# The embedding dimension for models/text-embedding-004
EMBEDDING_DIM = 768


def _check_keys() -> bool:
    """Return True if the key is set, otherwise print guidance and return False."""
    from app.core.config import settings

    if not settings.gemini_api_key:
        print("\nMissing: GEMINI_API_KEY\n")
        print("How to get a free key (no credit card needed):")
        print("  1. Go to https://aistudio.google.com")
        print("  2. Sign in with your Google account")
        print('  3. Click "Get API key" → "Create API key"')
        print("  4. Copy it and add to your .env file:\n")
        print("     GEMINI_API_KEY=AIza...\n")
        print("Then re-run this script.")
        return False

    log.info("GEMINI_API_KEY is set.")
    return True


async def _fix_embedding_table(conn) -> None:
    """Drop activity_embeddings if it has the wrong vector dimension.

    SQLAlchemy's create_all won't ALTER an existing table, so if the table
    was created with Vector(1536) we need to drop and recreate it.
    Since no embeddings have been saved yet this is safe.
    """
    from sqlalchemy import text

    row = (await conn.execute(text("""
        SELECT a.atttypmod
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        WHERE c.relname = 'activity_embeddings'
          AND a.attname = 'embedding'
          AND a.atttypmod > 0
    """))).fetchone()

    if row is None:
        return  # table doesn't exist yet — create_all will make it

    current_dim = row[0]
    if current_dim != EMBEDDING_DIM:
        log.info(
            "activity_embeddings has dimension %d, need %d — recreating table...",
            current_dim, EMBEDDING_DIM,
        )
        await conn.execute(text("DROP TABLE IF EXISTS activity_embeddings CASCADE"))
        log.info("Old table dropped. create_tables() will recreate it with dimension %d.", EMBEDDING_DIM)


async def main() -> int:
    # Step 1: verify key before doing anything
    if not _check_keys():
        return 1

    # Step 2: fix dimension if needed, then enable pgvector + create tables
    log.info("Setting up database...")
    import app.models  # noqa: F401 — registers all models with Base.metadata
    from app.core.database import create_tables, AsyncSessionFactory, engine
    from sqlalchemy import text

    async with engine.begin() as conn:
        await _fix_embedding_table(conn)

    await create_tables()
    log.info("Database ready.")

    # Step 3: embed all existing activities
    from sqlalchemy import select
    from app.models.user import User
    from app.services.embeddings import embed_activities
    from app.core.config import settings

    async with AsyncSessionFactory() as db:
        user = (await db.execute(select(User))).scalars().first()
        if not user:
            log.error(
                "No user found in the database. "
                "Complete the Strava OAuth flow first: "
                "open http://localhost:8000/auth/strava/login"
            )
            return 2

        log.info("User found: %s", user.username)
        log.info(
            "Embedding activities using model '%s' — calls Gemini once per activity...",
            settings.embedding_model,
        )

        count = await embed_activities(db, user.id, only_missing=True)
        await db.commit()

        total = (await db.execute(
            text("SELECT COUNT(*) FROM activity_embeddings")
        )).scalar()

    if count == 0:
        log.info("All activities already embedded (%d total). Nothing to do.", total)
    else:
        log.info("Embedded %d new activities. Total in database: %d.", count, total)

    # Step 4: tell the user how to test
    print("\n" + "=" * 60)
    print("Setup complete! Here's how to test the assistant:\n")
    print("1. Start the app:")
    print("   uvicorn app.main:app --reload\n")
    print("2. Ask a question (in a separate terminal):")
    print('   curl -s -X POST http://localhost:8000/assistant/ask \\')
    print('     -H "Content-Type: application/json" \\')
    print('     -d \'{"question": "How has my running pace trended recently?"}\' \\')
    print("     | python3 -m json.tool\n")
    print("3. Future syncs embed new activities automatically.")
    print("=" * 60 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
