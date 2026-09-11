"""Local mixed prose/formula OCR with free Pix2Text models.

Originally CoreML-only.  It now resolves one of three backends and refuses to
pretend: whichever is selected is verified against the loaded model before any
page is read, so an NVIDIA machine cannot quietly spend a night on the CPU.

    coreml    Apple GPU through ONNX Runtime's CoreML provider   (unchanged)
    cuda      NVIDIA GPU through ONNX Runtime's CUDA provider
    cpu       ONNX Runtime on the CPU                            (unchanged)

All three run the same ONNX models, so only the placement of the work changes.
There is no PyTorch-backend path: Pix2Text names ``*-pytorch`` repositories for
mfd-1.5 and mfr-1.5, but only the ONNX repositories exist on the hub, so that
route cannot load a model at all.  PyTorch still matters here -- see
``preload_cuda_runtime``.
"""
import io
import os
from pathlib import Path

from .latex_output import escape_text

#: Accepted ``--device`` words.  "gpu" and "auto" keep their original meaning.
DEVICES = ("gpu", "auto", "cpu", "cuda")

COREML_PROVIDER = "CoreMLExecutionProvider"
CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


def preload_cuda_runtime() -> bool:
    """Import torch so ONNX Runtime can find the CUDA libraries it needs.

    ``onnxruntime_providers_cuda.dll`` links against cuBLAS and cuDNN and does
    not ship them.  The CUDA 12.8 torch wheels do, in ``torch/lib``, and
    importing torch puts that folder on the process's DLL search path.  Without
    this the provider fails to load with "cublasLt64_12.dll is missing" and ONNX
    Runtime quietly runs the session on the CPU instead.

    -> True when a CUDA-capable torch was imported.
    """
    try:
        import torch
    except Exception:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def cuda_is_usable() -> bool:
    """-> True when this machine has a CUDA device torch can reach."""
    return preload_cuda_runtime()


class MathOCR:
    """Return escaped LaTeX fragments, preserving recognized math commands."""

    def __init__(self, device: str, model_dir: Path):
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        # Set cache defaults before importing the model libraries. Explicit roots
        # below keep downloaded weights alongside this project as well.
        for variable, folder in (("HF_HOME", "huggingface"),
                                 ("PIX2TEXT_HOME", "pix2text"),
                                 ("CNOCR_HOME", "cnocr"), ("CNSTD_HOME", "cnstd"),
                                 ("YOLO_CONFIG_DIR", "yolo")):
            (model_dir / folder).mkdir(parents=True, exist_ok=True)
            os.environ.setdefault(variable, str(model_dir / folder))
        # Before onnxruntime, always: on a CUDA machine this is what makes the
        # provider loadable, and anywhere else it costs one import.
        cuda_torch = preload_cuda_runtime()
        try:
            import onnxruntime as ort
            from PIL import Image
            from pix2text import TextFormulaOCR                  # noqa: F401
            from pix2text.utils import merge_line_texts
        except ImportError as error:
            raise RuntimeError("Math OCR needs the project environment described in "
                               "README.md. Run setup.bat, then use "
                               ".venv\\Scripts\\python.exe convert_pdfs.py. "
                               f"Import failed: {error}") from error

        self.image = Image
        self.merge = merge_line_texts
        self.model_dir = model_dir
        self.backend = self._resolve_backend(device, ort.get_available_providers(),
                                             cuda_torch)
        self.device_label = "cuda" if self.backend == "cuda" else "cpu"

        print(f"Loading math OCR ({self.backend}); first use downloads and prepares "
              "models.", flush=True)
        try:
            self._load()
        except Exception:
            # 'auto' promised a best effort, so a GPU backend that will not
            # initialize degrades to the CPU one; an explicit request does not.
            if device != "auto" or self.backend == "cpu":
                raise
            print(f"{self.backend} math OCR initialization failed; retrying on CPU.",
                  flush=True)
            self.backend, self.device_label = "cpu", "cpu"
            self._load()
        print(f"Math OCR ready: {self.describe()}", flush=True)

    # -- backend selection --------------------------------------------------

    @staticmethod
    def _resolve_backend(device: str, providers, cuda_torch: bool) -> str:
        """-> one of coreml / cuda / cpu, or raise.

        Availability, not the platform, decides: a CoreML provider only exists
        in a macOS ONNX Runtime build and a CUDA one only in onnxruntime-gpu,
        so asking what is installed answers the platform question as well.
        ``cuda_torch`` is required alongside the provider, because without a
        CUDA-capable torch the provider cannot load its libraries.
        """
        if device not in DEVICES:
            raise RuntimeError(f"Unknown math OCR device: {device}")
        coreml = COREML_PROVIDER in providers
        cuda = CUDA_PROVIDER in providers and cuda_torch

        if device == "cpu":
            return "cpu"
        if device == "cuda":
            if cuda:
                return "cuda"
            missing = ("an onnxruntime-gpu build providing CUDAExecutionProvider"
                       if CUDA_PROVIDER not in providers
                       else "a CUDA-enabled PyTorch to supply the CUDA libraries")
            raise RuntimeError(f"CUDA math OCR needs {missing}. Run setup.bat, or "
                               "use --device cpu.")
        if device == "gpu":
            if coreml:
                return "coreml"
            if cuda:
                return "cuda"
            raise RuntimeError(
                "No GPU is available for math OCR: this environment has neither "
                f"{COREML_PROVIDER} (a macOS onnxruntime build) nor a working "
                f"{CUDA_PROVIDER}. Use --device cpu to run on CPU.")
        # auto: whatever is there, CPU if nothing is.
        if coreml:
            return "coreml"
        return "cuda" if cuda else "cpu"

    # -- model construction -------------------------------------------------

    def _provider(self) -> str:
        return {"coreml": COREML_PROVIDER,
                "cuda": CUDA_PROVIDER}.get(self.backend, CPU_PROVIDER)

    def _model_configs(self, provider: str) -> dict:
        configs = {"provider": provider, "use_io_binding": False, "use_cache": False}
        if provider == COREML_PROVIDER:
            coreml_cache = self.model_dir / "coreml"
            coreml_cache.mkdir(exist_ok=True)
            configs["provider_options"] = {
                "ModelFormat": "MLProgram", "MLComputeUnits": "CPUAndGPU",
                "ModelCacheDirectory": str(coreml_cache),
            }
        elif provider == CUDA_PROVIDER:
            # A list, not the single `provider`: CUDA cannot run every operator
            # in these graphs, and without CPU behind it ONNX Runtime refuses
            # the ones it has to place elsewhere.
            configs.pop("provider")
            configs["providers"] = [CUDA_PROVIDER, CPU_PROVIDER]
            configs["provider_options"] = [{"device_id": 0}, {}]
        return configs

    def _torch_device(self) -> str:
        """The device Pix2Text hands to ``ORTModel.to()`` after construction.

        This is not cosmetic.  Optimum rebuilds the session around the device it
        is moved to, so leaving it at "cpu" on a CUDA machine hands back a CPU
        session however the providers were configured.
        """
        return "cuda" if self.backend == "cuda" else "cpu"

    def _load(self) -> None:
        """Build the recognizer and confirm it kept the requested provider."""
        from pix2text import TextFormulaOCR

        provider = self._provider()
        total_configs = {
            "languages": ["en"],
            "text": {"rec_root": str(self.model_dir / "cnocr"),
                     "det_root": str(self.model_dir / "cnstd")},
            "mfd": {"model_name": "mfd-1.5", "model_backend": "onnx",
                    "root": str(self.model_dir / "pix2text")},
            "formula": {"model_name": "mfr-1.5", "model_backend": "onnx",
                        "root": str(self.model_dir / "pix2text"),
                        "more_model_configs": self._model_configs(provider)},
        }
        self.ocr = TextFormulaOCR.from_config(
            total_configs=total_configs,
            # ONNX keeps its host tensors wherever this says. 'mps' does not
            # enable CoreML -- the provider above does that -- but 'cuda' is
            # required for the CUDA provider to survive Pix2Text's own
            # ``model.to(device)``. See _torch_device.
            device=self._torch_device(), enable_spell_checker=False,
        )
        for held in self.providers_in_use():
            if held != provider:
                raise RuntimeError(f"Formula model did not retain {provider} "
                                   f"(it is on {held}); reinstall the environment "
                                   "with setup.bat, or choose --device cpu.")
        self.detail = provider
        if provider == COREML_PROVIDER:
            self.detail += " (CPU and Apple GPU)"
        elif provider == CUDA_PROVIDER:
            self.detail += f" on {self._cuda_device_name()}"

    @staticmethod
    def _cuda_device_name() -> str:
        try:
            import torch

            return torch.cuda.get_device_name(0)
        except Exception:
            return "CUDA device 0"

    # -- use ----------------------------------------------------------------

    def providers_in_use(self) -> list[str]:
        """-> the provider each half of the formula model actually holds.

        Reported rather than the requested one, because the two differ exactly
        when something is wrong.
        """
        model = self.ocr.latex_ocr.model
        return [component.session.get_providers()[0]
                for component in (model.encoder, model.decoder)]

    def describe(self) -> str:
        return f"{self.backend} [{getattr(self, 'detail', '')}]".strip()

    def __call__(self, png: bytes) -> str:
        with self.image.open(io.BytesIO(png)) as image:
            results = self.ocr.recognize(image.convert("RGB"), return_text=False,
                                         contain_formula=True, mfr_batch_size=1,
                                         resized_shape=1024)
        # Escape only prose. Equations must retain commands such as \frac, the
        # subscript '_', and alignment '&' rather than being treated as text.
        escaped = []
        for result in results:
            row = dict(result)
            if row.get("type", "text") not in ("embedding", "isolated"):
                row["text"] = escape_text(row["text"])
            escaped.append(row)
        return self.merge(escaped, auto_line_break=False, line_sep="\n",
                          embed_sep=(r"\(", r"\)"),
                          isolated_sep=("\\[\n", "\n\\]"))

    def release(self) -> None:
        """Drop per-document scratch space without unloading the weights.

        Called between PDFs.  The models stay resident -- reloading them per
        file is the expensive mistake this exists to avoid -- but the CUDA
        blocks an unusually large page needed are returned to the driver.
        """
        if self.backend != "cuda":
            return
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
