# How to read the memory view

When you press **Execute** (F5), stacksim runs the whole program with its
inputs and records every step. Afterwards you can go through the recording with
**Play**, the step buttons or the timeline, in any direction, at any speed. At
every step, the right side of the window shows the complete memory of the
program at that moment.

## Executing and inputs

A program can get two kinds of input, both in the **Input** tab:

- **Command-line arguments** - if `main` is declared as `int main(int argc, char *argv[])`. Execute asks for them before the program starts (an empty list is fine). `argv[0]` is the program name, `./` plus the file name.
- **Keyboard input** - read by `scanf`, `getchar` and `fgets`. You can type it into the tab in advance; otherwise Execute asks for one line whenever the program wants to read more, showing what the program has printed so far. *End of input (EOF)* tells the program that there is no more.

After Execute, the inputs are **locked**: they belong to that run, and playing
it back always shows the result for exactly these inputs. When you execute a
program that uses input again, stacksim asks whether to use the same inputs or
to reset them and enter new ones. *Reset inputs* in the tab clears them (the
next Execute asks again), and *Edit inputs* unlocks them for changes. Changing
the code or the inputs makes the recorded run outdated: Play is unavailable
until you execute again.

## The explanation panel

The panel above the picture describes the current step:

- **Just ran line N** - the line that was executed to get from the previous step to this one, followed by what it did ("New variable x ...", "malloc(16) reserves ...", "The condition i < 3 is true ...").
- **Problems** found in this step, in orange (warnings) or red (crashes), each with a hint.
- **Next: line N** - the line that runs next. In the editor it is marked with a green arrow and a green background; the line that just ran has a grey arrow.

## The stack (left)

Each function call gets a **stack frame**: a box with the function's name, its
parameters and its local variables. `main()` is at the top; every call adds a
new frame below it, and the frame disappears again when the function returns.
The frame with the thick blue border belongs to the function that is running
right now. The other frames show the line where they are waiting for the
function they called.

Each variable is shown with its name, its type, its value and (on the left)
its address. Arrays are shown as a row of cells with their index above each
cell; structs as a box with one line per member. A char array that holds a
valid string also shows the string in quotes below its cells.

When a function is about to return, its frame shows the return value at the
bottom.

If `main` takes `argc` and `argv`, a **Command-line arguments** card sits above
`main()`'s frame, at the very top of the stack: the text of every argument and,
below it, the `argv` array with one pointer per argument and a `NULL` at the
end. The C runtime puts them there before `main()` starts; `main()`'s `argv`
parameter points to the array.

### Return address and saved frame pointer

*View > Show return addresses* (off by default) reveals the two 8-byte
bookkeeping slots at the top of every frame, in a shaded band above the
variables:

- **return address** - written by the call itself. It is the address in the
  calling function's code where the program continues after the call, shown as
  e.g. `0x40116a  return to main(), line 14`. Point at it to highlight that line
  in the editor. `main()` itself was called by the C runtime, so its return
  address leads back into the runtime, which then ends the program.
- **saved frame ptr** - written by the called function when it starts: a copy of
  the caller's frame pointer, restored when the function returns. The address
  of this slot is the frame's own frame pointer, so the saved copies link all
  frames into a chain; the grey arrows show that chain. In `main()` it is 0,
  because there is no frame above it.

When a call or a return happens, the explanation panel also describes what the
CPU did with these slots. If a local array overflows into them, they are marked
**OVERWRITTEN!** in red, and a *stack smashing* problem is reported when the
function returns. (A real program would now jump to a garbage address and
crash; stacksim reports it and continues as if the slots were intact.)

### Leftover stack values

When a block ends (for example a `for` loop with `int i`) or a function
returns, its variables no longer exist, and the stack pointer moves back up.
But the memory is not cleared: the old values stay where they were until a new
variable or function call uses that memory.

*View > Show leftover stack values* (off by default) shows this. A dashed
**stack pointer** line marks the end of the stack in use; everything below it
is free stack space. Below the line, faded, hatched cards show what is left:

- **"main() - ended block"**: variables of `main()` whose block has ended, such as a loop variable after the loop.
- **"square() - has returned"**: the leftover frame of a function that has returned, including its return address and saved frame pointer if *Show return addresses* is on.

A leftover disappears as soon as anything new uses any of its bytes. The
explanation panel says when that happens, e.g. "y is placed in the memory where
i (of main()) used to be - it has no value yet, so it shows i's leftover bytes".
A dangling pointer to a variable that no longer exists points to its leftover,
and writing through such a pointer changes the leftover, which is a good way to see
why it is undefined behavior. Leftovers are not variables: C does not let a
program use them, so they are shown only to explain what is in memory.

## Static data and heap (right)

**Static data** holds global variables, `static` variables and the string
literals that a pointer currently points to (for example `"Hello"` in
`const char *s = "Hello";`). These exist for the whole run of the program.

The **heap** shows every block reserved with `malloc`, `calloc`, `realloc` or
`strdup`, sorted by address. The header says how big the block is and in which
line it was allocated. As soon as the program converts the block's address to
a typed pointer (for example `int *p = malloc(...)`), the block is shown as
values of that type; before that it is shown as raw bytes.

- A **green** block is in use.
- An **orange** block is **leaked**: no pointer in the program leads to it any more, so it can never be freed.
- A **grey, hatched** block has been **freed**. It is still drawn so that you can see what dangling pointers point to. (*View > Show freed heap blocks* hides them.)

## Values, colours and arrows

| What you see | Meaning |
|---|---|
| yellow cell | this value changed in the step that just ran |
| light blue cell | this value was read (but not changed) in the step that just ran - including reads by library functions like `memcpy` or `printf("%s")`, and the return address when a function returns |
| grey cell with `?` | this memory was never given a value - it contains garbage (hover to see it) |
| red outline | memory involved in a problem in this step |
| blue dot with an arrow | a pointer, and what it points to |
| red dashed arrow | a dangling pointer (to freed memory or to a variable that no longer exists) or a pointer to memory that belongs to nothing |
| grey arrow between frames | a saved frame pointer, pointing to the caller's frame (only with *Show return addresses*) |
| faded, dashed row or card below the stack pointer | a leftover value of a variable that no longer exists (only with *Show leftover stack values*) |
| grey dashed arrow | a pointer stored inside a freed block |
| short line ending in a cross | the pointer points somewhere that is not shown |
| `NULL` | a pointer that points to nothing |

Hover over any value, variable or block for details: type, size, address and
the exact value. *View > Show raw bytes* adds the bytes of every value (in
memory order, little-endian), and *View > Show addresses* switches addresses
on or off. Hold **Ctrl** and turn the mouse wheel to zoom. *View > Theme*
switches between the dark theme (the default) and a light theme.

## Moving through the program

| Control | Shortcut | What it does |
|---|---|---|
| Execute | F5 | compile the program, run it with its inputs and record every step |
| Play / Pause | F6 | play the executed run forward at the chosen speed |
| Stop | Esc or Shift+F5 | pause the playback (or cancel an execution that takes too long) |
| Play backwards | Shift+F6 | play backward |
| Step / Back | F10 / Shift+F10 | one step forward / back |
| Step over / Back over | F11 / Shift+F11 | to the next / previous line of the current function, without going into the functions it calls |
| First step / Last step | Alt+Home / Alt+End | jump to the beginning / end |
| Timeline | | click or drag to jump anywhere; orange and red marks show problems |
| Breakpoints | click in the editor margin, or F9 | Play (and "Instant") stops at these lines |

The **Speed** box sets how fast Play runs; *Instant* jumps straight to the end
of the program (or to the next breakpoint). Execute itself never starts the
playback: after it, the run waits at its first step.

## What is simplified

The picture is meant to be easy to understand, not an exact copy of what a real
compiler does. Variables appear when their declaration runs (real compilers
reserve a whole frame at once), nothing is optimized away or kept in CPU
registers, and the memory layout is a simplified version of a 64-bit Linux
process. See *Help > Supported C and library functions* for the details.
