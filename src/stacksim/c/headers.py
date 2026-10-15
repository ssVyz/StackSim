"""The standard headers stacksim knows about.

Each header is plain C source (macros, typedefs, prototypes) that is spliced in
by ``#include``. Every function declared here has a Python implementation in
``stacksim.vm.libc``.
"""

_SIZE_T = "typedef unsigned long size_t;\n"
_NULL = "#define NULL ((void*)0)\n"

HEADERS: dict[str, str] = {
    "stddef.h": _SIZE_T + _NULL + "typedef long ptrdiff_t;\n",
    "stdbool.h": "#define __bool_true_false_are_defined 1\n",
    "stdio.h": _SIZE_T + _NULL + """
typedef struct _FILE FILE;
#define EOF (-1)
extern FILE *stdin;
extern FILE *stdout;
extern FILE *stderr;
int printf(const char *format, ...);
int fprintf(FILE *stream, const char *format, ...);
int sprintf(char *str, const char *format, ...);
int snprintf(char *str, size_t size, const char *format, ...);
int puts(const char *s);
int putchar(int c);
int fputs(const char *s, FILE *stream);
int fputc(int c, FILE *stream);
int putc(int c, FILE *stream);
int scanf(const char *format, ...);
int sscanf(const char *str, const char *format, ...);
int getchar(void);
int fgetc(FILE *stream);
int getc(FILE *stream);
char *fgets(char *s, int size, FILE *stream);
int fflush(FILE *stream);
void perror(const char *s);
""",
    "stdlib.h": _SIZE_T + _NULL + """
#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1
#define RAND_MAX 2147483647
void *malloc(size_t size);
void *calloc(size_t count, size_t size);
void *realloc(void *ptr, size_t size);
void free(void *ptr);
void exit(int status);
void abort(void);
int abs(int x);
long labs(long x);
int atoi(const char *s);
long atol(const char *s);
double atof(const char *s);
long strtol(const char *s, char **endptr, int base);
double strtod(const char *s, char **endptr);
int rand(void);
void srand(unsigned int seed);
void qsort(void *base, size_t count, size_t size, int (*compare)(const void *, const void *));
""",
    "string.h": _SIZE_T + _NULL + """
size_t strlen(const char *s);
char *strcpy(char *dest, const char *src);
char *strncpy(char *dest, const char *src, size_t n);
char *strcat(char *dest, const char *src);
char *strncat(char *dest, const char *src, size_t n);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t n);
char *strchr(const char *s, int c);
char *strrchr(const char *s, int c);
char *strstr(const char *haystack, const char *needle);
char *strdup(const char *s);
void *memcpy(void *dest, const void *src, size_t n);
void *memmove(void *dest, const void *src, size_t n);
void *memset(void *s, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);
""",
    "ctype.h": """
int isalpha(int c);
int isdigit(int c);
int isalnum(int c);
int isspace(int c);
int isupper(int c);
int islower(int c);
int ispunct(int c);
int isprint(int c);
int isxdigit(int c);
int toupper(int c);
int tolower(int c);
""",
    "math.h": """
#define M_PI 3.14159265358979323846
#define M_E 2.7182818284590452354
#define INFINITY (1e999)
#define NAN (0.0 / 0.0)
double sqrt(double x);
double pow(double x, double y);
double fabs(double x);
double floor(double x);
double ceil(double x);
double round(double x);
double trunc(double x);
double fmod(double x, double y);
double fmin(double x, double y);
double fmax(double x, double y);
double sin(double x);
double cos(double x);
double tan(double x);
double atan(double x);
double atan2(double y, double x);
double exp(double x);
double log(double x);
double log10(double x);
double log2(double x);
double hypot(double x, double y);
""",
    "limits.h": """
#define CHAR_BIT 8
#define SCHAR_MIN (-128)
#define SCHAR_MAX 127
#define UCHAR_MAX 255
#define CHAR_MIN (-128)
#define CHAR_MAX 127
#define SHRT_MIN (-32768)
#define SHRT_MAX 32767
#define USHRT_MAX 65535
#define INT_MIN (-2147483647 - 1)
#define INT_MAX 2147483647
#define UINT_MAX 4294967295U
#define LONG_MIN (-9223372036854775807L - 1)
#define LONG_MAX 9223372036854775807L
#define ULONG_MAX 18446744073709551615UL
#define LLONG_MIN (-9223372036854775807LL - 1)
#define LLONG_MAX 9223372036854775807LL
#define ULLONG_MAX 18446744073709551615ULL
""",
    "stdint.h": _SIZE_T + """
typedef signed char int8_t;
typedef short int16_t;
typedef int int32_t;
typedef long int64_t;
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long uint64_t;
typedef long intptr_t;
typedef unsigned long uintptr_t;
#define INT8_MIN (-128)
#define INT8_MAX 127
#define UINT8_MAX 255
#define INT16_MIN (-32768)
#define INT16_MAX 32767
#define UINT16_MAX 65535
#define INT32_MIN (-2147483647 - 1)
#define INT32_MAX 2147483647
#define UINT32_MAX 4294967295U
#define INT64_MIN (-9223372036854775807L - 1)
#define INT64_MAX 9223372036854775807L
#define UINT64_MAX 18446744073709551615UL
#define SIZE_MAX 18446744073709551615UL
""",
    "assert.h": """
void __stacksim_assert(int condition, const char *expression, int line);
#define assert(x) __stacksim_assert((x) != 0, #x, __LINE__)
""",
    "time.h": """
typedef long time_t;
time_t time(time_t *t);
""",
}

# Where each library name lives, for "did you forget #include" hints.
NAME_TO_HEADER: dict[str, str] = {}


def _index() -> None:
    import re

    for hdr, src in HEADERS.items():
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", src):
            NAME_TO_HEADER.setdefault(m.group(1), hdr)
        for m in re.finditer(r"#define\s+([A-Za-z_]\w*)", src):
            NAME_TO_HEADER.setdefault(m.group(1), hdr)
        for m in re.finditer(r"typedef[^;]*?\b([A-Za-z_]\w*)\s*;", src):
            NAME_TO_HEADER.setdefault(m.group(1), hdr)
        for m in re.finditer(r"extern\s+FILE\s*\*\s*(\w+)", src):
            NAME_TO_HEADER.setdefault(m.group(1), hdr)
    # Prefer the most commonly taught header for names that several headers define.
    NAME_TO_HEADER["size_t"] = "stddef.h"
    NAME_TO_HEADER["NULL"] = "stdlib.h"
    NAME_TO_HEADER.pop("__stacksim_assert", None)


_index()

STD_TYPEDEF_NAMES = frozenset(
    n for n, h in NAME_TO_HEADER.items() if n.endswith("_t") or n == "FILE"
)
