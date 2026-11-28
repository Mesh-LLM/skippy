"""Offline installer regressions for the separately packaged NVML headers."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile


SCRIPT = Path(__file__).resolve().parents[1] / "ci-windows-cuda.py"
spec = importlib.util.spec_from_file_location("ci_windows_cuda", SCRIPT)
cuda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cuda)


class WindowsCuda(unittest.TestCase):
    def inputs(self, version, header=b"/* NVIDIA NVML declarations fixture */\n"):
        manifest, downloads = {}, {}
        # Independent expected roster: omission in install() must fail this test.
        components = ["cuda_nvcc", "cuda_cccl", "cuda_nvml_dev", "cuda_cudart", "libcublas", "libnvjitlink"]
        if version == "13.1.2":
            components += ["cuda_crt", "libnvvm"]
        for component in components:
            name = f"{component}-windows-x86_64-fixture"
            stream = io.BytesIO()
            with ZipFile(stream, "w") as archive:
                archive.writestr(name + "/LICENSE", component + " license\n")
                if component == "cuda_nvml_dev":
                    if header is not None:
                        archive.writestr(name + "/include/nvml.h", header)
                else:
                    archive.writestr(name + f"/include/{component}.h", component)
            data = stream.getvalue()
            relative = name + ".zip"
            manifest[component] = {"windows-x86_64": {
                "relative_path": relative, "size": str(len(data)), "sha256": hashlib.sha256(data).hexdigest(),
            }}
            downloads[cuda.REDIST + relative] = data
        raw = json.dumps(manifest).encode()
        downloads[cuda.REDIST + f"redistrib_{version}.json"] = raw
        return downloads, hashlib.sha256(raw).hexdigest()

    def install(self, version, root, downloads, manifest_sha):
        requested = []
        def urlopen(url, **_kwargs):
            requested.append(url)
            return io.BytesIO(downloads[url])
        with patch.dict(cuda.MANIFESTS, {version: manifest_sha}), \
             patch.object(cuda.urllib.request, "urlopen", side_effect=urlopen):
            cuda.install(version, root)
        return requested

    def test_both_pinned_toolkits_extract_nvml_into_the_shared_include_directory(self):
        for version in cuda.MANIFESTS:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                downloads, sha = self.inputs(version)
                root = Path(tmp) / "cuda"
                requested = self.install(version, root, downloads, sha)
                self.assertEqual(set(requested), set(downloads))
                self.assertIn(b"NVML declarations", (root / "include/nvml.h").read_bytes())
                for component in cuda.REDISTRIBUTED:
                    self.assertIn((component + " license").encode(), (root / "EULA.txt").read_bytes())

    def test_missing_or_empty_nvml_header_rejects_the_toolkit(self):
        for version in cuda.MANIFESTS:
            for header in (None, b""):
                with self.subTest(version=version, header=header), tempfile.TemporaryDirectory() as tmp:
                    downloads, sha = self.inputs(version, header)
                    with self.assertRaisesRegex(ValueError, "NVML development header is missing or empty"):
                        self.install(version, Path(tmp) / "cuda", downloads, sha)

    def test_nvml_download_must_match_the_pinned_archive_digest(self):
        for version in cuda.MANIFESTS:
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                downloads, sha = self.inputs(version)
                url = next(url for url in downloads if "cuda_nvml_dev-windows" in url)
                data = bytearray(downloads[url])
                data[-1] ^= 1  # Preserve length so this exercises SHA-256 validation.
                downloads[url] = bytes(data)
                with self.assertRaisesRegex(ValueError, "cuda_nvml_dev archive checksum mismatch"):
                    self.install(version, Path(tmp) / "cuda", downloads, sha)


if __name__ == "__main__":
    unittest.main()
