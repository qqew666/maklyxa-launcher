#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""luax — карманный интерпретатор подмножества Lua для скриптов лаунчера.

Скрипт выполняется в той же программе, но без доступа к машине: у движка
нет файловых, сетевых и процессных функций. Наружу торчат только те
 объекты и вызовы, которые передаёт宿主 через env. Ограничения по шагам,
глубине и длине строк означают, что бесконечный цикл не подвесит UI.

Поддерживается: local, присваивания (включая a.b.c и t[i]), if/elseif/
else, while, repeat/until, числовой for, for-in по pairs/ipairs, функции
(включая анонимные и замыкания), return, break, таблицы-конструкторы,
строки, арифметика, конкатенация, #, and/or/not, сравнения, и минимальные
библиотеки string/math/table плюс print/tostring/tonumber/type/assert.

Не поддерживается (намеренно): require, io, os, потоки, metatables,
goto, множественные возвраты, метод-синтаксис с self.
"""

import math
import re

__all__ = ["LuaxError", "run_script", "Iterator", "LIMITS"]

LIMITS = {
    "steps": 2_000_000,      # примерно 1-2 секунды чистых вычислений
    "depth": 90,             # вложенность вызовов
    "string_len": 60_000,    # длина одной строки
    "table_len": 5_000,      # элементов в таблице
}

KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
}

_TWO_CHAR = ("..", "==", "~=", "<=", ">=")
_ONE_CHAR = "+-*/%^#(){}[].,;:=<>~"

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\\\", '"': '"',
            "'": "'", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


class LuaxError(Exception):
    """Ошибка скрипта: синтаксис, тип или срабатывание лимита."""

    def __init__(self, message, line=None):
        self.line = line
        self.message = str(message)
        super().__init__(self.message)

    def __str__(self):
        if self.line:
            return f"строка {self.line}: {self.message}"
        return self.message


# ---- лексер -------------------------------------------------------------------


def tokenize(src):
    tokens = []
    i, line, n = 0, 1, len(src)
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if src.startswith("--", i):
            if src.startswith("--[[", i):
                close = src.find("]]", i + 4)
                if close < 0:
                    raise LuaxError("не закрыт блочный комментарий", line)
                line += src.count("\n", i, close)
                i = close + 2
                continue
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch in "0123456789":
            match = re.match(r"0[xX][0-9a-fA-F]+|\d+\.?\d*([eE][+-]?\d+)?",
                             src[i:])
            text = match.group(0)
            if text.lower().startswith("0x"):
                tokens.append(("num", int(text, 16), line))
            else:
                tokens.append(("num", _to_number(text), line))
            i += len(text)
            continue
        if ch in "\"'":
            quote = ch
            start_line = line
            buf, j = [], i + 1
            while j < n:
                cur = src[j]
                if cur == "\\":
                    if j + 1 >= n:
                        break
                    nxt = src[j + 1]
                    buf.append(_ESCAPES.get(nxt, nxt))
                    j += 2
                    continue
                if cur == quote:
                    j += 1
                    break
                if cur == "\n":
                    raise LuaxError("строка не закрыта", line)
                buf.append(cur)
                j += 1
            else:
                raise LuaxError("строка не закрыта", line)
            text = "".join(buf)
            if len(text) > LIMITS["string_len"]:
                raise LuaxError("строка длиннее разрешённой", start_line)
            tokens.append(("str", text, start_line))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            tokens.append(("kw" if word in KEYWORDS else "name", word, line))
            i = j
            continue
        pair = src[i:i + 2]
        if pair in _TWO_CHAR:
            tokens.append(("op", pair, line))
            i += 2
            continue
        if ch in _ONE_CHAR:
            tokens.append(("op", ch, line))
            i += 1
            continue
        raise LuaxError(f"непонятный символ {ch!r}", line)
    tokens.append(("eof", "", line))
    return tokens


def _to_number(text):
    value = float(text)
    return int(value) if value.is_integer() and "." not in text \
        and "e" not in text.lower() else value


# ---- парсер -------------------------------------------------------------------


class Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self, offset=0):
        return self.t[min(self.i + offset, len(self.t) - 1)]

    def kind(self):
        return self.peek()[0]

    def value(self):
        return self.peek()[1]

    def line(self):
        return self.peek()[2]

    def advance(self):
        token = self.t[self.i]
        if self.i < len(self.t) - 1:
            self.i += 1
        return token

    def at(self, kind, value=None):
        token = self.peek()
        return token[0] == kind and (value is None or token[1] == value)

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.advance()
        return None

    def expect(self, kind, value=None, what=None):
        if not self.at(kind, value):
            raise LuaxError(
                f"ожидалось {what or value or kind}, "
                f"а тут {self.value()!r}", self.line())
        return self.advance()

    # -- блок

    def block(self, stop=("end", "else", "elseif", "until")):
        statements = []
        while not any(self.at("kw", word) for word in stop) \
                and self.kind() != "eof":
            statements.append(self.statement())
        return statements

    def chunk(self):
        statements = self.block(("end",))
        if self.kind() != "eof":
            raise LuaxError(f"лишнее {self.value()!r}", self.line())
        return ["chunk", statements]

    # -- операторы

    def statement(self):
        line = self.line()
        if self.at("kw", "if"):
            return self.if_stmt()
        if self.at("kw", "while"):
            self.advance()
            cond = self.expression()
            self.expect("kw", "do", "do")
            body = self.block()
            self.expect("kw", "end", "end")
            return ["while", cond, body, line]
        if self.at("kw", "repeat"):
            self.advance()
            body = self.block(("until",))
            self.expect("kw", "until", "until")
            cond = self.expression()
            return ["repeat", body, cond, line]
        if self.at("kw", "for"):
            return self.for_stmt()
        if self.at("kw", "function"):
            return self.function_stmt()
        if self.at("kw", "local"):
            self.advance()
            if self.at("kw", "function"):
                self.advance()
                name = self.expect("name", what="имя функции")[1]
                params, body = self.function_body()
                return ["localfn", name, params, body, line]
            names = [self.expect("name", what="имя")[1]]
            while self.accept("op", ","):
                names.append(self.expect("name", what="имя")[1])
            values = []
            if self.accept("op", "="):
                values = self.expr_list()
            return ["local", names, values, line]
        if self.at("kw", "return"):
            self.advance()
            values = [] if self.at("kw", "end") or self.at("kw", "else") \
                or self.at("kw", "elseif") or self.at("kw", "until") \
                or self.kind() == "eof" else self.expr_list()
            self.accept("op", ";")
            return ["return", values, line]
        if self.at("kw", "break"):
            self.advance()
            return ["break", line]
        first = self.expression()
        if self.at("op", "=") or self.at("op", ","):
            targets = [first]
            while self.accept("op", ","):
                targets.append(self.expression())
            self.expect("op", "=", "=")
            values = self.expr_list()
            for target in targets:
                if target[0] not in ("var", "index"):
                    raise LuaxError("присваивание не переменной", line)
            return ["assign", targets, values, line]
        if first[0] != "call":
            raise LuaxError("не оператор и не вызов",
                            first[3] if first[0] == "var" else line)
        self.accept("op", ";")
        return ["expr", first, line]

    def if_stmt(self):
        line = self.line()
        self.expect("kw", "if", "if")
        arms, elses = [], None
        cond = self.expression()
        self.expect("kw", "then", "then")
        arms.append((cond, self.block()))
        while self.at("kw", "elseif"):
            self.advance()
            branch = self.expression()
            self.expect("kw", "then", "then")
            arms.append((branch, self.block()))
        if self.accept("kw", "else"):
            elses = self.block()
        self.expect("kw", "end", "end")
        return ["if", arms, elses, line]

    def for_stmt(self):
        line = self.line()
        self.expect("kw", "for", "for")
        name = self.expect("name", what="имя")[1]
        if self.accept("op", "="):
            start = self.expression()
            self.expect("op", ",", ",")
            stop = self.expression()
            step = None
            if self.accept("op", ","):
                step = self.expression()
            self.expect("kw", "do", "do")
            body = self.block()
            self.expect("kw", "end", "end")
            return ["fornum", name, start, stop, step, body, line]
        names = [name]
        while self.accept("op", ","):
            names.append(self.expect("name", what="имя")[1])
        self.expect("kw", "in", "in")
        iterators = self.expr_list()
        self.expect("kw", "do", "do")
        body = self.block()
        self.expect("kw", "end", "end")
        return ["forin", names, iterators, body, line]

    def function_stmt(self):
        line = self.line()
        self.expect("kw", "function", "function")
        name = self.expect("name", what="имя функции")[1]
        target = ["var", name, None, line]
        while True:
            if self.at("op", ":"):
                raise LuaxError("методы с self не поддерживаются", line)
            if not self.accept("op", "."):
                break
            field = self.expect("name", what="имя поля")[1]
            target = ["index", target, ["str", field, None, None, line],
                      None, line]
        params, body = self.function_body()
        return ["fnglobal", target, params, body, line]

    def function_body(self):
        self.expect("op", "(", "(")
        params = []
        if not self.at("op", ")"):
            params.append(self.expect("name", what="параметр")[1])
            while self.accept("op", ","):
                params.append(self.expect("name", what="параметр")[1])
        self.expect("op", ")", ")")
        body = self.block()
        self.expect("kw", "end", "end")
        return params, body

    # -- выражения

    def expr_list(self):
        values = [self.expression()]
        while self.accept("op", ","):
            values.append(self.expression())
        return values

    def expression(self):
        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.at("kw", "or"):
            self.advance()
            left = ["bin", "or", left, self.and_expr(), self.line()]
        return left

    def and_expr(self):
        left = self.compare()
        while self.at("kw", "and"):
            self.advance()
            left = ["bin", "and", left, self.compare(), self.line()]
        return left

    def compare(self):
        left = self.concat()
        while self.kind() == "op" and self.value() in \
                ("==", "~=", "<", "<=", ">", ">="):
            op = self.advance()[1]
            left = ["bin", op, left, self.concat(), self.line()]
        return left

    def concat(self):
        left = self.additive()
        if self.at("op", ".."):
            self.advance()
            return ["bin", "..", left, self.concat(), self.line()]
        return left

    def additive(self):
        left = self.multiplicative()
        while self.kind() == "op" and self.value() in ("+", "-"):
            op = self.advance()[1]
            left = ["bin", op, left, self.multiplicative(), self.line()]
        return left

    def multiplicative(self):
        left = self.unary()
        while self.kind() == "op" and self.value() in ("*", "/", "%", "^"):
            op = self.advance()[1]
            left = ["bin", op, left, self.unary(), self.line()]
        return left

    def unary(self):
        if self.at("kw", "not"):
            line = self.line()
            self.advance()
            return ["un", "not", self.unary(), line]
        if self.at("op", "-"):
            line = self.line()
            self.advance()
            return ["un", "-", self.unary(), line]
        if self.at("op", "#"):
            line = self.line()
            self.advance()
            return ["len", self.unary(), line]
        return self.primary()

    def primary(self):
        kind, value, line = self.peek()
        if kind == "kw" and value in ("nil", "true", "false"):
            self.advance()
            return ["nil" if value == "nil" else "bool",
                    None if value == "nil" else value == "true",
                    None, line]
        if kind in ("num", "str"):
            self.advance()
            return [kind, value, None, line]
        if kind == "name":
            return self.primary_name()
        if kind == "kw" and value == "function":
            self.advance()
            params, body = self.function_body()
            return ["func", params, body, None, line]
        if self.at("op", "{"):
            return self.table_ctor()
        if self.at("op", "("):
            self.advance()
            inner = self.expression()
            self.expect("op", ")", ")")
            if inner[0] in ("var", "index", "call"):
                return self.suffixes(inner)
            return inner
        raise LuaxError(f"неожиданное {value!r}", line)

    def primary_name(self):
        kind, value, line = self.peek()
        if kind != "name":
            raise LuaxError(f"ожидалось имя, а тут {value!r}", line)
        self.advance()
        node = ["var", value, None, line]
        return self.suffixes(node)

    def suffixes(self, node):
        line = self.line()
        while True:
            if self.at("op", "."):
                self.advance()
                field = self.expect("name", what="поле")[1]
                node = ["index", node, ["str", field, None, None, line],
                        None, line]
                continue
            if self.at("op", "["):
                self.advance()
                key = self.expression()
                self.expect("op", "]", "]")
                node = ["index", node, key, None, line]
                continue
            if self.at("op", "(") or self.at("str") or self.at("op", "{"):
                args = self.call_args()
                node = ["call", node, args, None, line]
                continue
            return node

    def call_args(self):
        line = self.line()
        if self.at("str"):
            return [["str", self.advance()[1], None, line]]
        if self.at("op", "{"):
            return [self.table_ctor()]
        self.expect("op", "(", "(")
        args = [] if self.at("op", ")") else self.expr_list()
        self.expect("op", ")", ")")
        return args

    def table_ctor(self):
        line = self.line()
        self.expect("op", "{", "{")
        rows = []
        while not self.at("op", "}"):
            if self.at("op", "["):
                self.advance()
                key = self.expression()
                self.expect("op", "]", "]")
                self.expect("op", "=", "=")
                rows.append((key, self.expression()))
            elif self.kind() == "name" and self.peek(1)[0] == "op" \
                    and self.peek(1)[1] == "=":
                name = self.advance()[1]
                self.advance()
                rows.append((["str", name, None, None, line],
                             self.expression()))
            else:
                rows.append((None, self.expression()))
            if not (self.accept("op", ",") or self.accept("op", ";")):
                break
        self.expect("op", "}", "}")
        return ["table", rows, None, line]


# ---- значения -----------------------------------------------------------------


class Table:
    """Lua-таблица: массив + хэш-часть."""

    __slots__ = ("array", "map")

    def __init__(self, array=None, map=None):
        self.array = array if array is not None else []
        self.map = map or {}

    def get(self, key):
        if isinstance(key, bool):
            key = str(key)
        if isinstance(key, int) and not isinstance(key, bool) \
                and 1 <= key <= len(self.array):
            return self.array[key - 1]
        if key is None:
            return None
        return self.map.get(key)

    def set(self, key, value):
        if isinstance(key, bool):
            key = str(key)
        if key is None:
            raise LuaxError("nil как ключ таблицы")
        if isinstance(key, int) and not isinstance(key, bool) and key > 0 \
                and key - len(self.array) <= 1 and not self.map:
            if key == len(self.array) + 1:
                self.array.append(value)
                return
            if 1 <= key <= len(self.array):
                self.array[key - 1] = value
                return
        self.map[key] = value

    def length(self):
        return len(self.array)

    def items(self):
        for index, value in enumerate(self.array, 1):
            yield index, value
        for key, value in self.map.items():
            yield key, value

    def push(self, value):
        if len(self.array) >= LIMITS["table_len"]:
            raise LuaxError("таблица слишком большая")
        self.array.append(value)

    def __repr__(self):
        return f"table({len(self.array)}+{len(self.map)})"


class Iterator:
    """Внутренний итератор для for-in (pairs, ipairs, хост-коллекции)."""

    def __init__(self, source):
        self.source = source

    def next(self):
        if hasattr(self.source, "__next__"):
            return next(self.source, None)
        return self.source()


class Function:
    __slots__ = ("params", "body", "scope", "name")

    def __init__(self, params, body, scope, name=""):
        self.params = params
        self.body = body
        self.scope = scope
        self.name = name


class HostFunction:
    __slots__ = ("callback", "name")

    def __init__(self, callback, name=""):
        self.callback = callback
        self.name = name or getattr(callback, "__name__", "host")


class Scope:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.vars:
                return scope.vars
            scope = scope.parent
        return None

    def get(self, name):
        table = self.lookup(name)
        return table[name] if table is not None else None

    def declare(self, name, value):
        self.vars[name] = value

    def assign(self, name, value):
        table = self.lookup(name)
        if table is None:
            self.vars[name] = value
        else:
            table[name] = value


_NIL = None


# ---- интерпретатор -------------------------------------------------------------


class Engine:
    def __init__(self, globals_, on_print, should_stop, steps):
        self.out = on_print or (lambda text: None)
        self.should_stop = should_stop
        self.budget = steps
        self.steps = 0
        self.depth = 0
        self.globals = Scope()
        self.globals.vars.update(globals_ or {})

    # -- учёт шагов

    def tick(self, line=None):
        self.steps += 1
        if self.steps > self.budget:
            raise LuaxError("превышен лимит шагов (слишком долгий скрипт)",
                            line)
        if self.should_stop is not None and self.steps % 4096 == 0 \
                and self.should_stop():
            raise LuaxError("скрипт остановлен вручную", line)

    # -- вызовы

    def call(self, target, args, line=None):
        if isinstance(target, HostFunction):
            try:
                return _unwrap(target.callback(*args))
            except LuaxError:
                raise
            except TypeError as exc:
                raise LuaxError(f"неподходящие аргументы: {exc}", line) \
                    from None
            except Exception as exc:  # noqa: BLE001 — границы хост-вызова
                raise LuaxError(f"ошибка хост-функции: {exc}", line) from None
        if not isinstance(target, Function):
            name = _type_name(target)
            raise LuaxError(f"вызов не функции ({name})", line)
        if self.depth >= LIMITS["depth"]:
            raise LuaxError("слишком глубокая рекурсия", line)
        scope = Scope(target.scope)
        for index, param in enumerate(target.params):
            scope.declare(param, args[index] if index < len(args) else _NIL)
        self.depth += 1
        try:
            self.exec_block(target.body, scope)
        except _Return as signal:
            return signal.values[0] if signal.values else _NIL
        finally:
            self.depth -= 1
        return _NIL

    def iterate(self, value):
        """Следующий кортеж значений итератора или None."""
        if isinstance(value, Iterator):
            return value.next()
        if isinstance(value, (HostFunction, Function)):
            result = self.call(value, [], None)
            if isinstance(result, Table):
                return tuple(result.array)
            if result is _NIL:
                return None
            return (result,)
        raise LuaxError("после in нужен итератор (pairs, ipairs)", None)

    # -- блоки

    def exec_block(self, statements, scope):
        for statement in statements:
            self.tick(statement[3] if len(statement) > 3 else None)
            self.exec(statement, scope)

    def exec(self, statement, scope):  # noqa: C901 - таблица операторов
        kind = statement[0]
        line = statement[-1] if isinstance(statement[-1], int) else None
        if kind == "local":
            _names, values = statement[1], statement[2]
            got = [self.eval(value, scope) for value in values]
            for index, name in enumerate(_names):
                scope.declare(name, got[index] if index < len(got) else _NIL)
            return
        if kind == "assign":
            targets, values = statement[1], statement[2]
            got = [self.eval(value, scope) for value in values]
            for index, target in enumerate(targets):
                value = got[index] if index < len(got) else _NIL
                self.store(target, value, scope, line)
            return
        if kind == "expr":
            self.eval(statement[1], scope)
            return
        if kind == "return":
            raise _Return([self.eval(value, scope) for value in statement[1]])
        if kind == "break":
            raise _Break()
        if kind == "if":
            for cond, body in statement[1]:
                if truthy(self.eval(cond, scope)):
                    self.exec_block(body, scope)
                    return
            if statement[2] is not None:
                self.exec_block(statement[2], scope)
            return
        if kind == "while":
            while truthy(self.eval(statement[1], scope)):
                self.tick(line)
                try:
                    self.exec_block(statement[2], scope)
                except _Break:
                    return
            return
        if kind == "repeat":
            while True:
                self.tick(line)
                try:
                    self.exec_block(statement[1], scope)
                except _Break:
                    return
                if truthy(self.eval(statement[2], scope)):
                    return
            return
        if kind == "fornum":
            _name, start, stop = statement[1], statement[2], statement[3]
            step = self.eval(statement[4], scope) if statement[4] else 1
            inner = Scope(scope)
            inner.declare(_name, self.eval(start, scope))
            limit = self.eval(stop, scope)
            step = step if step else 1
            while (inner.get(_name) <= limit if step > 0
                    else inner.get(_name) >= limit):
                self.tick(line)
                try:
                    self.exec_block(statement[5], inner)
                except _Break:
                    return
                inner.assign(_name, inner.get(_name) + step)
            return
        if kind == "forin":
            names, iterators = statement[1], statement[2]
            sources = [self.eval(value, scope) for value in iterators]
            current = sources[0] if sources else _NIL
            inner = Scope(scope)
            while True:
                self.tick(line)
                pair = self.iterate(current)
                if pair is None:
                    return
                for index, name in enumerate(names):
                    inner.declare(name, pair[index]
                                  if index < len(pair) else _NIL)
                try:
                    self.exec_block(statement[3], inner)
                except _Break:
                    return
            return
        if kind == "localfn":
            scope.declare(statement[1], _NIL)
            scope.assign(statement[1],
                         Function(statement[2], statement[3], scope,
                                  statement[1]))
            return
        if kind == "fnglobal":
            func = Function(statement[2], statement[3], scope)
            self.store(statement[1], func, scope, line)
            return
        raise LuaxError(f"неизвестный оператор {kind}", line)

    def store(self, target, value, scope, line):
        kind = target[0]
        if kind == "var":
            scope.assign(target[1], value)
            return
        if kind == "index":
            holder = self.eval(target[1], scope)
            key = self.eval(target[2], scope)
            if isinstance(holder, Table):
                if len(holder.array) + len(holder.map) >= \
                        LIMITS["table_len"]:
                    raise LuaxError("таблица переполнена", line)
                holder.set(key, value)
                return
            raise LuaxError("присваивание не в таблицу", line)
        raise LuaxError("присваивание не переменной", line)

    # -- выражения

    def eval(self, node, scope):  # noqa: C901
        kind = node[0]
        line = node[-1] if isinstance(node[-1], int) else None
        if kind == "nil":
            return _NIL
        if kind == "bool":
            return node[1]
        if kind in ("num", "str"):
            return node[1]
        if kind == "var":
            value = scope.get(node[1])
            if value is None and node[1] not in scope.vars \
                    and scope.lookup(node[1]) is None:
                return _NIL
            return value
        if kind == "index":
            holder = self.eval(node[1], scope)
            key = self.eval(node[2], scope)
            if isinstance(holder, Table):
                return holder.get(key)
            if isinstance(holder, str) and key == "len":
                return len(holder)
            if holder is _NIL:
                raise LuaxError("у nil нет полей — проверь имя переменной",
                                line)
            raise LuaxError(
                f"индексация типа {_type_name(holder)}", line)
        if kind == "call":
            callee = self.eval(node[1], scope)
            args = [self.eval(argument, scope) for argument in node[2]]
            return self.call(callee, args, line)
        if kind == "func":
            return Function(node[1], node[2], scope)
        if kind == "table":
            table = Table()
            for key, value in node[1]:
                got = self.eval(value, scope)
                if key is None:
                    table.push(got)
                else:
                    table.set(self.eval(key, scope), got)
            return table
        if kind == "len":
            value = self.eval(node[1], scope)
            if isinstance(value, str):
                return len(value)
            if isinstance(value, Table):
                return value.length()
            raise LuaxError("# применён к числу или nil", line)
        if kind == "un":
            operand = self.eval(node[2], scope)
            if node[1] == "not":
                return not truthy(operand)
            return -_as_number(operand, line)
        if kind == "bin":
            return self.binary(node, scope, line)
        raise LuaxError(f"неизвестное выражение {kind}", line)

    def binary(self, node, scope, line):
        op = node[1]
        if op == "and":
            left = self.eval(node[2], scope)
            return self.eval(node[3], scope) if truthy(left) else left
        if op == "or":
            left = self.eval(node[2], scope)
            return left if truthy(left) else self.eval(node[3], scope)
        left = self.eval(node[2], scope)
        right = self.eval(node[3], scope)
        if op == "==":
            return _equals(left, right)
        if op == "~=":
            return not _equals(left, right)
        if op == "..":
            text = _tostring(left) + _tostring(right)
            if len(text) > LIMITS["string_len"]:
                raise LuaxError("строка переросла лимит", line)
            return text
        if op in ("<", "<=", ">", ">="):
            if isinstance(left, str) and isinstance(right, str):
                pass
            elif isinstance(left, (int, float)) \
                    and isinstance(right, (int, float)):
                pass
            else:
                raise LuaxError("сравнение разных типов", line)
            return {
                "<": left < right, "<=": left <= right,
                ">": left > right, ">=": left >= right}[op]
        a = _as_number(left, line)
        b = _as_number(right, line)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise LuaxError("деление на ноль", line)
            result = a / b
            return int(result) if float(result).is_integer() \
                and not isinstance(a, float) and not isinstance(b, float) \
                else result
        if op == "%":
            if b == 0:
                raise LuaxError("остаток от нуля", line)
            return a - math.floor(a / b) * b
        if op == "^":
            return a ** b
        raise LuaxError(f"неизвестный оператор {op}", line)


class _Return(Exception):
    def __init__(self, values):
        self.values = values


class _Break(Exception):
    pass


# ---- семантика значений ---------------------------------------------------------


def truthy(value):
    return not (value is _NIL or value is False)


def _type_name(value):
    if value is _NIL:
        return "nil"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Table):
        return "table"
    if isinstance(value, (Function, HostFunction)):
        return "function"
    if isinstance(value, Iterator):
        return "function"
    return type(value).__name__


def _equals(left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
            and not isinstance(left, bool) and not isinstance(right, bool):
        return float(left) == float(right)
    return left is right or (type(left) is type(right) and left == right)


def _as_number(value, line=None):
    if isinstance(value, bool):
        raise LuaxError("число вместо boolean", line)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return _to_number(value.strip())
        except ValueError:
            raise LuaxError(f"не число: {value!r}", line) from None
    raise LuaxError(f"нужно число, а {_type_name(value)}", line)


def _tostring(value):
    if value is _NIL:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.14g}"
    if isinstance(value, str):
        return value
    if isinstance(value, Table):
        return f"table: 0x{id(value):08x}"
    if isinstance(value, (Function, HostFunction)):
        name = getattr(value, "name", "") or "function"
        return f"function: {name}"
    if isinstance(value, Iterator):
        return "function: iterator"
    return str(value)


def _unwrap(value):
    """Привести результат хост-функции к типу Lua."""
    if callable(value) and not isinstance(value, (HostFunction, Function)):
        return HostFunction(value, getattr(value, "__name__", "host"))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (Table, Function, HostFunction, Iterator)):
        return value
    if isinstance(value, (list, tuple)):
        table = Table()
        for item in value[:LIMITS["table_len"]]:
            table.push(_unwrap(item))
        return table
    if isinstance(value, dict):
        table = Table()
        for key, item in list(value.items())[:LIMITS["table_len"]]:
            table.set(key, _unwrap(item))
        return table
    return str(value)


# ---- стандартная библиотека ------------------------------------------------------


def _lua_print(engine):
    def print_(*args):
        engine.out(" ".join(_tostring(argument) for argument in args))
        return _NIL
    return HostFunction(print_, "print")


def _lua_tostring(value=_NIL, _radix=10):
    if value is _NIL:
        return "nil"
    return _tostring(value)


def _lua_tonumber(value, radix=10):
    if isinstance(value, bool):
        return _NIL
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip()
    try:
        if radix in (10, None):
            return _to_number(text) if re.fullmatch(
                r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text) else _NIL
        return int(text, int(radix))
    except (ValueError, TypeError):
        return _NIL


def _lua_type(value):
    return _type_name(value)


def _lua_error(message, _level=1):
    raise LuaxError(_tostring(message))


def _lua_assert(value, message="assertion failed!", *_rest):
    if not truthy(value):
        raise LuaxError(_tostring(message))
    return value


def _lua_pairs(table):
    if not isinstance(table, Table):
        raise LuaxError("pairs нужен table")
    entries = list(table.items())
    index = iter(range(len(entries)))

    def step():
        for position in index:
            return entries[position]
        return None
    return Iterator(step)


def _lua_ipairs(table):
    if not isinstance(table, Table):
        raise LuaxError("ipairs нужен table")
    position = [0]

    def step():
        position[0] += 1
        value = table.get(position[0])
        if value is _NIL:
            position[0] -= 1
            return None
        return (position[0], value)
    return Iterator(step)


def _string_format(fmt, *args):
    result, position, values = [], 0, list(args)
    spec = re.compile(r"%(-)?(\d+)?(\.\d+)?([dsifgqx])")
    while position < len(fmt):
        match = spec.search(fmt, position)
        if match is None:
            result.append(fmt[position:].replace("%%", "%"))
            break
        result.append(fmt[position:match.start()].replace("%%", "%"))
        flags, width, precision, letter = match.groups()
        value = values.pop(0) if values else _NIL
        if letter in "dgx":
            number = _as_number(value, None)
            if letter == "d":
                text = f"{int(number):d}"
            elif letter == "g":
                text = f"{number:g}"
            else:
                text = f"{int(number):x}"
        elif letter == "f":
            digits = precision[1:] if precision else "6"
            text = f"{_as_number(value, None):.{digits}f}"
        else:
            text = _tostring(value)
        if width:
            text = text.ljust(int(width)) if flags else text.rjust(int(width))
        result.append(text)
        position = match.end()
    text = "".join(result)
    if len(text) > LIMITS["string_len"]:
        raise LuaxError("string.format: строка переросла лимит")
    return text


def _string_sub(text, start=1, _stop=-1):
    text = str(text)
    start = max(1, int(start))
    stop = len(text) if _stop < 0 else max(0, int(_stop))
    return text[start - 1:stop] if stop >= start else ""


def _table_concat(table, separator="", _start=1, _end=_NIL):
    parts = [str(_tostring(value)) for value in table.array]
    return separator.join(parts)


def _table_insert(table, *args):
    if len(args) == 1:
        table.push(args[0])
        return _NIL
    position, value = args[0], args[1]
    if isinstance(position, int) and 1 <= position <= len(table.array) + 1:
        table.array.insert(position - 1, value)
        return _NIL
    table.set(position, value)
    return _NIL


def _table_remove(table, position=_NIL):
    if position is _NIL:
        return table.array.pop() if table.array else _NIL
    index = int(position) - 1
    if 0 <= index < len(table.array):
        return table.array.pop(index)
    return _NIL


def _table_sort(table, comparator=_NIL):
    if comparator is not _NIL:
        raise LuaxError("table.sort со своей функцией сравнения не поддержан")
    table.array.sort(key=lambda value: (
        value is _NIL, str(_type_name(value)), str(value)))
    return _NIL


def _string_find(haystack, needle, start=1):
    """Упрощённый поиск подстроки: позиция с 1 или nil. Без шаблонов Lua."""
    text, want = str(haystack), str(needle)
    position = text.find(want, max(0, int(start)) - 1)
    return position + 1 if position >= 0 else _NIL


def build_environment(extra=None, on_print=None, should_stop=None,
                      steps=None):
    """Собрать глобальное окружение скрипта."""
    engine = Engine({}, on_print, should_stop, steps or LIMITS["steps"])
    string_lib = Table()
    for name, value in (
        ("len", lambda text: len(str(text))),
        ("upper", lambda text: str(text).upper()),
        ("lower", lambda text: str(text).lower()),
        ("sub", _string_sub),
        ("rep", lambda text, count: str(text) * max(0, int(count))),
        ("reverse", lambda text: str(text)[::-1]),
        ("format", _string_format),
        ("find", _string_find),
    ):
        string_lib.set(name, HostFunction(value, f"string.{name}"))
    math_lib = Table()
    for name, value in (
        ("floor", lambda value: int(math.floor(_as_number(value)))),
        ("ceil", lambda value: int(math.ceil(_as_number(value)))),
        ("abs", lambda value: abs(_as_number(value))),
        ("sqrt", lambda value: math.sqrt(_as_number(value))),
        ("sin", lambda value: math.sin(_as_number(value))),
        ("cos", lambda value: math.cos(_as_number(value))),
        ("log", lambda value: math.log(_as_number(value))),
        ("exp", lambda value: math.exp(_as_number(value))),
        ("min", lambda *args: min(_as_number(a) for a in args)),
        ("max", lambda *args: max(_as_number(a) for a in args)),
        ("pi", math.pi),
    ):
        math_lib.set(name, HostFunction(value, f"math.{name}")
                     if callable(value) else value)
    table_lib = Table()
    for name, value in (
        ("insert", _table_insert),
        ("remove", _table_remove),
        ("concat", _table_concat),
        ("sort", _table_sort),
        ("getn", lambda target: len(target.array)),
    ):
        table_lib.set(name, HostFunction(value, f"table.{name}"))

    engine.globals.vars.update({
        "print": _lua_print(engine),
        "tostring": HostFunction(_lua_tostring, "tostring"),
        "tonumber": HostFunction(_lua_tonumber, "tonumber"),
        "type": HostFunction(_lua_type, "type"),
        "error": HostFunction(_lua_error, "error"),
        "assert": HostFunction(_lua_assert, "assert"),
        "pairs": HostFunction(_lua_pairs, "pairs"),
        "ipairs": HostFunction(_lua_ipairs, "ipairs"),
        "string": string_lib,
        "math": math_lib,
        "table": table_lib,
    })
    for name, value in (extra or {}).items():
        engine.globals.vars[name] = _wrap(value)
    return engine


def _wrap(value):
    if callable(value) and not isinstance(value, (HostFunction, Function)):
        return HostFunction(value, getattr(value, "__name__", "host"))
    return _unwrap(value)


def run_script(source, env=None, on_print=None, should_stop=None,
               steps=None, timeout_note=""):
    """Выполнить скрипт. Возвращает текст ошибки или пустую строку."""
    try:
        engine = build_environment(env, on_print, should_stop, steps)
        tree = Parser(tokenize(str(source or ""))).chunk()
        engine.exec_block(tree[1], engine.globals)
        return ""
    except LuaxError as exc:
        return str(exc)
    except RecursionError:
        return "слишком глубокая рекурсия"
    except _Return:
        return ""
    except Exception as exc:  # noqa: BLE001 — скрипт не должен ронять UI
        return f"сбой движка: {exc}"
