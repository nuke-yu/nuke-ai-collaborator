# Claude Code TS vs. Nuke Collaborator: Parity Audit & Code Review Summary
## (Architect Decision Matrix — Consensus Edition)

This document presents a horizontal parity audit comparing Nuke Collaborator (Python/FastAPI) against the official TypeScript codebase of Claude Code (`claude-code-haha-main`). It incorporates team architectural decisions and details refined engineering conclusions.

---

## 1. Feature & Architecture Level Matrix

| Dimension | 🤖 Nuke Collaborator | 💬 Claude Code TS (`claude-code-haha-main`) | Refined Parity Decision / Action |
| :--- | :--- | :--- | :--- |
| **Sandbox Substrate** | **Docker Containers** | **Host OS Sandboxing** (`srt` / `bwrap` / `sandbox-exec`) | **Keep As Is**: Nuke uses Docker containers for portable OS-level isolation across macOS/Windows/Linux, whereas Claude uses host sandboxing wrappers. |
| **Filesystem & Network Control** | Mounts workspace; network is all-or-nothing (`--network=none` or full). | Fine-Grained OS Filtering via HTTP/SOCKS5 localhost proxies. | **No Action**: Nuke relies on container isolation rather than complex domain-level proxying on the host. |
| **Main Repo Protection** | None. Sandbox can read and write all files inside the bind-mounted workspace. | **Bare Git Repo Protection** (deny write to `HEAD`, `config`, etc. + post-command scrubbing) | **REJECTED (Gap 4 - Inapplicable)**: Claude Code runs Git unsandboxed on the host, creating an escape vector. Nuke runs symmetrically (either both local or both containerized). No escape route exists. |
| **LSP Operations** | Request-Response for `definition`, `references`, `hover`, `document_symbols`. | Full operations suite (`goToDefinition`, `findReferences`, `hover`, `documentSymbol`, `workspaceSymbol`, `goToImplementation`, etc.) | **No Action**: Nuke's current Jedi + typescript-language-server surface is sufficient for ReAct code intelligence. |
| **LSP Passive Diagnostics** | None. Server-initiated notifications are ignored. | Passive Diagnostics Streaming + auto-injected system prompt attachments. | **DEFERRED (Gap 1)**: Highly coupled with workspace diagnostics lifecycle. Delayed until doc pipeline redesign. |
| **Memory Management** | 5-stage pipeline (viewpoint multi-bot compression + log truncation redirection). | 3-stage pipeline (microcompact, snip, compaction) + Session Memory Background Agent. | **DEFERRED (Gap 2)**: Background extraction via forked subagents deferred. Needs analysis vs ChromaDB memory layers. |
| **Compaction Robustness** | Count-based microcompaction + snip + 9-section structured prompt compaction. | Image/document stripping + PTL (Prompt Too Long) retry head truncation. | **PARTIALLY ACCEPTED (Gap 3)**: Image stripping is YAGNI due to Nuke's 2000-char truncation. PTL retry is low priority. **Fix Base64 stringification clutter bug** (quality issue). |

---

## 2. Technical Gap Decision Rationale

### Gap 1: LSP Passive Diagnostics & Context Attachments
* **Decision**: **DEFERRED**
* **Rationale**: While diagnostic streaming keeps the LLM informed of compiler issues on file save, implementing this in Python is complex and requires tight integration with workspace diagnostics state. Deferred until the document management system is redesigned.

### Gap 2: Background Session Memory Agent
* **Decision**: **DEFERRED**
* **Rationale**: Background extraction of notes and todo tracking into `.session_memory.md` overlaps with Nuke's existing ChromaDB semantic vector search and recap engines. Deferred pending a comparative validation of cost vs memory recall quality.

### Gap 3: Compaction Image Stripping & PTL Retry
* **Decision**: **PARTIALLY ACCEPTED (Optional PTL Retry + Fix Base64 Clutter Bug)**
* **Rationale**: 
  * Nuke truncates all message content to 2000 characters before compaction, making prompt-overflow from raw image blocks impossible. Image stripping is **YAGNI**.
  * However, stringifying multimodal lists (`str(mc)`) dumps raw base64 image data into the compaction prompt context, wasting tokens. This is a **generation quality bug**.
  * PTL retry (slicing oldest messages when the compaction call itself fails) is low-priority but useful as a safety guard for long sessions.

### Gap 4: Bare Git Repository Sandbox Escape Guard
* **Decision**: **REJECTED (Inapplicable Threat Model)**
* **Rationale**: 
  * Claude Code sandboxes its bash execution but runs Git unsandboxed on the host. This asymmetry allows malicious Git hooks planted in a sandbox to run code on the host outside the sandbox.
  * Nuke has symmetric execution: in `local` mode, both bash and Git run on the host (no sandbox to escape). In `container` mode, both run inside the Docker container (hooks are confined inside the container).
  * Adding a scanner to scrub workspace files is unnecessary, risks false-positive deletions, and is net-negative.

---

## 3. Targeted Code Fix: Clean Multimodal Content Formatter

To fix the base64 clutter bug under Gap 3, the list-to-string conversion in [compact.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/compact.py) must cleanly extract text blocks and drop raw image/document data.

```python
def clean_multimodal_content(content) -> str:
    """Extract clean text from multimodal content, replacing image blocks with a placeholder."""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text") or "")
            elif btype in ("image", "image_url"):
                parts.append("[图片数据]")
            elif btype == "document":
                parts.append("[文档数据]")
            elif btype == "tool_result" and isinstance(block.get("content"), list):
                parts.append(clean_multimodal_content(block["content"]))
            else:
                parts.append(f"[{btype or '未知数据类型'}]")
        return " ".join(p for p in parts if p.strip())
    return str(content)
```
