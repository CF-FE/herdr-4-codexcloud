"""Outcome checks for the offline Projects iteration planner."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skill/github-projects-operations/scripts/plan_iterations.py"


class IterationPlannerTests(unittest.TestCase):
    def run_plan(self, start, days, count, title="Sprint"):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--start", start, "--days", str(days),
                 "--count", str(count), "--title-prefix", title],
                cwd=directory, capture_output=True, text=True,
            )
            self.assertEqual(list(Path(directory).iterdir()), [])
            return result

    def test_calendar_spans_leap_day_month_and_year(self):
        for start, days, expected in [
            ("2028-02-26", 7, ["2028-02-26", "2028-03-04", "2028-03-11"]),
            ("2027-12-28", 7, ["2027-12-28", "2028-01-04", "2028-01-11"]),
            ("2027-02-28", 1, ["2027-02-28", "2027-03-01", "2027-03-02"]),
        ]:
            with self.subTest(start=start):
                result = self.run_plan(start, days, len(expected), "迭代")
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads(result.stdout)
                self.assertEqual(plan["start_date"], start)
                self.assertEqual(plan["iteration_duration"], days)
                self.assertEqual([item["start_date"] for item in plan["iterations"]], expected)
                self.assertEqual([item["title"] for item in plan["iterations"]], ["迭代 1", "迭代 2", "迭代 3"])
                self.assertTrue(all(item["duration"] == days for item in plan["iterations"]))

    def test_invalid_inputs_emit_no_partial_plan(self):
        cases = [
            ("2027-02-29", 7, 3, "Sprint"),
            ("20281027", 7, 3, "Sprint"),
            ("2028-2-01", 7, 3, "Sprint"),
            ("2028-02-01", 0, 3, "Sprint"),
            ("2028-02-01", -1, 3, "Sprint"),
            ("2028-02-01", 7, 0, "Sprint"),
            ("2028-02-01", "1.5", 3, "Sprint"),
            ("2028-02-01", 7, 3, "  "),
            ("9999-12-31", 2, 1, "Sprint"),
            ("2028-02-01", 7, 10**15, "Sprint"),
        ]
        for case in cases:
            with self.subTest(case=case):
                result = self.run_plan(*case)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)

    def test_requires_explicit_period_count(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--start", "2028-02-01", "--days", "7", "--title-prefix", "Sprint"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("--count", result.stderr)


if __name__ == "__main__":
    unittest.main()
