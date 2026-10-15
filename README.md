# StackSim

**StackSim** is a teaching tool for people who are new to C or to manual memory
management. You write a small C program, press **Run**, and stacksim executes it
in a simulated computer, recording every step. You can then play the program
forwards and backwards, in slow motion or line by line, and watch what happens
in memory:

- the **stack**, with one frame per function call and every local variable, and
  optionally each frame's return address and saved frame pointer and the
  leftover values that dead variables leave behind below the stack pointer,
- the **heap**, with every block from `malloc`/`calloc`/`realloc`,
- **static data** (globals, `static` variables, string literals),
- **pointers** as arrows to what they point to.

Memory bugs are caught where they happen and explained in plain language:
out-of-bounds writes (including which variable gets overwritten), use after
free, dangling pointers, double free, uninitialized variables, buffer
overflows, memory leaks, NULL dereferences, stack overflows and more.

The simulation is meant to teach, not to be exact. It is modelled on a 64-bit
Linux system: real sizes, real addresses, little-endian bytes, struct padding,
and a malloc with block headers. It is simplified where that makes things easier
to follow.

## Running it

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.13 and PySide6 for you).

```sh
uv run stacksim            # start the app
uv run stacksim prog.c     # start the app and open a file
```

Start with the **Examples** menu. Each example demonstrates one idea, such as
stack frames, pass-by-pointer, linked lists, memory leaks, use after free or
stack overflow.

## Using it

| | |
|---|---|
| **Run** (F5) | compile, run and record the program |
| **Play / Pause** (F6), **Play backwards** (Shift+F6) | play at the speed chosen in the toolbar, from *very slow* to *instant* |
| **Step** (F10), **Back** (Shift+F10) | one step forward or back |
| **Step over** (F11), **Back over** (Shift+F11) | the next or previous line of the current function, skipping over calls |
| **Timeline** | drag to any point of the run; marks show where problems occurred |
| **Breakpoints** | click in the editor margin (or press F9); playback stops there |
| **Input (stdin)** tab | the text that `scanf`, `getchar` and `fgets` read |
| **Problems** tab | every compile and run-time problem; click one to jump to its step |

Inside the app, *Help > How to read the memory view* explains the picture, and
*Help > Supported C and library functions* lists exactly what is implemented.
The same texts are in [`src/stacksim/docs/`](src/stacksim/docs/).

## What C is supported?

Most of the C that beginners write. That covers all basic types, pointers,
arrays, structs, unions, enums, typedefs, function pointers, all operators and
control structures, recursion, global and static variables, initializer lists,
and a preprocessor with `#include`, `#define` and `#if`. The library side
covers the common parts of `stdio.h`, `stdlib.h`, `string.h`, `ctype.h` and
`math.h`, plus `assert.h`, `limits.h`, `stdint.h`, `stddef.h`, `stdbool.h` and
`time()`.

Not supported: multiple files, `goto`, variable-length arrays, file I/O, your
own variadic functions, and a few rarer features. See
[supported_c.md](src/stacksim/docs/supported_c.md) for the full list.

## Development

```sh
uv run pytest              # run the test suite
```

| Path | Contents |
|---|---|
| `src/stacksim/c/` | front end: lexer, preprocessor, built-in headers, parser, semantic checker |
| `src/stacksim/vm/` | virtual machine: memory, heap allocator, interpreter, C library, trace recording |
| `src/stacksim/viewmodel.py` | turns one step of a trace into a description of what to draw |
| `src/stacksim/gui/` | PySide6 user interface: editor, memory canvas, timeline, main window, examples |
| `tests/` | pytest suite |

How it works: the C source is preprocessed, parsed and type-checked into an
annotated syntax tree. The interpreter executes it on a byte-addressed virtual
memory. Before every statement it records a step: the memory writes since the
previous step (as undo/redo entries), explanations of what happened, and any
problems found. The GUI moves to any step by replaying those writes forwards
or backwards, then decodes the memory into frames, variables, heap blocks and
pointer arrows.

See [CHANGELOG.md](CHANGELOG.md) for the version history.

## AI disclosure

This program is made using agentic programming with claude code. (mostly Opus 5.5)
