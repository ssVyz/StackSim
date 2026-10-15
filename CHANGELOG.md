# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.5] - 2026-10-15

### Added

- Interactive input: when a program reads more than the Input (stdin) tab contains (`scanf`, `getchar`, `fgetc`/`getc`, `fgets`), a dialog asks for one line while the run is recorded. It shows the waiting call, the line and the program's last output (e.g. the prompt it printed), and warns when a `scanf` format ends with whitespace. Answers are appended to the Input tab and reused in later runs; the dialog can also end the input (EOF) or stop the program.
- Input tab: *Clear input* (also *Run > Clear saved input*) to be asked again, and *Input ends here (EOF)* to give the program end of input instead of asking; both are remembered.
- Explanations in the step log when typed input is used ("You typed ... as input for scanf()").
- `input_provider` and `stdin_eof` options for `run_source`/`execute`, and tests for interactive input.

### Changed

- The "Reading input with scanf" example starts without saved input, so its first run asks for the numbers, and it initializes its variables.

## [0.0.4] - 2026-10-15

### Added

- *View > Show leftover stack values* (off by default, remembered): a dashed stack-pointer line marks the end of the stack in use; below it, faded cards show the leftover bytes of variables whose block has ended ("main() - ended block") and of functions that have returned ("f() - has returned"), until a new variable or call reuses the memory. Dangling pointers to dead stack variables now point at these leftovers, and writes through them visibly change the leftover.
- Explanations when values become leftovers and when new variables or frames take over their memory (e.g. "y is placed in the memory where i used to be - it has no value yet, so it shows i's leftover bytes").
- *Run > Frame limit*: a program may nest at most 15 function calls by default (2 to 200), so recursion stays small enough to follow; deeper calls end in a stack overflow that explains the limit. The limit is also available as `max_frames` in `run_source`/`execute`.
- Tests for leftover values, the frame limit and the new view option.

### Changed

- Stack frames are no longer collapsed into "... more stack frames" in the app; every frame is drawn (collapsing remains only as a safety net above 250 frames).

## [0.0.3] - 2026-10-15

### Added

- *View > Show return addresses* (off by default, remembered): every stack frame shows its return address and saved frame pointer in a band at the top. Return addresses are labelled with the place they lead back to (e.g. "return to main(), line 14") and highlight that line in the editor on hover; saved frame pointers are drawn as a chain of arrows between frames; overwritten slots are marked in red.
- Explanations of what the CPU does with these slots at every call and return (shown when the option is on).
- Out-of-bounds messages name the return address or saved frame pointer when an overflow hits them; stack smashing is reported separately for each slot, with the address the return would have jumped to.
- Tests for frame layout, return addresses, the frame-pointer chain and the new view option.

### Changed

- Stack frames follow the x86-64 layout: the return address sits above the saved frame pointer, and the stack is 16-byte aligned at each call.
- Each function's code now gets an address range in which every source line has its own addresses, so return addresses map back to the line of the call; `main()` returns into a small C runtime stub, and callbacks from `qsort` return into the library.

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
