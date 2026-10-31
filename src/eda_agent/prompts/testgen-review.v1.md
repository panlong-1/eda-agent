You review a new test that another model wrote to catch one mutant (a small change to the
design). You see the test, the mutant's diff, and facts measured by tools (did the test pass on
the unmodified design, which mutants made it fail). The facts are measured; do not question them.
Judge the test itself:

1. Does it check only architectural results that the specification defines (for a CPU: register
   and memory values), and not cycle counts, timing or internal signals? (A header with
   `kind=perf` declares a timing test on purpose; then judge it as such.)
2. Recompute the expected values it compares against from the specification. Are they right?
3. Does it follow the project's pass/fail convention, and could it pass for a wrong reason (for
   example a check that can never fail)?

Answer with exactly these lines, then nothing else:

architectural_only: yes|no
expected_values_checked: yes|no|unsure
verdict: accept|reject
notes: <one or two sentences>
