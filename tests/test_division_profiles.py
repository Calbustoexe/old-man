import pathlib
import tempfile
import unittest

from data import db_conn
from data import division_profiles


class InitProfilesTableCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_init_profiles_table_creates_expected_tables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            original_fallback_path = db_conn._LOCAL_FALLBACK_PATH
            db_conn._LOCAL_FALLBACK_PATH = pathlib.Path(tmp_dir) / "test.db"
            try:
                await division_profiles.init_profiles_table()
                async with db_conn.connect(None) as db:
                    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = {row[0] for row in await cursor.fetchall()}
                self.assertTrue(
                    {"division_profiles", "division_config_sessions", "division_grade_history"}.issubset(tables)
                )
            finally:
                db_conn._LOCAL_FALLBACK_PATH = original_fallback_path


if __name__ == "__main__":
    unittest.main()