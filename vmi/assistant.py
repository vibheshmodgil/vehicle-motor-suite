"""AssistantMixin -- a collapsible local-LLM + RAG chat sidebar.

Docks into the app's existing tk.PanedWindow (see app.py) as a third pane,
toggled open/closed from a toolbar button -- closed by default, like the
Claude/ChatGPT sidebar in VS Code. All LLM work (retrieval + Ollama calls,
knowledge-base rebuilds) runs on a background thread; results come back
through a queue.Queue that a self.after() poller drains on the Tk main
thread -- this is the app's first background-threading code, so it's kept
self-contained rather than introducing a general async framework.
"""

import datetime
import json
import queue
import threading

import customtkinter as ctk

from .theme import COLORS, FONTS
from . import llm_client
from . import rag_store

SYSTEM_PROMPT = (
    "You are an assistant embedded in the Vehicle-Motor Integration Suite, "
    "a powertrain sizing tool. Answer using the provided context chunks "
    "(past scenarios, the standard-motor library, testing standards, "
    "datasheets, and the app's own flow documentation) when relevant. If the "
    "context doesn't cover the question, say so plainly instead of guessing."
)

CHAT_LOG_PATH = "assistant_chat_log.jsonl"


class AssistantMixin:

    def build_assistant_panel(self):
        """Build the sidebar's contents. Not docked yet -- toggle_assistant_panel
        adds/removes it from the PanedWindow."""
        self._assistant_open = False
        self._assistant_queue = queue.Queue()
        self._assistant_busy = False

        panel = ctk.CTkFrame(self.paned, fg_color=COLORS["background"])
        self.assistant_panel = panel

        ctk.CTkLabel(
            panel, text="Assistant", font=(FONTS["family_semibold"], 13, "bold"),
            text_color=COLORS["text"],
        ).pack(side="top", anchor="w", padx=10, pady=(10, 4))

        self.assistant_history = ctk.CTkTextbox(
            panel, fg_color=COLORS["card"], text_color=COLORS["text"],
            wrap="word", font=(FONTS["family"], 12), state="disabled",
        )
        self.assistant_history.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 6))
        self.assistant_history.tag_config("user", foreground=COLORS["primary"])
        self.assistant_history.tag_config("assistant", foreground=COLORS["text"])
        self.assistant_history.tag_config("meta", foreground=COLORS["text_muted"])

        input_row = ctk.CTkFrame(panel, fg_color="transparent")
        input_row.pack(side="top", fill="x", padx=10, pady=(0, 6))
        self.assistant_entry = ctk.CTkEntry(
            input_row, placeholder_text="Ask about results, standards, or how this works...",
        )
        self.assistant_entry.pack(side="left", fill="x", expand=True)
        self.assistant_entry.bind("<Return>", lambda e: self._send_chat_message())
        self.assistant_send_btn = ctk.CTkButton(
            input_row, text="Send", width=60, command=self._send_chat_message,
        )
        self.assistant_send_btn.pack(side="left", padx=(6, 0))

        kb_row = ctk.CTkFrame(panel, fg_color="transparent")
        kb_row.pack(side="top", fill="x", padx=10, pady=(0, 4))
        self.assistant_kb_btn = ctk.CTkButton(
            kb_row, text="Rebuild Knowledge Base", command=self._rebuild_kb_async,
        )
        self.assistant_kb_btn.pack(side="left")

        self.assistant_status_label = ctk.CTkLabel(
            panel, text="", font=(FONTS["family"], 11),
            text_color=COLORS["text_muted"], anchor="w",
        )
        self.assistant_status_label.pack(side="top", fill="x", padx=10, pady=(0, 8))

        self._append_history(
            "Ask me about past results, testing standards, datasheets you've "
            "dropped into knowledge_base/, or how an analysis in this app "
            "works. Click \"Rebuild Knowledge Base\" after adding or "
            "removing files there.",
            "meta",
        )
        self._poll_assistant_queue()

    def toggle_assistant_panel(self):
        if self._assistant_open:
            self.paned.forget(self.assistant_panel)
            self._assistant_open = False
        else:
            # No before=/after= -> PanedWindow appends at the end, i.e. the
            # rightmost pane (container and plot_frame were already added).
            self.paned.add(self.assistant_panel, minsize=280)
            self._assistant_open = True

    def _append_history(self, text, tag="assistant"):
        self.assistant_history.configure(state="normal")
        self.assistant_history.insert("end", text.rstrip() + "\n\n", tag)
        self.assistant_history.configure(state="disabled")
        self.assistant_history.see("end")

    def _set_assistant_status(self, text):
        try:
            self.assistant_status_label.configure(text=text)
        except Exception:
            pass

    def _set_assistant_busy(self, busy):
        self._assistant_busy = busy
        state = "disabled" if busy else "normal"
        self.assistant_send_btn.configure(state=state)
        self.assistant_kb_btn.configure(state=state)

    def _send_chat_message(self):
        if self._assistant_busy:
            return
        question = self.assistant_entry.get().strip()
        if not question:
            return
        self.assistant_entry.delete(0, "end")
        self._append_history(f"You: {question}", "user")
        self._set_assistant_busy(True)
        self._set_assistant_status("Thinking...")
        threading.Thread(target=self._chat_worker, args=(question,), daemon=True).start()

    def _chat_worker(self, question):
        try:
            hits = rag_store.query(question)
            context = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits) or "(no matching context found)"
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ]
            reply = llm_client.chat(messages)
            self._log_chat_exchange(question, reply, "ok")
            self._assistant_queue.put(("chat_reply", reply))
        except llm_client.OllamaError as e:
            self._log_chat_exchange(question, str(e), "error")
            self._assistant_queue.put(("chat_error", str(e)))
        except Exception as e:
            self._log_chat_exchange(question, f"Unexpected error: {e}", "error")
            self._assistant_queue.put(("chat_error", f"Unexpected error: {e}"))

    def _log_chat_exchange(self, question, answer, status):
        """Append one question/answer pair to CHAT_LOG_PATH (JSON Lines) so
        the conversation history can be reviewed or exported later for
        fine-tuning / evaluation. Runs on the background chat thread -- no
        Tk widgets touched here, so no queue hop is needed."""
        record = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "answer": answer,
            "status": status,
        }
        try:
            with open(CHAT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _rebuild_kb_async(self):
        if self._assistant_busy:
            return
        self._set_assistant_busy(True)
        self._set_assistant_status("Rebuilding knowledge base...")

        def progress(msg):
            self._assistant_queue.put(("kb_progress", msg))

        def worker():
            try:
                n_files, n_chunks, warnings = rag_store.rebuild_index(progress=progress)
                self._assistant_queue.put(("kb_done", (n_files, n_chunks, warnings)))
            except llm_client.OllamaError as e:
                self._assistant_queue.put(("kb_error", str(e)))
            except Exception as e:
                self._assistant_queue.put(("kb_error", f"Unexpected error: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_assistant_queue(self):
        try:
            while True:
                kind, payload = self._assistant_queue.get_nowait()
                if kind == "chat_reply":
                    self._append_history(payload, "assistant")
                    self._set_assistant_status("Ready.")
                    self._set_assistant_busy(False)
                elif kind == "chat_error":
                    self._append_history(f"[error] {payload}", "meta")
                    self._set_assistant_status("Error -- see message above.")
                    self._set_assistant_busy(False)
                elif kind == "kb_progress":
                    self._set_assistant_status(payload)
                elif kind == "kb_done":
                    n_files, n_chunks, warnings = payload
                    msg = f"Indexed {n_files} changed file(s), {n_chunks} chunk(s)."
                    if warnings:
                        msg += f" {len(warnings)} file(s) skipped."
                    self._append_history(msg, "meta")
                    self._set_assistant_status("Ready." if not warnings else "; ".join(warnings)[:200])
                    self._set_assistant_busy(False)
                elif kind == "kb_error":
                    self._append_history(f"[error] {payload}", "meta")
                    self._set_assistant_status("Error -- see message above.")
                    self._set_assistant_busy(False)
        except queue.Empty:
            pass
        self.after(150, self._poll_assistant_queue)
