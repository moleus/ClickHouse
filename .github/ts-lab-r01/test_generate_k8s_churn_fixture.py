#!/usr/bin/env python3
"""Pure-Python R01 fixture unit tests; independent of ClickHouse availability."""
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest


def load_generator():
    path = pathlib.Path(__file__).with_name("generate_k8s_churn_fixture.py")
    spec = importlib.util.spec_from_file_location("r01_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestKubernetesChurnFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_generator()

    def test_closed_form_for_full_400_node_three_day_shape(self):
        series, samples = self.mod.expected_counts(3, 400)
        self.assertEqual(series, {"node60": 9600, "pod300": 19200, "burst15": 96})
        self.assertEqual(samples, {"node60": 1728000, "pod300": 691200, "burst15": 69120})
        self.assertEqual(sum(series.values()), 28896)
        self.assertEqual(sum(samples.values()), 2488320)

    def test_unique_series_and_time_range_for_one_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = pathlib.Path(tmp) / "fixture.jsonl"
            manifest = pathlib.Path(tmp) / "manifest.json"
            info = self.mod.generate(fixture, manifest, days=1, slots=4)
            self.assertEqual(info["series"], 104)
            self.assertEqual(info["samples"], 13824)
            self.assertEqual(info["rows_by_tier"], {"node60": 32, "pod300": 64, "burst15": 8})
            self.assertEqual(json.loads(manifest.read_text()), info)
            self.assertEqual(info["fixture_bytes"], fixture.stat().st_size)
            self.assertEqual(info["node_slots"], 4)
            seen = set()
            timestamps = set()
            labels = set()
            n = 0
            for line in fixture.open():
                row = json.loads(line)
                n += 1
                identity = (row["metric_name"], tuple(sorted(row["tags"].items())))
                self.assertNotIn(identity, seen)
                seen.add(identity)
                self.assertEqual(row["tags"]["region"], "eu-north-1")
                self.assertEqual(row["tags"]["cluster"], "synthetic-k8s")
                self.assertIn(row["metric_name"], ("tslab_node_cpu_ratio", "tslab_pod_cpu_ratio"))
                cadence = int(row["tags"]["scrape"].rstrip("s"))
                expected_points = 10800 // cadence
                self.assertEqual(len(row["samples"]), expected_points)
                for when, value in row["samples"]:
                    moment = dt.datetime.strptime(when, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=dt.timezone.utc)
                    stamp = int(moment.timestamp())
                    self.assertGreaterEqual(stamp, self.mod.BASE)
                    self.assertLess(stamp, self.mod.BASE + 86400)
                    self.assertIsInstance(value, float)
                    self.assertTrue(0 <= value <= 1.0)
                    timestamps.add(stamp)
                labels.update(row["tags"])
            self.assertEqual(n, 104)
            self.assertEqual(len(seen), 104)
            self.assertIn("namespace", labels)
            self.assertIn("node", labels)
            self.assertIn("project", labels)
            self.assertIn("pod", labels)
            self.assertEqual(min(timestamps), self.mod.BASE + 14)
            self.assertEqual(max(timestamps), self.mod.BASE + 86400 - 1)

    def test_byte_exact_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirpath = pathlib.Path(tmp)
            a = self.mod.generate(dirpath / "a.jsonl", dirpath / "a.json", days=1, slots=1)
            b = self.mod.generate(dirpath / "b.jsonl", dirpath / "b.json", days=1, slots=1)
            self.assertEqual(a["sha256"], b["sha256"])
            self.assertEqual(a["series"], 32)
            self.assertEqual(a["samples"], 7776)
            self.assertEqual((dirpath / "a.jsonl").read_bytes(), (dirpath / "b.jsonl").read_bytes())
            self.assertEqual((dirpath / "a.json").read_bytes(), (dirpath / "b.json").read_bytes())

    def test_reject_invalid_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            for days, slots in ((0, 400), (15, 400), (3, 0), (3, 4001)):
                with self.subTest(days=days, slots=slots):
                    with self.assertRaises(ValueError):
                        self.mod.generate(folder / "x", folder / "y", days, slots)


if __name__ == "__main__":
    unittest.main()
