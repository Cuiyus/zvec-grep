import json
import unittest
from unittest.mock import patch

import requests

from zg_bench.case_replay import Mcp


class ReplayEncodingTests(unittest.TestCase):
    def test_sse_without_charset_preserves_unicode(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": "#1 代码.py:1-2\n  …\n"}]}}
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "text/event-stream"
        response.encoding = "ISO-8859-1"  # requests' text/* default.
        body = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        response._content = body.encode("utf-8")
        client = Mcp.__new__(Mcp)
        client.headers, client.next_id = {}, 1
        with patch("zg_bench.case_replay.requests.post", return_value=response):
            actual = client.call("tools/call", {"name": "search"})
        self.assertEqual(actual["response"], payload)
        self.assertEqual(actual["raw_response"], body)
