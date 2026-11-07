// Run from the repo root: node --test "rag/docusaurus-plugin/**/*.test.js"
import assert from 'node:assert/strict';
import {test} from 'node:test';

import {parseAnswer} from './parseAnswer.js';

const CITATIONS = [
  {n: 1, title: 'Intro', url: '/docs/intro#start'},
  {n: 3, title: 'Guide', url: '/docs/guide'},
];

function parts(text) {
  return parseAnswer(text, CITATIONS)[0].parts;
}

test('plain text stays one part', () => {
  assert.deepEqual(parts('Hello there.'), [{type: 'text', text: 'Hello there.'}]);
});

test('a citation becomes a link to its URL', () => {
  assert.deepEqual(parts('Run it [1].'), [
    {type: 'text', text: 'Run it '},
    {type: 'citation', n: 1, url: '/docs/intro#start'},
    {type: 'text', text: '.'},
  ]);
});

test('citation numbers with gaps keep their own URLs', () => {
  assert.deepEqual(parts('[1][3]'), [
    {type: 'citation', n: 1, url: '/docs/intro#start'},
    {type: 'citation', n: 3, url: '/docs/guide'},
  ]);
});

test('a number with no citation stays plain text', () => {
  assert.deepEqual(parts('Fake [2].'), [{type: 'text', text: 'Fake [2].'}]);
});

test('an array index is not a citation', () => {
  assert.deepEqual(parts('Use items[1] here.'), [
    {type: 'text', text: 'Use items[1] here.'},
  ]);
});

test('inline code keeps its brackets', () => {
  assert.deepEqual(parts('Use `items[1]`.'), [
    {type: 'text', text: 'Use '},
    {type: 'code', text: 'items[1]'},
    {type: 'text', text: '.'},
  ]);
});

test('bold is recognised', () => {
  assert.deepEqual(parts('**Note** this.'), [
    {type: 'bold', text: 'Note'},
    {type: 'text', text: ' this.'},
  ]);
});

test('a code block is split from the text around it', () => {
  const text = 'Run this [1]:\n\n```bash\nnpm start [1]\n```\n\nThen wait.';
  const blocks = parseAnswer(text, CITATIONS);
  assert.deepEqual(
    blocks.map((block) => block.type),
    ['text', 'code', 'text'],
  );
  assert.equal(blocks[1].language, 'bash');
  assert.equal(blocks[1].code, 'npm start [1]');
});

test('a code block with no language works', () => {
  const [block] = parseAnswer('```\nnpm start\n```', CITATIONS);
  assert.deepEqual(block, {type: 'code', language: '', code: 'npm start'});
});

test('Windows line endings work', () => {
  const [block] = parseAnswer('```bash\r\nnpm start\r\n```', CITATIONS);
  assert.equal(block.code, 'npm start');
});

test('an unclosed fence stays as text instead of losing content', () => {
  const [block] = parseAnswer('```bash\nnpm start', CITATIONS);
  assert.equal(block.type, 'text');
  assert.match(block.parts[0].text, /npm start/);
});

test('a Markdown link in the answer never becomes a link', () => {
  const all = parts('[click](https://evil.example)');
  assert.deepEqual(all, [{type: 'text', text: '[click](https://evil.example)'}]);
});

test('HTML in the answer stays plain text', () => {
  const html = '<img src=x onerror=alert(1)>';
  assert.deepEqual(parts(html), [{type: 'text', text: html}]);
});

test('an empty answer has no blocks', () => {
  assert.deepEqual(parseAnswer('  ', CITATIONS), []);
});
