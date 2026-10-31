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

Reply with the complete test file in exactly one fenced code block. Put any explanation before
the block, never after it.
