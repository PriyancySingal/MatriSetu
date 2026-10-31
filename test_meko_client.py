import json
import unittest
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import config
import meko_client


class MekoTriageMemoryTests(unittest.TestCase):
    def test_remembered_triage_never_lowers_current_level(self):
        self.assertEqual(meko_client.highest_triage_level("RED", "GREEN"), "RED")
        self.assertEqual(meko_client.highest_triage_level("AMBER", "RED"), "RED")
        self.assertEqual(meko_client.highest_triage_level("GREEN", "AMBER"), "AMBER")
        self.assertEqual(meko_client.highest_triage_level("MYTH", "RED"), "MYTH")

    def test_keeps_highest_active_triage_until_a_resolution_record(self):
        now = datetime.now(timezone.utc)
        records = [
            {
                "level": "RED",
                "status": "active",
                "recorded_at": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "level": "AMBER",
                "status": "active",
                "recorded_at": (now - timedelta(hours=1)).isoformat(),
            },
        ]
        text = "\n".join(
            meko_client._TRIAGE_RECORD_PREFIX + json.dumps(record)
            for record in records
        )

        self.assertEqual(
            meko_client.recent_triage_record(text, now)[:2],
            ("RED", "active"),
        )
        self.assertEqual(meko_client.recent_triage_level(text, now), "RED")

        resolved_record = {
            "level": "AMBER",
            "status": "resolved",
            "recorded_at": now.isoformat(),
        }
        resolved_text = text + "\n" + (
            meko_client._TRIAGE_RECORD_PREFIX + json.dumps(resolved_record)
        )

        self.assertIsNone(meko_client.recent_triage_level(resolved_text, now))

    def test_reads_records_nested_in_meko_search_response(self):
        now = datetime.now(timezone.utc)
        marker = meko_client._TRIAGE_RECORD_PREFIX + json.dumps({
            "level": "RED",
            "status": "active",
            "recorded_at": now.isoformat(),
        })
        response = json.dumps({
            "result": json.dumps({
                "results": {"memory": [{"memory": marker}]},
            }),
        })

        self.assertEqual(meko_client.recent_triage_level(response, now), "RED")

    def test_resolved_or_expired_records_do_not_raise_triage(self):
        now = datetime.now(timezone.utc)
        resolved = {
            "level": "RED",
            "status": "resolved",
            "recorded_at": now.isoformat(),
        }
        expired = {
            "level": "RED",
            "status": "active",
            "recorded_at": (now - timedelta(hours=25)).isoformat(),
        }

        self.assertIsNone(
            meko_client.recent_triage_level(
                meko_client._TRIAGE_RECORD_PREFIX + json.dumps(resolved), now
            )
        )
        self.assertIsNone(
            meko_client.recent_triage_level(
                meko_client._TRIAGE_RECORD_PREFIX + json.dumps(expired), now
            )
        )

    def test_patient_message_is_not_used_as_recall_query(self):
        with patch.object(
            meko_client,
            "_patient_conversation",
            return_value=("conv-1", "matrisetu:P-hash"),
        ):
            with patch.object(meko_client, "recall", return_value="") as recall:
                meko_client.get_triage_memory_level("whatsapp:+15551234567")

        self.assertEqual(
            recall.call_args.args,
            ("conv-1", "MATRISETU_TRIAGE_V1"),
        )
        self.assertEqual(recall.call_args.kwargs["agent_id"], "matrisetu:P-hash")

    def test_memory_record_contains_no_patient_identifier_or_symptom_text(self):
        patient_phone = "whatsapp:+15551234567"
        with patch.object(
            meko_client,
            "_patient_conversation",
            return_value=("conv-1", "matrisetu:P-hash"),
        ):
            with patch.object(meko_client, "remember", return_value="memory-1") as remember:
                result = meko_client.remember_triage(
                    patient_phone, "RED", 24, postpartum=False
                )

        memory_text = remember.call_args.args[1]
        self.assertEqual(result, "memory-1")
        self.assertNotIn(patient_phone, memory_text)
        self.assertNotIn("bleeding", memory_text.lower())
        self.assertIn('"level":"RED"', memory_text)
        self.assertEqual(remember.call_args.kwargs["agent_id"], "matrisetu:P-hash")

    def test_invalid_configuration_skips_remote_call(self):
        with patch.object(config, "MEKO_ENABLED", False):
            with patch.object(meko_client, "_call_meko_async") as call:
                self.assertIsNone(meko_client._safe_call("memory_add", {}))

        call.assert_not_called()

    def test_scopes_memory_search_to_the_patient_conversation(self):
        with patch.object(config, "MEKO_PATIENTS_DATAPACK", "patient-pack"):
            with patch.object(meko_client, "_safe_call", return_value=None) as call:
                meko_client.recall(
                    "conversation-1",
                    "MATRISETU_TRIAGE_V1",
                    agent_id="matrisetu:P-hash",
                )

        tool_name, arguments = call.call_args.args
        self.assertEqual(tool_name, "memory_search")
        self.assertEqual(arguments["run_id"], "conversation-1")
        self.assertEqual(arguments["agent_id"], "matrisetu:P-hash")

    def test_queued_local_record_is_cached_without_waiting_for_meko(self):
        patient_identifier = "whatsapp:+15551234567"
        with patch.object(config, "MEKO_ENABLED", True):
            with patch.object(config, "MEKO_API_KEY", "configured"):
                with patch.object(config, "MEKO_PATIENT_SALT", "test-salt"):
                    with patch.dict(meko_client._TRIAGE_CACHE, {}, clear=True):
                        with patch.object(
                            meko_client._MEKO_WRITE_EXECUTOR,
                            "submit",
                            return_value=Future(),
                        ):
                            meko_client.queue_triage_memory_record(
                                patient_identifier, "RED", 24
                            )

                        self.assertEqual(
                            meko_client.cached_triage_memory_level(patient_identifier),
                            "RED",
                        )

    def test_cached_record_expires_after_24_hours(self):
        patient_identifier = "whatsapp:+15551234567"
        now = datetime.now(timezone.utc)
        with patch.object(config, "MEKO_ENABLED", True):
            with patch.object(config, "MEKO_API_KEY", "configured"):
                with patch.object(config, "MEKO_PATIENT_SALT", "test-salt"):
                    with patch.dict(meko_client._TRIAGE_CACHE, {}, clear=True):
                        meko_client._cache_triage_level(
                            patient_identifier,
                            "RED",
                            "active",
                            now - timedelta(hours=25),
                        )

                        self.assertIsNone(
                            meko_client.cached_triage_memory_level(patient_identifier)
                        )

    def test_exception_groups_report_nested_exception_types(self):
        error = ExceptionGroup(
            "Meko MCP failures",
            [TimeoutError(), ValueError()],
        )

        self.assertEqual(
            meko_client._exception_type_names(error),
            ["TimeoutError", "ValueError"],
        )

    def test_extracts_ids_from_nested_mcp_results(self):
        response = {
            "result": json.dumps({"results": [{"id": "memory-1"}]}),
        }

        self.assertEqual(
            meko_client._extract_id(response, "memory_id"),
            "memory-1",
        )

    def test_correct_updates_owned_active_memories_with_writer_agent_id(self):
        memory_text = meko_client._TRIAGE_RECORD_PREFIX + json.dumps({
            "level": "RED",
            "status": "active",
            "week": 24,
            "postpartum": False,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        search_result = SimpleNamespace(
            structured_content={
                "result": json.dumps({
                    "results": [{
                        "id": "memory-1",
                        "memory": memory_text,
                        "agent_id": "matrisetu:P-hash",
                        "run_id": "conversation-1",
                    }]
                })
            },
            content=[],
            is_error=False,
        )
        with patch.object(config, "MEKO_ENABLED", True):
            with patch.object(config, "MEKO_PATIENTS_DATAPACK", "pack-1"):
                with patch.object(config, "MEKO_PATIENT_SALT", "test-salt"):
                    with patch.object(
                        meko_client,
                        "_patient_conversation",
                        return_value=("conversation-1", "matrisetu:P-hash"),
                    ):
                        with patch.object(
                            meko_client,
                            "_safe_call",
                            side_effect=[
                                search_result,
                                SimpleNamespace(is_error=False),
                            ],
                        ) as call:
                            with patch.object(meko_client, "_log_meko_event_async"):
                                result = meko_client.correct_patient_triage_memory(
                                    "whatsapp:+15551234567",
                                    "AMBER",
                                    "active",
                                    "asha-1",
                                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["memory_id"], "memory-1")
        self.assertEqual(call.call_args_list[1].args[0], "memory_update")
        update_args = call.call_args_list[1].args[1]
        self.assertEqual(update_args["memory_id"], "memory-1")
        self.assertEqual(update_args["conversation_id"], "conversation-1")
        self.assertEqual(update_args["agent_id"], "matrisetu:P-hash")
        self.assertIn('"level":"AMBER"', update_args["text"])
        self.assertNotIn("15551234567", update_args["text"])

    def test_forget_deletes_only_patient_records_with_writer_agent_id(self):
        def memory(level, agent_id):
            return {
                "id": level.lower() + "-memory",
                "memory": meko_client._TRIAGE_RECORD_PREFIX + json.dumps({
                    "level": level,
                    "status": "active",
                    "week": 24,
                    "postpartum": False,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }),
                "agent_id": agent_id,
                "run_id": "conversation-1",
            }

        search_result = SimpleNamespace(
            structured_content={
                "result": json.dumps({
                    "results": [
                        memory("RED", "matrisetu:P-hash"),
                        memory("AMBER", "other-agent"),
                    ]
                })
            },
            content=[],
            is_error=False,
        )
        with patch.object(config, "MEKO_ENABLED", True):
            with patch.object(config, "MEKO_PATIENTS_DATAPACK", "pack-1"):
                with patch.object(config, "MEKO_PATIENT_SALT", "test-salt"):
                    with patch.object(
                        meko_client,
                        "_patient_conversation",
                        return_value=("conversation-1", "matrisetu:P-hash"),
                    ):
                        with patch.object(
                            meko_client,
                            "_safe_call",
                            side_effect=[
                                search_result,
                                SimpleNamespace(is_error=False),
                            ],
                        ) as call:
                            with patch.object(
                                meko_client, "_log_meko_event_async"
                            ):
                                result = meko_client.forget_patient_triage_memory(
                                    "whatsapp:+15551234567",
                                    "asha-1",
                                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(call.call_args_list[1].args[0], "memory_delete_by_id")
        delete_args = call.call_args_list[1].args[1]
        self.assertEqual(delete_args["memory_id"], "red-memory")
        self.assertEqual(delete_args["conversation_id"], "conversation-1")
        self.assertEqual(delete_args["agent_id"], "matrisetu:P-hash")

    def test_panel_snapshot_parses_meko_search_id_and_record_fields(self):
        memory_text = meko_client._TRIAGE_RECORD_PREFIX + json.dumps({
            "level": "RED",
            "status": "active",
            "week": 24,
            "postpartum": False,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        response = SimpleNamespace(
            structured_content={
                "result": json.dumps({
                    "results": [{
                        "id": "memory-1",
                        "memory": memory_text,
                        "agent_id": "matrisetu:P-hash",
                        "run_id": "conversation-1",
                    }]
                })
            },
            content=[],
            is_error=False,
        )
        with patch.object(config, "MEKO_ENABLED", True):
            with patch.object(config, "MEKO_PATIENTS_DATAPACK", "pack-1"):
                with patch.object(
                    meko_client,
                    "_patient_conversation",
                    return_value=("conversation-1", "matrisetu:P-hash"),
                ):
                    with patch.object(meko_client, "_safe_call", return_value=response):
                        with patch.object(meko_client, "_log_meko_event_async"):
                            snapshot = meko_client._panel_snapshot_from_search(
                                "whatsapp:+15551234567",
                                "asha-1",
                            )

        self.assertEqual(snapshot["state"], "available")
        self.assertEqual(snapshot["record"]["memory_id"], "memory-1")
        self.assertEqual(snapshot["record"]["level"], "RED")
        self.assertEqual(snapshot["record"]["status"], "active")
        self.assertEqual(snapshot["record"]["week"], 24)
        self.assertEqual(snapshot["effective_level"], "RED")
        self.assertIn(memory_text, snapshot["record"]["raw_excerpt"])


if __name__ == "__main__":
    unittest.main()
