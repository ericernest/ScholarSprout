from __future__ import annotations

import unittest

try:
    from handlers.paper_reading.handler import _batched
except ModuleNotFoundError as error:
    _IMPORT_ERROR = error
    _batched = None
else:
    _IMPORT_ERROR = None


@unittest.skipIf(_IMPORT_ERROR is not None, f"project dependencies unavailable: {_IMPORT_ERROR}")
class TestSurveyReadingMapPerformanceGuards(unittest.TestCase):
    def test_batched_limits_survey_chunk_submission_size(self) -> None:
        chunks = [{"chunk_id": f"chunk:{index}"} for index in range(14)]
        batches = _batched(chunks, 6)

        self.assertEqual([len(batch) for batch in batches], [6, 6, 2])
        self.assertEqual(batches[0][0]["chunk_id"], "chunk:0")
        self.assertEqual(batches[-1][-1]["chunk_id"], "chunk:13")


if __name__ == "__main__":
    unittest.main()
