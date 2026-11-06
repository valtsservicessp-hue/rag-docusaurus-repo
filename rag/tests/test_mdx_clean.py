"""MDX cleaning tests. Snippets marked 'real' are copied from docs/."""

from docchat.mdx_clean import clean_mdx, scan_fences


def test_removes_single_line_imports():
    text = "import Tabs from '@theme/Tabs';\nimport X from './x';\n\nBody text."
    assert clean_mdx(text) == "Body text."


def test_removes_multi_line_import():
    text = "import {\n  One,\n\n  Two,\n} from './x';\n\nBody text."
    assert clean_mdx(text) == "Body text."


def test_removes_real_multi_line_export_and_unwraps_component():
    # real: docs/tutorial-basics/markdown-features.mdx
    text = """export const Highlight = ({children, color}) => (
  <span
    style={{
      backgroundColor: color,
      borderRadius: '20px',
      color: '#fff',
      padding: '10px',
      cursor: 'pointer',
    }}
    onClick={() => {
      alert(`You clicked the color ${color} with label ${children}`);
    }}>
    {children}
  </span>
);

This is <Highlight color="#25c2a0">Docusaurus green</Highlight> !

This is <Highlight color="#1877F2">Facebook blue</Highlight> !"""
    assert clean_mdx(text) == "This is Docusaurus green !\n\nThis is Facebook blue !"


def test_a_word_starting_with_import_is_kept():
    assert clean_mdx("important: read this") == "important: read this"


def test_unwraps_tabs_keeping_the_label():
    text = '<Tabs><TabItem label="Windows">text</TabItem></Tabs>'
    assert clean_mdx(text) == "Windows: text"


def test_unwraps_multi_line_tabs():
    text = """<Tabs>
  <TabItem value="win" label="Windows">

  Use PowerShell.

  </TabItem>
  <TabItem value="mac">

  Use Terminal.

  </TabItem>
</Tabs>"""
    result = clean_mdx(text)
    assert "Windows: Use PowerShell." in result
    assert "mac: Use Terminal." in result
    assert "<" not in result


def test_removes_self_closing_component_and_arrow_in_attribute():
    text = 'Before <Button onClick={() => go(1 > 0)} label="x" /> after'
    assert clean_mdx(text) == "Before  after"


def test_real_admonition_with_bracket_title():
    # real: docs/tutorial-basics/markdown-features.mdx
    text = ":::tip[My tip]\n\nUse this awesome feature option\n\n:::"
    assert clean_mdx(text) == "Tip (My tip): Use this awesome feature option"


def test_real_admonition_without_title():
    # real: docs/tutorial-extras/translate-your-site.mdx
    text = ":::caution\n\nIn development, you can only use one locale at a time.\n\n:::"
    expected = "Caution: In development, you can only use one locale at a time."
    assert clean_mdx(text) == expected


def test_legacy_admonition_title():
    assert clean_mdx(":::tip Title\n\nBody\n\n:::") == "Tip (Title): Body"


def test_admonition_around_a_code_block():
    text = ":::note\n\nRun this:\n\n```bash\nnpm test\n```\n\n:::"
    assert clean_mdx(text) == "Note: Run this:\n\nCode (bash):\n```bash\nnpm test\n```"


def test_code_block_gets_language_prefix_and_keeps_its_content():
    # real: docs/tutorial-basics/create-a-page.mdx
    text = """```jsx title="src/pages/my-react-page.js"
import React from 'react';
import Layout from '@theme/Layout';

export default function MyReactPage() {
  return (
    <Layout>
      <h1>My React page</h1>
    </Layout>
  );
}
```"""
    assert clean_mdx(text) == f"Code (jsx):\n{text}"


def test_code_block_without_language_has_no_prefix():
    assert clean_mdx("```\nplain\n```") == "```\nplain\n```"


def test_admonition_syntax_inside_code_is_untouched():
    # real: docs/tutorial-basics/markdown-features.mdx
    text = "```md\n:::tip[My tip]\n\nUse this awesome feature option\n\n:::\n```"
    assert clean_mdx(text) == f"Code (md):\n{text}"


def test_real_nested_fence_stays_one_block():
    # real: docs/tutorial-basics/markdown-features.mdx
    text = """````md
```jsx title="src/components/HelloDocusaurus.js"
function HelloDocusaurus() {
  return <h1>Hello, Docusaurus!</h1>;
}
```
````"""
    assert clean_mdx(text) == f"Code (md):\n{text}"


def test_drops_comments():
    text = "One <!-- hidden --> two {/* hidden */} three"
    assert clean_mdx(text) == "One  two  three"


def test_drops_multi_line_comments():
    assert clean_mdx("One\n\n{/*\nhidden\n*/}\n\n<!--\nhidden\n-->\n\nTwo") == (
        "One\n\nTwo"
    )


def test_inline_code_is_protected():
    # real: docs/tutorial-basics/markdown-features.mdx
    text = "Add `{/* #my-custom-id */}` after the heading text."
    assert clean_mdx(text) == text


def test_inline_jsx_in_code_is_protected():
    assert clean_mdx("Wrap it in `<Tabs>` first.") == "Wrap it in `<Tabs>` first."


def test_empty_after_cleaning():
    assert clean_mdx("import X from './x';\n\n{/* note */}") == ""


def test_scan_fences_kinds():
    lines = list(scan_fences("a\n```js\nb\n```\nc"))
    assert [kind for _, kind in lines] == ["text", "open", "code", "code", "text"]


def test_scan_fences_ignores_shorter_or_different_closers():
    text = "````md\n```\n~~~~\n````\nafter"
    assert [kind for _, kind in scan_fences(text)][-1] == "text"
    assert [kind for _, kind in scan_fences(text)][-2] == "code"


def test_scan_fences_treats_inline_triple_backticks_as_text():
    assert list(scan_fences("```not a fence```")) == [("```not a fence```", "text")]


def test_scan_fences_handles_indented_fence_in_a_list():
    text = "- step\n\n    ```bash\n    # comment\n    ```\n\n- next"
    kinds = [kind for _, kind in scan_fences(text)]
    assert kinds == ["text", "text", "open", "code", "code", "text", "text"]
