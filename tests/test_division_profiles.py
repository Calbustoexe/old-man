import pathlib
import tempfile
import unittest

import aiosqlite

from data import division_profiles


class InitProfilesTableCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_init_profiles_table_creates_expected_tables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "test.db"
            original_db_path = division_profiles.DB_PATH
            division_profiles.DB_PATH = db_path
            try:
                await division_profiles.init_profiles_table()
                async with aiosqlite.connect(db_path) as db:
                    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = {row[0] for row in await cursor.fetchall()}
                self.assertTrue(
                    {"division_profiles", "division_config_sessions", "division_grade_history"}.issubset(tables)
                )
            finally:
                division_profiles.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
