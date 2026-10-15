# Supported C in stacksim

stacksim runs **one C file** in a simulated computer. It implements the part of
C that matters when learning about variables, functions, pointers and memory,
plus the most commonly taught parts of the standard library. Everything runs
inside the simulator: no real compiler is involved and nothing touches your
computer's memory or files.

## Language

### Supported

| Area | What works |
|---|---|
| Types | `char`, `signed`/`unsigned char`, `short`, `int`, `long`, `long long` (and their `unsigned` forms), `float`, `double`, `long double` (treated as `double`), `bool`/`_Bool`, `void`, `enum` |
| Derived types | pointers (also pointers to pointers and to functions), arrays (also multi-dimensional), `struct`, `union`, `typedef` |
| Declarations | anywhere in a block, in `for (int i = 0; ...)`, several per line (`int a = 1, *p = &a;`), global and `static` variables (also `static` locals), `extern`, function prototypes |
| Initializers | scalars, nested braces, designated initializers (`.x = 1`, `[3] = 7`), strings into char arrays (`char s[] = "hi";`), array length from the initializer, brace elision (`int m[2][2] = {1, 2, 3, 4};`) |
| Statements | blocks, `if`/`else`, `while`, `do`/`while`, `for`, `switch`/`case`/`default` (with fall-through), `break`, `continue`, `return` |
| Operators | all arithmetic, bitwise, logical, comparison and assignment operators (including `+=` etc.), `++`/`--`, `?:`, the comma operator, casts, `sizeof`, `&`, `*`, `[]`, `.`, `->`, function calls (also through function pointers), recursion |
| Preprocessor | `#include <header.h>` for the built-in headers below, `#define` (object-like and function-like, with `#` stringification), `#undef`, `#if`/`#ifdef`/`#ifndef`/`#elif`/`#else`/`#endif`, `defined(...)`, `__LINE__`, `__FILE__`, `#error`; `#pragma` is ignored |
| C23 conveniences | `bool`, `true` and `false` work without `#include <stdbool.h>` |

`const`, `volatile`, `restrict`, `inline`, `register` and `auto` are accepted
but have no effect (in particular, `const` is not enforced).

### Not supported

- More than one file, and `#include "myheader.h"`
- `goto` and labels
- Variable-length arrays (`int a[n];` with a variable `n`) - use `malloc` instead
- Compound literals (`(struct point){1, 2}`), flexible array members, bit-fields, anonymous struct/union members
- Your own variadic functions (`...` with `<stdarg.h>`), old K&R-style function definitions
- The `##` operator and variadic macros
- Taking the address of a library function (e.g. passing `free` as a function pointer)
- `case` labels nested inside other statements (Duff's device)
- Wide characters, complex numbers, `_Generic`, `_Atomic`, threads, `setjmp`/`longjmp`, signals, inline assembly

## Standard library

Only the headers below can be included. A function is available after its
header has been included; forgetting the `#include` produces an error with a
hint naming the right header.

| Header | Functions and macros |
|---|---|
| `<stdio.h>` | `printf`, `fprintf`, `sprintf`, `snprintf`, `puts`, `putchar`, `fputs`, `fputc`, `putc`, `scanf`, `sscanf`, `getchar`, `fgetc`, `getc`, `fgets`, `fflush`, `perror`; `stdin`, `stdout`, `stderr`, `EOF`, `NULL`, `FILE`, `size_t` |
| `<stdlib.h>` | `malloc`, `calloc`, `realloc`, `free`, `exit`, `abort`, `abs`, `labs`, `atoi`, `atol`, `atof`, `strtol`, `strtod`, `rand`, `srand`, `qsort`; `EXIT_SUCCESS`, `EXIT_FAILURE`, `RAND_MAX`, `NULL` |
| `<string.h>` | `strlen`, `strcpy`, `strncpy`, `strcat`, `strncat`, `strcmp`, `strncmp`, `strchr`, `strrchr`, `strstr`, `strdup`, `memcpy`, `memmove`, `memset`, `memcmp` |
| `<ctype.h>` | `isalpha`, `isdigit`, `isalnum`, `isspace`, `isupper`, `islower`, `ispunct`, `isprint`, `isxdigit`, `toupper`, `tolower` |
| `<math.h>` | `sqrt`, `pow`, `fabs`, `floor`, `ceil`, `round`, `trunc`, `fmod`, `fmin`, `fmax`, `sin`, `cos`, `tan`, `atan`, `atan2`, `exp`, `log`, `log10`, `log2`, `hypot`; `M_PI`, `M_E`, `INFINITY`, `NAN` |
| `<stdbool.h>` | (nothing needed - `bool`, `true`, `false` are built in) |
| `<stddef.h>` | `size_t`, `ptrdiff_t`, `NULL` |
| `<stdint.h>` | `int8_t` ... `int64_t`, `uint8_t` ... `uint64_t`, `intptr_t`, `uintptr_t`, `INT32_MAX` etc., `SIZE_MAX` |
| `<limits.h>` | `CHAR_BIT`, `CHAR_MIN`/`MAX`, `SCHAR_*`, `UCHAR_MAX`, `SHRT_*`, `USHRT_MAX`, `INT_MIN`/`MAX`, `UINT_MAX`, `LONG_*`, `ULONG_MAX`, `LLONG_*`, `ULLONG_MAX` |
| `<assert.h>` | `assert` |
| `<time.h>` | `time`, `time_t` |

### Details and differences from a real C library

- **printf family:** conversions `%d %i %u %o %x %X %c %s %f %F %e %E %g %G %a %A %p %%`, flags `- + space # 0`, width and precision (also `*`), length modifiers `hh h l ll z j t L`. `%n` is ignored. `%s` of `NULL` prints `(null)`, `%p` of `NULL` prints `(nil)`, like glibc.
- **scanf family:** `%d %i %u %o %x %X %f %e %g %c %s %[...] %p %n %%`, with widths, `*` (skip) and length modifiers. Input comes from the **Input (stdin)** tab, which has to be filled in before pressing Run.
- `fgets`, `fgetc` and `getc` can only read from `stdin`; `fprintf`, `fputs` and `fputc` can write to `stdout` and `stderr` (shown in red). There are no files.
- Output appears immediately; there is no output buffering.
- `rand()` produces exactly the same numbers as glibc (Linux). `time()` always returns `1700000000`, so every run is reproducible.
- `strcmp`, `strncmp` and `memcmp` return -1, 0 or 1.
- `perror(s)` prints `s: Error` (there is no `errno`).
- `math.h` functions take and return `double` (there are no `sqrtf`-style float versions).

### Not included

File I/O (`fopen`, `fread`, ...), `errno`, `getenv`, `system`, `bsearch`,
`strtok`, `sprintf`-style functions with `va_list`, locale and wide-character
functions, `<signal.h>`, `<setjmp.h>`, threads, and all time functions except
`time()`.

## The simulated machine

stacksim behaves like a typical 64-bit Linux system (x86-64, gcc), simplified
where that makes memory easier to understand.

| Type | Size (bytes) |
|---|---|
| `char`, `bool` | 1 |
| `short` | 2 |
| `int`, `float`, `enum` | 4 |
| `long`, `long long`, `double`, `size_t`, pointers | 8 |

- Values are stored **little-endian**; structs get the usual alignment padding (`struct { char c; int i; }` has 8 bytes).
- **Memory layout:**

  | Address | Region |
  |---|---|
  | `0x00000000` | unused (NULL page) - any access crashes |
  | `0x00401000` | code: the addresses of your functions |
  | `0x00402000` | read-only data: string literals |
  | `0x00404000` | data: global and static variables |
  | `0x00405000` and up | heap: grows upwards |
  | below `0x7ffff000` | stack: grows downwards, 128 KiB |

- **Stack:** every function call reserves 16 bytes for the return address and the saved frame pointer, then its parameters and local variables. A variable appears when its declaration runs and disappears when its block ends. (Real compilers reserve a whole frame at once when the function starts; stacksim shows the step-by-step view because it is easier to follow.) The stack holds 128 KiB, much less than the usual 8 MiB, so that endless recursion fails quickly.
- **Heap:** a first-fit allocator. Every block has a 16-byte header in front of it and starts on a 16-byte boundary; freed neighbouring blocks are merged, and freed memory is reused. The heap can grow to 16 MiB; after that `malloc` returns `NULL`.
- **Uninitialized memory** (local variables without a value, memory from `malloc`) contains garbage. On a real system fresh heap memory is often all zeros, but you cannot rely on that; stacksim always shows garbage so the mistake is visible. Stack memory keeps the values of earlier, finished function calls - also like on a real system.
- **Freed memory** keeps its old content. (A real allocator overwrites the first bytes with its own bookkeeping data.)
- Each run is limited to 50,000 steps (changeable in *Run > Step limit*), to catch endless loops.

## Problems that stacksim detects

**Before the program runs** (compile errors and warnings): syntax errors
explained in plain language, undeclared names (with "did you forget
`#include <...>`?" and "did you mean ...?" hints), type errors such as using an
integer as a pointer, assigning to an array, `.` vs. `->` mix-ups, wrong
numbers of arguments, `printf`/`scanf` format mismatches (including a missing
`&` in `scanf`), `=` used in a condition, returning the address of a local
variable, and functions that can end without returning a value.

**While the program runs**, problems are reported at the exact step where they
happen. The program then continues, so you can watch the consequences:

- reading or writing outside an array or a heap block (and which variable gets overwritten)
- use after free, dangling pointers to variables of functions that have returned
- double free, freeing something that did not come from `malloc`
- reading variables that were never given a value
- buffer overflows in `strcpy`, `strcat`, `sprintf`, `scanf("%s")`, `memcpy`, ...
- strings without a terminating `'\0'`
- writing into a string literal
- signed integer overflow, invalid shift amounts
- stack smashing (an overwritten return address) and heap corruption (an overwritten malloc header)
- memory leaks: blocks that become unreachable are marked in the picture as soon as it happens, and all unfreed blocks are listed when the program ends

Some problems stop the program, just like a crash would: dereferencing `NULL`,
using a garbage pointer (segmentation fault), integer division by zero, stack
overflow, `abort()`, a failed `assert`, and reaching the step limit.
