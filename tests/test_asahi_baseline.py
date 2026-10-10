import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("baseline", Path(__file__).resolve().parents[1] / "asahi/baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


class BaselineTests(unittest.TestCase):
    def test_disabled_is_distinct_from_unknown(self):
        values = baseline.parse_config('CONFIG_DRM=y\nCONFIG_ANDROID_BINDER_IPC=m\n# CONFIG_ANDROID_BINDERFS is not set\nCONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder"\n')
        self.assertEqual(values["CONFIG_DRM"], "y")
        self.assertEqual(values["CONFIG_ANDROID_BINDER_IPC"], "m")
        self.assertEqual(values["CONFIG_ANDROID_BINDERFS"], "n")
        self.assertEqual(values["CONFIG_ANDROID_BINDER_DEVICES"], '"binder,hwbinder"')
        self.assertEqual(values["CONFIG_DRM_ASAHI"], "unknown")

    def test_missing_config_does_not_claim_features_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            result = baseline.kernel_config(Path(directory), "test")
            self.assertIsNone(result["source"])
            self.assertTrue(all(v == "unknown" for v in result["symbols"].values()))
            self.assertEqual(len(result["attempts"]), 3)

    def test_corrupt_proc_config_falls_back_to_running_boot_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proc").mkdir()
            (root / "proc/config.gz").write_bytes(b"broken gzip")
            (root / "boot").mkdir()
            (root / "boot/config-test").write_text("CONFIG_ARM64_16K_PAGES=y\n")
            result = baseline.kernel_config(root, "test")
            self.assertEqual(result["source"], "/boot/config-test")
            self.assertEqual(result["symbols"]["CONFIG_ARM64_16K_PAGES"], "y")

    def test_proc_config_and_real_driver_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proc").mkdir()
            (root / "proc/config.gz").write_bytes(gzip.compress(b"CONFIG_DRM_ASAHI=m\n"))
            device = root / "sys/class/drm/renderD128/device"
            device.mkdir(parents=True)
            driver = root / "sys/bus/platform/drivers/asahi"
            driver.mkdir(parents=True)
            (device / "driver").symlink_to(driver)
            result = baseline.collect(root, "test")
            self.assertEqual(result["kernel_config"]["source"], "/proc/config.gz")
            self.assertEqual(result["drm"][0]["driver"], "asahi")


if __name__ == "__main__":
    unittest.main()
