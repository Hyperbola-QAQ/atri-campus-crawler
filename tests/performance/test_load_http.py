"""压测计数器与失败统计回归，不访问网络。"""

import importlib.util
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location(
    "load_http_under_test", Path(__file__).with_name("load_http.py")
)
load = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load)


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"ok"


class LoadMetricsTest(unittest.TestCase):
    def test_nearest_rank_and_empty_samples(self):
        self.assertIsNone(load.percentile([], 95))
        self.assertEqual(load.percentile([5, 1, 3, 2, 4], 50), 3)
        self.assertEqual(load.percentile([5, 1, 3, 2, 4], 95), 5)

    def test_all_requests_including_remainder_are_counted(self):
        with patch.object(load.urllib.request, "urlopen", return_value=Response()):
            result = load.run(
                "http://test", users=3, requests=10, timeout=1, expected_status=200
            )
        self.assertEqual(result["completed"], 10)
        self.assertEqual(result["statuses"], {"200": 10})
        self.assertEqual(result["error_rate"], 0)
        self.assertGreater(result["qps"], 0)
        self.assertIsNone(result["target"]["peak_rss_bytes"])

    def test_transport_errors_are_not_success(self):
        with patch.object(load.urllib.request, "urlopen", side_effect=TimeoutError):
            result = load.run(
                "http://test", users=2, requests=7, timeout=1, expected_status=200
            )
        self.assertEqual(result["statuses"], {"transport_error": 7})
        self.assertEqual(result["error_rate"], 1)
        self.assertEqual(result["completed"], 7)

    def test_expected_rejection_is_counted_separately(self):
        import io

        def reject(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://test", 401, "unauthorized", {}, io.BytesIO(b"denied")
            )

        with patch.object(load.urllib.request, "urlopen", side_effect=reject):
            result = load.run(
                "http://test", users=2, requests=5, timeout=1, expected_status=401
            )
        self.assertEqual(result["statuses"], {"401": 5})
        self.assertEqual(result["error_rate"], 0)
        self.assertIsNone(load.process_sample(-1))


if __name__ == "__main__":
    unittest.main()
