"""Lexer, preprocessor, parser and checker: errors, warnings, friendly hints."""

from __future__ import annotations

import pytest

from conftest import main
from stacksim.c.compiler import compile_source


def err(src: str):
    r = compile_source(src)
    assert not r.ok, "expected a compile error"
    return r.errors[0]


def warnings(src: str):
    r = compile_source(src)
    assert r.ok, "; ".join(d.format() for d in r.diagnostics)
    return [d for d in r.diagnostics if d.severity == "warning"]


# ----------------------------------------------------------------- errors


def test_missing_semicolon_points_at_previous_line():
    e = err("int main(void) {\n    int x = 5\n    return x;\n}\n")
    assert "';'" in e.message
    assert e.line == 2


def test_missing_semicolon_same_line():
    e = err("int main(void) { int x = 5 int y; return 0; }")
    assert "';'" in e.message


def test_undeclared_malloc_hints_header():
    e = err("int main(void) { int *p = malloc(4); return 0; }")
    assert "malloc" in e.message
    assert "#include <stdlib.h>" in (e.hint or "")


def test_undeclared_null_hints_header():
    e = err("int main(void) { int *p = NULL; return 0; }")
    assert "NULL" in e.message
    assert "#include" in (e.hint or "")


def test_undeclared_variable_suggests_close_name():
    e = err("int main(void) { int count = 1; return cout; }")
    assert "cout" in e.message
    assert "count" in (e.hint or "")


def test_unknown_type_size_t_hint():
    e = err("int main(void) { size_t n = 3; return 0; }")
    assert "size_t" in e.message
    assert "stddef.h" in (e.hint or "")


def test_unknown_struct_name_without_keyword():
    e = err("struct point { int x; };\nint main(void) { point p; return 0; }")
    assert "point" in e.message
    assert "struct point" in (e.hint or "")


def test_unclosed_brace():
    e = err("int main(void) {\n    if (1) {\n        return 0;\n}\n")
    assert "never closed" in e.message


def test_dot_on_pointer_suggests_arrow():
    e = err("struct p { int x; };\nint main(void) { struct p v; struct p *q = &v; q.x = 1; return 0; }")
    assert "->" in e.message


def test_arrow_on_struct_suggests_dot():
    e = err("struct p { int x; };\nint main(void) { struct p v; v->x = 1; return 0; }")
    assert "'.'" in e.message


def test_unknown_member_suggestion():
    e = err("struct p { int value; };\nint main(void) { struct p v; v.valeu = 1; return 0; }")
    assert "valeu" in e.message
    assert "value" in (e.hint or "")


def test_array_assignment_error():
    e = err("int main(void) { int a[3]; int b[3]; a = b; return 0; }")
    assert "array" in e.message


def test_vla_is_rejected_with_heap_hint():
    e = err("int main(void) { int n = 4; int a[n]; return 0; }")
    assert "constant" in e.message
    assert "malloc" in (e.hint or "")


def test_missing_main():
    e = err("int f(void) { return 1; }")
    assert "main" in e.message


def test_wrong_argument_count():
    e = err("int add(int a, int b) { return a + b; }\nint main(void) { return add(1); }")
    assert "expects 2 arguments" in e.message


def test_int_to_pointer_error():
    e = err("int main(void) { int x = 3; int *p = x; return 0; }")
    assert "pointer" in e.message


def test_pointer_to_int_error():
    e = err("int main(void) { int x = 3; int *p = &x; int y = p; return 0; }")
    assert "pointer" in e.message


def test_redeclaration_error():
    e = err("int main(void) { int x = 1; int x = 2; return 0; }")
    assert "already declared" in e.message


def test_break_outside_loop():
    e = err("int main(void) { break; return 0; }")
    assert "break" in e.message


def test_nested_function_definition():
    e = err("int main(void) {\n int f(void) { return 1; }\n return 0; }")
    assert "inside another function" in e.message


def test_void_function_returning_value():
    e = err("void f(void) { return 3; }\nint main(void) { f(); return 0; }")
    assert "void" in e.message


def test_deref_non_pointer():
    e = err("int main(void) { int x = 1; return *x; }")
    assert "not a pointer" in e.message


def test_declared_but_not_defined():
    e = err("int f(int);\nint main(void) { return f(1); }")
    assert "never defined" in e.message


def test_redefining_library_function():
    e = err("#include <stdlib.h>\nvoid *malloc(size_t n) { return 0; }\nint main(void) { return 0; }")
    assert "standard library" in e.message


def test_goto_unsupported():
    e = err("int main(void) { goto end; return 0; }")
    assert "goto" in e.message


# --------------------------------------------------------------- warnings


def test_assignment_in_if_condition_warns():
    ws = warnings("int main(void) { int x = 1; if (x = 2) { x = 3; } return 0; }")
    assert any("==" in (w.hint or "") for w in ws)


def test_printf_int_format_with_double_warns():
    ws = warnings(main('printf("%d\\n", 1.5); return 0;'))
    assert any(w.kind == "format" and "%f" in (w.hint or "") for w in ws)


def test_printf_too_few_arguments_warns():
    ws = warnings(main('printf("%d %d\\n", 1); return 0;'))
    assert any(w.kind == "format" for w in ws)


def test_printf_too_many_arguments_warns():
    ws = warnings(main('printf("%d\\n", 1, 2); return 0;'))
    assert any(w.kind == "format" for w in ws)


def test_scanf_without_address_warns():
    ws = warnings(main('int x; scanf("%d", x); return 0;'))
    assert any("&x" in (w.hint or "") for w in ws)


def test_scanf_lf_for_double_hint():
    ws = warnings(main('double d; scanf("%f", &d); return 0;'))
    assert any("%lf" in (w.hint or "") for w in ws)


def test_returning_address_of_local_warns():
    ws = warnings("int *f(void) { int n = 1; return &n; }\nint main(void) { f(); return 0; }")
    assert any(w.kind == "dangling" for w in ws)


def test_missing_return_warns():
    ws = warnings("int f(int x) { if (x) return 1; }\nint main(void) { return f(1); }")
    assert any("return" in w.message for w in ws)


def test_integer_division_constant_warns():
    ws = warnings("int main(void) { double d = 1 / 2; return 0; }")
    assert any("integer division" in w.message for w in ws)


def test_clean_program_has_no_warnings():
    assert warnings(main('int x = 1; printf("%d\\n", x); return 0;')) == []


# ----------------------------------------------------------- preprocessor


def test_define_object_and_function_like_macros():
    r = compile_source(
        "#define N 3\n#define SQ(x) ((x) * (x))\nint main(void) { int a[N]; return SQ(N) - 9 + a[0] * 0; }"
    )
    assert r.ok


def test_stringification_and_assert_compile():
    r = compile_source("#include <assert.h>\nint main(void) { assert(1 + 1 == 2); return 0; }")
    assert r.ok


def test_ifdef_and_if_zero():
    src = (
        "#define DEBUG\n#ifdef DEBUG\nint flag = 1;\n#else\nint flag = 2;\n#endif\n"
        "#if 0\nthis is not C at all\n#endif\n"
        "#ifndef DEBUG\nint broken = ;\n#endif\n"
        "int main(void) { return flag; }"
    )
    r = compile_source(src)
    assert r.ok


def test_if_elif_expression():
    r = compile_source("#define V 2\n#if V == 1\nint x = ;\n#elif V == 2\nint x = 2;\n#else\nint x = ;\n#endif\nint main(void) { return x; }")
    assert r.ok


def test_unknown_header_error():
    e = err("#include <windows.h>\nint main(void) { return 0; }")
    assert "windows.h" in e.message
    assert "stdio.h" in (e.hint or "")


def test_local_include_rejected():
    e = err('#include "mylib.h"\nint main(void) { return 0; }')
    assert "single file" in e.message


def test_unterminated_if():
    e = err("#ifdef X\nint main(void) { return 0; }")
    assert "#endif" in e.message


def test_error_directive():
    e = err("#error stop here\nint main(void) { return 0; }")
    assert "stop here" in e.message


# ------------------------------------------------------------------- lexer


def test_escape_sequences_compile():
    body = r'char s[] = "a\tb\n\x41\101\0"; char c = ' + "'\\''" + "; char d = '\\\\'; return 0;"
    r = compile_source(main(body))
    assert r.ok, r.diagnostics[0].format() if r.diagnostics else ""


def test_invalid_number():
    e = err("int main(void) { int x = 12abc; return 0; }")
    assert "invalid number" in e.message


def test_invalid_octal():
    e = err("int main(void) { int x = 089; return 0; }")
    assert "octal" in e.message


def test_unterminated_string():
    e = err('int main(void) { char *s = "abc;\n return 0; }')
    assert "closing" in e.message


def test_unterminated_comment():
    e = err("int main(void) { /* oops\n return 0; }")
    assert "*/" in e.message


def test_multichar_constant():
    e = err("int main(void) { char c = 'ab'; return 0; }")
    assert "exactly one character" in e.message


def test_unexpected_character():
    e = err("int main(void) { int x = 1 @ 2; return 0; }")
    assert "@" in e.message


@pytest.mark.parametrize(
    "decl",
    [
        "unsigned long long x = 1;",
        "long double d = 1.0L;",
        "short s = 1;",
        "signed char c = -1;",
        "const char *const p = \"x\";",
        "int (*fp)(int, int) = 0;",
        "int *arr[3];",
        "int (*pa)[3];",
        "enum color { RED, GREEN = 5, BLUE } c = BLUE;",
        "typedef struct { int x, y; } Point; Point pt = {1, 2};",
        "union u { int i; char c[4]; } un;",
        "bool b = true;",
    ],
)
def test_declarations_parse(decl):
    r = compile_source("#include <stdbool.h>\nint main(void) {\n" + decl + "\nreturn 0; }")
    assert r.ok, r.diagnostics[0].format() if r.diagnostics else ""
