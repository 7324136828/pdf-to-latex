#!/usr/bin/env python3
"""Prepare this project: .venv, dependencies, the right PyTorch, and a check.

    setup.bat                     first run and every run after it
    setup.bat --force             reinstall dependencies and PyTorch
    setup.bat --recreate-venv     throw .venv away and build it again
    setup.bat --skip-torch        leave the PyTorch installation alone
    setup.bat --verbose           show the diagnostic detail

Started with any supported system Python, it creates ``.venv`` beside this
project and re-runs itself with that interpreter; everything after that point
installs into the environment and never touches the system Python.  Nothing has
to be activated afterwards -- the launchers name ``.venv``'s interpreter.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PYTHON_ROOT.parent
sys.path.insert(0, str(PYTHON_ROOT))

from setup_tools import dependencies as deps            # noqa: E402
from setup_tools import environment as env              # noqa: E402
from setup_tools import hardware, pytorch, validation   # noqa: E402
from setup_tools.console import Console                 # noqa: E402
from setup_tools.environment import SetupError          # noqa: E402

VENV_DIR = PROJECT_ROOT / ".venv"
REQUIREMENTS = PYTHON_ROOT / "requirements.txt"
REQUIREMENTS_MATH = PYTHON_ROOT / "requirements-math.txt"
REQUIREMENTS_ONNX_CPU = PYTHON_ROOT / "requirements-onnx-cpu.txt"
REQUIREMENTS_ONNX_CUDA = PYTHON_ROOT / "requirements-onnx-cuda.txt"
CONSTRAINTS = PROJECT_ROOT / ".venv" / "pdfconv-constraints.txt"

TOTAL_STEPS = 8


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
                        help="do not install the requirements files")
    parser.add_argument("--cpu", action="store_true",
                        help="install the CPU build even on an RTX machine")
    parser.add_argument("--verbose", action="store_true",
                        help="print pip and diagnostic detail")
    return parser.parse_args(argv)


def bootstrap(argv: list[str]) -> int | None:
    """Steps 1 and 2, then hand over to ``.venv``'s interpreter.

    -> the child's exit status when this process was the bootstrap one, or
    ``None`` when we are already inside the environment and should carry on.
    """
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

        # --recreate-venv has been honoured; the child must not do it again.
        forwarded = [a for a in argv if a != "--recreate-venv"]
        return env.reenter(interpreter, Path(__file__).resolve(), forwarded)
    return None


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Paths and pip output may carry characters a legacy console cannot encode.
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

    # The bootstrap process printed steps 1 and 2 before handing over; repeat
    # them here as confirmations so one continuous log describes one setup.
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
        # Freeze the three PyTorch packages for the rest of the installation so
        # a transitive dependency cannot pull the CPU build over the CUDA one.
        constraints = deps.write_constraints(
            CONSTRAINTS, {name: pytorch.installed_version(name) or pin
                          for name, pin in wanted.items()})
        console.detail(f"constraints: {constraints}")
        for requirements in (REQUIREMENTS, REQUIREMENTS_MATH):
            console.info(f"{requirements.name}")
            deps.install_requirements(requirements, constraints=constraints,
                                      upgrade=args.force)
        # The ONNX Runtime build is the second hardware-dependent choice, after
        # PyTorch: only onnxruntime-gpu carries the CUDAExecutionProvider the
        # formula recognizer needs, and the two distributions cannot coexist.
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
    traditional = validation.check_traditional(str(PYTHON_ROOT))
    console.info(f"Traditional converter: {'READY' if traditional.ok else 'NOT READY'}")
    if traditional.ok:
        console.detail(traditional.detail)
    else:
        console.warn(f"Traditional converter: {traditional.detail}")

    cuda_ready = report.cuda_available if report else use_cuda
    ocr, backend = validation.check_pdf2tex(str(PYTHON_ROOT), cuda=cuda_ready)
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

    console.finish()
    print()
    print("Convert PDFs with:")
    print(f'  "{env.venv_python(VENV_DIR)}" "{PYTHON_ROOT / "convert_pdfs.py"}"')
    print("or simply:  convert_pdfs.bat")
    # A warning here means the environment is usable but not what was asked for
    # -- an RTX card without CUDA, most importantly -- so it is not a success.
    return 1 if console.warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
