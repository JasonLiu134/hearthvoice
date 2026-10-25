import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from src.services import database


class CallDatabaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {
            "SUPABASE_CALL_LOGGING": "true", "SUPABASE_USER_ID": "u1",
            "POST_CALL_SMS_ENABLED": "false",
            "PATIENT_PHONE_NUMBER": "+15551234567", "CALL_LOG_DIRECTORY": self.directory.name,
        })
        env.start()
        self.addCleanup(env.stop)
        self.upload = patch.object(database, "_upload")
        self.mock_upload = self.upload.start()
        self.addCleanup(self.upload.stop)

    def row(self):
        return json.loads(next(Path(self.directory.name).glob("*.json")).read_text())

    async def test_retry_is_one_row_and_late_snapshot_preserves_terminal_transcript(self):
        first = [{"role": "assistant", "content": "Hello"}]
        full = first + [{"role": "user", "content": "I slept well"}]
        await database.save_call("CAone", "+15551234567", full, "completed")
        identity = self.row()["id"]
        await database.save_call("CAone", "+15551234567", first, "in-progress")
        row = self.row()
        self.assertEqual(row["id"], identity)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(row["transcript"], "Agent: Hello\nPatient: I slept well")
        self.assertEqual(len(list(Path(self.directory.name).glob("*.json"))), 1)

    async def test_failed_upload_retains_replayable_snapshot(self):
        self.mock_upload.side_effect = RuntimeError("private error contents")
        with self.assertLogs(database.log, level="ERROR") as logs:
            await database.save_call("CAone", "+15551234567", [{"role": "user", "content": "Hello"}])
        self.assertNotIn("private error contents", " ".join(logs.output))
        self.assertIn("Hello", self.row()["transcript"])
        self.mock_upload.side_effect = None
        database.replay()
        self.assertEqual(self.mock_upload.call_count, 2)

    async def test_disabled_or_unmapped_caller_is_not_stored(self):
        with patch.dict(os.environ, {"SUPABASE_CALL_LOGGING": "false"}):
            await database.save_call("CAone", "+15551234567", [])
        await database.save_call("CAtwo", "+15557654321", [])
        self.assertFalse(list(Path(self.directory.name).glob("*.json")))
        self.mock_upload.assert_not_called()

    async def test_terminal_callback_summarizes_persisted_transcript(self):
        await database.save_call("CAone", "+15551234567", [{"role": "user", "content": "Slept well"}])
        with patch("src.core.agent.HearthAgent.summarize", new_callable=AsyncMock,
                   return_value={"summary": "Patient slept well."}) as summary, patch(
                       "src.core.agent.HearthAgent.close", new_callable=AsyncMock):
            await database.finish_call("CAone")
            await database.finish_call("CAone")
        summary.assert_awaited_once_with([{
            "role": "user", "content": "Full call transcript:\nPatient: Slept well"}])
        self.assertEqual(self.row()["summary"], "Patient slept well.")
        self.assertEqual(self.row()["status"], "completed")


    async def test_caller_sms_works_without_supabase_logging_or_profile(self):
        from unittest.mock import Mock
        provider = Mock()
        provider.send_sms.return_value = "SMcaller"
        history = [{"role": "user", "content": "I slept well"}]
        with patch.dict(os.environ, {
            "SUPABASE_CALL_LOGGING": "false", "SUPABASE_USER_ID": "",
            "POST_CALL_SMS_ENABLED": "true", "POST_CALL_SMS_DATABASE": self.directory.name + "/sms.sqlite3",
        }), patch.object(database, "_client", side_effect=AssertionError("Unexpected Supabase access")) as db_client, patch(
            "src.core.agent.HearthAgent.summarize", new_callable=AsyncMock,
            return_value={"summary": "Patient reported sleeping well."}
        ), patch("src.core.agent.HearthAgent.close", new_callable=AsyncMock), patch(
            "src.services.call_summary_sms._provider", return_value=("twilio", provider)
        ):
            await database.finish_call("CAunmapped", "+15557654321", history)
        db_client.assert_not_called()
        self.assertEqual(provider.send_sms.call_args.args[0], "+15557654321")
        self.assertEqual(provider.send_sms.call_count, 1)


class SupabaseAdapterTests(unittest.TestCase):
    def test_sdk_upsert_and_cleanup(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_KEY": "secret"}), patch(
            "supabase.create_client", return_value=client
        ):
            database._upload({"id": "call-id", "status": "in-progress"})
        client.table.assert_called_once_with("calls")
        client.table.return_value.upsert.assert_called_once_with({"id": "call-id", "status": "in_progress"}, on_conflict="id")
        client.postgrest.session.close.assert_called_once()

    def test_real_sdk_serializes_upsert_without_network(self):
        import httpx
        from supabase import create_client
        from supabase.lib.client_options import SyncClientOptions
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(201, json=[{"id": "call-id"}])
        http = httpx.Client(transport=httpx.MockTransport(handle))
        client = create_client("https://example.supabase.co", "test-secret",
                               options=SyncClientOptions(httpx_client=http))
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_KEY": "test-secret"}), patch(
            "supabase.create_client", return_value=client
        ):
            database._upload({"id": "call-id"})
        self.assertEqual(requests[0].url.path, "/rest/v1/calls")
        self.assertEqual(requests[0].url.params["on_conflict"], "id")
        self.assertIn("resolution=merge-duplicates", requests[0].headers["prefer"])
        self.assertTrue(http.is_closed)

    def test_twilio_terminal_failures_map_to_database_enum_without_mutating_snapshot(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        with patch.object(database, "_client", return_value=client):
            for status in ["busy", "no-answer", "canceled"]:
                row = {"id": "call-id", "status": status}
                database._upload(row)
                self.assertEqual(row["status"], status)
                self.assertEqual(client.table.return_value.upsert.call_args.args[0]["status"], "failed")

    def test_unscheduled_call_uses_observed_start_for_required_scheduled_for(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        row = {"id": "call-id", "status": "completed", "scheduled_for": None,
               "started_at": "2026-10-25T01:09:15+00:00"}
        with patch.object(database, "_client", return_value=client):
            database._upload(row)
        self.assertEqual(client.table.return_value.upsert.call_args.args[0]["scheduled_for"], row["started_at"])
        self.assertIsNone(row["scheduled_for"])
