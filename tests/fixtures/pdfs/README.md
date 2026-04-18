# PDF test fixtures

Real-PDF fixtures for the import pipeline's regression tests. The PDFs
themselves are not committed to git (they're usually large and often
copyrighted). Each fixture is referenced by absolute or user-relative
path from a test that asserts expected output shape.

## Adding a fixture

1. Place the PDF somewhere reachable (e.g. `~/Documents/`).
2. Run the import manually once and eyeball the output.
3. Write a test that skips when the PDF is absent:

    @pytest.mark.skipif(
        not Path.home().joinpath("Documents/Agentic_Design_Patterns.pdf").exists(),
        reason="fixture PDF not present on this machine",
    )
    def test_import_agentic_design_patterns(...):
        ...

4. Record the expected chapter count and a few expected chapter titles
   in the test's assertions.

## Current fixtures

None committed. The original regression case
(`Agentic_Design_Patterns.pdf`, the 265-fragment disaster) should be
added as a local fixture once the pipeline is working.
