"""Math OCR adapter contracts without native imports or model downloads."""

import contextlib
import copy
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch, sentinel

from pdfconv.pdf2tex.math_ocr import MathOCR


COREML = "CoreMLExecutionProvider"
CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"


class MathOCRTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.model_dir = Path(temporary.name) / "models"

        self.providers = Mock(return_value=[COREML, CPU])
        self.recognizer = MagicMock()
        self.model = self.recognizer.latex_ocr.model
        self.retain_provider(COREML)
        self.factory = Mock(return_value=self.recognizer)
        self.merge = Mock(return_value="merged LaTeX")
        self.image_api = MagicMock()
        self.opened_image = self.image_api.open.return_value.__enter__.return_value
        self.opened_image.convert.return_value = sentinel.rgb_image

        modules = {}
        for name in ("onnxruntime", "PIL", "pix2text", "pix2text.utils"):
            modules[name] = types.ModuleType(name)
        modules["onnxruntime"].get_available_providers = self.providers
        modules["PIL"].Image = self.image_api
        modules["pix2text"].TextFormulaOCR = types.SimpleNamespace(from_config=self.factory)
        modules["pix2text.utils"].merge_line_texts = self.merge
        module_patch = patch.dict(sys.modules, modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        # These cases describe a machine without CUDA; the CUDA backend has its
        # own tests below. Without this the host's GPU would decide.
        cuda_patch = patch("pdfconv.pdf2tex.math_ocr.preload_cuda_runtime",
                           return_value=False)
        self.cuda_usable = cuda_patch.start()
        self.addCleanup(cuda_patch.stop)
        # Restore cache defaults set by the adapter after every test.
        environment_patch = patch.dict(os.environ)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    def retain_provider(self, provider):
        for component in (self.model.encoder, self.model.decoder):
            component.session.get_providers.return_value = [provider]

    def create_adapter(self, device):
        with contextlib.redirect_stdout(io.StringIO()):
            return MathOCR(device, self.model_dir)

    def test_gpu_uses_free_onnx_models_with_coreml_and_cpu_host_tensors(self):
        self.create_adapter("gpu")
        arguments = self.factory.call_args.kwargs
        self.assertEqual(arguments["device"], "cpu")
        self.assertFalse(arguments["enable_spell_checker"])
        configs = arguments["total_configs"]
        self.assertEqual(configs["mfd"]["model_name"], "mfd-1.5")
        self.assertEqual(configs["formula"]["model_name"], "mfr-1.5")
        for name in ("mfd", "formula"):
            self.assertEqual(configs[name]["model_backend"], "onnx")
            self.assertEqual(configs[name]["root"], str(self.model_dir / "pix2text"))
        runtime = configs["formula"]["more_model_configs"]
        self.assertEqual(runtime["provider"], COREML)
        self.assertFalse(runtime["use_io_binding"])
        self.assertFalse(runtime["use_cache"])
        options = runtime["provider_options"]
        self.assertEqual(options["MLComputeUnits"], "CPUAndGPU")
        self.assertEqual(options["ModelFormat"], "MLProgram")
        self.assertEqual(options["ModelCacheDirectory"], str(self.model_dir / "coreml"))
        self.assertTrue((self.model_dir / "coreml").is_dir())

    def test_gpu_without_any_accelerator_fails_before_downloading_models(self):
        self.providers.return_value = [CPU]
        with self.assertRaisesRegex(RuntimeError, "CoreMLExecutionProvider"):
            self.create_adapter("gpu")
        self.factory.assert_not_called()

    def test_cuda_without_any_cuda_support_fails_before_downloading_models(self):
        self.providers.return_value = [CPU]
        with self.assertRaisesRegex(RuntimeError, "CUDA math OCR needs"):
            self.create_adapter("cuda")
        self.factory.assert_not_called()

    def test_the_provider_actually_held_is_reported_not_the_one_requested(self):
        adapter = self.create_adapter("gpu")
        self.assertEqual(adapter.providers_in_use(), [COREML, COREML])

    def test_gpu_rejects_provider_fallback_in_either_model_component(self):
        for name in ("encoder", "decoder"):
            with self.subTest(component=name):
                self.retain_provider(COREML)
                getattr(self.model, name).session.get_providers.return_value = [CPU, COREML]
                with self.assertRaisesRegex(RuntimeError, "did not retain CoreMLExecutionProvider"):
                    self.create_adapter("gpu")

    def test_non_cuda_backends_keep_host_tensors_on_the_cpu(self):
        for backend, providers in (("coreml", [COREML, CPU]), ("cpu", [CPU])):
            with self.subTest(backend=backend):
                self.providers.return_value = providers
                self.retain_provider(providers[0])
                self.create_adapter("auto")
                self.assertEqual(self.factory.call_args.kwargs["device"], "cpu")

    def test_cpu_explicitly_avoids_coreml_even_when_available(self):
        self.retain_provider(CPU)
        self.create_adapter("cpu")
        runtime = self.factory.call_args.kwargs["total_configs"]["formula"]["more_model_configs"]
        self.assertEqual(runtime["provider"], CPU)
        self.assertNotIn("provider_options", runtime)
        self.assertFalse((self.model_dir / "coreml").exists())

    def test_auto_uses_cpu_when_coreml_is_unavailable(self):
        self.providers.return_value = [CPU]
        self.retain_provider(CPU)
        self.create_adapter("auto")
        runtime = self.factory.call_args.kwargs["total_configs"]["formula"]["more_model_configs"]
        self.assertEqual(runtime["provider"], CPU)

    def test_auto_selects_coreml_when_available(self):
        self.create_adapter("auto")
        runtime = self.factory.call_args.kwargs["total_configs"]["formula"]["more_model_configs"]
        self.assertEqual(runtime["provider"], COREML)

    def test_auto_retries_with_cpu_when_coreml_initialization_fails(self):
        self.factory.side_effect = [RuntimeError("CoreML model plan failed"), self.recognizer]
        self.retain_provider(CPU)

        with contextlib.redirect_stdout(io.StringIO()):
            MathOCR("auto", self.model_dir)

        self.assertEqual(self.factory.call_count, 2)
        runtime = self.factory.call_args.kwargs["total_configs"]["formula"]["more_model_configs"]
        self.assertEqual(runtime["provider"], CPU)
        self.assertNotIn("provider_options", runtime)

    def test_cuda_uses_the_onnx_cuda_provider(self):
        self.providers.return_value = [CUDA, CPU]
        self.cuda_usable.return_value = True
        self.retain_provider(CUDA)
        adapter = self.create_adapter("cuda")
        self.assertEqual(adapter.backend, "cuda")
        self.assertEqual(adapter.device_label, "cuda")
        configs = self.factory.call_args.kwargs["total_configs"]
        for name in ("mfd", "formula"):
            self.assertEqual(configs[name]["model_backend"], "onnx")
        runtime = configs["formula"]["more_model_configs"]
        # A provider list with CPU behind CUDA, not a single provider: some
        # operators in these graphs have no CUDA implementation.
        self.assertNotIn("provider", runtime)
        self.assertEqual(runtime["providers"], [CUDA, CPU])
        self.assertEqual(runtime["provider_options"], [{"device_id": 0}, {}])
        # Optimum rebuilds the session around the device it is moved to, so a
        # "cpu" here would undo the providers above.
        self.assertEqual(self.factory.call_args.kwargs["device"], "cuda")

    def test_cuda_needs_the_provider_and_a_cuda_torch(self):
        # The provider's library depends on the CUDA runtime the torch wheels
        # ship, so either half missing means the GPU is not actually reachable.
        for providers, cuda_torch, expected in (
                ([CUDA, CPU], False, "CUDA-enabled PyTorch"),
                ([CPU], True, "onnxruntime-gpu"),
                ([CUDA, CPU], True, None)):
            with self.subTest(providers=providers, cuda_torch=cuda_torch):
                if expected is None:
                    self.assertEqual(
                        MathOCR._resolve_backend("cuda", providers, cuda_torch), "cuda")
                else:
                    with self.assertRaisesRegex(RuntimeError, expected):
                        MathOCR._resolve_backend("cuda", providers, cuda_torch)

    def test_cuda_rejects_a_session_that_fell_back_to_the_cpu(self):
        self.providers.return_value = [CUDA, CPU]
        self.cuda_usable.return_value = True
        self.retain_provider(CPU)
        with self.assertRaisesRegex(RuntimeError, "did not retain CUDAExecutionProvider"):
            self.create_adapter("cuda")

    def test_auto_prefers_coreml_then_cuda_then_cpu(self):
        for providers, cuda_torch, expected in (
                ([COREML, CUDA, CPU], True, "coreml"),
                ([CUDA, CPU], True, "cuda"),
                ([CUDA, CPU], False, "cpu"),
                ([CPU], True, "cpu")):
            with self.subTest(providers=providers, cuda_torch=cuda_torch):
                self.assertEqual(
                    MathOCR._resolve_backend("auto", providers, cuda_torch), expected)

    def test_page_escapes_prose_preserves_math_and_uses_latex_delimiters(self):
        rows = [
            {"type": "text", "text": "Rate 5% & cash_$", "line_number": 0},
            {"type": "embedding", "text": r"a_i=\frac{x^2}{y}", "line_number": 0},
            {"type": "isolated", "text": r"\begin{aligned}x &= y \\ z &= 1\end{aligned}",
             "line_number": 1},
            {"text": "#Note {1}", "line_number": 2},
        ]
        original_rows = copy.deepcopy(rows)
        self.recognizer.recognize.return_value = rows
        adapter = self.create_adapter("gpu")

        self.assertEqual(adapter(b"mock PNG bytes"), "merged LaTeX")
        self.assertEqual(self.image_api.open.call_args.args[0].getvalue(), b"mock PNG bytes")
        self.opened_image.convert.assert_called_once_with("RGB")
        self.image_api.open.return_value.__exit__.assert_called_once()
        self.recognizer.recognize.assert_called_once_with(
            sentinel.rgb_image, return_text=False, contain_formula=True,
            mfr_batch_size=1, resized_shape=1024,
        )
        escaped_rows = self.merge.call_args.args[0]
        self.assertEqual(escaped_rows[0]["text"], r"Rate 5\% \& cash\_\$")
        self.assertEqual(escaped_rows[1], original_rows[1])
        self.assertEqual(escaped_rows[2], original_rows[2])
        self.assertEqual(escaped_rows[3]["text"], r"\#Note \{1\}")
        self.assertEqual(rows, original_rows)
        self.assertEqual(self.merge.call_args.kwargs, {
            "auto_line_break": False,
            "line_sep": "\n",
            "embed_sep": (r"\(", r"\)"),
            "isolated_sep": ("\\[\n", "\n\\]"),
        })


if __name__ == "__main__":
    unittest.main()
