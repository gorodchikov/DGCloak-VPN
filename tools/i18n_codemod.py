# -*- coding: utf-8 -*-
"""Кодмод: обернуть кириллические строковые литералы в self.t(...) / T(...)
внутри display-контекстов cloak_admin.py. Позиционная правка справа налево."""
import ast, io, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "cloak_admin.py"
src = io.open(SRC, encoding="utf-8").read()
bsrc = src.encode("utf-8")
blines = bsrc.splitlines(keepends=True)
# абсолютные офсеты начала строк (в байтах — col_offset у ast байтовый)
offs = [0]
for l in blines:
    offs.append(offs[-1] + len(l))

def pos(node):
    return offs[node.lineno - 1] + node.col_offset

def endpos(node):
    return offs[node.end_lineno - 1] + node.end_col_offset

CYR = __import__("re").compile(r"[А-Яа-яЁё]")
tree = ast.parse(src)

# имена вызовов, внутри которых кириллические литералы — display-текст
TARGET_FNS = {
    "say", "vsay", "_say", "_mark_log",
    "showinfo", "showerror", "showwarning", "askyesno", "askokcancel",
    "askstring", "asksaveasfilename", "askopenfilename",
    "Tooltip", "Label", "Button", "Checkbutton", "Labelframe",
    "title", "heading", "config", "set", "add", "insert",
}
RAISE_CLASSES = {"SSHErr", "CloakAPIErr", "RuntimeError"}
SKIP_WRAPS = {"t", "T"}          # уже обёрнуто
SAFE_SET = {"set"}               # .set(...) — только на v_* StringVar? нет, любой .set с кириллицей — текст

# литералы-ключи словарей/списков данных, которые НЕЛЬЗЯ трогать:
# lbl-мапа валидатора (ключи — имена полей, значения — display),
# heads/_user_tips/_dep_tips — ключи RU, значения display,
# STEPS — имена шагов (оборачиваем в точке показа), LANG-комбобокс значения
SKIP_VALUE_KEY_LITERALS = {"Русский", "English", "deploy", "users"}

def is_docstring(node, parent):
    return isinstance(parent, ast.Expr)

def call_name(fn):
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""

wraps = []   # (start, end, wrapper_name)

class V(ast.NodeVisitor):
    def __init__(self):
        self.stack = []      # стек родителей
        self.in_method = []  # есть ли self (внутри функции с self)
        self.target_depth = 0  # внутри display-вызова?

    def visit(self, node):
        parent = self.stack[-1] if self.stack else None
        is_doc = (isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and isinstance(parent, ast.Expr))
        self.stack.append(node)
        if is_doc:
            self.stack.pop()
            return  # docstring — не обходим (там только литерал)
        self.generic_visit(node)
        self.stack.pop()

    def generic_visit(self, node):
        parent = self.stack[-1] if self.stack else None

        if isinstance(node, ast.FunctionDef):
            has_self = bool(node.args.args) and node.args.args[0].arg == "self"
            # вложенные функции видят self замыканием — флаг не сбрасываем
            self.in_method.append(has_self or bool(self.in_method and
                                                   self.in_method[-1]))
            # только body: дефолты/аннотации вычисляются при def — там self нет
            for ch in node.body:
                self.visit(ch)
            self.in_method.pop()
            return

        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name in TARGET_FNS or name in RAISE_CLASSES:
                self.target_depth += 1
                for ch in ast.iter_child_nodes(node):
                    self.visit(ch)
                self.target_depth -= 1
                return

        if isinstance(node, ast.Compare):
            # сравнения — это проверки сохранённых данных (старые RU-записи,
            # маркеры внешнего вывода): операнды не переводим
            return

        if isinstance(node, ast.Dict):
            # значения dict с кириллицей — display (lbl/heads/_tips).
            # ключи НЕ трогаем — по ним идёт lookup
            for k, v in zip(node.keys, node.values):
                if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                        and CYR.search(v.value):
                    # только в методах (есть self) и только «длинные» подписи —
                    # иначе это может быть data-словарь
                    if self.in_method and self.in_method[-1]:
                        wraps.append((pos(v), endpos(v), "self.t"))
                else:
                    self.visit(v)
            return

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CYR.search(node.value) and self.in_method:
                if node.value in SKIP_VALUE_KEY_LITERALS:
                    return
                if isinstance(parent, ast.JoinedStr):
                    return
                w = "self.t" if self.in_method[-1] else "T"
                wraps.append((pos(node), endpos(node), w))
            return

        for ch in ast.iter_child_nodes(node):
            self.visit(ch)

V().visit(tree)

# применяем справа налево на БАЙТОВОМ представлении (col_offset — байты)
out = bsrc
n = 0
seen = set()
for s, e, w in sorted(wraps, key=lambda x: x[0], reverse=True):
    if (s, e) in seen:
        continue
    seen.add((s, e))
    prefix = out[max(0, s - 8):s]
    if prefix.rstrip().endswith((b"self.t(", b"T(")):
        continue
    out = out[:s] + w.encode() + b"(" + out[s:e] + b")" + out[e:]
    n += 1

io.open(SRC, "wb").write(out)
print("wrapped:", n)
