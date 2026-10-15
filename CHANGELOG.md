# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2] - 2026-10-15

### Added

- C front end: lexer, preprocessor (`#include` of built-in headers, `#define` with function-like macros and `#`, `#if`/`#ifdef` family), recursive-descent parser and semantic checker for a large teaching-oriented C subset (all basic types, pointers, arrays, structs, unions, enums, typedefs, function pointers, initializer lists with designators, `switch`, recursion, global and static variables).
- Beginner-friendly compile errors and warnings with hints (missing semicolons, missing `#include`, "did you mean", `.` vs. `->`, array assignment, `=` in conditions, `printf`/`scanf` format checks including a missing `&`, returning the address of a local variable).
- Virtual machine with a byte-addressed 64-bit-Linux-like memory model (LP64 sizes, little-endian, struct padding, code/rodata/data/heap/stack regions), a first-fit heap allocator with block headers, and pointer provenance tracking.
- Run-time detection of memory problems that warns and continues: out-of-bounds access (naming the overwritten variable), use after free, dangling pointers, double and invalid free, uninitialized reads, buffer overflows in library functions, unterminated strings, writes to string literals, signed overflow, stack smashing, heap corruption and memory leaks. Crashes are reported for NULL dereference, segmentation faults, division by zero, stack overflow, `abort`/`assert` and the step limit.
- C standard library subset: `stdio.h` (printf/scanf families, `puts`, `getchar`, `fgets`, ...), `stdlib.h` (`malloc`, `calloc`, `realloc`, `free`, `qsort`, glibc-compatible `rand`, ...), `string.h`, `ctype.h`, `math.h`, `assert.h`, `limits.h`, `stdint.h`, `stddef.h`, `stdbool.h`, `time()`.
- Step-by-step trace recording with per-step explanations; memory can be reconstructed at any step by replaying undo/redo logs.
- PySide6 GUI: code editor with syntax highlighting, breakpoints and execution markers; output, stdin and problems panels; memory view with stack frames, static data, heap blocks (live, leaked, freed), changed values, uninitialized values and pointer arrows (dangling pointers in red); playback forward and backward at adjustable speed, single steps, step over, timeline scrubbing; optional addresses and raw bytes; light and dark themes.
- Twenty example programs, each demonstrating one memory concept or bug.
- In-app documentation of the supported C subset, the library and the memory view (`src/stacksim/docs/`); README with usage and architecture.
- pytest test suite.

### Changed

- Converted the project to a package (`src/stacksim`) with a `stacksim` console script; `main.py` now launches the app.

## [0.0.1] - 2026-10-15

### Added

- Initial project scaffold created with `uv init`.
- `CHANGELOG.md` and `CLAUDE.md` with repository rules for versioning and agent workflow.
