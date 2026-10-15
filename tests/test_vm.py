"""The virtual machine: C semantics, library functions, runtime problems, time travel."""

from __future__ import annotations

import pytest

from conftest import kinds, main, out, run_ok
from stacksim.runner import output_text
from stacksim.vm.trace import Cursor


def p(body: str, stdin: str = "", headers=("stdio.h", "stdlib.h", "string.h")) -> str:
    return out(main(body, headers), stdin)


# ------------------------------------------------------------ arithmetic


def test_integer_division_truncates_toward_zero():
    assert p('printf("%d %d %d %d\\n", 7 / 2, -7 / 2, 7 % 3, -7 % 3); return 0;') == "3 -3 1 -1\n"


def test_unsigned_wraparound():
    assert p('unsigned int u = 0; u = u - 1; printf("%u\\n", u); return 0;') == "4294967295\n"


def test_unsigned_char_wrap():
    assert p('unsigned char c = 250; c += 10; printf("%d\\n", c); return 0;') == "4\n"


def test_signed_overflow_warns_and_wraps():
    _, tr = run_ok(main('int x = 2147483647; x = x + 1; printf("%d\\n", x); return 0;'))
    assert output_text(tr) == "-2147483648\n"
    assert "overflow" in kinds(tr)


def test_char_arithmetic():
    assert p("char c = 'a'; c = c + 2; printf(\"%c %d\\n\", c, c); return 0;") == "c 99\n"


def test_float_vs_double_precision():
    assert p('float f = 0.1f; double d = 0.1; printf("%.10f %.10f\\n", f, d); return 0;') == "0.1000000015 0.1000000000\n"


def test_int_to_double_conversion_and_casts():
    assert p('int a = 7, b = 2; double r = (double)a / b; printf("%.2f %d\\n", r, (int)3.99); return 0;') == "3.50 3\n"


def test_shifts_and_bitwise():
    assert p('printf("%d %d %d %d %d\\n", 1 << 4, 256 >> 2, 6 & 3, 6 | 3, 6 ^ 3); return 0;') == "16 64 2 7 5\n"


def test_logical_short_circuit():
    src = main('int n = 0; int *p = NULL; if (p != NULL && *p == 1) n = 1; if (n == 0 || *p) n = 2; printf("%d\\n", n); return 0;')
    assert out(src) == "2\n"


def test_increment_decrement():
    assert p('int i = 5; int a = i++; int b = ++i; int c = i--; printf("%d %d %d %d\\n", a, b, c, i); return 0;') == "5 7 7 6\n"


def test_compound_assignment_with_double():
    assert p('int x = 10; x += 2.5; x *= 2; printf("%d\\n", x); return 0;') == "24\n"


def test_ternary_and_comma():
    assert p('int a = 3; int b = (a > 2) ? 10 : 20; int c = (a++, a * 2); printf("%d %d\\n", b, c); return 0;') == "10 8\n"


# ---------------------------------------------------------------- printf


@pytest.mark.parametrize(
    "fmt,args,expected",
    [
        ("[%5d]", "42", "[   42]"),
        ("[%-5d]|", "42", "[42   ]|"),
        ("[%05.1f]", "3.14159", "[003.1]"),
        ("%x %X", "255, 255", "ff FF"),
        ("%#x", "255", "0xff"),
        ("%o", "8", "10"),
        ("%#o", "8", "010"),
        ("%c%c", "'h', 105", "hi"),
        ("%s!", '"hey"', "hey!"),
        ("%.2s", '"hello"', "he"),
        ("100%%", "", "100%"),
        ("%ld", "1234567890123L", "1234567890123"),
        ("%lu", "18446744073709551615UL", "18446744073709551615"),
        ("%zu", "sizeof(int)", "4"),
        ("%+d % d", "5, 5", "+5  5"),
        ("%e", "12345.678", "1.234568e+04"),
        ("%g", "0.0001", "0.0001"),
        ("%10.3f|", "2.5", "     2.500|"),
        ("%*d", "4, 7", "   7"),
    ],
)
def test_printf_formats(fmt, args, expected):
    call = f'printf("{fmt}"{", " + args if args else ""});'
    assert p(call + " return 0;") == expected


def test_printf_null_string_and_nil_pointer():
    assert p('char *s = NULL; printf("%s %p", s, (void *)0); return 0;') == "(null) (nil)"


def test_printf_pointer_is_hex_address():
    text = p('int x; printf("%p", (void *)&x); return 0;')
    assert text.startswith("0x7fff")


def test_puts_and_putchar():
    assert p('puts("line"); putchar(65); putchar(10); return 0;') == "line\nA\n"


def test_fprintf_stderr_separate_stream():
    _, tr = run_ok(main('fprintf(stderr, "oops\\n"); fprintf(stdout, "fine\\n"); return 0;'))
    assert output_text(tr, streams=("stderr",)) == "oops\n"
    assert output_text(tr, streams=("stdout",)) == "fine\n"


def test_sprintf_and_snprintf():
    assert p('char b[32]; sprintf(b, "%d-%s", 7, "x"); char c[4]; int n = snprintf(c, sizeof(c), "%s", "abcdef"); printf("%s %s %d", b, c, n); return 0;') == "7-x abc 6"


# --------------------------------------------------------------- structs


def test_struct_copy_and_nested():
    src = """#include <stdio.h>
struct inner { int a; int b; };
struct outer { struct inner in; char tag; };
int main(void) {
    struct outer x = {{1, 2}, 'q'};
    struct outer y = x;
    y.in.a = 9;
    printf("%d %d %c %d\\n", x.in.a, x.in.b, y.tag, y.in.a);
    return 0;
}
"""
    assert out(src) == "1 2 q 9\n"


def test_array_of_structs_and_designated_init():
    src = """#include <stdio.h>
struct pt { int x; int y; };
int main(void) {
    struct pt pts[3] = {{1, 2}, [2] = {.x = 5, .y = 7}};
    struct pt q = {.y = 4};
    printf("%d %d %d %d %d %d %d\\n", pts[0].x, pts[0].y, pts[1].x, pts[2].x, pts[2].y, q.x, q.y);
    return 0;
}
"""
    assert out(src) == "1 2 0 5 7 0 4\n"


def test_designators_out_of_order():
    src = """#include <stdio.h>
struct pt { int x; int y; };
int main(void) {
    struct pt q = {.y = 7, .x = 5};
    printf("%d %d\\n", q.x, q.y);
    return 0;
}
"""
    assert out(src) == "5 7\n"


def test_brace_elision_2d_array():
    assert p('int m[2][3] = {1, 2, 3, 4, 5, 6}; printf("%d %d %d\\n", m[0][2], m[1][0], m[1][2]); return 0;') == "3 4 6\n"


def test_unsized_array_from_initializer():
    assert p('int a[] = {4, 5, 6, 7}; char s[] = "hey"; printf("%zu %zu\\n", sizeof(a), sizeof(s)); return 0;') == "16 4\n"


def test_struct_pass_and_return_by_value():
    src = """#include <stdio.h>
struct v { int x; int y; };
struct v add(struct v a, struct v b) { struct v r = {a.x + b.x, a.y + b.y}; return r; }
int main(void) {
    struct v a = {1, 2}, b = {10, 20};
    struct v c = add(a, b);
    printf("%d %d %d\\n", c.x, c.y, add(a, a).y);
    return 0;
}
"""
    assert out(src) == "11 22 4\n"


def test_union_shares_memory():
    assert p('union u { int i; unsigned char c[4]; } x; x.i = 0x01020304; printf("%d %d\\n", x.c[0], x.c[3]); return 0;') == "4 1\n"


def test_enum_values():
    src = "#include <stdio.h>\nenum color { RED, GREEN = 5, BLUE };\nint main(void) { enum color c = BLUE; printf(\"%d %d\\n\", RED, c); return 0; }"
    assert out(src) == "0 6\n"


# -------------------------------------------------------------- pointers


def test_pointer_arithmetic_and_difference():
    src = main('int a[5] = {10, 20, 30, 40, 50}; int *p = a; int *q = &a[4]; p += 2; printf("%d %d %ld %d\\n", *p, *(p + 1), q - p, p[-1]); return 0;')
    assert out(src) == "30 40 2 20\n"


def test_pointer_to_pointer():
    assert p('int x = 1; int *px = &x; int **ppx = &px; **ppx = 5; printf("%d\\n", x); return 0;') == "5\n"


def test_pointer_comparison_and_iteration():
    assert p('int a[4] = {1, 2, 3, 4}; int s = 0; for (int *q = a; q < a + 4; q++) s += *q; printf("%d\\n", s); return 0;') == "10\n"


def test_function_pointers():
    src = """#include <stdio.h>
int add(int a, int b) { return a + b; }
int mul(int a, int b) { return a * b; }
int apply(int (*f)(int, int), int x, int y) { return f(x, y); }
int main(void) {
    int (*ops[2])(int, int) = {add, mul};
    printf("%d %d %d\\n", apply(add, 2, 3), ops[1](4, 5), (*ops[0])(1, 1));
    return 0;
}
"""
    assert out(src) == "5 20 2\n"


def test_qsort_with_comparator():
    src = """#include <stdio.h>
#include <stdlib.h>
int cmp(const void *a, const void *b) { return *(const int *)b - *(const int *)a; }
int main(void) {
    int v[6] = {5, 1, 4, 2, 6, 3};
    qsort(v, 6, sizeof(int), cmp);
    for (int i = 0; i < 6; i++) printf("%d", v[i]);
    return 0;
}
"""
    assert out(src) == "654321"


# ----------------------------------------------------------- control flow


def test_recursion_fib():
    src = "#include <stdio.h>\nint fib(int n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }\nint main(void) { printf(\"%d\\n\", fib(10)); return 0; }"
    assert out(src) == "55\n"


def test_switch_fallthrough_and_default():
    src = main(
        """for (int i = 0; i < 4; i++) {
    switch (i) {
    case 0: printf("a");
    case 1: printf("b"); break;
    case 2: { printf("c"); break; }
    default: printf("d");
    }
}
return 0;"""
    )
    assert out(src) == "abbcd"


def test_do_while_break_continue():
    src = main(
        """int i = 0; int s = 0;
do { i++; if (i == 2) continue; if (i == 5) break; s += i; } while (i < 10);
printf("%d %d\\n", i, s); return 0;"""
    )
    assert out(src) == "5 8\n"


def test_nested_loops_with_break():
    assert p('int c = 0; for (int i = 0; i < 3; i++) { for (int j = 0; j < 3; j++) { if (j == 2) break; c++; } } printf("%d\\n", c); return 0;') == "6\n"


def test_static_locals_keep_value():
    src = "#include <stdio.h>\nint counter(void) { static int n = 0; n++; return n; }\nint main(void) { counter(); counter(); printf(\"%d\\n\", counter()); return 0; }"
    assert out(src) == "3\n"


def test_globals_zero_initialized():
    src = "#include <stdio.h>\nint g; int arr[3]; double d; char *ptr;\nint main(void) { printf(\"%d %d %.1f %d\\n\", g, arr[2], d, ptr == 0); return 0; }"
    assert out(src) == "0 0 0.0 1\n"


def test_global_initializer_with_address():
    src = "#include <stdio.h>\nint g = 5; int *pg = &g; const char *msg = \"hi\";\nint main(void) { *pg = 6; printf(\"%d %s\\n\", g, msg); return 0; }"
    assert out(src) == "6 hi\n"


def test_main_with_argc_argv():
    src = '#include <stdio.h>\nint main(int argc, char *argv[]) { printf("%d %s %d\\n", argc, argv[0], argv[1] == 0); return 0; }'
    assert out(src) == "1 ./prog 1\n"


# --------------------------------------------------------------- string.h


def test_string_functions():
    src = main(
        """char a[32] = "hello";
char b[32];
strcpy(b, a);
strcat(b, " world");
printf("%zu %s %d %d %d\\n", strlen(b), b, strcmp(a, "hello"), strcmp("a", "b") < 0, strncmp("abcx", "abcy", 3));
char c[8];
strncpy(c, "xy", sizeof(c));
printf("%s %s %s\\n", c, strchr(b, 'w'), strstr(b, "lo w"));
printf("%d\\n", strrchr(b, 'o') - b);
char *d = strdup(a);
printf("%s\\n", d);
free(d);
int x[4];
memset(x, 0, sizeof(x));
int y[4] = {1, 2, 3, 4};
memcpy(x, y, sizeof(y));
memmove(y + 1, y, 2 * sizeof(int));
printf("%d %d %d %d\\n", x[3], y[1], y[2], memcmp(x, y, sizeof(int)));
return 0;"""
    )
    assert out(src) == "11 hello world 0 1 0\nxy world lo world\n7\nhello\n4 1 2 0\n"


# --------------------------------------------------------------- stdlib


def test_atoi_strtol_abs():
    src = main(
        """char *end;
long v = strtol("  123abc", &end, 10);
printf("%d %ld %s %d %d %ld\\n", atoi("-42xyz"), v, end, abs(-5), atoi("zz"), strtol("ff", NULL, 16));
printf("%.2f\\n", atof("2.5e1"));
return 0;"""
    )
    assert out(src) == "-42 123 abc 5 0 255\n25.00\n"


def test_rand_matches_glibc():
    assert p('printf("%d %d\\n", rand(), rand()); return 0;') == "1804289383 846930886\n"


def test_srand_one_is_default_sequence():
    a = p('printf("%d %d", rand(), rand()); return 0;')
    b = p('srand(1); printf("%d %d", rand(), rand()); return 0;')
    c = p('srand(42); printf("%d %d", rand(), rand()); return 0;')
    assert a == b
    assert a != c


def test_ctype():
    src = main('printf("%d%d%d%d %c%c\\n", isdigit(\'5\') != 0, isalpha(\'5\') != 0, isspace(\' \') != 0, isupper(\'a\') != 0, toupper(\'a\'), tolower(\'Q\')); return 0;', ("stdio.h", "ctype.h"))
    assert out(src) == "1010 Aq\n"


def test_math():
    src = main('printf("%.1f %.1f %.1f %.1f %.1f\\n", sqrt(16.0), pow(2, 10), floor(-2.5), ceil(2.1), fabs(-3.0)); return 0;', ("stdio.h", "math.h"))
    assert out(src) == "4.0 1024.0 -3.0 3.0 3.0\n"


def test_limits_and_stdint():
    src = main('int32_t a = INT_MAX; uint8_t b = 255; printf("%d %d %zu\\n", a, b, sizeof(int64_t)); return 0;', ("stdio.h", "limits.h", "stdint.h"))
    assert out(src) == "2147483647 255 8\n"


def test_time_is_deterministic():
    src = main('printf("%ld\\n", time(NULL)); return 0;', ("stdio.h", "time.h"))
    assert out(src) == "1700000000\n"


# ------------------------------------------------------------------ input


def test_scanf_reads_values():
    src = main('int a; double d; char w[16]; int n = scanf("%d %lf %s", &a, &d, w); printf("%d %d %.2f %s\\n", n, a, d, w); return 0;')
    assert out(src, "12 3.5 word\n") == "3 12 3.50 word\n"


def test_scanf_returns_eof_and_partial():
    src = main('int a = 0, b = 0; int n = scanf("%d %d", &a, &b); int m = scanf("%d", &a); printf("%d %d %d\\n", n, m, a); return 0;')
    assert out(src, "7 x") == "1 0 7\n"
    assert out(main('int a; printf("%d\\n", scanf("%d", &a)); return 0;'), "") == "-1\n"


def test_getchar_and_fgets():
    src = main('char line[16]; int c = getchar(); fgets(line, sizeof(line), stdin); printf("[%c][%s]", c, line); printf("%d", getchar()); return 0;')
    assert out(src, "Xhello\n") == "[X][hello\n]-1"


def test_sscanf():
    assert p('int a, b; sscanf("10,20", "%d,%d", &a, &b); printf("%d\\n", a + b); return 0;') == "30\n"


# ---------------------------------------------------------- program end


def test_exit_code():
    _, tr = run_ok(main('printf("bye"); exit(3); printf("never"); return 0;'))
    assert tr.exit_code == 3
    assert output_text(tr) == "bye"


def test_main_return_code():
    _, tr = run_ok(main("return 7;"))
    assert tr.exit_code == 7
    assert tr.end_reason == "exit"


def test_assert_failure_is_fatal():
    _, tr = run_ok(main("int x = 1;\nassert(x == 2);\nreturn 0;", ("stdio.h", "assert.h")))
    assert tr.end_reason == "error"
    assert "assert" in kinds(tr)
    assert "Assertion `x == 2' failed" in output_text(tr, streams=("stderr",))


def test_sizeof_lp64():
    src = main('struct s { char c; int i; }; struct t { char a; double d; char b; };\nprintf("%zu %zu %zu %zu %zu %zu %zu %zu\\n", sizeof(char), sizeof(short), sizeof(int), sizeof(long), sizeof(int *), sizeof(double), sizeof(struct s), sizeof(struct t)); return 0;')
    assert out(src) == "1 2 4 8 8 8 8 24\n"


# ------------------------------------------------------- runtime problems


def test_out_of_bounds_write_into_neighbour():
    _, tr = run_ok(main("int secret = 1;\nint a[2] = {0, 0};\na[2] = 99;\nprintf(\"%d\\n\", secret);\nreturn 0;"))
    assert "out-of-bounds" in kinds(tr)
    d = next(d for d in tr.diagnostics if d.kind == "out-of-bounds")
    assert "secret" in d.message
    assert output_text(tr) == "99\n"


def test_out_of_bounds_read_heap():
    _, tr = run_ok(main("int *p = malloc(2 * sizeof(int));\np[0] = 1; p[1] = 2;\nint x = p[2];\nfree(p);\nreturn x * 0;"))
    assert "out-of-bounds" in kinds(tr)


def test_use_after_free():
    _, tr = run_ok(main("int *p = malloc(sizeof(int));\n*p = 1;\nfree(p);\nint x = *p;\nreturn x * 0;"))
    assert "use-after-free" in kinds(tr)


def test_double_free():
    _, tr = run_ok(main("int *p = malloc(4);\nfree(p);\nfree(p);\nreturn 0;"))
    assert "double-free" in kinds(tr)
    assert tr.end_reason == "exit"


def test_invalid_free_stack_variable():
    _, tr = run_ok(main("int x = 1;\nfree(&x);\nreturn 0;"))
    assert "invalid-free" in kinds(tr)


def test_invalid_free_middle_of_block():
    _, tr = run_ok(main("char *p = malloc(10);\nfree(p + 2);\nfree(p);\nreturn 0;"))
    assert "invalid-free" in kinds(tr)
    assert "memory-leak" not in kinds(tr)


def test_free_null_is_fine():
    _, tr = run_ok(main("int *p = NULL;\nfree(p);\nreturn 0;"))
    assert kinds(tr) == set()


def test_memory_leak_reported_at_exit():
    _, tr = run_ok(main("int *p = malloc(40);\np = NULL;\nreturn 0;"))
    leak = [d for d in tr.diagnostics if d.kind == "memory-leak"]
    assert len(leak) == 1
    assert "40 bytes" in leak[0].message
    assert leak[0].line == 5


def test_uninitialized_read():
    _, tr = run_ok(main("int x;\nint y = x + 1;\nreturn y * 0;"))
    assert "uninitialized" in kinds(tr)


def test_malloc_memory_is_uninitialized_but_calloc_is_zero():
    _, tr = run_ok(main("int *a = malloc(8);\nint *b = calloc(2, sizeof(int));\nint s = b[0] + b[1];\nint t = a[0];\nprintf(\"%d\", s);\nfree(a); free(b);\nreturn t * 0;"))
    assert output_text(tr) == "0"
    unin = [d for d in tr.diagnostics if d.kind == "uninitialized"]
    assert len(unin) == 1 and unin[0].line == 8


def test_dangling_pointer_to_returned_local():
    src = "#include <stdio.h>\nint *f(void) { int n = 42; return &n; }\nint main(void) {\n int *p = f();\n int v = *p;\n return v * 0;\n}"
    _, tr = run_ok(src)
    assert "dangling-pointer" in kinds(tr)


def test_buffer_overflow_strcpy():
    _, tr = run_ok(main('char small[4];\nstrcpy(small, "too long");\nreturn 0;'))
    assert "buffer-overflow" in kinds(tr)


def test_null_pointer_dereference_is_fatal():
    _, tr = run_ok(main("int *p = NULL;\n*p = 5;\nreturn 0;"))
    assert tr.end_reason == "error"
    assert tr.fatal.kind == "null-pointer"
    assert tr.fatal.line == 6
    assert tr.steps[-1].kind == "error"


def test_uninitialized_pointer_segfault():
    _, tr = run_ok(main("int *p;\n*p = 5;\nreturn 0;"))
    assert tr.end_reason == "error"
    assert tr.fatal.kind in ("segfault", "null-pointer")
    assert "uninitialized" in kinds(tr)


def test_division_by_zero_is_fatal():
    _, tr = run_ok(main("int z = 0;\nint x = 5 / z;\nreturn x;"))
    assert tr.end_reason == "error"
    assert tr.fatal.kind == "division-by-zero"


def test_float_division_by_zero_is_inf():
    assert p('double z = 0.0; printf("%f %f\\n", 1.0 / z, -1.0 / z); return 0;') == "inf -inf\n"


def test_stack_overflow_is_fatal():
    src = "int f(int n) { return f(n + 1) + 1; }\nint main(void) { return f(0); }"
    _, tr = run_ok(src)
    assert tr.end_reason == "error"
    assert tr.fatal.kind == "stack-overflow"


def test_step_limit():
    _, tr = run_ok(main("int i = 0;\nwhile (1) { i++; }\nreturn 0;"), max_steps=500)
    assert tr.end_reason == "limit"
    assert len(tr.steps) <= 502
    assert "step-limit" in kinds(tr)


def test_write_to_string_literal_warns():
    _, tr = run_ok(main('char *s = "hello";\ns[0] = \'H\';\nreturn 0;'))
    assert "read-only" in kinds(tr)


def test_stack_smashing_detected():
    src = '#include <string.h>\nvoid f(void) {\n char buf[4];\n strcpy(buf, "this is way too long for buf");\n}\nint main(void) { f(); return 0; }'
    _, tr = run_ok(src)
    assert "stack-smashing" in kinds(tr)


def test_heap_corruption_detected_on_free():
    src = main("char *a = malloc(16);\nchar *b = malloc(16);\nmemset(a, 'x', 24);\nfree(b);\nfree(a);\nreturn 0;")
    _, tr = run_ok(src)
    assert "heap-corruption" in kinds(tr)


def test_realloc_in_place_and_moving():
    src = main(
        """int *a = malloc(2 * sizeof(int));
a[0] = 1; a[1] = 2;
int *a2 = realloc(a, 4 * sizeof(int));
int *blocker = malloc(sizeof(int));
int *a3 = realloc(a2, 64 * sizeof(int));
printf("%d %d %d %d\\n", a2 == a, a3 != a2, a3[0], a3[1]);
free(a3); free(blocker);
return 0;"""
    )
    _, tr = run_ok(src)
    assert output_text(tr) == "1 1 1 2\n"
    events = [e for st in tr.steps for e in st.events]
    assert any("in place" in e for e in events)
    assert any("could not grow" in e for e in events)
    assert kinds(tr) == set()


def test_use_of_old_pointer_after_moving_realloc():
    src = main("int *a = malloc(4);\nint *b = malloc(4);\nint *c = realloc(a, 400);\n*a = 1;\nfree(b); free(c);\nreturn 0;")
    _, tr = run_ok(src)
    assert "use-after-free" in kinds(tr)


def test_malloc_huge_returns_null():
    assert p('int *q = malloc((size_t)1 << 40); printf("%d\\n", q == NULL); return 0;') == "1\n"


def test_warnings_are_rate_limited_per_line():
    _, tr = run_ok(main("int a[2];\nfor (int i = 0; i < 20; i++) a[i % 2] = a[i % 2] + 1;\nreturn 0;"))
    unin = [d for d in tr.diagnostics if d.kind == "uninitialized" and d.severity == "warning"]
    assert 1 <= len(unin) <= 3


# ---------------------------------------------------------- trace & steps


def test_steps_have_events_and_lines():
    _, tr = run_ok("#include <stdio.h>\nint sq(int x) {\n return x * x;\n}\nint main(void) {\n int y = sq(3);\n printf(\"%d\", y);\n return 0;\n}")
    kinds_seq = [s.kind for s in tr.steps]
    assert kinds_seq[0] == "start"
    assert "call" in kinds_seq and "return" in kinds_seq
    assert kinds_seq[-1] == "exit"
    assert tr.steps[-1].line is None
    all_events = " ".join(e for s in tr.steps for e in s.events)
    assert "calls sq()" in all_events
    assert "sq() returns 9" in all_events


def test_frames_are_nested_and_die():
    _, tr = run_ok("int f(int n) { return n ? f(n - 1) : 0; }\nint main(void) { return f(3); }")
    depths = [tr.frames[s.frame].depth for s in tr.steps if s.frame is not None]
    assert max(depths) == 4
    assert all(fr.death is not None for fr in tr.frames)


def test_output_grows_monotonically():
    _, tr = run_ok(main('for (int i = 0; i < 3; i++) printf("%d", i);\nreturn 0;'))
    lens = [s.out_len for s in tr.steps]
    assert lens == sorted(lens)
    assert output_text(tr, step=len(tr.steps) - 1) == "012"


def _snapshot(mem):
    return [(r.name, bytes(r.data), bytes(r.init)) for r in mem.regions]


def test_time_travel_restores_memory():
    src = main(
        """int *a = malloc(5 * sizeof(int));
for (int i = 0; i < 5; i++) a[i] = i * i;
char s[8] = "abc";
strcpy(s, "xyz");
int *b = realloc(a, 50 * sizeof(int));
free(b);
return 0;"""
    )
    _, tr = run_ok(src)
    cur = Cursor(tr)
    last = len(tr.steps) - 1
    final = _snapshot(tr.memory)
    cur.goto(0)
    assert _snapshot(tr.memory) != final
    cur.goto(last)
    assert _snapshot(tr.memory) == final
    for i in range(last, -1, -1):
        cur.goto(i)
    first = _snapshot(tr.memory)
    for i in range(0, last + 1):
        cur.goto(i)
    assert _snapshot(tr.memory) == final
    cur.goto(0)
    assert _snapshot(tr.memory) == first


def test_time_travel_values_at_step():
    from stacksim.vm.values import decode
    from stacksim.c import ctype as T

    _, tr = run_ok(main("int x = 1;\nx = 2;\nx = 3;\nreturn 0;"))
    obj = next(o for o in tr.objects if o.name == "x")
    cur = Cursor(tr)
    seen = []
    for i in range(len(tr.steps)):
        cur.goto(i)
        if obj.alive_at(i):
            data, init = tr.memory.read(obj.addr, 4)
            if all(init):
                seen.append(decode(T.INT, data))
    assert [v for j, v in enumerate(seen) if j == 0 or seen[j - 1] != v] == [1, 2, 3]
