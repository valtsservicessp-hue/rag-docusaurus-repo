import React, {useCallback, useEffect, useRef, useState} from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';

import {parseAnswer} from './parseAnswer';
import styles from './styles.module.css';

const DEFAULT_API_URL = 'http://127.0.0.1:8001';
const MAX_QUESTION_CHARS = 1000;
const HEALTH_TIMEOUT_MS = 3000;
// Longer than OLLAMA_TIMEOUT can be on a CPU-only machine, so the server decides.
const ASK_TIMEOUT_MS = 360000;
const SLOW_HINT_MS = 8000;
const PHONE_QUERY = '(max-width: 600px)';

async function askDocs(apiUrl, question) {
  let response;
  try {
    response = await fetch(`${apiUrl}/api/ask`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question}),
      signal: AbortSignal.timeout(ASK_TIMEOUT_MS),
    });
  } catch {
    throw new Error('The docs assistant did not respond. Please try again.');
  }

  if (response.status === 429) {
    const wait = response.headers.get('Retry-After');
    throw new Error(`Too many questions. Try again in ${wait} seconds.`);
  }
  if (!response.ok) {
    throw new Error('The docs assistant could not answer. Please try again.');
  }
  return response.json();
}

function InlineParts({parts, onNavigate}) {
  return parts.map((part, index) => {
    if (part.type === 'code') {
      return <code key={index}>{part.text}</code>;
    }
    if (part.type === 'bold') {
      return <strong key={index}>{part.text}</strong>;
    }
    if (part.type === 'citation') {
      return (
        <Link key={index} to={part.url} onClick={onNavigate}>
          [{part.n}]
        </Link>
      );
    }
    return <React.Fragment key={index}>{part.text}</React.Fragment>;
  });
}

function AnswerText({text, citations, onNavigate}) {
  return parseAnswer(text, citations).map((block, index) =>
    block.type === 'code' ? (
      <pre key={index} className={styles.code}>
        <code>{block.code}</code>
      </pre>
    ) : (
      <p key={index} className={styles.text}>
        <InlineParts parts={block.parts} onNavigate={onNavigate} />
      </p>
    ),
  );
}

function Sources({citations, onNavigate}) {
  return (
    <div className={styles.sources}>
      <strong>Sources</strong>
      <ul>
        {citations.map((citation) => (
          <li key={citation.n}>
            [{citation.n}]{' '}
            <Link to={citation.url} onClick={onNavigate}>
              {citation.title}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Answer({reply, onNavigate}) {
  // Page lists repeat every link as plain text, so show the intro line and let
  // Sources carry the links.
  const text =
    reply.mode === 'generated' ? reply.answer : reply.answer.split('\n')[0];
  const citations = reply.mode === 'generated' ? reply.citations : [];
  return (
    <>
      <AnswerText text={text} citations={citations} onNavigate={onNavigate} />
      {reply.citations.length > 0 && (
        <Sources citations={reply.citations} onNavigate={onNavigate} />
      )}
    </>
  );
}

function Message({message, onNavigate}) {
  if (message.role === 'user') {
    return <div className={styles.user}>{message.text}</div>;
  }
  if (message.role === 'error') {
    return (
      <div className={styles.error} role="alert">
        {message.text}
      </div>
    );
  }
  return (
    <div className={styles.assistant}>
      <Answer reply={message.reply} onNavigate={onNavigate} />
    </div>
  );
}

export default function DocsChat() {
  const {siteConfig} = useDocusaurusContext();
  const apiUrl = (siteConfig.customFields?.docsChatApiUrl ?? DEFAULT_API_URL).replace(
    /\/+$/,
    '',
  );

  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState('checking');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [slow, setSlow] = useState(false);
  const toggleRef = useRef(null);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  const checkHealth = useCallback(async () => {
    setHealth('checking');
    try {
      const response = await fetch(`${apiUrl}/healthz`, {
        signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
      });
      setHealth(response.ok ? 'online' : 'offline');
    } catch {
      setHealth('offline');
    }
  }, [apiUrl]);

  useEffect(() => {
    if (open) {
      checkHealth();
    }
  }, [open, checkHealth]);

  useEffect(() => {
    if (open && health === 'online') {
      inputRef.current?.focus();
    }
  }, [open, health]);

  useEffect(() => {
    const list = listRef.current;
    if (list) {
      list.scrollTop = list.scrollHeight;
    }
  }, [messages, busy, slow, open]);

  useEffect(() => {
    if (!busy) {
      setSlow(false);
      return undefined;
    }
    const timer = setTimeout(() => setSlow(true), SLOW_HINT_MS);
    return () => clearTimeout(timer);
  }, [busy]);

  const close = useCallback(() => {
    setOpen(false);
    toggleRef.current?.focus();
  }, []);

  // On the document, because focus leaves the panel when the form is replaced
  // by the offline notice.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        close();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, close]);

  // On a phone the panel covers the page, so a followed link must reveal it.
  function handleNavigate() {
    if (window.matchMedia(PHONE_QUERY).matches) {
      setOpen(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const question = input.trim();
    if (!question || busy) {
      return;
    }

    setInput('');
    setMessages((current) => [...current, {role: 'user', text: question}]);
    setBusy(true);
    try {
      const reply = await askDocs(apiUrl, question);
      setMessages((current) => [...current, {role: 'assistant', reply}]);
    } catch (error) {
      setMessages((current) => [...current, {role: 'error', text: error.message}]);
      checkHealth();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {open && (
        <section
          className={styles.panel}
          role="dialog"
          aria-label="Docs assistant">
          <header className={styles.header}>
            <strong>Ask the docs</strong>
            <button
              type="button"
              className={styles.close}
              aria-label="Close docs assistant"
              onClick={close}>
              ×
            </button>
          </header>

          <div className={styles.messages} ref={listRef} aria-live="polite">
            {messages.length === 0 && (
              <p className={styles.hint}>Ask a question about these docs.</p>
            )}
            {messages.map((message, index) => (
              <Message key={index} message={message} onNavigate={handleNavigate} />
            ))}
            {busy && (
              <p className={styles.hint}>
                {slow
                  ? 'Still thinking… a local model can take a minute or two.'
                  : 'Thinking…'}
              </p>
            )}
          </div>

          {health === 'offline' ? (
            <div className={styles.offline}>
              <span>Docs assistant is offline.</span>
              <button type="button" className={styles.send} onClick={checkHealth}>
                Retry
              </button>
            </div>
          ) : (
            <form className={styles.form} onSubmit={handleSubmit}>
              <input
                ref={inputRef}
                className={styles.input}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                maxLength={MAX_QUESTION_CHARS}
                placeholder="Ask a question"
                aria-label="Your question"
                autoComplete="off"
              />
              <button
                type="submit"
                className={styles.send}
                disabled={busy || health !== 'online' || !input.trim()}>
                Send
              </button>
            </form>
          )}
        </section>
      )}

      <button
        type="button"
        ref={toggleRef}
        className={styles.toggle}
        aria-label={open ? 'Close docs assistant' : 'Open docs assistant'}
        aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}>
        {open ? '×' : '?'}
      </button>
    </>
  );
}
