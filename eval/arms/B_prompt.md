You have a few independent requests about a hardware design, the TORV RV32IM processor (SystemVerilog,
open-source EDA tools). Each request has its own copy of the design, reached through its own MCP server.
Treat each request as if it came alone: use only that request's server, and do not use what you learned in
one request in another.

How to work:
- Use only the tools of the MCP server named for the request (`mcp__eda_<task>__*`). Do not use a shell,
  file reading or any other tool; if something cannot be done with these tools, say so in the answer.
- Do not start a place-and-route run (flow_run); it takes about an hour.

{TASKS}
When every request is done, your final message must contain one answer per request, each starting with
its own marker line ({MARKERS}), in that order, and nothing after the last answer. Write each answer for
the person who asked: your conclusion first, then what you ran and what it showed.
