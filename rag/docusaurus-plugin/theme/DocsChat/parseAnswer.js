// Turn an answer into plain data for React to render. Answers come from a model
// reading page text, so they are never inserted as HTML, and links only ever
// come from the server's citations, never from the answer text.

const FENCE = /```([\w+-]*)[ \t]*\r?\n([\s\S]*?)```/g;
// `code`, **bold**, or [n]. The lookbehind leaves items[1] alone, as the server does.
const INLINE = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|(?<![\w)])\[(\d+)\]/g;

function parseInline(text, citations) {
  const urls = new Map(citations.map((citation) => [citation.n, citation.url]));
  const parts = [];
  let position = 0;

  for (const match of text.matchAll(INLINE)) {
    const [whole, code, bold, number] = match;
    const url = urls.get(Number(number));
    if (number !== undefined && url === undefined) {
      continue;
    }
    if (match.index > position) {
      parts.push({type: 'text', text: text.slice(position, match.index)});
    }
    if (code !== undefined) {
      parts.push({type: 'code', text: code});
    } else if (bold !== undefined) {
      parts.push({type: 'bold', text: bold});
    } else {
      parts.push({type: 'citation', n: Number(number), url});
    }
    position = match.index + whole.length;
  }

  if (position < text.length) {
    parts.push({type: 'text', text: text.slice(position)});
  }
  return parts;
}

export function parseAnswer(text, citations) {
  const blocks = [];
  let position = 0;

  const addText = (value) => {
    const trimmed = value.trim();
    if (trimmed) {
      blocks.push({type: 'text', parts: parseInline(trimmed, citations)});
    }
  };

  for (const match of text.matchAll(FENCE)) {
    addText(text.slice(position, match.index));
    blocks.push({type: 'code', language: match[1], code: match[2].trimEnd()});
    position = match.index + match[0].length;
  }
  addText(text.slice(position));
  return blocks;
}
