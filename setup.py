#!/usr/bin/env python3
"""Prepare PDF to LaTeX: .venv, PyTorch, dependencies, and frontend packages.

    setup.bat                     first run and every run after it
    setup.bat --force             reinstall dependencies and PyTorch
    setup.bat --recreate-venv     delete .venv and build it again
    setup.bat --skip-torch        leave PyTorch installation alone
    setup.bat --skip-frontend     do not install frontend npm packages
    setup.bat --verbose           show diagnostic detail
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
VENV_DIR = PROJECT_ROOT / ".venv"

sys.path.insert(0, str(BACKEND_ROOT))

from setup_tools import dependencies as deps            # noqa: E402
from setup_tools import environment as env              # noqa: E402
from setup_tools import hardware, pytorch, validation   # noqa: E402
from setup_tools.console import Console                 # noqa: E402
from setup_tools.environment import SetupError          # noqa: E402

REQUIREMENTS = BACKEND_ROOT / "requirements.txt"
REQUIREMENTS_MATH = BACKEND_ROOT / "requirements-math.txt"
REQUIREMENTS_ONNX_CPU = BACKEND_ROOT / "requirements-onnx-cpu.txt"
REQUIREMENTS_ONNX_CUDA = BACKEND_ROOT / "requirements-onnx-cuda.txt"
CONSTRAINTS = VENV_DIR / "pdfconv-constraints.txt"

TOTAL_STEPS = 9


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="setup.bat", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true",
                        help="reinstall dependencies and PyTorch even when the "
                             "installed versions already match")
    parser.add_argument("--recreate-venv", action="store_true",
                        help="delete .venv and build a fresh one")
    parser.add_argument("--skip-torch", action="store_true",
                        help="do not touch torch, torchvision or torchaudio")
    parser.add_argument("--skip-dependencies", action="store_true",
                        help="do not install python requirements files")
    parser.add_argument("--skip-frontend", action="store_true",
                        help="do not install frontend npm packages")
    parser.add_argument("--cpu", action="store_true",
                        help="install the CPU build even on an RTX machine")
    parser.add_argument("--verbose", action="store_true",
                        help="print pip and diagnostic detail")
    return parser.parse_args(argv)


def bootstrap(argv: list[str]) -> int | None:
    """Steps 1 and 2, then hand over to .venv interpreter."""
    args = parse_args(argv)
    console = Console(TOTAL_STEPS, args.verbose)
    inside = env.running_inside(VENV_DIR)

    if not inside:
        console.banner("Project Setup")
        console.step("Checking Python...")
        console.ok(env.check_python_version())

        console.step("Checking virtual environment...")
        interpreter, action = env.ensure_venv(VENV_DIR, recreate=args.recreate_venv)
        console.info(f"Using: {VENV_DIR}  ({action})")
        console.ok(f"Re-entering setup with {interpreter}")

        forwarded = [a for a in argv if a != "--recreate-venv"]
        return env.reenter(interpreter, Path(__file__).resolve(), forwarded)
    return None


def setup_frontend(console: Console, verbose: bool = False) -> None:
    if not FRONTEND_ROOT.exists():
        console.warn("Frontend directory not found. Skipping UI setup.")
        return

    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    npm_path = shutil.which(npm_cmd) or shutil.which("npm")
    if not npm_path:
        console.warn("Node.js/npm not found. Please install Node.js (v18+) to run the React Web UI.")
        return

    console.info(f"Using npm: {npm_path}")
    console.info("Running npm install in frontend...")
    try:
        subprocess.run([npm_path, "install"], cwd=str(FRONTEND_ROOT), check=True,
                       capture_output=not verbose, text=True)
        console.ok("Frontend packages installed.")
    except subprocess.CalledProcessError as err:
        console.warn(f"npm install failed: {err}")


def configure(console: Console, args: argparse.Namespace) -> int:
    console.step("Upgrading packaging tools...")
    deps.upgrade_packaging_tools()
    console.ok()

    console.step("Detecting GPU...")
    gpu = hardware.detect_nvidia_gpus()
    for line in hardware.describe_gpus(gpu):
        console.info(line)
    console.detail(f"nvidia-smi detail: {gpu.detail or 'none'}")
    use_cuda = gpu.has_rtx and not args.cpu
    if args.cpu and gpu.has_rtx:
        console.info("--cpu given: installing the CPU build anyway.")
    print()

    wanted = pytorch.wanted_versions(use_cuda)

    console.step("Configuring PyTorch...")
    if args.skip_torch:
        console.ok("skipped (--skip-torch)")
    else:
        action = pytorch.install(wanted, cuda=use_cuda, force=args.force)
        console.info(action)
        for name, pin in wanted.items():
            console.info(f"{name:<12}{pin}")
        console.ok()

    console.step("Installing project dependencies...")
    if args.skip_dependencies:
        console.ok("skipped (--skip-dependencies)")
    else:
        constraints = deps.write_constraints(
            CONSTRAINTS, {name: pytorch.installed_version(name) or pin
                          for name, pin in wanted.items()})
        console.detail(f"constraints: {constraints}")
        for requirements in (REQUIREMENTS, REQUIREMENTS_MATH):
            console.info(f"{requirements.name}")
            deps.install_requirements(requirements, constraints=constraints,
                                      upgrade=args.force)
        onnx_requirements = REQUIREMENTS_ONNX_CUDA if use_cuda else REQUIREMENTS_ONNX_CPU
        console.info(f"{onnx_requirements.name}")
        console.info(deps.select_onnxruntime(onnx_requirements, cuda=use_cuda,
                                             constraints=constraints))
        broken = deps.import_check("onnxruntime")
        if broken:
            raise SetupError("ONNX Runtime is not importable after installation "
                             f"({broken}). Re-run setup.bat --force.")
        console.ok()

    console.step("Verifying CUDA...")
    if args.skip_torch and not pytorch.already_correct(wanted):
        console.ok("skipped (--skip-torch)")
        report = None
    else:
        report = pytorch.verify(wanted, cuda=use_cuda)
        for line in report.lines:
            console.info(line)
        if report.problems:
            for problem in report.problems:
                console.warn(problem)
        print()

    console.step("Checking PDF conversion dependencies...")
    traditional = validation.check_traditional(str(BACKEND_ROOT))
    console.info(f"Traditional converter: {'READY' if traditional.ok else 'NOT READY'}")
    if traditional.ok:
        console.detail(traditional.detail)
    else:
        console.warn(f"Traditional converter: {traditional.detail}")

    cuda_ready = report.cuda_available if report else use_cuda
    ocr, backend = validation.check_pdf2tex(str(BACKEND_ROOT), cuda=cuda_ready)
    console.info(f"pdf2tex converter: {'READY' if ocr.ok else 'NOT READY'}")
    if ocr.ok:
        console.info(f"pdf2tex device: {'cuda' if backend.startswith('cuda') else 'cpu'}"
                     f" ({backend})")
        console.detail(ocr.detail)
        if use_cuda and not backend.startswith("cuda"):
            console.warn("An RTX GPU is present but the OCR fallback would run on "
                         "the CPU. Conversions of scanned PDFs will be very slow.")
    else:
        console.warn(f"pdf2tex converter: {ocr.detail}")

    for name, found, why in deps.check_external_tools():
        console.info(f"{name}: {found or 'not found -- ' + why}")

    console.step("Configuring Frontend UI...")
    if args.skip_frontend:
        console.ok("skipped (--skip-frontend)")
    else:
        setup_frontend(console, verbose=args.verbose)

    console.finish()
    print()
    print("Start the Web UI & Backend with:")
    print("  run.bat (Windows) or ./run.sh (Linux/macOS)")
    print()
    print("Or convert PDFs via CLI with:")
    print("  convert_pdfs.bat (Windows) or ./convert_pdfs.sh (Linux/macOS)")
    return 1 if console.warnings else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(OSError, ValueError, AttributeError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        status = bootstrap(argv)
    except SetupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if status is not None:
        return status

    args = parse_args(argv)
    console = Console(TOTAL_STEPS, args.verbose)

    console.banner("Project Setup")
    console.step("Checking Python...")
    console.ok(env.check_python_version())

    console.step("Checking virtual environment...")
    console.info(f"Using: {VENV_DIR}")
    console.ok(f"Interpreter: {sys.executable}")

    try:
        return configure(console, args)
    except SetupError as error:
        console.fail(str(error))
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run setup.bat; completed steps are not repeated.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
