import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import completion


def tagged(code):
    return f"<INSERT>{code}</INSERT>"


def complete(code, prefix, suffix="\n"):
    return completion.extract_completion(tagged(code), prefix, suffix)[0]


def reason(output, prefix, suffix="\n"):
    return completion.extract_completion(output, prefix, suffix)[1]


class PromptTests(unittest.TestCase):
    def test_split_fim_normalizes_line_endings(self):
        self.assertEqual(completion.split_fim("<fim_prefix>a\r\nb<fim_suffix>c\r\n<fim_middle>"), ("a\nb", "c\n"))

    def test_split_fim_repairs_cursor_between_cr_and_lf(self):
        self.assertEqual(completion.split_fim("<fim_prefix>x \r<fim_suffix>\nrest<fim_middle>"), ("x ", "\nrest"))

    def test_split_fim_ignores_markers_inside_context(self):
        raw = "<fim_prefix>// t: <fim_prefix>{{p}}<fim_suffix>{{s}}<fim_middle>\ncode<fim_suffix>after<fim_middle>"
        prefix, suffix = completion.split_fim(raw)
        self.assertTrue(prefix.endswith("\ncode"))
        self.assertEqual(suffix, "after")

    def test_prompt_with_and_without_instructions(self):
        self.assertEqual(completion.transform_fim_to_prompt("a", "b", False), "[CODE]\na<CURSOR>b\n[/CODE]")
        self.assertTrue(completion.transform_fim_to_prompt("a", "b", True).startswith(completion.SYSTEM_PROMPT))


class ExtractionTests(unittest.TestCase):
    def test_tagged_completion(self):
        self.assertEqual(complete("getNames(fullName);", "const names = "), "getNames(fullName);")

    def test_explanation_around_tags_is_dropped(self):
        output = "Looking at this, the answer is:\n<INSERT>(fullName);</INSERT>\nDone."
        self.assertEqual(completion.extract_completion(output, "x = getNames", "\n"), ("(fullName);", ""))

    def test_untagged_reply_is_discarded(self):
        self.assertEqual(reason("Looking at the code structure, a div is missing.", "<div> ", "</div>\n"), "untagged")

    def test_cursor_marker_is_discarded(self):
        self.assertEqual(reason(tagged("The code at <CURSOR> is complete"), "x"), "mentions-cursor")

    def test_prose_inside_tags_is_discarded(self):
        self.assertEqual(reason(tagged("Looking at the code, you need a closing tag"), "<div> ", "</div>\n"), "prose")

    def test_empty_tags(self):
        self.assertEqual(completion.extract_completion("<INSERT></INSERT>", "x = ", "\n"), ("", "empty"))

    def test_fences_inside_tags(self):
        self.assertEqual(complete('\n```tsx\nclassName="mb-4"\n```\n', "<div ", ">\n"), 'className="mb-4"')

    def test_template_literal_is_kept(self):
        self.assertEqual(complete("`${a} ${b}`", "log(", ")\n"), "`${a} ${b}`")


class PrefixOverlapTests(unittest.TestCase):
    def test_trims_repeated_partial_word(self):
        self.assertEqual(complete("getNames(fullName);", "const names = getNames"), "(fullName);")
        self.assertEqual(complete("getNames(fullName);", "const names = g"), "etNames(fullName);")

    def test_trims_repeated_line(self):
        self.assertEqual(complete("const names = getNames(fullName);", "const names = "), "getNames(fullName);")
        self.assertEqual(complete("return x;", "  return "), "x;")

    def test_keeps_unrelated_text(self):
        self.assertEqual(complete("getNames(fullName);", "const names = "), "getNames(fullName);")
        self.assertEqual(complete("items.map(x => x)", "const value = items.map"), "(x => x)")


class SameLineTests(unittest.TestCase):
    def test_formatting_line_break_is_removed(self):
        self.assertEqual(complete("\ngetNames(fullName);\n", "const names = "), "getNames(fullName);")

    def test_single_leading_space_is_kept(self):
        self.assertEqual(complete(' className="a"', "<div", ">\n"), ' className="a"')
        self.assertEqual(complete(" 1}", "# d.py\nd = {'a':"), " 1}")

    def test_no_double_space(self):
        self.assertEqual(complete(" getNames(fullName);", "const names = "), "getNames(fullName);")

    def test_else_continues_closing_brace_line(self):
        self.assertEqual(complete(" else {\n    y();\n  }", "  if (a) {\n    x();\n  }"), " else {\n    y();\n  }")

    def test_one_liner_after_brace_stays(self):
        self.assertEqual(complete("a: 1 }", "const o = {"), "a: 1 }")
        self.assertEqual(complete("\n}\n", "  function f() {"), "}")

    def test_text_right_of_cursor_forces_single_line(self):
        self.assertEqual(complete("a,\nb", "print(", ")\n"), "a,")
        self.assertEqual(complete("foo", "const x = 1;", " // note\n"), "foo")


class NewLineTests(unittest.TestCase):
    def test_brace_opens_indented_body(self):
        for code in ("\nreturn a + b;\n", "\n  return a + b;\n", "\n return a + b;\n"):
            self.assertEqual(complete(code, "function add(a: number, b: number) {", "\n}\n"), "\n  return a + b;")

    def test_nested_brace_uses_file_indent_step(self):
        prefix = "class A {\n  run() {\n    if (x) {"
        self.assertEqual(complete("\nconsole.log(x);\n", prefix, "\n    }\n  }\n}\n"), "\n      console.log(x);")

    def test_python_header(self):
        self.assertEqual(complete("\nreturn n % 2 == 0\n", "# check.py\ndef is_even(n):"), "\n    return n % 2 == 0")
        self.assertEqual(complete("\n    return n % 2 == 0\n", "# check.py\ndef is_even(n):"), "\n    return n % 2 == 0")
        self.assertEqual(complete("\nreturn 1\n", "# f.py\ndef f(x):\n    if x:"), "\n        return 1")

    def test_statement_end_starts_next_statement(self):
        prefix = "const names = getNames(fullName);\nconsole.log(names);"
        self.assertEqual(complete("\nconsole.log(names[0]);\n", prefix), "\nconsole.log(names[0]);")
        self.assertEqual(complete("\nreturn x;\n", "function f() {\n  const x = 1;", "\n}\n"), "\n  return x;")

    def test_closing_blocks_after_statement_keep_their_indentation(self):
        prefix = "function f() {\n  if (a) {\n    if (b) {\n      x();"
        self.assertEqual(complete("\n    }\n  }\n}", prefix), "\n    }\n  }\n}")

    def test_indented_reply_signals_new_line_in_unknown_language(self):
        prefix = "class Greeter\n  def greet(name)"
        self.assertEqual(complete("\n    puts name\n", prefix, "\n  end\nend\n"), "\n    puts name")

    def test_ruby(self):
        prefix = "# greeter.rb\nclass Greeter\n  def greet(name)"
        self.assertEqual(complete("\nputs name\n", prefix, "\n  end\nend\n"), "\n    puts name")
        self.assertEqual(complete("\nputs item\n", "# items.rb\nitems.each do |item|", "\nend\n"), "\n  puts item")

    def test_lua_shell_yaml(self):
        self.assertEqual(complete("\nreturn a + b\n", "-- math.lua\nlocal function add(a, b)", "\nend\n"), "\n  return a + b")
        self.assertEqual(complete("\necho found\n", "# run.sh\nif [ -f \"$1\" ]; then", "\nfi\n"), "\n  echo found")
        prefix = "# docker-compose.yml\nservices:\n  web:\n    ports:"
        self.assertEqual(complete('\n- "80:80"\n', prefix), '\n      - "80:80"')

    def test_go_uses_tabs(self):
        prefix = "// main.go\npackage main\n\nfunc add(a int, b int) int {"
        self.assertEqual(complete("\n    return a + b\n", prefix, "\n}\n"), "\n\treturn a + b")
        prefix = "// main.go\nfunc main() {\n\tf, err := open()\n\tif err != nil {"
        self.assertEqual(complete("\n        return\n", prefix, "\n\t}\n}\n"), "\n\t\treturn")

    def test_model_tabs_become_file_spaces(self):
        prefix = "// a.ts\nfunction f() {\n  const x = 1;\n  if (x) {"
        self.assertEqual(complete("\n\t\tconsole.log(x);\n", prefix, "\n  }\n}\n"), "\n    console.log(x);")

    def test_allman_braces(self):
        prefix = ("// Calc.cs\npublic class Calc\n{\n    public int Add(int a, int b)\n    {\n        return a + b;\n"
                  "    }\n\n    public int Sub(int a, int b)")
        expected = "\n    {\n        return a - b;\n    }"
        self.assertEqual(complete("\n{\n    return a - b;\n}\n", prefix, "\n}\n"), expected)
        self.assertEqual(complete(expected + "\n", prefix, "\n}\n"), expected)

    def test_k_and_r_brace_stays_on_line(self):
        prefix = "// a.ts\nfunction a() {\n  return 1;\n}\n\nfunction b()"
        self.assertEqual(complete(" {\n  return 2;\n}", prefix), " {\n  return 2;\n}")


class RelativeIndentationTests(unittest.TestCase):
    PREFIX = "  return (\n    <div>\n      <div>\n        "
    SUFFIX = "\n      </div>\n    </div>\n"

    def test_block_below_floor_is_shifted(self):
        expected = "<TabsSelect\n          value={v}\n        />"
        self.assertEqual(complete("\n<TabsSelect\n  value={v}\n/>\n", self.PREFIX, self.SUFFIX), expected)

    def test_absolute_indentation_is_kept(self):
        expected = "<TabsSelect\n          value={v}\n        />"
        self.assertEqual(complete(f"\n{expected}\n", self.PREFIX, self.SUFFIX), expected)

    def test_shallow_block_body_is_shifted(self):
        prefix = "# check.py\ndef check(x):\n    if x > 0:\n        return 1\n    "
        expected = "elif x < 0:\n        return -1\n    else:\n        return 0"
        self.assertEqual(complete("\nelif x < 0:\n    return -1\nelse:\n    return 0\n", prefix), expected)
        self.assertEqual(complete(f"\n{expected}\n", prefix), expected)


class SuffixOverlapTests(unittest.TestCase):
    def test_duplicate_closers_are_trimmed(self):
        self.assertEqual(complete("names)", "console.log(", ");\n"), "names")
        self.assertEqual(complete("names);", "console.log(", ");\n"), "names")

    def test_duplicate_closing_line_is_trimmed(self):
        self.assertEqual(complete("\nreturn a + b;\n}\n", "function add(a, b) {", "\n}\n"), "\n  return a + b;")

    def test_closer_of_own_block_is_kept(self):
        prefix = "// a.ts\nclass A {\n  run()"
        self.assertEqual(complete(" {\n    go();\n  }", prefix, "\n}\n"), " {\n    go();\n  }")

    def test_single_identifier_character_is_not_trimmed(self):
        self.assertEqual(complete("a", "const v = ", "a;\n"), "a")


class BalanceTests(unittest.TestCase):
    def test_over_close_is_cut(self):
        self.assertEqual(completion.extract_completion(tagged("2)"), "const x = 1", "\n"), ("2", "over-close"))

    def test_dangling_multi_line_open_is_discarded(self):
        self.assertEqual(reason(tagged("{\n  doA();\n  doB();"), "const f = () => "), "dangling-open")

    def test_open_closed_by_suffix_is_kept(self):
        self.assertEqual(complete("{\n  doA();\n  doB();", "const f = () => ", "\n}\n"), "{\n  doA();\n  doB();")

    def test_brackets_in_strings_are_ignored(self):
        self.assertEqual(complete('"(a"', "log(", ")\n"), '"(a"')

    def test_python_floor_division_is_not_a_comment(self):
        self.assertEqual(complete(" + b)", "# m.py\nx = (a // 2"), " + b)")

    def test_brackets_in_comments_are_ignored(self):
        self.assertEqual(complete("compute()", "# m.py\n# call(\nvalue = "), "compute()")


class RepetitionAndLengthTests(unittest.TestCase):
    def test_repeated_lines_are_discarded(self):
        self.assertEqual(reason(tagged("foo();\nfoo();\nfoo();"), "  "), "repetition")

    def test_repeated_unit_is_discarded(self):
        self.assertEqual(reason(tagged("foo = foo = foo = foo = foo"), "x = "), "repetition")

    def test_separator_comment_is_kept(self):
        self.assertEqual(complete("// ------------------------------", ""), "// ------------------------------")

    def test_long_completion_is_capped_on_a_line_boundary(self):
        code = "\n".join(f"v{i} = {i};" for i in range(400))
        text, why = completion.extract_completion(tagged(code), "", "\n")
        self.assertEqual(why, "too-long")
        self.assertLessEqual(len(text), completion.MAX_COMPLETION_CHARS)
        self.assertTrue(code.startswith(text) and code[len(text)] == "\n")


class LanguageDetectionTests(unittest.TestCase):
    def test_uses_last_file_header(self):
        name, lines = completion.detect_language("// Path: x.py\n// foo\n// main.go\npackage main")
        self.assertEqual((name, lines), ("go", ["package main"]))

    def test_without_header(self):
        self.assertEqual(completion.detect_language("x = 1")[0], None)

    def test_formatting_profile(self):
        language = completion.DEFAULT_LANGUAGE
        self.assertEqual(completion.formatting_profile(["a {", "\tb", "\tc {", "\t\td"], language)[:2], (4, True))
        self.assertEqual(completion.formatting_profile(["a {", "  b", "   c"], language)[:2], (2, False))
        self.assertTrue(completion.formatting_profile(["a()", "{", "  b", "}"], language)[2])
        self.assertFalse(completion.formatting_profile(["a() {", "  b", "}"], language)[2])


if __name__ == "__main__":
    unittest.main()
