import json
import unittest
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI

from src.core.agent import HearthAgent


class AgentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_local_chat_completion_with_transcript_and_persona(self):
        requests = []
        def handler(request):
            requests.append(request)
            payload = {"id": "test", "object": "chat.completion.chunk", "created": 1,
                       "model": "local-test", "choices": [{"index": 0,
                       "delta": {"content": "Hello there."}, "finish_reason": None}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode())
        agent = HearthAgent()
        agent._client = AsyncOpenAI(api_key="local", base_url="http://model.test/v1",
                                   http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with patch("src.core.agent.LOCAL_MODEL_NAME", "local-test"):
                reply = "".join([s async for s in agent.reply_stream(
                    [{"role": "user", "content": "Hello"}], {"preferred_name": "Julie"})])
            self.assertEqual(reply, "Hello there.")
            self.assertEqual(requests[0].url.path, "/v1/chat/completions")
            body = json.loads(requests[0].content)
            self.assertTrue(body["stream"])
            self.assertEqual(body["model"], "local-test")
            self.assertIn("Julie", body["messages"][0]["content"])
            self.assertEqual(body["messages"][-1]["content"], "Hello")
        finally:
            await agent.close()
        self.assertIsNone(agent._client)
