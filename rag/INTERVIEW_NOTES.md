# DocsChat: interview notes

Every row points at real code in this repo, so each claim can be shown, not just described. For the diagrams, see [README.md](README.md).

**The 30-second version:** "A chat assistant for a Docusaurus developer portal. It answers only from the site's own docs, and every claim links to the exact heading it came from. Retrieval is local and multilingual; the LLM is pluggable: none, a local Ollama model, or Claude. The same search is exposed to AI agents over MCP. CI gates every PR on retrieval quality and on citation links resolving against the real site build, and each release is a container where the image version is the docs version."

---

## 1. RAG concepts

| Concept | Where | What to say |
|---|---|---|
| **MDX cleaning** | [docchat/mdx_clean.py](docchat/mdx_clean.py) `clean_mdx()` | MDX is Markdown plus JSX. Imports and component tags would be embedded as if they were content, so they're removed while the text inside is kept: `<TabItem label="Windows">` becomes `Windows: ...`, `:::tip` becomes `Tip (Title):`. Code blocks are never altered. |
| **URL mapping** | [docchat/urls.py](docchat/urls.py) `doc_slug()`, `page_url()`, `Slugger` | Citations are only useful if they open. The code mirrors Docusaurus routing: number prefixes stripped, `index.md` maps to the folder, front matter `id` and `slug`, `baseUrl`, explicit `{#id}`, and `-1` for repeated headings. Proven against the real build by `check_citation_urls.py`. |
| **Chunking** | [docchat/chunking.py](docchat/chunking.py) `chunk_document()`, `split_body()` | ~800 characters with 150 overlap, split at paragraphs, then lines, then sentences. Code is split only between lines. Each chunk is prefixed `Title > Section`, so a short section still embeds with its context. Small chunks make citations precise. |
| **Embeddings and cosine** | [docchat/store.py](docchat/store.py) `FastEmbedder`, `DocStore` | A local multilingual model turns text into 768-number vectors; Chroma is configured for cosine distance and `score = 1 - distance`. Local means no per-query cost and no doc text leaving the machine at ingest. Multilingual means a Spanish question finds an English section (measured at 0.455). |
| **Threshold and not-found path** | [docchat/retrieve.py](docchat/retrieve.py) `retrieve()`, [docchat/generate.py](docchat/generate.py) `answer_question()` | `MIN_SCORE=0.38` was measured, not guessed: midpoint between the best off-topic score (0.335) and the worst correct hit (0.424). With no hit above it, the answer is "not covered" and **the LLM is never called**: zero tokens, zero hallucination risk. |
| **Grounding prompt** | [docchat/generate.py](docchat/generate.py) `SYSTEM_PROMPT`, `REMINDER`, `build_user_prompt()` | Answer only from numbered context blocks, cite as `[n]`, answer in the question's language. The rules are repeated **after** the context, because small models obey what they read last. |
| **Citations as links** | [docchat/generate.py](docchat/generate.py) `filter_citations()`, [docusaurus-plugin/theme/DocsChat/parseAnswer.js](docusaurus-plugin/theme/DocsChat/parseAnswer.js) | URLs are never in the prompt; the model only sees block numbers, and code maps `[n]` to the URL. So the model **can't invent a link**. Invalid numbers and brackets inside code are dropped. |
| **Prompt-injection defense** | `generate.py` `_escape_tag()`, `MODE_UNCITED`; [tests/fixtures/](tests/fixtures/) | Layered: context is labelled as data; `</context>` in page text is escaped; the reminder comes last; and the check that doesn't rely on the model: **a reply with no valid citation is never shown** (`uncited`). Tested with an "ignore your instructions" fixture that lives only in tests, never in `docs/`. |
| **Idempotent ingest** | `chunking.py` `chunk_id()`, `store.py` `DocStore.sync()` | Ids are a hash of source, section, index and text. Re-running ingest embeds only new or changed chunks, deletes vanished ones, and writes before deleting, so a crash leaves extra chunks, never missing ones. Metadata is compared too: ids hash the text, not the URL, so without that check a slug change would leave the index citing the old URL. |
| **Embedding versioning** | `store.py` `DocStore.__init__` | The model name is stored in the collection metadata. Opening the index with a different model is refused, because vectors from two models are not comparable. The fix is explicit: `ingest.py --reset`. |
| **Pluggable LLM** | [docchat/llm.py](docchat/llm.py) `LLMClient`, `get_llm_client()` | One method, `complete(system, user)`. Retrieval, prompt and citation checks are identical for every provider. Swapping Ollama for Claude is a config change, and tests inject a fake. |
| **Graceful degradation** | [app.py](app.py) `build_llm()`, the `except LLMError` in `ask()` | A broken provider (Ollama stopped, bad key) never stops the backend starting, and a failed call still returns the closest pages (`retrieval_only`). The real reason goes to the server log, not the visitor. |
| **Evaluation** | [eval/eval_retrieval.py](eval/eval_retrieval.py) `evaluate()`, [eval/questions.json](eval/questions.json) | 12 labelled questions plus 3 off-topic. Measures hit@5 (92%), MRR (0.850) and off-topic rejection (100%). Measured **after** `MIN_SCORE`, because a correct page below the threshold is what the user experiences as "not covered". |

## 2. Delivery

| Concept | Where | What to say |
|---|---|---|
| **CI without secrets** | [.github/workflows/ci.yml](../.github/workflows/ci.yml), [tests/](tests/) | 277 tests with a fake LLM and a fake embedder; no key, no Ollama, no network. The workflow token is read-only. Forks and outside contributors can run CI safely. |
| **Retrieval-quality gate** | `ci.yml` job `rag-eval` | Builds the real index from the real docs and fails below 80% hit@5 or if any off-topic question slips through. It's deterministic because no LLM is involved, so it's safe to gate on. |
| **Stale-question check** | `eval_retrieval.py` `stale_questions()` | Thresholds alone hide a single renamed doc (11 of 12 still passes 80%). Any expected URL missing from the index fails the run, naming the question. Demonstrated by renaming `deploy-your-site.mdx` in a draft PR. |
| **Citation-link check against the build** | [eval/check_citation_urls.py](eval/check_citation_urls.py) `check_urls()` | Every URL the index can cite, including the `#anchor`, must exist in the HTML Docusaurus actually produced. The build is the ground truth, so this catches URL-mapping bugs that unit tests can't. |
| **Docs version = image version** | [Dockerfile](Dockerfile) `RUN python ingest.py --reset` | The index is built inside the image. Tag `v0.1.0` answers from exactly the docs at that tag; rolling back the image rolls back the docs. No runtime ingest, no drift. |
| **Container smoke test** | [.github/workflows/release.yml](../.github/workflows/release.yml) | Before pushing: `/healthz` must report chunks > 0, `/api/ask` must return citations, and the eval gate runs inside the image. A broken image is never published. |
| **Tag releases** | `release.yml` | `git tag v0.1.0` and push: build, smoke test, push `:0.1.0` and `:latest` to GHCR, create a GitHub Release. Write permissions exist only in this workflow, and login uses the built-in token. |
| **Layer order and security** | `Dockerfile`, [Dockerfile.dockerignore](Dockerfile.dockerignore) | Dependencies, then the 1 GB model, then code, so a code change rebuilds in seconds. Non-root user. The ignore file is an allowlist, so `.env` can't leak in by accident. |
| **Model cache in CI** | `ci.yml` step "Cache the embedding model" | The 1 GB model is cached between runs, keyed on model name and `requirements.txt`. |

## 3. MCP and agents

| Concept | Where | What to say |
|---|---|---|
| **Docs as a tool for agents** | [mcp_server.py](mcp_server.py) `build_server()`, `search_docs()` | The same retrieval offered as an MCP tool, so Claude Code or any agent can ground itself in the portal's docs. |
| **No LLM in the server** | `search_docs()` | The agent is already a model: the server returns sections with absolute URLs, and the agent writes the answer. No key, no cost, no double generation. |
| **Safe by default** | `main()`, tool schema, `ToolAnnotations` | Bound to `127.0.0.1`, which enables DNS-rebinding protection (a forged `Host` gets 421). Inputs validated by the schema; marked read-only; instructions say results are data, not instructions. |
| **SDK verified, not assumed** | `requirements.txt` `mcp==2.3.0` | In v2, `FastMCP` became `MCPServer` and `host`/`port` moved to `run()`. Checked against the installed package before writing code. |

## 4. Product

**Unanswered questions are a docs roadmap.** Every `not_found` and `uncited` answer is a visitor telling you what the docs are missing, in their own words. For a developer portal:

1. Log the question, the top scores and the mode (no personal data).
2. Group similar questions weekly (the same embeddings can cluster them).
3. Each cluster becomes a docs ticket: "12 people asked how to rotate an API key; we have no page."
4. After the page ships, the eval gains a question for it, and CI keeps it answered.

That turns the assistant from a cost centre into a **feedback loop**: support deflection plus a measurable docs-gap signal. Add 👍/👎 for answer quality, and the eval set grows from real traffic instead of guesses.

Other product points: citations build trust because users can verify; "not covered" is better than a confident wrong answer for a developer audience; retrieval-only mode means the feature degrades instead of breaking.

## 5. Alexa+ talking points

| Topic | Point |
|---|---|
| **Latency** | Retrieval is milliseconds and local; the LLM dominates (seconds with Claude, ~1–2 min on CPU-only Ollama). Voice needs a sub-second first response: stream tokens, use a smaller or faster model, cache popular questions, and keep retrieval near the model. |
| **Spoken short answers** | The prompt already says "keep answers short". For voice: one or two sentences, no code blocks, no `[n]` read aloud; the full answer and links go to the companion app. The `mode` field lets the voice layer choose phrasing ("I couldn't find that in the docs"). |
| **Source attribution** | Citations are structured data (`{n, title, url}`), not text, so a voice layer can say "according to the Deploy your site guide" and send the link to the phone. Attribution survives the change of medium. |
| **Permissions** | For private or personal content, filter at **retrieval** time by the user's permissions, never after generation: content the user can't see must never reach the prompt. Same principle as the local-only ingest and the `Origin` check. |
| **Safety** | `not_found` without calling the LLM, and `uncited` hiding unsupported replies, are guardrails that don't depend on the model behaving. Those matter more when there's no screen to check. |
| **Multilingual** | The embedding model is multilingual, and the prompt answers in the question's language, so one index serves many locales. |

## 6. Questions you might get

**Why not just give the LLM the whole docs site?** Cost and latency grow with every question, accuracy drops in long contexts, and you lose precise citations. Retrieval sends ~4,000 characters instead of the whole site.

**Why a local embedding model?** No per-query cost, no rate limits, docs never leave the machine at ingest, and it works offline and in CI. The trade-off is a 1 GB model and a 3 GB image.

**Why Chroma?** Embedded, file-based, zero ops for 37 chunks. At scale: pgvector if you already run Postgres, or a managed vector DB; the `DocStore` wrapper is the only place that would change.

**How do you know it works?** Unit tests for each rule, an eval with numbers (hit@5, MRR, rejection), a link check against the real build, and a smoke test on the actual image. All in CI.

**What would you do next?** Hosted backend, hybrid search plus reranking (the two weak eval questions show why), feedback logging, and unanswered-question analytics. See the [production gaps](README.md#12-production-gaps).

**What was the hardest bug?** Citation URLs. Docusaurus has many routing rules (number prefixes, `index.md`, slugs, duplicate-heading suffixes like `#deploy-your-site-1`). That's why the link check runs against the real build instead of trusting the unit tests.
