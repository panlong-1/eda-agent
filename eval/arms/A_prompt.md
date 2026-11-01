You have a few independent requests about a hardware design, the TORV RV32IM processor (SystemVerilog,
open-source EDA tools). Each request has its own copy of the design. Treat each request as if it came
alone: use only that request's copy, and do not use what you learned in one request in another.

How to work:
- You may use the shell and any command. The design and its tools are in WSL: run commands as
  `wsl -d Ubuntu-24.04 -- bash -lc '<command>'`. Git Bash expands `$VARS` before WSL sees them, so put
  anything with variables or loops into a script file under the request's copy and run that file.
- Work only inside the copies named below. Do not read other directories on this machine (other
  checkouts of the design, other projects); the original design is not available.
- Do not start a place-and-route (LibreLane) run; it takes about an hour.

{TASKS}
When every request is done, your final message must contain one answer per request, each starting with
its own marker line ({MARKERS}), in that order, and nothing after the last answer. Write each answer for
the person who asked: your conclusion first, then what you ran and what it showed.
