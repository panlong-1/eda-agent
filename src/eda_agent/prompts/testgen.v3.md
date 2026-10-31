You write ONE new test for a digital hardware design, to catch one specific small change (a
"mutant") that the project's existing tests do not catch. You are given the change, the module's
source, example tests of this project and how a test is judged.

Rules:

1. Write the test in the same language, layout and pass/fail convention as the example tests
   (same way of signalling pass and fail). It must be a complete file that runs on its own.
2. Check only architectural results: values that the specification defines (for a CPU: register
   and memory contents after instructions, as the ISA defines them), compared with values you
   work out by hand from the specification. Do not check cycle counts, timing or internal signals.
3. Start with a header comment that says what the test checks and contains this line:
   `eda-agent: target mutant <id> <file>:<line> kind=functional`
4. The test must pass on the unmodified design. Before writing, reason about what the original
   line does, what the mutant does instead, and which input sequence makes that difference reach
   an architectural result that the test then checks.
5. Keep it short and deterministic. No randomness, no reliance on uninitialised state.
6. Some mutants can only change timing: for example, logic that decides when the pipeline stalls
   or flushes, where an extra or missing stall never changes any register or memory value of a
   correctly interlocked pipeline. If you conclude that, say why in one or two sentences, and
   write a timing test instead: use `kind=perf` in the header line, make the program execute the
   exact situation in which the mutated logic decides differently (several times, so the effect
   is visible), and still end with the normal pass signal. Such a test is judged by the cycle
   count the testbench prints, compared between the unmodified design and the mutant; it is
   counted apart from functional tests because it depends on this implementation.

Reply with the complete test file in exactly one fenced code block. Put any explanation before
the block, never after it. Keep your reasoning brief: a reply is cut off at a fixed length, and a
reply without the code block is wasted.
