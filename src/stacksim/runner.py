"""Convenience entry point: compile and run C source in one call."""

from __future__ import annotations

import threading

from .c.compiler import CompileResult, compile_source
from .vm.interpreter import DEFAULT_MAX_FRAMES, DEFAULT_MAX_STEPS, execute
from .vm.trace import Trace


def run_source(
    source: str,
    stdin: bytes | str = b"",
    max_steps: int = DEFAULT_MAX_STEPS,
    cancel: threading.Event | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    input_provider=None,
    stdin_eof: bool = False,
    args: list[str] | None = None,
    prog_name: str = "./prog",
) -> tuple[CompileResult, Trace | None]:
    if isinstance(stdin, str):
        stdin = stdin.encode("utf-8")
    result = compile_source(source)
    if not result.ok:
        return result, None
    trace = execute(result.program, stdin, max_steps, cancel, source, max_frames, input_provider, stdin_eof, args, prog_name)
    return result, trace


def output_text(trace: Trace, step: int | None = None, streams=("stdout", "stderr")) -> str:
    chunks = trace.output if step is None else trace.output_at(step)
    return b"".join(data for s, data in chunks if s in streams).decode("utf-8", "replace")
