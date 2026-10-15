"""Example programs, each focused on one idea about memory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Example:
    title: str
    group: str
    code: str
    stdin: str = ""


EXAMPLES: list[Example] = [
    Example(
        "Variables live on the stack",
        "Basics",
        """\
#include <stdio.h>

int main(void) {
    int age = 20;
    double height = 1.75;
    char initial = 'A';
    int not_set;            // no initial value: contains garbage!

    age = age + 1;
    printf("%c is %d years old and %.2f m tall\\n", initial, age, height);

    not_set = 42;
    printf("not_set = %d\\n", not_set);
    return 0;
}
""",
    ),
    Example(
        "Blocks and scope",
        "Basics",
        """\
#include <stdio.h>

int main(void) {
    int total = 0;
    for (int i = 1; i <= 3; i++) {
        int square = i * i;     // created and destroyed in every round
        total += square;
    }
    // i and square do not exist here any more
    printf("total = %d\\n", total);
    return 0;
}
""",
    ),
    Example(
        "Function calls and stack frames",
        "Functions",
        """\
#include <stdio.h>

int square(int x) {
    int result = x * x;
    return result;
}

int sum_of_squares(int a, int b) {
    int s = square(a) + square(b);
    return s;
}

int main(void) {
    int n = sum_of_squares(3, 4);
    printf("3*3 + 4*4 = %d\\n", n);
    return 0;
}
""",
    ),
    Example(
        "Pass by value vs. by pointer (swap)",
        "Pointers",
        """\
#include <stdio.h>

void swap_wrong(int a, int b) {     // gets copies
    int tmp = a;
    a = b;
    b = tmp;
}

void swap(int *a, int *b) {         // gets addresses
    int tmp = *a;
    *a = *b;
    *b = tmp;
}

int main(void) {
    int x = 1, y = 2;
    swap_wrong(x, y);
    printf("after swap_wrong: x=%d y=%d\\n", x, y);
    swap(&x, &y);
    printf("after swap:       x=%d y=%d\\n", x, y);
    return 0;
}
""",
    ),
    Example(
        "Pointer basics",
        "Pointers",
        """\
#include <stdio.h>

int main(void) {
    int value = 10;
    int *p = &value;        // p holds the address of value
    int **pp = &p;          // a pointer to a pointer

    *p = 20;                // changes value through p
    **pp = 30;              // ...and through pp

    int numbers[4] = {1, 2, 3, 4};
    int *q = numbers;       // an array name turns into a pointer to its first element
    q++;                    // move to the next element (4 bytes further)
    *q = 99;

    printf("value=%d numbers[1]=%d\\n", value, numbers[1]);
    printf("p points to %p, q points to %p\\n", (void *)p, (void *)q);
    return 0;
}
""",
    ),
    Example(
        "Recursion: factorial",
        "Functions",
        """\
#include <stdio.h>

int factorial(int n) {
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);
}

int main(void) {
    int f = factorial(4);
    printf("4! = %d\\n", f);
    return 0;
}
""",
    ),
    Example(
        "Arrays and out-of-bounds writes",
        "Arrays & strings",
        """\
#include <stdio.h>

int main(void) {
    int secret = 1234;
    int data[3] = {10, 20, 30};

    for (int i = 0; i <= 3; i++) {   // bug: <= runs one time too often
        data[i] = 0;
    }
    printf("secret = %d\\n", secret); // secret was overwritten!
    return 0;
}
""",
    ),
    Example(
        "Strings are char arrays",
        "Arrays & strings",
        """\
#include <stdio.h>
#include <string.h>

int main(void) {
    char name[8] = "Ada";         // 3 letters + '\\0', rest filled with 0
    const char *greeting = "Hello";  // points to a read-only string literal

    printf("%s, %s! (length %zu)\\n", greeting, name, strlen(name));

    strcpy(name, "Grace");          // fits: 5 letters + '\\0'
    strcat(name, " Hopper");        // does NOT fit: buffer overflow
    printf("%s\\n", name);
    return 0;
}
""",
    ),
    Example(
        "malloc and free: a dynamic array",
        "Heap",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int count = 5;
    int *numbers = malloc(count * sizeof(int));   // memory on the heap
    if (numbers == NULL) {
        return 1;
    }
    for (int i = 0; i < count; i++) {
        numbers[i] = i * 10;
    }
    printf("last number: %d\\n", numbers[count - 1]);

    free(numbers);      // give the memory back
    numbers = NULL;     // good habit: no dangling pointer
    return 0;
}
""",
    ),
    Example(
        "Linked list",
        "Heap",
        """\
#include <stdio.h>
#include <stdlib.h>

struct node {
    int value;
    struct node *next;
};

struct node *push(struct node *head, int value) {
    struct node *n = malloc(sizeof(struct node));
    n->value = value;
    n->next = head;
    return n;
}

int main(void) {
    struct node *list = NULL;
    for (int i = 1; i <= 3; i++) {
        list = push(list, i);
    }

    for (struct node *p = list; p != NULL; p = p->next) {
        printf("%d ", p->value);
    }
    printf("\\n");

    while (list != NULL) {          // free every node
        struct node *next = list->next;
        free(list);
        list = next;
    }
    return 0;
}
""",
    ),
    Example(
        "Memory leak",
        "Heap",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int *data = malloc(4 * sizeof(int));
    data[0] = 1;

    data = malloc(4 * sizeof(int));   // the first block is now unreachable!
    data[0] = 2;

    free(data);
    return 0;                         // the first block was never freed
}
""",
    ),
    Example(
        "Use after free and double free",
        "Heap",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int *p = malloc(sizeof(int));
    *p = 7;
    free(p);

    printf("%d\\n", *p);    // use after free: the memory is not ours any more

    int *q = malloc(sizeof(int));   // malloc may hand out the same memory again
    *q = 99;
    printf("%d\\n", *p);    // p now sees q's data!

    free(p);               // double free
    return 0;
}
""",
    ),
    Example(
        "Returning a pointer to a local variable",
        "Pointers",
        """\
#include <stdio.h>

int *make_number(void) {
    int n = 42;
    return &n;          // n dies when the function returns!
}

int other(void) {
    int junk = 1000;    // reuses the same stack memory
    return junk;
}

int main(void) {
    int *p = make_number();
    printf("%d\\n", *p);
    other();
    printf("%d\\n", *p);   // the value has changed
    return 0;
}
""",
    ),
    Example(
        "realloc: a growing array",
        "Heap",
        """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int capacity = 2;
    int size = 0;
    int *items = malloc(capacity * sizeof(int));
    int *blocker = malloc(sizeof(int));    // sits right behind items

    for (int i = 0; i < 5; i++) {
        if (size == capacity) {
            capacity *= 2;
            items = realloc(items, capacity * sizeof(int));  // may move!
        }
        items[size] = i * i;
        size++;
    }
    printf("size %d, capacity %d\\n", size, capacity);
    free(items);
    free(blocker);
    return 0;
}
""",
    ),
    Example(
        "Structs: copies and pointers",
        "Pointers",
        """\
#include <stdio.h>

struct point {
    int x;
    int y;
};

void move_copy(struct point p) {     // the whole struct is copied
    p.x += 10;
}

void move(struct point *p) {         // only the address is copied
    p->x += 10;
}

int main(void) {
    struct point a = {1, 2};
    struct point b = a;              // copies both members
    move_copy(a);
    move(&b);
    printf("a = (%d, %d), b = (%d, %d)\\n", a.x, a.y, b.x, b.y);
    return 0;
}
""",
    ),
    Example(
        "Reading input with scanf",
        "Input",
        """\
#include <stdio.h>

int main(void) {
    int count;
    printf("How many numbers? ");
    scanf("%d", &count);          // &count: scanf needs the address

    int sum = 0;
    for (int i = 0; i < count; i++) {
        int x;
        scanf("%d", &x);
        sum += x;
    }
    printf("\\nsum = %d\\n", sum);
    return 0;
}
""",
        stdin="3\n10 20 12\n",
    ),
    Example(
        "Uninitialized variables",
        "Basics",
        """\
#include <stdio.h>

int sum_up_to(int n) {
    int sum;                 // forgot: int sum = 0;
    for (int i = 1; i <= n; i++) {
        sum += i;
    }
    return sum;
}

int main(void) {
    printf("%d\\n", sum_up_to(3));
    return 0;
}
""",
    ),
    Example(
        "Stack overflow (endless recursion)",
        "Functions",
        """\
#include <stdio.h>

int count_down(int n) {
    // bug: the base case is missing, so this never stops
    return count_down(n - 1) + 1;
}

int main(void) {
    printf("%d\\n", count_down(3));
    return 0;
}
""",
    ),
    Example(
        "NULL pointer dereference",
        "Pointers",
        """\
#include <stdio.h>
#include <stdlib.h>

struct item {
    int id;
    struct item *next;
};

int main(void) {
    struct item first = {1, NULL};
    struct item *p = &first;
    printf("first id: %d\\n", p->id);
    p = p->next;            // there is no second item: p becomes NULL
    printf("next id: %d\\n", p->id);
    return 0;
}
""",
    ),
    Example(
        "Function pointers and qsort",
        "Functions",
        """\
#include <stdio.h>
#include <stdlib.h>

int compare(const void *a, const void *b) {
    int x = *(const int *)a;
    int y = *(const int *)b;
    return x - y;
}

int main(void) {
    int values[5] = {42, 7, 19, 3, 25};
    qsort(values, 5, sizeof(int), compare);
    for (int i = 0; i < 5; i++) {
        printf("%d ", values[i]);
    }
    printf("\\n");
    return 0;
}
""",
    ),
]

DEFAULT_PROGRAM = EXAMPLES[2].code

NEW_FILE = """\
#include <stdio.h>

int main(void) {

    return 0;
}
"""
