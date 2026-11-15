"""Import EventSphere application data from SQLite into an empty PostgreSQL database."""
import argparse
from pathlib import Path
import sqlite3

from sqlalchemy import Boolean, MetaData, Table, func, select, text

from main import engine, initialize


TABLES = (
    "users", "venues", "events", "event_schedule", "ticket_categories",
    "tickets", "bookings", "cancellations", "hires", "cancelled_events",
    "waitlist", "notifications", "notification_emails", "check_in_times", "messages",
)


def import_data(source, target):
    metadata = MetaData()
    tables = [Table(name, metadata, autoload_with=target) for name in TABLES]
    target.execute(text("LOCK TABLE " + ", ".join(TABLES) + " IN SHARE ROW EXCLUSIVE MODE"))
    totals = {}
    for table in tables:
        if target.scalar(select(func.count()).select_from(table)):
            raise ValueError(f"PostgreSQL table {table.name} already has data. Use an empty target.")

    source.execute("BEGIN")
    for table in tables:
        exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table.name,)
        ).fetchone()
        if not exists:
            continue
        rows = source.execute(f'SELECT * FROM "{table.name}" ORDER BY rowid')
        count = 0
        while batch := rows.fetchmany(500):
            target.execute(table.insert(), [
                {column.name: bool(row[column.name]) if isinstance(column.type, Boolean)
                 and row[column.name] is not None else row[column.name]
                 for column in table.columns if column.name in row.keys()}
                for row in batch
            ])
            count += len(batch)
        if target.scalar(select(func.count()).select_from(table)) != count:
            raise ValueError(f"Row count mismatch for {table.name}")
        totals[table.name] = count
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_file", type=Path)
    args = parser.parse_args()
    if engine.dialect.name != "postgresql":
        parser.error("Set DATABASE_URL to the target PostgreSQL database first.")
    path = args.sqlite_file.resolve()
    if not path.is_file():
        parser.error("SQLite source file does not exist.")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        initialize()
        with engine.begin() as target:
            totals = import_data(source, target)
    for name, count in totals.items():
        print(f"{name}: {count}")
    print("Migration committed. Sign in again on the deployed site.")


if __name__ == "__main__":
    main()
