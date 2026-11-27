# Expandable tool traces

Within the existing conversation and Agent debug trace panel, completed or failed tool calls are individually expandable. The collapsed row keeps its summary and status. The expanded row shows the resolved tool name, input, and output or error. Other rows, including historical rows without details, keep their current summary-only presentation.

The two chat streams send bounded previews on tool completion. Structured input and output mask common credential-named fields recursively; this does not detect arbitrary secrets embedded in free-form text. Details are never added to persistent logs, exports, or stored messages. The current page can still show live previews after a normal log synchronization: overlay them onto saved rows by trace event identity and run, never by tool name or position. Refreshing the page discards previews. Debug traces remain in the debug view's memory only.

Verify workspace, published, and debug chat streams, successful and failed tools, redaction and truncation, absence of previews in logs, repeated calls to the same tool, and collapsed rendering with readable, escaped text.
