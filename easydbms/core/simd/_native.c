/*
 * Native accelerator for SQL scripts.
 *
 *  - find_first_of(): index of the first character equal to one of up to 8 ASCII needles,
 *    using AVX2 (32 bytes per step) or SSE2 (16) on x86-64, chosen at run time; a plain C loop
 *    elsewhere (ARM relies on the compiler's auto-vectorisation).
 *  - split(): the statement splitter of splitter.py reimplemented over the raw string buffer.
 *    It follows the Python lexer's token rules exactly (that is what the differential tests
 *    check), and uses find_first_of() to skip the bodies of strings, comments and $$ quotes.
 *    It returns None ("bail out") for input it does not model bit-for-bit, in which case the
 *    caller runs the Python splitter.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))
#include <immintrin.h>
#define HAVE_X86_SIMD 1
#endif

#define MAX_NEEDLES 8

/* ------------------------------------------------------------------ SIMD scanning (1-byte kind) */

typedef Py_ssize_t (*find_fn)(const uint8_t *, Py_ssize_t, Py_ssize_t, const uint8_t *, int);

static Py_ssize_t find_scalar(const uint8_t *p, Py_ssize_t i, Py_ssize_t n, const uint8_t *nd, int nn)
{
    for (; i < n; i++) {
        uint8_t c = p[i];
        for (int k = 0; k < nn; k++)
            if (c == nd[k])
                return i;
    }
    return -1;
}

#ifdef HAVE_X86_SIMD
static Py_ssize_t find_sse2(const uint8_t *p, Py_ssize_t i, Py_ssize_t n, const uint8_t *nd, int nn)
{
    __m128i v[MAX_NEEDLES];
    for (int k = 0; k < nn; k++)
        v[k] = _mm_set1_epi8((char)nd[k]);
    for (; i + 16 <= n; i += 16) {
        __m128i x = _mm_loadu_si128((const __m128i *)(p + i));
        __m128i m = _mm_cmpeq_epi8(x, v[0]);
        for (int k = 1; k < nn; k++)
            m = _mm_or_si128(m, _mm_cmpeq_epi8(x, v[k]));
        int mask = _mm_movemask_epi8(m);
        if (mask)
            return i + __builtin_ctz((unsigned)mask);
    }
    return find_scalar(p, i, n, nd, nn);
}

__attribute__((target("avx2")))
static Py_ssize_t find_avx2(const uint8_t *p, Py_ssize_t i, Py_ssize_t n, const uint8_t *nd, int nn)
{
    __m256i v[MAX_NEEDLES];
    for (int k = 0; k < nn; k++)
        v[k] = _mm256_set1_epi8((char)nd[k]);
    for (; i + 32 <= n; i += 32) {
        __m256i x = _mm256_loadu_si256((const __m256i *)(p + i));
        __m256i m = _mm256_cmpeq_epi8(x, v[0]);
        for (int k = 1; k < nn; k++)
            m = _mm256_or_si256(m, _mm256_cmpeq_epi8(x, v[k]));
        unsigned mask = (unsigned)_mm256_movemask_epi8(m);
        if (mask)
            return i + __builtin_ctz(mask);
    }
    return find_sse2(p, i, n, nd, nn);
}
#endif

static find_fn find_impl = find_scalar;
static const char *impl_name = "scalar";
static int simd_enabled = 1;

static void select_impl(void)
{
#ifdef HAVE_X86_SIMD
    __builtin_cpu_init();
    if (__builtin_cpu_supports("avx2")) {
        find_impl = find_avx2;
        impl_name = "avx2";
    } else if (__builtin_cpu_supports("sse2")) {
        find_impl = find_sse2;
        impl_name = "sse2";
    }
#endif
}

/* ------------------------------------------------------------------ string access */

typedef struct {
    const void *data;
    int kind;
    Py_ssize_t n;
    unsigned flags;
    int bail; /* set when a decision would depend on str.upper() of a non-ASCII character */
} Src;

#define RD(s, i) ((Py_UCS4)PyUnicode_READ((s)->kind, (s)->data, (i)))

/* First index >= i holding one of the ASCII needles, or -1. */
static Py_ssize_t find_of(const Src *s, Py_ssize_t i, const char *needles, int nn)
{
    if (i >= s->n)
        return -1;
    if (s->kind == PyUnicode_1BYTE_KIND) {
        find_fn fn = simd_enabled ? find_impl : find_scalar;
        return fn((const uint8_t *)s->data, i, s->n, (const uint8_t *)needles, nn);
    }
    for (; i < s->n; i++) {
        Py_UCS4 c = RD(s, i);
        for (int k = 0; k < nn; k++)
            if (c == (Py_UCS4)(unsigned char)needles[k])
                return i;
    }
    return -1;
}

/* ------------------------------------------------------------------ tokenizer (mirrors lexer.py) */

enum {
    F_DASH_SPACE = 1 << 0, F_HASH = 1 << 1, F_NESTED = 1 << 2, F_DQ_STRING = 1 << 3,
    F_BACKSLASH = 1 << 4, F_E_PREFIX = 1 << 5, F_DOLLAR_QUOTE = 1 << 6, F_DOLLAR_NUM = 1 << 7,
    F_DOLLAR_NAMED = 1 << 8, F_QUESTION = 1 << 9, F_COLON = 1 << 10, F_AT_PARAM = 1 << 11,
    F_AT_VAR = 1 << 12, F_ID_DQ = 1 << 13, F_ID_BT = 1 << 14, F_ID_BR = 1 << 15,
};

enum { T_WS, T_COMMENT, T_STRING, T_QIDENT, T_WORD, T_NUMBER, T_PARAM, T_OP, T_PUNCT };

typedef struct {
    int kind;
    Py_ssize_t start, end;
    Py_UCS4 ch; /* the character of a T_PUNCT token */
} Tok;

#define BAIL (-1)

static int is_space_c(Py_UCS4 c) { return c < 128 ? (c == ' ' || (c >= 9 && c <= 13) || (c >= 28 && c <= 31)) : Py_UNICODE_ISSPACE(c); }
static int is_ascii_digit(Py_UCS4 c) { return c >= '0' && c <= '9'; }
static int is_ascii_alpha(Py_UCS4 c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
/* `\w` of Python's re for str: letters, digits, underscore. */
static int is_word_c(Py_UCS4 c) { return c < 128 ? (is_ascii_alpha(c) || is_ascii_digit(c) || c == '_') : Py_UNICODE_ISALNUM(c); }
/* `[^\W\d]`: a word character that is not a decimal digit. */
static int is_word_start(Py_UCS4 c) { return c < 128 ? (is_ascii_alpha(c) || c == '_') : (Py_UNICODE_ISALNUM(c) && !Py_UNICODE_ISDECIMAL(c)); }

static int is_op_char(const Src *s, Py_UCS4 c)
{
    switch (c) {
    case '+': case '-': case '*': case '/': case '<': case '>': case '=': case '!':
    case '~': case '|': case '&': case '^': case '%':
        return 1;
    case '#': return !(s->flags & F_HASH);
    case '@': return !(s->flags & (F_AT_PARAM | F_AT_VAR));
    case '?': return !(s->flags & F_QUESTION);
    default: return 0;
    }
}

/* End of a line comment starting at i, or -1 if none starts there. */
static Py_ssize_t line_comment_end(const Src *s, Py_ssize_t i)
{
    Py_UCS4 c = RD(s, i);
    int starts = 0;
    if (c == '-' && i + 1 < s->n && RD(s, i + 1) == '-') {
        starts = 1;
        if (s->flags & F_DASH_SPACE && i + 2 < s->n) {
            Py_UCS4 f = RD(s, i + 2);
            if (!is_space_c(f) && f >= 32)
                starts = 0;
        }
    } else if (c == '#' && (s->flags & F_HASH)) {
        starts = 1;
    }
    if (!starts)
        return -1;
    Py_ssize_t nl = find_of(s, i, "\n", 1);
    return nl < 0 ? s->n : nl;
}

static int starts_comment(const Src *s, Py_ssize_t i)
{
    return (RD(s, i) == '/' && i + 1 < s->n && RD(s, i + 1) == '*') || line_comment_end(s, i) >= 0;
}

/* Quoted run starting at i (which holds the opening quote). Sets *terminated. */
static Py_ssize_t quoted_end(const Src *s, Py_ssize_t i, char close, int doubled, int backslash, int *terminated)
{
    char needles[2] = {close, '\\'};
    int nn = backslash ? 2 : 1;
    Py_ssize_t j = i + 1;
    for (;;) {
        j = find_of(s, j, needles, nn);
        if (j < 0) {
            *terminated = 0;
            return s->n;
        }
        if (RD(s, j) == '\\') {
            j += 2;
            if (j > s->n)
                j = s->n;
            if (j >= s->n) {
                *terminated = 0;
                return s->n;
            }
            continue;
        }
        if (doubled && j + 1 < s->n && RD(s, j + 1) == (Py_UCS4)close) {
            j += 2;
            continue;
        }
        *terminated = 1;
        return j + 1;
    }
}

static Py_ssize_t block_comment_end(const Src *s, Py_ssize_t i, int nested, int *terminated)
{
    int depth = 1;
    Py_ssize_t j = i + 2;
    for (;;) {
        j = find_of(s, j, nested ? "*/" : "*", nested ? 2 : 1);
        if (j < 0) {
            *terminated = 0;
            return s->n;
        }
        if (RD(s, j) == '*') {
            if (j + 1 < s->n && RD(s, j + 1) == '/') {
                j += 2;
                if (--depth == 0) {
                    *terminated = 1;
                    return j;
                }
            } else {
                j += 1;
            }
        } else { /* '/' (nested only) */
            if (j + 1 < s->n && RD(s, j + 1) == '*') {
                depth++;
                j += 2;
            } else {
                j += 1;
            }
        }
    }
}

/* A non-ASCII decimal digit (the lexer's \d matches those; this scanner does not model them). */
static int foreign_digit(const Src *s, Py_ssize_t j)
{
    if (j >= s->n)
        return 0;
    Py_UCS4 c = RD(s, j);
    return c > 127 && Py_UNICODE_ISDECIMAL(c);
}

/* Number token at i per the lexer's regex; returns its end, BAIL on non-ASCII decimal digits. */
static Py_ssize_t number_end(const Src *s, Py_ssize_t i)
{
    const Py_ssize_t n = s->n;
    Py_ssize_t j;
    if (RD(s, i) == '0' && i + 2 < n && (RD(s, i + 1) == 'x' || RD(s, i + 1) == 'X')) {
        Py_ssize_t k = i + 2;
        while (k < n) {
            Py_UCS4 h = RD(s, k);
            if (is_ascii_digit(h) || (h >= 'a' && h <= 'f') || (h >= 'A' && h <= 'F'))
                k++;
            else
                break;
        }
        if (k > i + 2)
            return k;
    }
    j = i + 1;
    if (RD(s, i) == '.') { /* \.\d[\d_]* */
        while (j < n && (is_ascii_digit(RD(s, j)) || RD(s, j) == '_'))
            j++;
    } else { /* \d[\d_]*(\.\d*)? */
        while (j < n && (is_ascii_digit(RD(s, j)) || RD(s, j) == '_'))
            j++;
        if (foreign_digit(s, j))
            return BAIL;
        if (j < n && RD(s, j) == '.') {
            j++;
            while (j < n && is_ascii_digit(RD(s, j)))
                j++;
        }
    }
    if (foreign_digit(s, j))
        return BAIL;
    if (j < n && (RD(s, j) == 'e' || RD(s, j) == 'E')) {
        Py_ssize_t k = j + 1;
        if (k < n && (RD(s, k) == '+' || RD(s, k) == '-'))
            k++;
        if (foreign_digit(s, k))
            return BAIL;
        if (k < n && is_ascii_digit(RD(s, k))) {
            while (k < n && is_ascii_digit(RD(s, k)))
                k++;
            if (foreign_digit(s, k))
                return BAIL;
            j = k;
        }
    }
    return j;
}

static int next_token(const Src *s, Py_ssize_t i, Tok *t)
{
    const Py_ssize_t n = s->n;
    const Py_UCS4 c = RD(s, i);
    t->start = i;
    t->ch = c;
    int term;

    if (is_space_c(c)) {
        Py_ssize_t j = i + 1;
        while (j < n && is_space_c(RD(s, j)))
            j++;
        t->kind = T_WS; t->end = j;
        return 0;
    }
    Py_ssize_t e = line_comment_end(s, i);
    if (e >= 0) {
        t->kind = T_COMMENT; t->end = e;
        return 0;
    }
    if (c == '/' && i + 1 < n && RD(s, i + 1) == '*') {
        t->kind = T_COMMENT; t->end = block_comment_end(s, i, (s->flags & F_NESTED) != 0, &term);
        return 0;
    }
    if (c == '\'' || (c == '"' && (s->flags & F_DQ_STRING))) {
        t->kind = T_STRING; t->end = quoted_end(s, i, (char)c, 1, (s->flags & F_BACKSLASH) != 0, &term);
        return 0;
    }
    if (c == '"' && (s->flags & F_ID_DQ)) {
        t->kind = T_QIDENT; t->end = quoted_end(s, i, '"', 1, 0, &term);
        return 0;
    }
    if (c == '`' && (s->flags & F_ID_BT)) {
        t->kind = T_QIDENT; t->end = quoted_end(s, i, '`', 1, 0, &term);
        return 0;
    }
    if (c == '[' && (s->flags & F_ID_BR)) {
        t->kind = T_QIDENT; t->end = quoted_end(s, i, ']', 0, 0, &term);
        return 0;
    }
    if (c > 127 && Py_UNICODE_ISDECIMAL(c))
        return BAIL;
    if (c == '.' && foreign_digit(s, i + 1))
        return BAIL;
    if (is_ascii_digit(c) || (c == '.' && i + 1 < n && is_ascii_digit(RD(s, i + 1)))) {
        Py_ssize_t j = number_end(s, i);
        if (j == BAIL)
            return BAIL;
        t->kind = T_NUMBER; t->end = j;
        return 0;
    }
    if (c < 128 ? (is_ascii_alpha(c) || c == '_') : 1) {
        if (c < 128 || is_word_start(c)) {
            Py_ssize_t j = i + 1;
            while (j < n && (is_word_c(RD(s, j)) || RD(s, j) == '$'))
                j++;
            if ((s->flags & F_E_PREFIX) && j == i + 1 && (c == 'E' || c == 'e') && j < n && RD(s, j) == '\'') {
                t->kind = T_STRING; t->end = quoted_end(s, j, '\'', 1, 1, &term);
                return 0;
            }
            t->kind = T_WORD; t->end = j;
            return 0;
        }
        t->kind = T_OP; t->end = i + 1; /* non-ASCII, not a word character */
        return 0;
    }
    if (c == '$') {
        if (s->flags & F_DOLLAR_NUM) {
            Py_ssize_t j = i + 1;
            while (j < n && is_ascii_digit(RD(s, j)))
                j++;
            if (foreign_digit(s, j))
                return BAIL;
            if (j > i + 1) {
                t->kind = T_PARAM; t->end = j;
                return 0;
            }
        }
        if (s->flags & F_DOLLAR_QUOTE) {
            Py_ssize_t j = i + 1;
            if (j < n && is_word_start(RD(s, j))) {
                j++;
                while (j < n && is_word_c(RD(s, j)))
                    j++;
            }
            if (j < n && RD(s, j) == '$') {
                Py_ssize_t tag_len = j + 1 - i;
                Py_ssize_t p = j + 1;
                for (;;) {
                    p = find_of(s, p, "$", 1);
                    if (p < 0 || p + tag_len > n) {
                        t->kind = T_STRING; t->end = n;
                        return 0;
                    }
                    Py_ssize_t k = 0;
                    while (k < tag_len && RD(s, p + k) == RD(s, i + k))
                        k++;
                    if (k == tag_len) {
                        t->kind = T_STRING; t->end = p + tag_len;
                        return 0;
                    }
                    p++;
                }
            }
        }
        if (s->flags & F_DOLLAR_NAMED) {
            Py_ssize_t j = i + 1;
            while (j < n && is_word_c(RD(s, j)))
                j++;
            if (j > i + 1) {
                t->kind = T_PARAM; t->end = j;
                return 0;
            }
        }
    }
    if (c == '?' && (s->flags & F_QUESTION)) {
        Py_ssize_t j = i + 1;
        while (j < n && is_ascii_digit(RD(s, j)))
            j++;
        if (foreign_digit(s, j))
            return BAIL;
        t->kind = T_PARAM; t->end = j;
        return 0;
    }
    if (c == ':') {
        if (i + 1 < n && RD(s, i + 1) == ':') {
            t->kind = T_OP; t->end = i + 2;
            return 0;
        }
        if ((s->flags & F_COLON) && i + 1 < n && is_word_start(RD(s, i + 1))) {
            Py_ssize_t j = i + 2;
            while (j < n && is_word_c(RD(s, j)))
                j++;
            t->kind = T_PARAM; t->end = j;
            return 0;
        }
        t->kind = T_OP; t->end = i + 1;
        return 0;
    }
    if (c == '@') {
        if (s->flags & F_AT_VAR) {
            Py_ssize_t j = i + 1;
            if (j < n && RD(s, j) == '@')
                j++;
            Py_ssize_t k = j;
            while (k < n && (is_word_c(RD(s, k)) || RD(s, k) == '.' || RD(s, k) == '$'))
                k++;
            if (k > j) {
                t->kind = T_PARAM; t->end = k;
                return 0;
            }
        } else if (s->flags & F_AT_PARAM) {
            Py_ssize_t j = i + 1;
            while (j < n && is_word_c(RD(s, j)))
                j++;
            if (j > i + 1) {
                t->kind = T_PARAM; t->end = j;
                return 0;
            }
        }
    }
    if (c == '(' || c == ')' || c == ',' || c == ';' || c == '.' || c == '[' || c == ']') {
        t->kind = T_PUNCT; t->end = i + 1;
        return 0;
    }
    Py_ssize_t j = i + 1;
    while (j < n && is_op_char(s, RD(s, j)) && !starts_comment(s, j))
        j++;
    t->kind = T_OP; t->end = j;
    return 0;
}

/* ------------------------------------------------------------------ statement splitter */

/* Characters whose str.upper() contains ASCII letters (ss, FI, I, S, ...). Comparing such a word
 * byte-wise would disagree with the Python splitter, so the caller falls back. */
static int upper_special(Py_UCS4 c)
{
    return c == 0xDF || c == 0x131 || c == 0x149 || c == 0x17F || c == 0x1F0 ||
           (c >= 0x1E96 && c <= 0x1E9A) || (c >= 0xFB00 && c <= 0xFB06);
}

static int word_is(Src *s, const Tok *t, const char *upper)
{
    Py_ssize_t len = (Py_ssize_t)strlen(upper);
    if (s->kind != PyUnicode_1BYTE_KIND || t->end - t->start != len) {
        for (Py_ssize_t k = t->start; k < t->end; k++)
            if (RD(s, k) > 127 && upper_special(RD(s, k))) {
                s->bail = 1;
                return 0;
            }
        if (t->end - t->start != len)
            return 0;
    } else {
        for (Py_ssize_t k = t->start; k < t->end; k++)
            if (RD(s, k) == 0xDF) {
                s->bail = 1;
                return 0;
            }
    }
    for (Py_ssize_t k = 0; k < len; k++) {
        Py_UCS4 c = RD(s, t->start + k);
        if (c >= 'a' && c <= 'z')
            c -= 32;
        if (c != (Py_UCS4)(unsigned char)upper[k])
            return 0;
    }
    return 1;
}

static int word_in(Src *s, const Tok *t, const char *const *set)
{
    for (; *set; set++)
        if (word_is(s, t, *set))
            return 1;
    return 0;
}

static const char *const OBJECTS[] = {"TRIGGER", "PROCEDURE", "FUNCTION", "EVENT", NULL};
static const char *const END_SUFFIXES[] = {"IF", "LOOP", "WHILE", "REPEAT", NULL};
#define COMPOUND_LOOKAHEAD 24

typedef struct {
    Py_ssize_t begin, end, first_start, first_end;
    int terminated, first_kind; /* 0 other, 1 word, 2 "(" */
} Stmt;

static PyObject *native_split(PyObject *self, PyObject *args)
{
    PyObject *text;
    unsigned flags;
    if (!PyArg_ParseTuple(args, "OI", &text, &flags) || !PyUnicode_Check(text))
        return PyErr_Format(PyExc_TypeError, "split(text: str, flags: int)");
    Src s = {PyUnicode_DATA(text), PyUnicode_KIND(text), PyUnicode_GET_LENGTH(text), flags, 0};

    Py_ssize_t cap = 64, count = 0;
    Stmt *out = PyMem_Malloc(cap * sizeof(Stmt));
    if (!out)
        return PyErr_NoMemory();

    Py_ssize_t begin = 0, pos = 0;
    int have_first = 0, sig_count = 0, depth = 0, compound = 0, skip_next_case = 0, paren_seen = 0;
    Py_ssize_t first_start = 0, first_end = 0;
    int first_kind = 0, first_is_create = 0;
    int bailed = 0;

    while (pos < s.n) {
        Tok t;
        if (next_token(&s, pos, &t) == BAIL) {
            bailed = 1;
            break;
        }
        pos = t.end;
        if (t.kind == T_WS || t.kind == T_COMMENT)
            continue;
        if (!have_first) {
            have_first = 1;
            sig_count = depth = compound = skip_next_case = paren_seen = 0;
            first_start = t.start;
            first_end = t.end;
            first_kind = t.kind == T_WORD ? 1 : (t.kind == T_PUNCT && t.ch == '(' ? 2 : 0);
            first_is_create = t.kind == T_WORD && word_is(&s, &t, "CREATE");
        }
        sig_count++;

        if (t.kind == T_PUNCT && t.ch == ';') {
            if (depth > 0)
                continue;
            if (sig_count > 1) {
                if (count == cap) {
                    cap *= 2;
                    Stmt *grown = PyMem_Realloc(out, cap * sizeof(Stmt));
                    if (!grown) {
                        PyMem_Free(out);
                        return PyErr_NoMemory();
                    }
                    out = grown;
                }
                out[count++] = (Stmt){begin, t.end, first_start, first_end, 1, first_kind};
            }
            begin = t.end;
            have_first = 0;
            continue;
        }
        if (t.kind == T_PUNCT && t.ch == '(' && !compound) {
            paren_seen = 1;
            continue;
        }
        if (t.kind != T_WORD)
            continue;
        if (!compound && sig_count <= COMPOUND_LOOKAHEAD)
            compound = first_is_create && word_in(&s, &t, OBJECTS) && !paren_seen;
        if (!compound)
            continue;
        if (skip_next_case) {
            skip_next_case = 0;
            if (word_is(&s, &t, "CASE"))
                continue;
        }
        if (word_is(&s, &t, "BEGIN") || word_is(&s, &t, "CASE")) {
            depth++;
        } else if (word_is(&s, &t, "END")) {
            /* the next significant token decides: END IF / LOOP / WHILE / REPEAT close nothing counted */
            Py_ssize_t p = t.end;
            int suffix = 0;
            while (p < s.n) {
                Tok nx;
                if (next_token(&s, p, &nx) == BAIL) {
                    bailed = 1;
                    break;
                }
                p = nx.end;
                if (nx.kind == T_WS || nx.kind == T_COMMENT)
                    continue;
                suffix = nx.kind == T_WORD && word_in(&s, &nx, END_SUFFIXES);
                break;
            }
            if (bailed)
                break;
            if (!suffix) {
                depth--;
                skip_next_case = 1;
            }
        }
    }
    if (bailed || s.bail) {
        PyMem_Free(out);
        Py_RETURN_NONE;
    }
    if (have_first) {
        if (count == cap) {
            cap += 1;
            Stmt *grown = PyMem_Realloc(out, cap * sizeof(Stmt));
            if (!grown) {
                PyMem_Free(out);
                return PyErr_NoMemory();
            }
            out = grown;
        }
        out[count++] = (Stmt){begin, s.n, first_start, first_end, 0, first_kind};
    }
    PyObject *list = PyList_New(count);
    if (!list) {
        PyMem_Free(out);
        return NULL;
    }
    for (Py_ssize_t k = 0; k < count; k++) {
        PyObject *row = Py_BuildValue("(nnnniI)", out[k].begin, out[k].end, out[k].first_start, out[k].first_end,
                                      out[k].terminated, (unsigned)out[k].first_kind);
        if (!row) {
            Py_DECREF(list);
            PyMem_Free(out);
            return NULL;
        }
        PyList_SET_ITEM(list, k, row);
    }
    PyMem_Free(out);
    return list;
}

/* ------------------------------------------------------------------ Python API */

static PyObject *py_find_first_of(PyObject *self, PyObject *args)
{
    PyObject *text, *needles;
    Py_ssize_t start;
    if (!PyArg_ParseTuple(args, "OnO", &text, &start, &needles) || !PyUnicode_Check(text) || !PyUnicode_Check(needles))
        return PyErr_Format(PyExc_TypeError, "find_first_of(text: str, start: int, needles: str)");
    Py_ssize_t nn = PyUnicode_GET_LENGTH(needles);
    if (nn < 1 || nn > MAX_NEEDLES)
        return PyErr_Format(PyExc_ValueError, "1 to %d needles are supported", MAX_NEEDLES);
    char nd[MAX_NEEDLES];
    for (Py_ssize_t k = 0; k < nn; k++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(needles, k);
        if (c > 127)
            return PyErr_Format(PyExc_ValueError, "needles must be ASCII");
        nd[k] = (char)c;
    }
    if (start < 0)
        start = 0;
    Src s = {PyUnicode_DATA(text), PyUnicode_KIND(text), PyUnicode_GET_LENGTH(text), 0, 0};
    return PyLong_FromSsize_t(find_of(&s, start, nd, (int)nn));
}

static PyObject *py_implementation(PyObject *self, PyObject *noargs)
{
    return PyUnicode_FromString(simd_enabled ? impl_name : "scalar");
}

static PyObject *py_set_simd(PyObject *self, PyObject *arg)
{
    simd_enabled = PyObject_IsTrue(arg);
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"find_first_of", py_find_first_of, METH_VARARGS, "Index of the first character equal to one of the ASCII needles, or -1."},
    {"split", native_split, METH_VARARGS, "Split a SQL script; None if the input needs the Python splitter."},
    {"implementation", py_implementation, METH_NOARGS, "Active scanner: avx2, sse2 or scalar."},
    {"set_simd", py_set_simd, METH_O, "Enable or disable the vector scanners (for benchmarks and tests)."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_native", NULL, -1, methods};

PyMODINIT_FUNC PyInit__native(void)
{
    select_impl();
    return PyModule_Create(&module);
}
